#!/usr/bin/env python3
"""Deep analysis for single-proxy burst CSV."""

from __future__ import annotations

import csv
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

M = 62**7


def load(path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            r["base62"] = int(r["base62"])
            r["ts"] = int(r["timestamp_ms"])
            rows.append(r)
    rows.sort(key=lambda x: x["ts"])
    return rows


def lin_slope(xs: list[float], ys: list[float]) -> float:
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    return num / den if den else 0.0


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("upload_burst_9334.csv")
    rows = load(path)
    n = len(rows)
    print(f"=== {path.name} ===")
    print(f"rows={n}")
    if n < 2:
        return

    t0, t1 = rows[0]["ts"], rows[-1]["ts"]
    span_s = (t1 - t0) / 1000
    print(f"span_s={span_s:.0f} ({span_s/60:.1f} min)  rate={n/span_s:.3f}/s")

    bvals = [r["base62"] for r in rows]
    print(f"base62 range: {min(bvals):,} .. {max(bvals):,}  spread={max(bvals)-min(bvals):,}")

    ts_f = [float(r["ts"]) for r in rows]
    slope = lin_slope(ts_f, [float(b) for b in bvals])
    print(f"linear slope db62/dt_ms={slope:.2f}  (~{slope*1000:,.0f} per second)")

    deltas = [rows[i]["base62"] - rows[i - 1]["base62"] for i in range(1, n)]
    abs_d = [abs(d) for d in deltas]
    print(
        f"sequential |delta|: min={min(abs_d):,} median={statistics.median(abs_d):,.0f} "
        f"p90={sorted(abs_d)[int(0.9*len(abs_d))]:,} max={max(abs_d):,}"
    )
    for thr in (50, 100, 500, 1000, 10000):
        c = sum(1 for x in abs_d if x < thr)
        print(f"  |delta|<{thr}: {c}/{len(abs_d)} ({100*c/len(abs_d):.2f}%)")

    # prefix clusters in sliding window of 5 consecutive uploads
    print("\n--- tight windows (5 consecutive, prefix5 mode, spread<500) ---")
    wins = 0
    for i in range(n - 4):
        chunk = rows[i : i + 5]
        p5 = [r["gid"][:5] for r in chunk]
        mode = Counter(p5).most_common(1)[0]
        if mode[1] >= 3:
            vals = [r["base62"] for r in chunk]
            spread = max(vals) - min(vals)
            if spread < 500:
                wins += 1
                gids = [r["gid"] for r in chunk]
                print(f"  ts={chunk[0]['ts']} prefix5={mode[0]} share={mode[1]}/5 spread={spread} -> {gids}")
    if not wins:
        print("  none")

    # prefix6 duplicates overall
    c6 = Counter(r["gid"][:6] for r in rows)
    multi = [(k, v) for k, v in c6.items() if v >= 2]
    multi.sort(key=lambda x: -x[1])
    print(f"\n--- prefix6 repeats (count>=2): {len(multi)} ---")
    for pre, cnt in multi[:15]:
        hits = [r for r in rows if r["gid"].startswith(pre)]
        vals = [h["base62"] for h in hits]
        spread = max(vals) - min(vals)
        gids = [h["gid"] for h in hits]
        print(f"  {pre}: n={cnt} spread={spread} -> {gids}")

    # gap-based clusters <2s
    clusters: list[list[dict]] = []
    cur = [rows[0]]
    for r in rows[1:]:
        if r["ts"] - cur[-1]["ts"] < 2000:
            cur.append(r)
        else:
            if len(cur) >= 2:
                clusters.append(cur)
            cur = [r]
    if len(cur) >= 2:
        clusters.append(cur)

    tight = 0
    for cl in clusters:
        p6 = [r["gid"][:6] for r in cl]
        common = Counter(p6).most_common(1)[0]
        vals = [r["base62"] for r in cl]
        spread = max(vals) - min(vals)
        if common[1] >= 2 and spread < 500:
            tight += 1
    print(f"\nclusters(gap<2s): {len(clusters)}  tight(prefix6>=2, spread<500): {tight}")

    # smallest consecutive deltas
    pairs = list(enumerate(deltas, start=1))
    pairs.sort(key=lambda x: abs(x[1]))
    print("\n--- smallest |delta| pairs ---")
    for i, d in pairs[:12]:
        a, b = rows[i - 1], rows[i]
        print(
            f"  {a['gid']} -> {b['gid']}  d={d:+,}  "
            f"dt={b['ts']-a['ts']}ms  prefix6={a['gid'][:6]==b['gid'][:6]}"
        )


if __name__ == "__main__":
    main()