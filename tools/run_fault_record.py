"""T3-26 on the real board (requirement R3-22) -- a fault record survives the reset and comes out on USB.

    python tools/run_fault_record.py --key <owner.pem>

Uploads onboard/fault_record/FaultRecord over USB CDC and sends it 'F': the
sketch writes to an unmapped address, the core logs [sys][FAULT] and resets.
The port is then opened the plain way (DTR raised as it opens, as pyserial
and most terminals do) and must show the [sys][FAULT] line followed by this
boot's [sys][BL] lines.

Exit 0 = pass, 1 = fail, 2 = setup problem.
"""

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (cfg, Section, Ok, Fail, BoardLog, find_board_log_ports,  # noqa: E402
                    get_iap_tool, get_scratch_dir, run_capture)

FQBN = "OpenPLC_Alpha:stm32:OPEN-PLC:pnum=PLC_H743,usb=CDCgen"
NAME = "FaultRecord"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cdc", default=getattr(cfg, "CDC_PORT", ""))
    ap.add_argument("--key", default="")
    args = ap.parse_args()

    sketch = Path(cfg.TEST_REPO) / "onboard" / "fault_record" / NAME
    build = get_scratch_dir() / ("t3_26_" + NAME)
    Section("build")
    out, rc = run_capture([cfg.ARDUINO_CLI, "compile", "--config-file",
                           cfg.ARDUINO_CLI_CONFIG, "--fqbn", FQBN,
                           "--build-path", build, sketch])
    if rc:
        print("\n".join(out.splitlines()[-5:]))
        Fail("compile failed")
        return 2

    Section("upload")
    argv = [get_iap_tool(), "cdc", str(build / (NAME + ".ino.bin")), args.cdc]
    if args.key:
        argv.append("--key=%s" % args.key)
    out, rc = run_capture(argv)
    if rc:
        print("\n".join(out.splitlines()[-8:]))
        Fail("upload failed (a newer app on the board is refused by the version gate)")
        return 2

    Section("fault")
    import serial
    port = None
    for _ in range(100):
        hits = find_board_log_ports()
        if hits:
            try:
                port = serial.Serial(hits[0], 115200, timeout=0.2)
                break
            except Exception:
                pass
        time.sleep(0.2)
    if port is None:
        Fail("board USB port 1 never came back after the upload")
        return 2
    time.sleep(1.0)
    port.write(b"F")
    try:   # the port goes away when the board resets
        end = time.monotonic() + 3
        while time.monotonic() < end:
            port.read(4096)
    except Exception:
        pass
    try:
        port.close()
    except Exception:
        pass

    Section("after the reset")
    log = BoardLog(sys_only=True)
    fault = after = None
    for ln in log.lines(20, until=re.compile(r"\[sys\]\[BL\].*(APP|UPLOAD) Mod")):
        print("  " + ln)
        if fault is None and "[sys][FAULT]" in ln:
            fault = ln
        elif fault is not None and "[sys][BL]" in ln:
            after = ln
    log.close()
    if fault is None:
        Fail("no [sys][FAULT] line on USB port 1 after the reset")
        return 1
    if after is None:
        Fail("[sys][FAULT] came out, but not this boot's [sys][BL] lines after it")
        return 1
    Ok("fault record survived the reset: " + fault)
    return 0


if __name__ == "__main__":
    sys.exit(main())
