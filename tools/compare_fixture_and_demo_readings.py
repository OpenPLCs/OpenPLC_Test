"""T3-27: does the board package read the hardware the way the fixture firmware does?

Compares, in PortTool full-test records, step 2 (functional test, fixture
firmware) with step 6 (user-level test, FixtureDemo built with the board package)
item by item. Criteria, default tolerances and their sources:
$PROD/docs/engineering/HOW-TO-RUN-TESTS.md "T3-27".

    python tools/compare_fixture_and_demo_readings.py RECORD
    python tools/compare_fixture_and_demo_readings.py STEP2_RECORD STEP6_RECORD

The record format is $PORTTOOL/src/ptreport/ptreport.go (Report, StepResult,
ItemRun, Attempt).

Exit 0 = every compared reading is consistent, 1 = some differ, 2 = bad input.
"""

import argparse
import json
import re
import sys
from pathlib import Path

FIXTURE_STEP = "2"
DEMO_STEP = "6"

# Defaults are provisional until measured on a board (decision 164).
DEFAULT_AI_TOL = 0.5     # % FS
DEFAULT_AO_TOL = 0.5     # % FS
DEFAULT_TEMP_TOL = 2.0   # deg C
DEFAULT_VDDA_TOL = 10.0  # mV

# Value fields compared numerically: item -> {field: (tolerance key, scale to the tolerance's unit)}.
# ch<n>[1] is deci-Celsius (plan step "temperature").
VALUE_FIELDS = {
    "temp.read": {"vdda": ("vdda", 1.0), "ch1[1]": ("temp", 0.1), "ch2[1]": ("temp", 0.1)},
}

UNITS = {"ai": "% FS", "ao": "% FS", "temp": "C", "vdda": "mV"}


class BadInput(Exception):
    pass


def load(path):
    try:
        rep = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BadInput("%s: %s" % (path, e))
    if not isinstance(rep, dict) or not isinstance(rep.get("steps"), list):
        raise BadInput("%s: not a PortTool record (no steps)" % path)
    return rep


def find_step(rep, path, typ, name):
    for st in rep["steps"]:
        if str(st.get("type")) != typ:
            continue
        if st.get("outcome") == "SKIPPED":
            raise BadInput("%s: step %s (%s) was not run: %s" % (path, typ, name, st.get("reason", "")))
        if not [it for it in st.get("items") or [] if it.get("item")]:
            raise BadInput("%s: step %s (%s) ran no item: %s" % (path, typ, name, st.get("reason", "")))
        return st
    raise BadInput("%s: has no step %s (%s)" % (path, typ, name))


def value_fields(text):
    """ItemRun.value is "k=v, k=v, ..." with free text mixed in; keep the numeric pairs."""
    out = {}
    for part in (text or "").split(", "):
        k, sep, v = part.partition("=")
        if not sep:
            continue
        try:
            out[k.strip()] = float(v.strip().split()[0])
        except (ValueError, IndexError):
            pass
    return out


def analog_kind(item):
    if item.startswith("ain.read"):
        return "ai"
    if item.startswith("aout.out"):
        return "ao"
    return None


def collect(step):
    """Per (item, channel): port, outcome, numeric value fields and analog points by set value."""
    rows = {}
    for it in step.get("items") or []:
        if not it.get("item"):
            continue  # a whole-step line carries no reading
        key = (it["item"], int(it.get("channel") or 0))
        rows[key] = {"port": it.get("port", ""), "outcome": it.get("outcome", ""),
                     "values": value_fields(it.get("value")), "points": {}}
    # A point is one attempt carrying set and dev_pct_fs; extra.item names its item (one channel per analog item).
    for att in step.get("attempts") or []:
        extra = att.get("extra") or {}
        if "set" not in extra or "dev_pct_fs" not in extra:
            continue
        key = (extra.get("item", ""), 0)
        if key in rows and analog_kind(key[0]):
            rows[key]["points"][extra["set"]] = {
                "dev": float(extra["dev_pct_fs"]),
                "raw": extra.get("raw", extra.get("ref_raw", "")),
            }
    return rows


def label(key):
    item, ch = key
    return "%s ch%d" % (item, ch) if ch else item


