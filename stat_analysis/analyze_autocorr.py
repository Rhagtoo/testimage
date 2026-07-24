#!/usr/bin/env python3
"""
Stage 2.3 (CORRECTED): Autocorrelation by CREATION ORDER.
Uses upload_intel.jsonl with timestamps, NOT alphabetically sorted anchor_ids.txt.
"""
import json, sys
from pathlib import Path
import numpy as np
from scipy.stats import pearsonr

CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
DATAFILE = "/mnt/c/Users/Rhagtoo/POSTIMG/upload_intel.jsonl"

def b2i(s):
    v = 0
    for c in s: v = v * 62 + CHARS.index(c)
    return v

def main():
    # Load and sort by timestamp
    records = []
    with open(DATAFILE) as f:
        for line in f:
            d = json.loads(line.strip())
            records.append({
                "gid": d["gid"],
                "ts": d["ts"],
                "seq": d.get("seq", 0),
                "base62": d.get("base62", b2i(d["gid"])),
                "base62_delta_prev": d.get("base62_delta_prev"),
                "upload_session": d.get("upload_session", ""),
            })
    
    # Sort by timestamp
    records.sort(key=lambda r: r["ts"])
    n = len(records)
    print(f"Loaded {n} records from upload_intel.jsonl (sorted by ts)")
    
    # 1. Gap analysis by creation order
    print("\n── Gap distribution (sorted by creation time) ──")
    ints = np.array([r["base62"] for r in records])
    
    gaps = []
    gap_by_session = {}
    current_session = records[0]["upload_session"]
    session_gaps = []
    
    for i in range(1, n):
        gap = ints[i] - ints[i-1]
        gaps.append(gap)
        
        if records[i]["upload_session"] != current_session:
            if session_gaps:
                gap_by_session[current_session[:20]] = {
                    "count": len(session_gaps),
                    "min": int(np.min(session_gaps)),
                    "max": int(np.max(session_gaps)),
                    "median": float(np.median(session_gaps)),
                    "n_eq_1": sum(1 for g in session_gaps if g == 1),
                    "n_small": sum(1 for g in session_gaps if 1 <= abs(g) <= 10),
                }
            current_session = records[i]["upload_session"]
            session_gaps = []
        
        session_gaps.append(gap)
    
    # Last session
    if session_gaps:
        gap_by_session[current_session[:20]] = {
            "count": len(session_gaps),
            "min": int(np.min(session_gaps)),
            "max": int(np.max(session_gaps)),
            "median": float(np.median(session_gaps)),
            "n_eq_1": sum(1 for g in session_gaps if g == 1),
            "n_small": sum(1 for g in session_gaps if 1 <= abs(g) <= 10),
        }
    
    gaps_arr = np.array(gaps)
    print(f"  Total gaps: {len(gaps)}")
    print(f"  Gap = 1: {np.sum(np.abs(gaps_arr) == 1)} ({np.sum(np.abs(gaps_arr) == 1)/len(gaps)*100:.1f}%)")
    print(f"  Gap ≤ 10: {np.sum(np.abs(gaps_arr) <= 10)} ({np.sum(np.abs(gaps_arr) <= 10)/len(gaps)*100:.1f}%)")
    print(f"  Gap ≤ 100: {np.sum(np.abs(gaps_arr) <= 100)} ({np.sum(np.abs(gaps_arr) <= 100)/len(gaps)*100:.1f}%)")
    print(f"  Min gap: {np.min(gaps_arr)}, Max gap: {np.max(gaps_arr)}")
    print(f"  Median gap: {np.median(gaps_arr):.0f}")
    print(f"  Sessions: {len(gap_by_session)}")
    
    # Show per-session stats
    sessions_with_gap1 = sum(1 for s in gap_by_session.values() if s["n_eq_1"] > 0)
    print(f"  Sessions with gap=1: {sessions_with_gap1}/{len(gap_by_session)}")
    
    # Show first few sessions
    print("\n  Per-session gap stats (first 10):")
    for i, (sess, stats) in enumerate(list(gap_by_session.items())[:10]):
        print(f"    session {sess}... count={stats['count']} gap=1:{stats['n_eq_1']} "
              f"≤10:{stats['n_small']} median={stats['median']:.0f}")
    
    # 2. Autocorrelation by CREATION ORDER
    print("\n── Autocorrelation (creation order, NOT alphabetical) ──")
    for lag in range(1, 11):
        if lag < len(ints):
            corr, p = pearsonr(ints[:-lag], ints[lag:])
            sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""
            print(f"  lag={lag:2d} r={corr:.6f} p={p:.4f} {sig}")
    
    # 3. Within-session autocorrelation
    print("\n── Within-session autocorrelation (first session with >10 IDs) ──")
    sessions_list = []
    current = []
    current_sess = records[0]["upload_session"]
    for r in records:
        if r["upload_session"] != current_sess:
            if len(current) >= 10:
                sessions_list.append(current)
            current = []
            current_sess = r["upload_session"]
        current.append(r["base62"])
    if len(current) >= 10:
        sessions_list.append(current)
    
    for si, sess_ints in enumerate(sessions_list[:5]):
        arr = np.array(sess_ints)
        print(f"  Session {si+1} (n={len(arr)}):")
        for lag in range(1, min(6, len(arr))):
            corr, p = pearsonr(arr[:-lag], arr[lag:])
            sig = "***" if p < 0.001 else "*" if p < 0.05 else ""
            print(f"    lag={lag}: r={corr:.4f} p={p:.4f} {sig}")

if __name__ == "__main__":
    main()
