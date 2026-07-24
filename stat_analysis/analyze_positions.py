#!/usr/bin/env python3
"""
Stage 2.1: Position independence analysis.
Chi-square test of independence for each position pair.
Also: per-position symbol distribution, entropy, bias.
"""
import json, sys
from pathlib import Path
from collections import Counter
import numpy as np
from scipy.stats import chi2_contingency

CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
ANCHORS_FILE = "/home/rhagtoo/testimage/anchor_ids.txt"
OUTPUT = "/home/rhagtoo/testimage/oracle_testset/position_analysis.json"

def load_ids():
    with open(ANCHORS_FILE) as f:
        return [l.strip() for l in f if len(l.strip()) == 7 and all(c in CHARS for c in l.strip())]

def main():
    ids = load_ids()
    n = len(ids)
    print(f"Loaded {n} IDs")
    
    # 1. Per-position symbol distribution
    print("\n── Per-position distribution ──")
    pos_counts = [Counter() for _ in range(7)]
    for gid in ids:
        for i, c in enumerate(gid):
            pos_counts[i][c] += 1
    
    # Chi-square per position vs uniform
    expected_per_pos = n / 62
    position_stats = []
    for i in range(7):
        observed = [pos_counts[i].get(c, 0) for c in CHARS]
        chi2 = sum((o - expected_per_pos)**2 / expected_per_pos for o in observed)
        # p-value from chi2 with 61 dof
        from scipy.stats import chi2 as chi2_dist
        p_value = 1 - chi2_dist.cdf(chi2, 61)
        
        # Entropy in bits
        probs = [o/n for o in observed if o > 0]
        entropy = -sum(p * np.log2(p) for p in probs)
        max_entropy = np.log2(62)  # ~5.954
        
        position_stats.append({
            "position": i,
            "chi2": round(chi2, 2),
            "p_value": float(p_value),
            "entropy_bits": round(entropy, 4),
            "max_entropy_bits": round(max_entropy, 4),
            "efficiency": round(entropy / max_entropy * 100, 2),
            "top3": pos_counts[i].most_common(3),
            "bottom3": pos_counts[i].most_common()[-3:],
        })
        
        sig = "***" if p_value < 0.001 else "**" if p_value < 0.01 else "*" if p_value < 0.05 else ""
        print(f"  pos[{i}] χ²={chi2:.1f} p={p_value:.2e} entropy={entropy:.4f}/{max_entropy:.4f} {sig}")
    
    # 2. Position-pair independence
    print("\n── Position-pair independence (chi-square) ──")
    pairs = []
    for i in range(7):
        for j in range(i+1, 7):
            # Build 62×62 contingency table
            table = np.zeros((62, 62))
            for gid in ids:
                ri = CHARS.index(gid[i])
                rj = CHARS.index(gid[j])
                table[ri][rj] += 1
            
            # Remove zero rows/columns for valid chi-square
            # Actually chi2_contingency handles this but may warn
            try:
                chi2, p, dof, expected = chi2_contingency(table)
                pairs.append({"i": i, "j": j, "chi2": round(chi2, 2), "p_value": float(p), "dof": dof})
                
                sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""
                if p < 0.05:
                    print(f"  ({i},{j}) χ²={chi2:.1f} p={p:.2e} dof={dof} {sig} ← DEPENDENT")
            except:
                pairs.append({"i": i, "j": j, "chi2": 0, "p_value": 1.0, "dof": 0, "error": "computation failed"})
    
    # Bonferroni correction: 21 tests, threshold = 0.05/21 ≈ 0.00238
    bonferroni_threshold = 0.05 / 21
    significant_after_bonferroni = [p for p in pairs if p["p_value"] < bonferroni_threshold]
    print(f"\n  Bonferroni-corrected threshold: p < {bonferroni_threshold:.4f}")
    print(f"  Significant pairs: {len(significant_after_bonferroni)}/21")
    
    # 3. Global statistics
    print(f"\n── Global ──")
    all_symbols = ''.join(ids)
    global_counter = Counter(all_symbols)
    global_probs = [global_counter.get(c, 0) / (n*7) for c in CHARS]
    global_entropy = -sum(p * np.log2(p) for p in global_probs if p > 0)
    
    result = {
        "n_ids": n,
        "position_stats": position_stats,
        "position_pairs": pairs,
        "bonferroni_threshold": bonferroni_threshold,
        "significant_after_bonferroni": len(significant_after_bonferroni),
        "global_entropy": round(global_entropy, 4),
        "total_entropy_bits": round(global_entropy * 7, 2),
        "max_total_bits": round(np.log2(62**7), 2),
    }
    
    Path(OUTPUT).parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        json.dump(result, f, indent=2, default=str)
    
    print(f"\nOutput: {OUTPUT}")

if __name__ == "__main__":
    main()
