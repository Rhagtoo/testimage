#!/usr/bin/env python3
"""Быстрый тест: сколько прокси «зрячие» (видят эталонные gallery ID)."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from pentest_site_gallery_scanner import (
    ProxyPool,
    TrustedProxyManager,
    load_reference_ids,
)


async def main() -> None:
    ap = argparse.ArgumentParser(description="Процент зрячих SOCKS-прокси")
    ap.add_argument("--proxies", default="working_proxies.txt")
    ap.add_argument("--reference-ids", default="found_counter_scan.txt")
    ap.add_argument("-c", "--concurrency", type=int, default=50)
    ap.add_argument(
        "--machine",
        action="store_true",
        help="строка SIGHTED=N TOTAL=M для auto_cycle.py",
    )
    args = ap.parse_args()

    pool = ProxyPool(fixed_only=True)
    proxies = await pool.load_file(Path(args.proxies), scheme="socks5")
    if not proxies:
        print(f"Нет прокси в {args.proxies}")
        return
    await pool.load_fixed(proxies)

    ref_ids = load_reference_ids(Path(args.reference_ids))
    if not ref_ids:
        print(f"Нет эталонных ID в {args.reference_ids}")
        return

    mgr = TrustedProxyManager(pool, ref_ids)
    n = await mgr.initial_scan(concurrency=args.concurrency)
    total = pool.count()
    pct = 100.0 * n / total if total else 0.0
    if args.machine:
        print(f"SIGHTED={n} TOTAL={total} PCT={pct:.1f}")
    else:
        print(f"Зрячих: {n} / {total} ({pct:.1f}%)")
        print(f"Эталоны: {', '.join(ref_ids)}")


if __name__ == "__main__":
    asyncio.run(main())