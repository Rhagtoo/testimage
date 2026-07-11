#!/usr/bin/env python3
"""Хвост лога скана в UTF-8 (для PowerShell, где counter_scan.log смешанный)."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT = ROOT / "counter_scan_current.log"
FALLBACK = ROOT / "counter_scan.log"


def tail(path: Path, n: int, follow: bool) -> None:
    if not path.exists():
        print(f"нет файла: {path}", file=sys.stderr)
        return
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
        for line in lines[-n:]:
            sys.stdout.write(line)
        if not follow:
            return
        fh.seek(0, 2)
        while True:
            line = fh.readline()
            if line:
                sys.stdout.write(line)
                sys.stdout.flush()
            else:
                time.sleep(0.3)


def main() -> None:
    ap = argparse.ArgumentParser(description="tail scan log (UTF-8)")
    ap.add_argument("-n", type=int, default=30)
    ap.add_argument("-f", "--follow", action="store_true")
    ap.add_argument("--file", default="")
    args = ap.parse_args()
    path = Path(args.file) if args.file else (DEFAULT if DEFAULT.exists() else FALLBACK)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tail(path, args.n, args.follow)


if __name__ == "__main__":
    main()