#!/usr/bin/env python3
"""Проверка CF Worker ref-канала после деплоя."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "worker_ref.json"
REF_IDS = ["y3tXqH0", "NyV85xs", "KKKXkLQ"]


def load_config(path: Path) -> tuple[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    url = str(data.get("url", "")).strip().rstrip("/")
    secret = str(data.get("secret", "")).strip()
    if not url:
        print("worker_ref.json: поле url пустое — вставь URL после деплоя", file=sys.stderr)
        sys.exit(1)
    if not secret:
        print("worker_ref.json: secret пустой", file=sys.stderr)
        sys.exit(1)
    return url, secret


def check_ref(base_url: str, secret: str, gid: str) -> tuple[bool, int, str]:
    url = f"{base_url}/json?action=list&page=1&album={gid}"
    headers = {"X-Key": secret}
    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        r = client.get(url, headers=headers)
        text = r.text[:200]
        if r.status_code != 200:
            return False, r.status_code, text
        try:
            data = r.json()
        except Exception:
            return False, r.status_code, text
        if data.get("error"):
            code = data["error"].get("code", 404)
            return False, int(code) if isinstance(code, int) else 404, text
        return True, 200, text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--gid", action="append", dest="gids")
    args = ap.parse_args()
    base, secret = load_config(Path(args.config))
    gids = args.gids or REF_IDS

    print(f"worker={base}")
    r = httpx.get(f"{base}/health", timeout=10.0)
    print(f"health={r.status_code} {r.text.strip()}")

    any_ok = False
    for gid in gids:
        ok, status, snippet = check_ref(base, secret, gid)
        mark = "OK" if ok else "FAIL"
        print(f"[{mark}] {gid} status={status} {snippet[:80]!r}")
        any_ok = any_ok or ok

    sys.exit(0 if any_ok else 2)


if __name__ == "__main__":
    main()