#!/usr/bin/env python3
"""
Stage 2.2-2.4: N-gram analysis, autocorrelation, prefix/suffix decomposition.
"""
import json, sys
from pathlib import Path
from collections import Counter
import numpy as np
from scipy.stats import chi2_contingency, pearsonr

CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
ANCHORS_FILE = "/home/rhagtoo/testimage/anchor_ids.txt"
OUTPUT = "/home/rhagtoo/testimage/oracle_testset/ngram_autocorr_analysis.json"

def load_ids():
    with open(ANCHORS_FILE) as f:
        return [l.strip() for l in f if len(l.strip()) == 7 and all(c in CHARS for c in l.strip())]

def b2i(s):
    v = 0
    for c in s: v = v * 62 + CHARS.index(c)
    return v

def main():
    ids = load_ids()
    n = len(ids)
    print(f"Loaded {n} IDs\n")
    
    results = {}
    
    # ── Bigrams ──
    print("── Bigram analysis ──")
    bigram_positions = [Counter() for _ in range(6)]  # 6 adjacent pairs
    for gid in ids:
        for i in range(6):
            bigram_positions[i][gid[i:i+2]] += 1
    
    for i in range(6):
        total_bigrams = 62 * 62
        expected = n / total_bigrams
        observed = np.array([bigram_positions[i].get(CHARS[a]+CHARS[b], 0) 
                           for a in range(62) for b in range(62)])
        chi2 = sum((o - expected)**2 / expected for o in observed)
        from scipy.stats import chi2 as chi2_dist
        dof = total_bigrams - 1
        p_value = 1 - chi2_dist.cdf(chi2, dof)
        
        # Top over-represented
        top_bigrams = bigram_positions[i].most_common(5)
        
        sig = "***" if p_value < 0.001 else "**" if p_value < 0.01 else "*" if p_value < 0.05 else ""
        print(f"  pos({i},{i+1}) χ²={chi2:.1f} p={p_value:.2e} top={top_bigrams[0]} {sig}")
    
    # ── Trigram analysis ──
    print("\n── Trigram analysis ──")
    trigram_positions = [Counter() for _ in range(5)]
    for gid in ids:
        for i in range(5):
            trigram_positions[i][gid[i:i+3]] += 1
    
    for i in range(5):
        total = 62**3
        expected = n / total
        observed = np.array([trigram_positions[i].get(CHARS[a]+CHARS[b]+CHARS[c], 0)
                           for a in range(62) for b in range(62) for c in range(62)])
        chi2 = sum((o - expected)**2 / expected for o in observed if expected > 0)
        from scipy.stats import chi2 as chi2_dist
        dof = total - 1
        p_value = 1 - chi2_dist.cdf(chi2, dof) if chi2 > 0 else 1.0
        top = trigram_positions[i].most_common(3)
        sig = "***" if p_value < 0.001 else "**" if p_value < 0.01 else "*" if p_value < 0.05 else ""
        print(f"  pos({i},{i+2}) χ²={chi2:.1f} p={p_value:.2e} top={[t[0] for t in top]} {sig}")
    
    # ── Adjacent symbol repeats ──
    print("\n── Same-symbol adjacency ──")
    repeat_counts = {i: 0 for i in range(6)}
    for gid in ids:
        for i in range(6):
            if gid[i] == gid[i+1]:
                repeat_counts[i] += 1
    
    expected_repeats = n / 62  # probability of same symbol at adjacent positions if uniform
    for i in range(6):
        obs = repeat_counts[i]
        z = (obs - expected_repeats) / np.sqrt(expected_repeats * (1 - 1/62))
        print(f"  pos({i},{i+1}) repeats: {obs} (expected {expected_repeats:.1f}) z={z:.2f}")
    
    # ── Autocorrelation by creation order ──
    print("\n── Autocorrelation (int representation, by list order) ──")
    ints = np.array([b2i(gid) for gid in ids])
    ac_values = []
    for lag in range(1, 11):
        if lag < len(ints):
            corr, p = pearsonr(ints[:-lag], ints[lag:])
            ac_values.append({"lag": lag, "pearson_r": round(corr, 6), "p_value": float(p)})
            sig = "*" if p < 0.05 else ""
            print(f"  lag={lag:2d} r={corr:.6f} p={p:.4f} {sig}")
    
    # ── Prefix/Suffix analysis ──
    print("\n── Prefix/Suffix analysis ──")
    prefixes = Counter(gid[:5] for gid in ids)
    suffixes = Counter(gid[5:] for gid in ids)
    
    # How many prefixes have multiple suffixes?
    prefix_to_suffixes = {}
    for gid in ids:
        prefix_to_suffixes.setdefault(gid[:5], set()).add(gid[5:])
    
    multi_suffix_prefixes = {p: ss for p, ss in prefix_to_suffixes.items() if len(ss) > 1}
    print(f"  Unique prefixes: {len(prefixes)}")
    print(f"  Unique suffixes: {len(suffixes)}")
    print(f"  Prefixes with >1 suffix: {len(multi_suffix_prefixes)}/{len(prefixes)}")
    
    # Check if suffix is correlated with prefix
    # Build contingency table for (prefix_letter, suffix_position)
    prefix_last = Counter(gid[4] for gid in ids)  # last char of prefix
    suffix_first = Counter(gid[5] for gid in ids)  # first char of suffix
    
    # Chi-square test: is suffix[0] independent of prefix[4]?
    table = np.zeros((62, 62))
    for gid in ids:
        table[CHARS.index(gid[4])][CHARS.index(gid[5])] += 1
    try:
        chi2, p, dof, _ = chi2_contingency(table)
        print(f"  prefix[4] vs suffix[0]: χ²={chi2:.1f} p={p:.2e} {'*** DEPENDENT' if p < 0.05 else ''}")
    except:
        print(f"  prefix[4] vs suffix[0]: test failed")
    
    results = {
        "n_ids": n,
        "autocorrelation": ac_values,
        "prefixes": {"unique": len(prefixes), "multi_suffix": len(multi_suffix_prefixes)},
        "suffixes": {"unique": len(suffixes)},
        "repeat_counts": {f"pos{i}": repeat_counts[i] for i in range(6)},
        "expected_repeats": float(expected_repeats),
    }
    
    Path(OUTPUT).parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(results, f, indent=2, default=str)
    
    print(f"\nOutput: {OUTPUT}")

if __name__ == "__main__":
    main()
