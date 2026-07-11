#!/usr/bin/env python3
"""Smoke test CloudflareWorkerApi."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pentest_site_gallery_scanner as s

ROOT = Path(__file__).resolve().parent


async def main() -> None:
    cfg = json.loads((ROOT / "worker_ref.json").read_text(encoding="utf-8"))
    url, key = cfg["url"].rstrip("/"), cfg["secret"]
    s.PENTEST_SITE_JSON_URL = f"{url}/json"

    ref_gids = s.load_reference_ids(s.REFERENCE_IDS_FILE) or ["y3tXqH0"]
    gate = s.CloudflareWorkerApi(url, key, ref_gids[0], ref_gids=ref_gids, max_concurrent=4)
    ref_ok, st = await gate.check_ref()
    print(f"ref={ref_ok} status={st} active_gid={gate._active_ref_gid}")
    ok, st2 = await gate.probe_gallery("y3tXqH0")
    print(f"combat_hit={ok} status={st2}")
    ok3, st3 = await gate.probe_gallery("AAAAAAA")
    print(f"combat_miss={ok3} status={st3}")
    await gate.close()


if __name__ == "__main__":
    asyncio.run(main())