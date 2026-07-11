#!/usr/bin/env python3
"""Оставить один auto_cycle; не трогать рабочий scanner/upload."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable
AUTO_CYCLE = ROOT / "auto_cycle.py"
AUTO_CYCLE_LOCK = ROOT / "auto_cycle.pid"
SCANNER_LOCK = ROOT / "scanner.pid"
BURST_LOCK = ROOT / "burst.pid"


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_pid(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return 0


def kill_pid(pid: int) -> None:
    if not pid_alive(pid):
        return
    print(f"kill {pid}")
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


def list_auto_cycle_pids() -> list[int]:
    if sys.platform != "win32":
        return []
    out = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -like '*auto_cycle.py*' } | "
                "Select-Object -ExpandProperty ProcessId"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    pids: list[int] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return sorted(set(pids))


def main() -> None:
    keep_scanner = read_pid(SCANNER_LOCK)
    keep_burst = read_pid(BURST_LOCK)
    print(f"keep scanner={keep_scanner} burst={keep_burst}")

    for pid in list_auto_cycle_pids():
        kill_pid(pid)

    AUTO_CYCLE_LOCK.unlink(missing_ok=True)
    time.sleep(2)

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    log_path = ROOT / "auto_cycle.log"
    with open(log_path, "a", encoding="utf-8", newline="\n") as log:
        log.write(f"\n--- cleanup restart {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        proc = subprocess.Popen(
            [PYTHON, "-u", str(AUTO_CYCLE), "--restart-burst", "-v"],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )
    print(f"started auto_cycle pid={proc.pid}")
    time.sleep(3)
    print(f"poll={proc.poll()}")
    print(f"scanner alive={pid_alive(keep_scanner)} burst alive={pid_alive(keep_burst)}")


if __name__ == "__main__":
    main()