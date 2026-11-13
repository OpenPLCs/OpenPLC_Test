"""T1-39 on the real board (requirement R1-45) -- unplug USB mid-upload, plug back, upload again without a reset.

    python tools/run_usb_unplug_reupload.py --key <owner.pem>

Needs a person at the board; every step is spoken (Windows speech) as well as
printed. Uploads onboard/usb_unplug/BigImage (about 1 MB) over USB CDC and
suspends IAPTool once three blocks are out, so the bootloader is left halfway
through receiving; the person unplugs the cable, IAPTool is let go, the person
plugs it back. The bootloader must then still answer as BOOTLD and take a
second upload over USB without a reset.

Not covered: power loss, or a cable that cuts data but keeps power.

Exit 0 = pass, 1 = fail, 2 = setup problem.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (cfg, PLATFORM, Section, Ok, Fail, find_board_log_ports,  # noqa: E402
                    get_iap_tool, get_scratch_dir, run_capture)

FQBN = "OpenPLC_Alpha:stm32:OPEN-PLC:pnum=PLC_H743,usb=CDCgen"
NAME = "BigImage"
BLOCK = 8192
WHERE = b"openplc_server_where_r_y\n"


class Speaker(object):
    """Speaks each cue as soon as it is given: a person cannot watch the screen
    and the cable at once. Prints only where Windows speech is not there."""

    def __init__(self):
        self.p = None
        if PLATFORM == "windows":
            self.p = subprocess.Popen(
                ["powershell", "-NoProfile", "-Command",
                 "Add-Type -AssemblyName System.Speech; "
                 "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                 "[Console]::InputEncoding=[Text.Encoding]::UTF8; "
                 "while(($l=[Console]::In.ReadLine()) -ne $null){ $s.Speak($l); [Console]::Out.WriteLine('done') }"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")

    def say(self, text):
        print(">>> " + text, flush=True)
        if self.p is not None:
            self.p.stdin.write(text + "\n")
            self.p.stdin.flush()
            self.p.stdout.readline()


def wait_port(want, timeout):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if bool(find_board_log_ports(cfg.BOARD_UID)) == want:
            return True
        time.sleep(0.2)
    return False


def identity(port_name):
    import serial
    try:
        s = serial.Serial(port_name, 1200, timeout=0.3)
        time.sleep(0.3)
        s.write(WHERE)
        end, b = time.monotonic() + 3, b""
        while time.monotonic() < end and b"\n" not in b:
            b += s.read(256)
        s.close()
        return b.decode("utf-8", "replace").strip()
    except Exception as e:
        return "<%s>" % e


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cdc", default=getattr(cfg, "CDC_PORT", ""))
    ap.add_argument("--key", default="")
    args = ap.parse_args()
    try:
        import psutil
    except ImportError:
        Fail("needs psutil (pip install psutil) to pause IAPTool mid-upload")
        return 2

    sketch = Path(cfg.TEST_REPO) / "onboard" / "usb_unplug" / NAME
    build = get_scratch_dir() / ("t1_39_" + NAME)
    image = str(build / (NAME + ".ino.bin"))
    Section("build")
    out, rc = run_capture([cfg.ARDUINO_CLI, "compile", "--config-file",
                           cfg.ARDUINO_CLI_CONFIG, "--fqbn", FQBN,
                           "--build-path", build, sketch])
    if rc:
        print("\n".join(out.splitlines()[-5:]))
        Fail("compile failed")
        return 2
    key = ["--key=%s" % args.key] if args.key else []
    sp = Speaker()

    Section("upload, unplugged halfway")
    sp.say("准备拔USB线。我说拔，就拔掉")
    p = subprocess.Popen([get_iap_tool(), "cdc", image, args.cdc, "-v"] + key,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace")
    paused = False
    for line in p.stdout:
        if "Sent " in line and " bytes of total " in line:
            if int(line.split("Sent ")[1].split(" ")[0]) >= 3 * BLOCK:
                psutil.Process(p.pid).suspend()
                paused = True
                break
    if not paused:
        p.wait()
        Fail("the upload never got three blocks out")
        return 2
    sp.say("拔")
    gone = wait_port(False, 60)
    psutil.Process(p.pid).resume()
    try:
        p.wait(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
    if not gone:
        Fail("the board's USB port never went away")
        return 2
    time.sleep(2)
    sp.say("插回去")
    if not wait_port(True, 60):
        Fail("the board's USB port did not come back")
        return 2
    time.sleep(3)

    Section("upload again, no reset")
    who = identity(args.cdc)
    print("  board answers: " + who)
    out, rc = run_capture([get_iap_tool(), "cdc", image, args.cdc] + key)
    sp.say("结束")
    if "_BOOTLD_" not in who:
        Fail("after the replug the board did not answer as the bootloader")
        return 1
    if rc:
        print("\n".join(out.splitlines()[-8:]))
        Fail("the second upload failed")
        return 1
    Ok("unplugged at three blocks, plugged back, uploaded again without a reset")
    return 0


if __name__ == "__main__":
    sys.exit(main())