def compare(fx, dm, tol):
    """Rows of (port, what, fixture, demo, note, verdict); verdict is same / DIFF / one side."""
    out = []
    for key in sorted(set(fx) | set(dm)):
        a, b = fx.get(key), dm.get(key)
        port = (a or b)["port"]
        if a is None or b is None:
            side = "fixture only" if b is None else "demo only"
            out.append((port, label(key), (a or {}).get("outcome", "-"), (b or {}).get("outcome", "-"),
                        "item ran in one step only", side))
            continue
        out.append((port, label(key) + " outcome", a["outcome"], b["outcome"], "",
                    "same" if a["outcome"] == b["outcome"] else "DIFF"))
        kind = analog_kind(key[0])
        if kind:
            for s in sorted(set(a["points"]) | set(b["points"]), key=float):
                pa, pb = a["points"].get(s), b["points"].get(s)
                what = "%s set=%s dev" % (label(key), s)
                if pa is None or pb is None:
                    out.append((port, what, "-" if pa is None else "%.3f" % pa["dev"],
                                "-" if pb is None else "%.3f" % pb["dev"], "point in one step only",
                                "fixture only" if pb is None else "demo only"))
                    continue
                d = pb["dev"] - pa["dev"]
                note = "diff %+.3f %s, tol %g; raw %s / %s" % (d, UNITS[kind], tol[kind], pa["raw"], pb["raw"])
                out.append((port, what, "%.3f" % pa["dev"], "%.3f" % pb["dev"], note,
                            "same" if abs(d) <= tol[kind] else "DIFF"))
        for field, (tkey, scale) in VALUE_FIELDS.get(key[0], {}).items():
            m = re.match(r"ch(\d+)\[", field)
            if m and key[1] and int(m.group(1)) != key[1]:
                continue  # temp.read is one item per channel, each carrying both channels' fields
            va, vb = a["values"].get(field), b["values"].get(field)
            what = "%s %s" % (label(key), field)
            if va is None or vb is None:
                out.append((port, what, "-" if va is None else "%g" % va, "-" if vb is None else "%g" % vb,
                            "field in one step only", "fixture only" if vb is None else "demo only"))
                continue
            d = (vb - va) * scale
            note = "diff %+g %s, tol %g" % (d, UNITS[tkey], tol[tkey])
            out.append((port, what, "%g" % va, "%g" % vb, note, "same" if abs(d) <= tol[tkey] else "DIFF"))
    return out


def report(rows):
    w = max(len(r[1]) for r in rows)
    ports = []
    for r in rows:
        if r[0] not in ports:
            ports.append(r[0])
    differ = []
    for port in ports:
        mine = [r for r in rows if r[0] == port]
        compared = [r for r in mine if r[5] in ("same", "DIFF")]
        bad = [r for r in compared if r[5] == "DIFF"]
        if not compared:
            verdict = "not compared (ran in one step only)"
        elif bad:
            verdict = "DIFFERS"
            differ.append(port)
        else:
            verdict = "consistent"
        print("")
        print("port %s: %s" % (port or "?", verdict))
        print("  %-*s  %-10s  %-10s  %-12s  %s" % (w, "compared", "fixture", "demo", "verdict", "note"))
        for r in mine:
            print("  %-*s  %-10s  %-10s  %-12s  %s" % (w, r[1], r[2], r[3], r[5], r[4]))
    return differ


def main(argv=None):
    ap = argparse.ArgumentParser(description="T3-27: compare a full test's step 2 (fixture firmware) "
                                             "with its step 6 (FixtureDemo), item by item.")
    ap.add_argument("records", nargs="+", metavar="RECORD",
                    help="one record holding steps 2 and 6, or the step-2 record then the step-6 record")
    ap.add_argument("--ai-tol", type=float, default=DEFAULT_AI_TOL, help="AI point, %% FS (default %(default)s)")
    ap.add_argument("--ao-tol", type=float, default=DEFAULT_AO_TOL, help="AO point, %% FS (default %(default)s)")
    ap.add_argument("--temp-tol", type=float, default=DEFAULT_TEMP_TOL,
                    help="temperature, deg C (default %(default)s)")
    ap.add_argument("--vdda-tol", type=float, default=DEFAULT_VDDA_TOL,
                    help="temperature channel reference, mV (default %(default)s)")
    args = ap.parse_args(argv)
    if len(args.records) > 2:
        ap.error("give one record, or two")
    tol = {"ai": args.ai_tol, "ao": args.ao_tol, "temp": args.temp_tol, "vdda": args.vdda_tol}

    try:
        fx_path = args.records[0]
        dm_path = args.records[-1]
        fx_rep, dm_rep = load(fx_path), load(dm_path)
        if fx_rep.get("board_uid", "") != dm_rep.get("board_uid", ""):
            raise BadInput("the records are of different boards: %s, %s"
                           % (fx_rep.get("board_uid") or "?", dm_rep.get("board_uid") or "?"))
        fx = collect(find_step(fx_rep, fx_path, FIXTURE_STEP, "functional, fixture firmware"))
        dm = collect(find_step(dm_rep, dm_path, DEMO_STEP, "user-level, FixtureDemo"))
    except BadInput as e:
        print("bad input: %s" % e, file=sys.stderr)
        return 2

    rows = compare(fx, dm, tol)
    if not any(r[5] in ("same", "DIFF") for r in rows):
        print("bad input: steps 2 and 6 have no item in common", file=sys.stderr)
        return 2
    print("board %s" % (fx_rep.get("board_uid") or "?"))
    print("fixture: step %s of %s (firmware %s)" % (FIXTURE_STEP, fx_path, fx_rep.get("firmware") or "?"))
    print("demo:    step %s of %s" % (DEMO_STEP, dm_path))
    differ = report(rows)
    one_side = sorted({r[1] + " (" + r[5] + ")" for r in rows if r[5] not in ("same", "DIFF")})
    print("")
    if one_side:
        print("not compared, ran in one step only: " + ", ".join(one_side))
    if differ:
        print("RESULT: board package differs from the fixture firmware on: " + ", ".join(differ))
        return 1
    print("RESULT: board package consistent with the fixture firmware on every compared port")
    return 0


if __name__ == "__main__":
    sys.exit(main())
