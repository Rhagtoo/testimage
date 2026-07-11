#!/usr/bin/env python3
"""Offline analysis of upload_dataset.csv — cluster + counter hypotheses."""

from __future__ import annotations

import csv
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

CHARSET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
M = 62**7
DEFAULT_CSV = Path("upload_dataset.csv")


def proxy_host(proxy: str) -> str:
    m = re.search(r"@([^:]+:\d+)", proxy)
    return m.group(1) if m else proxy


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            r["base62"] = int(r["base62"])
            r["timestamp_ms"] = int(r["timestamp_ms"])
            r["delta_prev"] = int(r["delta_prev"]) if r.get("delta_prev") else None
            rows.append(r)
    return rows


def corr(xs: list[float], ys: list[float]) -> float:
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    return num / den if den else 0.0


def cluster_by_proxy(rows: list[dict], gap_ms: int = 2000) -> list[tuple[str, list[dict]]]:
    by_proxy: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_proxy[r["proxy"]].append(r)
    clusters: list[tuple[str, list[dict]]] = []
    for px, items in by_proxy.items():
        items.sort(key=lambda x: x["timestamp_ms"])
        cur = [items[0]]
        for it in items[1:]:
            if it["timestamp_ms"] - cur[-1]["timestamp_ms"] < gap_ms:
                cur.append(it)
            else:
                if len(cur) >= 2:
                    clusters.append((px, cur))
                cur = [it]
        if len(cur) >= 2:
            clusters.append((px, cur))
    return clusters


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    rows = load_rows(path)
    print(f"rows={len(rows)}  file={path}")

    ts = [r["timestamp_ms"] for r in rows]
    b = [r["base62"] for r in rows]
    span_s = (max(ts) - min(ts)) / 1000
    print(f"time_span_s={span_s:.1f}  rate={len(rows) / span_s:.2f}/s")
    print(f"base62 min={min(b):,} max={max(b):,} span={max(b) - min(b):,} ({100*(max(b)-min(b))/M:.2f}% of 62^7)")

    print(f"corr(timestamp_ms, base62)={corr([float(x) for x in ts], [float(x) for x in b]):.4f}")

    by_proxy: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_proxy[r["proxy"]].append(r)
    for px in by_proxy:
        by_proxy[px].sort(key=lambda x: x["timestamp_ms"])

    match_worker = match_global = empty = 0
    for r in rows:
        if r["delta_prev"] is None:
            empty += 1
            continue
        seq = [x for x in by_proxy[r["proxy"]] if x["timestamp_ms"] < r["timestamp_ms"]]
        if seq and r["base62"] - seq[-1]["base62"] == r["delta_prev"]:
            match_worker += 1
        all_prev = [x for x in rows if x["timestamp_ms"] < r["timestamp_ms"]]
        if all_prev:
            prev = max(all_prev, key=lambda x: x["timestamp_ms"])
            if r["base62"] - prev["base62"] == r["delta_prev"]:
                match_global += 1
    print(
        f"delta_prev: empty={empty} matches_same_proxy_prev={match_worker} "
        f"matches_global_prev={match_global}"
    )

    clusters = cluster_by_proxy(rows, gap_ms=2000)
    print(f"clusters(proxy, gap<2s, size>=2)={len(clusters)}")

    interesting: list[tuple] = []
    for px, cl in clusters:
        p5 = [x["gid"][:5] for x in cl]
        common = max(set(p5), key=p5.count)
        share = p5.count(common) / len(p5)
        vals = [x["base62"] for x in cl]
        spread = max(vals) - min(vals)
        if share >= 0.5 and spread < 500:
            interesting.append((px, len(cl), common, share, spread, [x["gid"] for x in cl]))
    interesting.sort(key=lambda x: -x[1])
    print(f"tight_prefix_clusters (share>=50%, spread<500): {len(interesting)}")
    for px, n, pre, sh, sp, gids in interesting[:10]:
        print(f"  {proxy_host(px)}: n={n} prefix5={pre} share={sh:.0%} spread={sp} -> {gids}")

    print("\n--- DTw6ZB* on :9334 ---")
    for px, cl in clusters:
        hits = [x for x in cl if x["gid"].startswith("DTw6ZB")]
        if hits and ":9334" in px:
            for x in sorted(hits, key=lambda z: z["timestamp_ms"]):
                print(f"  {x['gid']}  base62={x['base62']:,}  ts={x['timestamp_ms']}")

    print("\n--- per-proxy sequential deltas (gap<2s pairs) ---")
    delta_counts: dict[int, int] = defaultdict(int)
    for _, cl in clusters:
        for i in range(1, len(cl)):
            d = cl[i]["base62"] - cl[i - 1]["base62"]
            delta_counts[d] += 1
    top = sorted(delta_counts.items(), key=lambda x: -x[1])[:12]
    print("top deltas:", top)

    # global time sort: consecutive delta distribution
    rows_sorted = sorted(rows, key=lambda x: x["timestamp_ms"])
    global_deltas = [rows_sorted[i]["base62"] - rows_sorted[i - 1]["base62"] for i in range(1, len(rows_sorted))]
    abs_d = [abs(x) for x in global_deltas]
    print(
        f"global consecutive |delta|: median={statistics.median(abs_d):,.0f} "
        f"p90={sorted(abs_d)[int(0.9*len(abs_d))]:,.0f} max={max(abs_d):,}"
    )
    small = sum(1 for x in abs_d if x < 100)
    print(f"global pairs with |delta|<100: {small}/{len(abs_d)} ({100*small/len(abs_d):.1f}%)")


if __name__ == "__main__":
    main()