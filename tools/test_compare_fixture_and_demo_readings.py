"""Unit tests for compare_fixture_and_demo_readings (T3-27's comparison). Run from the repo root:

    python tools/test_compare_fixture_and_demo_readings.py

The records are hand-made in the shape of $PORTTOOL/src/ptreport/ptreport.go:
the simulated board writes no record and cannot run step 6
($PROD/docs/modules/M4/PORTTOOL-FLOW.md C.3.3).

Exit 0 = all pass, 1 = at least one failed.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compare_fixture_and_demo_readings as cmp  # noqa: E402

UID = "003400413135511439303538"
T0 = datetime(2026, 11, 14, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))


def ts(sec):
    # Go writes nanoseconds; nine digits exercise the truncation.
    t = T0 + timedelta(seconds=sec)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + "%06d123" % t.microsecond + "+08:00"


def item(no, name, port, start, dur, outcome="PASS", ch=0, value=""):
    it = {"no": no, "where": "#%d" % no, "item": name, "port": port, "outcome": outcome,
          "value": value, "started_at": ts(start), "duration_ms": int(dur * 1000)}
    if ch:
        it["channel"] = ch
    return it


def point(at, item_, set_, dev, raw):
    return {"n": 1, "started_at": ts(at), "duration_ms": 900, "outcome": "PASS",
            "extra": {"item": item_, "set": set_, "raw": raw, "value": set_, "ref_mv": "1000", "dev_pct_fs": dev}}


def step(typ, name, base, ai1, ai2, rs485="PASS", temp="ok=1, vdda=2501, ch1[1]=307, ch2[1]=314",
         extra_items=()):
    """One step: AI1 and AI2 walked with the same set points, then RS485 and temperature."""
    items = [item(1, "ain.read1", "ain", base, 10, value="set=8750 mV, got=8750, value=0.1"),
             item(2, "ain.read2", "ain", base + 20, 10),
             item(3, "rs485.peer", "rs485", base + 40, 5, outcome=rs485, value="miss=0, junk=0"),
             item(4, "temp.read", "temp", base + 50, 2, ch=1, value=temp),
             item(5, "temp.read", "temp", base + 50, 2, ch=2, value=temp)]
    items += list(extra_items)
    atts = [{"n": 1, "started_at": ts(base - 5), "duration_ms": 10, "outcome": "PASS",
             "sent": ["pt.caps"]}]  # not a point: no set
    for i, (s, dev, raw) in enumerate(ai1):
        atts.append(point(base + 1 + i, "ain.read1", s, dev, raw))
    for i, (s, dev, raw) in enumerate(ai2):
        atts.append(point(base + 21 + i, "ain.read2", s, dev, raw))
    outcome = "PASS" if rs485 == "PASS" else "FAIL"
    return {"id": name, "type": typ, "outcome": outcome, "started_at": ts(base - 10),
            "duration_ms": 60000, "attempts": atts, "items": items}


AI1 = [("1250", "0.10", "8200"), ("8750", "0.20", "57000")]
AI2 = [("1250", "-0.05", "8100"), ("8750", "0.00", "56900")]


def record(steps, uid=UID):
    return {"plan": "station6-poweron", "limit_version": "1", "tool_version": "0.18.13",
            "firmware": "0.18.9", "board_uid": uid, "port": "COM12",
            "started_at": ts(0), "ended_at": ts(999), "steps": steps}


class Compare(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write(self, name, rep):
        p = Path(self.dir.name) / name
        p.write_text(rep if isinstance(rep, str) else json.dumps(rep, indent=2), encoding="utf-8")
        return str(p)

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cmp.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_consistent_in_one_record(self):
        demo_ai1 = [("1250", "0.30", "8230"), ("8750", "0.40", "57030")]
        p = self.write("r.json", record([step("2", "functional", 100, AI1, AI2),
                                         step("6", "user", 300, demo_ai1, AI2,
                                              temp="ok=1, vdda=2503, ch1[1]=320, ch2[1]=330")]))
        code, out, _ = self.run_main(p)
        self.assertEqual(code, 0, out)
        self.assertIn("port ain: consistent", out)
        self.assertIn("ain.read1 set=8750 dev", out)
        self.assertIn("raw 57000 / 57030", out)
        self.assertIn("port temp: consistent", out)

    def test_ai_point_beyond_tolerance(self):
        demo_ai2 = [("1250", "-0.05", "8100"), ("8750", "0.80", "57500")]
        p = self.write("r.json", record([step("2", "functional", 100, AI1, AI2),
                                         step("6", "user", 300, AI1, demo_ai2)]))
        code, out, _ = self.run_main(p)
        self.assertEqual(code, 1, out)
        self.assertIn("port ain: DIFFERS", out)
        # Attributed by time window: AI2's point differs, AI1's same set value does not.
        diff = [l for l in out.splitlines() if " DIFF " in l]
        self.assertEqual(len(diff), 1, out)
        self.assertIn("ain.read2 set=8750", diff[0])

    def test_tolerance_override(self):
        demo_ai2 = [("1250", "-0.05", "8100"), ("8750", "0.80", "57500")]
        p = self.write("r.json", record([step("2", "functional", 100, AI1, AI2),
                                         step("6", "user", 300, AI1, demo_ai2)]))
        code, out, _ = self.run_main(p, "--ai-tol", "1.0")
        self.assertEqual(code, 0, out)

    def test_outcome_differs(self):
        p = self.write("r.json", record([step("2", "functional", 100, AI1, AI2),
                                         step("6", "user", 300, AI1, AI2, rs485="FAIL")]))
        code, out, _ = self.run_main(p)
        self.assertEqual(code, 1, out)
        self.assertIn("port rs485: DIFFERS", out)
        self.assertIn("port ain: consistent", out)

    def test_temperature_beyond_tolerance(self):
        p = self.write("r.json", record([step("2", "functional", 100, AI1, AI2),
                                         step("6", "user", 300, AI1, AI2,
                                              temp="ok=1, vdda=2501, ch1[1]=340, ch2[1]=314")]))
        code, out, _ = self.run_main(p)
        self.assertEqual(code, 1, out)
        self.assertIn("port temp: DIFFERS", out)
        diff = [l for l in out.splitlines() if " DIFF " in l]
        self.assertEqual(len(diff), 1, out)
        self.assertIn("temp.read ch1 ch1[1]", diff[0])
        code, _, _ = self.run_main(p, "--temp-tol", "3.5")
        self.assertEqual(code, 0)

    def test_item_in_one_step_only_is_listed_not_judged(self):
        hold = item(6, "sdram.hold", "sdram", 160, 3)
        p = self.write("r.json", record([step("2", "functional", 100, AI1, AI2, extra_items=[hold]),
                                         step("6", "user", 300, AI1, AI2)]))
        code, out, _ = self.run_main(p)
        self.assertEqual(code, 0, out)
        self.assertIn("sdram.hold (fixture only)", out)

    def test_two_records(self):
        a = self.write("a.json", record([step("2", "functional", 100, AI1, AI2)]))
        b = self.write("b.json", record([step("6", "user", 300, AI1, AI2)]))
        code, out, _ = self.run_main(a, b)
        self.assertEqual(code, 0, out)

    def test_two_records_of_different_boards(self):
        a = self.write("a.json", record([step("2", "functional", 100, AI1, AI2)]))
        b = self.write("b.json", record([step("6", "user", 300, AI1, AI2)], uid="0" * 24))
        code, _, err = self.run_main(a, b)
        self.assertEqual(code, 2)
        self.assertIn("different boards", err)

    def test_step6_not_run(self):
        skipped = {"id": "user", "type": "6", "outcome": "SKIPPED", "reason": "not run: step 2 did not pass",
                   "started_at": ts(300), "duration_ms": 0}
        p = self.write("r.json", record([step("2", "functional", 100, AI1, AI2, rs485="FAIL"), skipped]))
        code, _, err = self.run_main(p)
        self.assertEqual(code, 2)
        self.assertIn("step 6", err)

    def test_not_a_record(self):
        code, _, _ = self.run_main(self.write("x.json", "{not json"))
        self.assertEqual(code, 2)
        code, _, _ = self.run_main(self.write("y.json", {"plan": "x"}))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=2).result
    sys.exit(0 if res.wasSuccessful() else 1)
