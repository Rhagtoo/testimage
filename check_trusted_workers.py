#!/usr/bin/env python3
"""Быстрый тест: сколько CF Workers «зрячие» (видят эталонные gallery ID)."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from pentest_site_gallery_scanner import (
    WorkerManager,
    load_reference_ids,
    load_workers_config,
)


async def main() -> None:
    ap = argparse.ArgumentParser(description="Процент зрячих CF Workers")
    ap.add_argument("--workers-config", default="worker_ref.json")
    ap.add_argument("--reference-ids", default="found_counter_scan.txt")
    ap.add_argument("-c", "--concurrency", type=int, default=8)
    ap.add_argument(
        "--machine",
        action="store_true",
        help="строка SIGHTED=N TOTAL=M для auto_cycle.py",
    )
    args = ap.parse_args()

    workers = load_workers_config(Path(args.workers_config))
    if not workers:
        print(f"Нет Workers в {args.workers_config}")
        return

    ref_ids = load_reference_ids(Path(args.reference_ids))
    if not ref_ids:
        print(f"Нет эталонных ID в {args.reference_ids}")
        return

    mgr = WorkerManager(workers, ref_ids)
    n = await mgr.initial_scan(concurrency=args.concurrency)
    total = mgr.total()
    pct = 100.0 * n / total if total else 0.0
    if args.machine:
        print(f"SIGHTED={n} TOTAL={total} PCT={pct:.1f}")
    else:
        print(f"Зрячих Workers: {n} / {total} ({pct:.1f}%)")
        print(f"Эталоны: {', '.join(ref_ids)}")
        for w in workers:
            mark = "ok" if w.url in mgr._trusted else "blind"
            print(f"  [{mark}] {w.url}")


if __name__ == "__main__":
    asyncio.run(main())