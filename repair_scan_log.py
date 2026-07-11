#!/usr/bin/env python3
"""Конвертирует смешанный counter_scan.log (UTF-16 + UTF-8) в чистый UTF-8."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "counter_scan.log"
DST = ROOT / "counter_scan_utf8.log"
BAK = ROOT / "counter_scan.log.bak"


def read_mixed(path: Path) -> str:
    data = path.read_bytes()
    parts: list[str] = []
    if data.startswith(b"\xff\xfe"):
        # UTF-16 LE до первого чистого UTF-8 маркера "--- scan"
        utf8_marker = b"--- scan"
        idx = data.find(utf8_marker)
        if idx < 0:
            return data.decode("utf-16-le", errors="replace")
        if idx % 2 == 1:
            idx += 1
        parts.append(data[:idx].decode("utf-16-le", errors="replace"))
        parts.append(data[idx:].decode("utf-8", errors="replace"))
    else:
        parts.append(data.decode("utf-8", errors="replace"))
    return "".join(parts)


def main() -> None:
    if not SRC.exists():
        print(f"нет {SRC}")
        return
    text = read_mixed(SRC)
    if not BAK.exists():
        SRC.rename(BAK)
    else:
        SRC.unlink(missing_ok=True)
    DST.write_text(text, encoding="utf-8", newline="\n")
    DST.rename(SRC)
    print(f"OK: {SRC} → UTF-8 ({len(text)} chars), backup: {BAK.name}")


if __name__ == "__main__":
    main()