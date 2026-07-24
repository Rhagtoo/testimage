#!/usr/bin/env python3
"""
Stage 1.1a: Verify anchor IDs via SESSIONKEY.
Confirms which of our 2207 anchor IDs still exist.

Output: oracle_testset/exists_verified.jsonl
"""
import subprocess, json, sys, time
from pathlib import Path

SESSIONKEY = "4f1115042cbfbd75b81e2ced3d6df18e7b26dd84dd3d37fd21e759373f36df46"
ANCHORS_FILE = "/home/rhagtoo/testimage/anchor_ids.txt"
OUTPUT = "/home/rhagtoo/testimage/oracle_testset/exists_verified.jsonl"
TARGET_COUNT = 100
BATCH = 10

def check(gid):
    """Returns (exists: bool, http_code: int, size: int)"""
    cmd = [
        "curl", "-s", "--max-time", "5",
        "-H", f"Cookie: SESSIONKEY={SESSIONKEY}",
        f"https://postimg.cc/json?action=list&album={gid}",
        "-o", "/dev/null", "-w", "%{http_code} %{size_download}"
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=7)
        parts = r.stdout.strip().split()
        http = int(parts[0]) if parts else 0
        size = int(parts[1]) if len(parts) > 1 else 0
        return (http == 500, http, size)
    except:
        return (False, 0, 0)

def main():
    with open(ANCHORS_FILE) as f:
        anchors = [l.strip() for l in f if len(l.strip()) == 7]

    print(f"Loaded {len(anchors)} anchors, target {TARGET_COUNT} verified")

    verified = []
    checked = 0

    for gid in anchors:
        if len(verified) >= TARGET_COUNT:
            break

        exists, http, size = check(gid)
        checked += 1

        if exists:
            verified.append({"gid": gid, "label": "KNOWN_EXISTS", "http": http, "size": size})
            print(f"  [{len(verified)}/{TARGET_COUNT}] {gid} → {http}/{size}B ✓")

        if checked % 50 == 0:
            print(f"  ... checked {checked}, found {len(verified)}")

    # Write output
    Path(OUTPUT).parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        for entry in verified:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"\nDone: {len(verified)} verified, {checked} checked")
    print(f"Output: {OUTPUT}")

if __name__ == "__main__":
    main()
