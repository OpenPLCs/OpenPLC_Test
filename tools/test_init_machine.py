"""Unit tests for init_machine. Run from the repo root:

    python3 tools/test_init_machine.py

Three groups, in the order the file is used:

  tables       -- SETTINGS and EXAMPLES are hand-edited lists, and the
                  common editing mistake is adding a row and forgetting one of
                  its columns. Cheap to check, and the failure is otherwise a
                  KeyError on a machine you are not sitting at.
  ports        -- the macOS branch of detect_rs232_ports, which no machine here
                  can exercise for real.
  ask_for      -- the prompt cannot be exercised the way it is used: it refuses
                  to ask unless stdin is a terminal, so a pipe cannot reach it
                  and an agent cannot either. That leaves quote stripping,
                  tilde expansion and the install-root validators untested, and
                  those are the parts most likely to be wrong.

Exit 0 = all pass, 1 = at least one failed, 2 = every case that ran passed but
the machine lacks the real paths the ask_for group needs.
"""

import builtins
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import init_machine as im  # noqa: E402

fails = 0


def check(name, got, expect):
    global fails
    ok = got == expect
    print("  [%s] %-52s got=%r" % ("PASS" if ok else "FAIL", name, got))
    if not ok:
        print("         expected=%r" % (expect,))
        fails += 1


def run(name, answers, key, kind, required, expect):
    """Answers are consumed in order; running out raises EOFError, which is what
    a real closed stdin does. Raising StopIteration instead would sail past the
    handler in ask_for and prove nothing."""
    it = iter(answers)

    def fake_input(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    builtins.input = fake_input
    check(name, im.ask_for(key, kind, required), expect)


# ---------------------------------------------------------------- tables
def test_tables():
    print("=== tables ===")
    keys = [e[0] for e in im.SETTINGS if e[0] != "__section__"]
    check("every setting has a unique name", len(keys), len(set(keys)))

    # A setting nobody can be asked about is a setting that silently stays
    # empty, and the report exists precisely to stop that.
    no_text = [k for k in keys if k not in im.WHAT_IT_IS]
    check("every setting says what it is", no_text, [])

    # EXAMPLES: windows and posix must both answer, or half the machines get a
    # "not found" with no example under it. macos is overrides only, so it is
    # allowed to be partial -- but not to invent keys.
    win, posix = im.EXAMPLES["windows"], im.EXAMPLES["posix"]
    check("windows and posix examples cover the same keys",
          sorted(set(win) ^ set(posix)), [])
    check("no example names a setting that does not exist",
          sorted((set(win) | set(posix) | set(im.EXAMPLES["macos"])) - set(keys)), [])
    check("macos overrides are a subset of posix",
          sorted(set(im.EXAMPLES["macos"]) - set(posix)), [])


# ---------------------------------------------------------------- ports
def test_macos_ports():
    """macOS has neither /dev/ttyUSB* nor /dev/ttyACM*, so the Linux globs find
    nothing there and the report reads "no adapter plugged in" on a machine
    where one is. No machine in this project can test that for real yet, so the
    filesystem is faked and only the ordering logic is under test."""
    print("=== detect_rs232_ports on macOS ===")
    fake = {
        "/dev/cu.usbserial*": ["/dev/cu.usbserial-1410"],
        "/dev/cu.usbmodem*": ["/dev/cu.usbmodem1103"],
        "/dev/cu.*": ["/dev/cu.Bluetooth-Incoming-Port", "/dev/cu.debug-console",
                      "/dev/cu.usbmodem1103", "/dev/cu.usbserial-1410"],
    }
    # IS_WIN as well as PLATFORM: detect_rs232_ports tests IS_WIN first, so
    # setting only PLATFORM leaves the Windows registry branch in charge and the
    # test silently checks nothing.
    real_platform, real_win, real_glob = im.PLATFORM, im.IS_WIN, im.glob.glob
    im.PLATFORM, im.IS_WIN = "macos", False
    im.glob.glob = lambda p: list(fake.get(p, []))
    try:
        check("USB adapters first, Bluetooth and debug-console dropped, no repeats",
              im.detect_rs232_ports(),
              ["/dev/cu.usbserial-1410", "/dev/cu.usbmodem1103"])
        im.glob.glob = lambda p: []
        before = len(im.NOTES)
        check("nothing plugged in is None, not an empty list",
              im.detect_rs232_ports(), None)
        check("and it says so rather than staying quiet",
              len(im.NOTES) > before, True)
    finally:
        im.PLATFORM, im.IS_WIN, im.glob.glob = real_platform, real_win, real_glob


# ---------------------------------------------------------------- ask_for
def test_ask_for():
    """Needs two real install roots -- one CubeIDE, one Arduino IDE -- so the
    validators have something true to accept. Without them every case would
    collapse into "rejected", which is the branch that needs a partner."""
    cube = im.detect_cubeide()
    ide = im.detect_ide()
    if not cube or not ide:
        print("=== ask_for === SKIP: needs a real CubeIDE and Arduino IDE here")
        print("       CubeIDE: %s" % (cube or "not found"))
        print("       IDE    : %s" % (ide or "not found"))
        return False
    cube_parent = str(Path(cube).parent)
    home = os.path.expanduser("~")

    print("=== ask_for ===")
    run("empty answer skips an optional setting", [""], "CUBEIDE", "path", False, None)
    run("quotes from a file manager are stripped",
        ['"%s"' % cube], "CUBEIDE", "path", False, cube)
    run("the parent of the install root is rejected, then accepted",
        [cube_parent, cube], "CUBEIDE", "path", False, cube)
    run("a path that does not exist is re-asked",
        [os.path.join(cube, "definitely-not-here"), ide], "IDE", "path", False, ide)
    run("a port is taken as typed, quotes stripped",
        ['"/dev/ttyUSB0"'], "RS232_PORT", "port", False, "/dev/ttyUSB0")
    run("a required setting asks twice before giving up",
        ["", ""], "BOOT_REPO", "path", True, None)
    run("EOF is a skip, not a crash", [], "CUBEIDE", "path", False, None)
    run("a tilde is expanded", ["~"], "WORKSPACE", "path", False, home)
    return True


def main():
    test_tables()
    print()
    test_macos_ports()
    print()

    asked = test_ask_for()

    print()
    if fails:
        print("%d failure(s)" % fails)
        return 1
    if not asked:
        print("all cases that could run pass; the ask_for group was skipped")
        return 2
    print("all cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
