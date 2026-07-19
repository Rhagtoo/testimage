#!/usr/bin/env python3
"""
Statistical analysis of gallery ID space.
Analyzes: character frequency, entropy, timestamp correlation, checksum detection.
Input: enriched_burst.csv + upload_intel.jsonl from the dataset.
"""
import csv, json, math, statistics
from collections import Counter

CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
CV = {c: i for i, c in enumerate(CHARS)}
N = len(CHARS)

def b2i(s):
    v = 0
    for c in s: v = v * N + CV[c]
    return v

def main():
    # Load from enriched_burst.csv
    gids, timestamps = [], []
    with open('enriched_burst.csv') as f:  # adjust path if needed
        for row in csv.DictReader(f):
            gid = row.get('gid', '').strip()
            st = row.get('server_time_ms', '').strip()
            if len(gid) == 7 and st and st != 'None':
                try:
                    gids.append(gid)
                    timestamps.append(float(st))
                except: pass

    print(f"Samples: {len(gids)}")
    print()

    # 1. Per-position entropy and frequency
    print("=" * 55)
    print("CHARACTER FREQUENCY PER POSITION (entropy bits)")
    for pos in range(7):
        counter = Counter(g[pos] for g in gids)
        total = len(gids)
        entropy = -sum((c/total) * math.log2(c/total) for c in counter.values())
        max_e = math.log2(N)
        top3 = counter.most_common(3)
        bottom3 = counter.most_common()[-3:]
        print(f"  pos[{pos}]: {entropy:.2f}/{max_e:.2f} bits  top3:{top3}  low3:{bottom3}")

    # 2. Overall
    all_chars = ''.join(gids)
    counter = Counter(all_chars)
    total = len(all_chars)
    entropy = -sum((c/total) * math.log2(c/total) for c in counter.values())
    print(f"\n  Global entropy: {entropy:.2f}/{math.log2(N):.2f} bits/char")
    print(f"  Per-ID entropy: {entropy*7:.1f}/{math.log2(N)*7:.1f} bits")
    print(f"  Unique: {len(set(gids))}/{len(gids)} ({100*len(set(gids))/len(gids):.1f}%)")

    # 3. Timestamp correlation
    print()
    print("=" * 55)
    print("TIMESTAMP vs ID CORRELATION")
    pairs = [(t, b2i(g), g) for t, g in zip(timestamps, gids)]
    pairs.sort()
    n = len(pairs)
    ts_vals = [p[0] for p in pairs]
    id_vals = [p[1] for p in pairs]
    id_sorted = sorted(range(n), key=lambda i: id_vals[i])
    id_rank = [0]*n
    for rank, i in enumerate(id_sorted): id_rank[i] = rank
    d2 = sum((i - r)**2 for i, r in enumerate(id_rank))
    rho = 1 - 6*d2/(n*(n**2-1))
    inc = sum(1 for i in range(1,n) if id_vals[i] > id_vals[i-1])
    print(f"  Spearman ρ: {rho:.4f}")
    print(f"  Monotonically increasing: {inc}/{n-1} ({100*inc/(n-1):.1f}%)")
    ts_mean, id_mean = statistics.mean(ts_vals), statistics.mean(id_vals)
    num = sum((ts_vals[i]-ts_mean)*(id_vals[i]-id_mean) for i in range(n))
    den = sum((ts_vals[i]-ts_mean)**2 for i in range(n))
    print(f"  Linear slope: {num/den:.1f} ID-units/ms" if den else "  N/A")

    # 4. Checksum
    print()
    print("=" * 55)
    print("CHECKSUM ANALYSIS")
    prefix_lasts = {}
    for g in gids:
        prefix_lasts.setdefault(g[:6], set()).add(g[6])
    multi = sum(1 for v in prefix_lasts.values() if len(v) > 1)
    print(f"  Prefixes with >1 last char: {multi}/{len(prefix_lasts)}")
    print(f"  Deterministic checksum: {'YES' if multi==0 else 'NO'}")

    # 5. Chi-squared
    print()
    print("=" * 55)
    print("UNIFORMITY TEST (χ²)")
    first_freq = Counter(g[0] for g in gids)
    last_freq = Counter(g[6] for g in gids)
    exp = len(gids)/N
    first_chi2 = sum((c-exp)**2/exp for c in first_freq.values())
    last_chi2 = sum((c-exp)**2/exp for c in last_freq.values())
    print(f"  First char χ²: {first_chi2:.1f} (critical ~80)")
    print(f"  Last char χ²:  {last_chi2:.1f} (critical ~80)")
    print(f"  Verdict: {'NON-UNIFORM' if max(first_chi2,last_chi2)>80 else 'uniform'}")

if __name__ == "__main__":
    main()
