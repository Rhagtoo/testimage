#!/usr/bin/env python3
"""
Stage 1.1d: Verify foreign gallery finds via SESSIONKEY.
Checks found_counter_scan.txt + scanner finds.

Output: oracle_testset/foreign_verified.jsonl
"""
import subprocess, json, sys
from pathlib import Path

SESSIONKEY = "4f1115042cbfbd75b81e2ced3d6df18e7b26dd84dd3d37fd21e759373f36df46"
COUNTER_FILE = "/mnt/c/Users/Rhagtoo/TESTIMAGE/found_counter_scan.txt"
OUTPUT = "/home/rhagtoo/testimage/oracle_testset/foreign_verified.jsonl"

# Known scanner finds
SCANNER_FINDS = ["bv68s9M", "wjBbsRH"]

def check(gid):
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

def extract_gids(filepath):
    """Extract 7-char IDs from counter scan output"""
    import re
    gids = set()
    try:
        with open(filepath) as f:
            for line in f:
                # Look for gallery URLs or bare IDs
                found = re.findall(r'/gallery/([a-zA-Z0-9]{7})', line)
                gids.update(found)
                found = re.findall(r'\b([a-zA-Z0-9]{7})\b', line)
                gids.update(found)
    except FileNotFoundError:
        print(f"  WARNING: {filepath} not found, using scanner finds only")
    return list(gids)

def main():
    # Load from counter file
    print(f"Loading foreign IDs from {COUNTER_FILE}...")
    gids = extract_gids(COUNTER_FILE)
    
    # Add scanner finds
    for gid in SCANNER_FINDS:
        if gid not in gids:
            gids.append(gid)
    
    print(f"Total unique IDs to verify: {len(gids)}")
    
    verified = []
    for gid in gids:
        exists, http, size = check(gid)
        label = "KNOWN_FOREIGN_EXISTS" if exists else "KNOWN_FOREIGN_DEAD"
        verified.append({"gid": gid, "label": label, "http": http, "size": size})
        
        status = "✓ EXISTS" if exists else "✗ dead"
        print(f"  {gid} → {http}/{size}B {status}")
    
    # Write output
    Path(OUTPUT).parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        for entry in verified:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    
    n_exists = sum(1 for v in verified if v["label"] == "KNOWN_FOREIGN_EXISTS")
    print(f"\nDone: {n_exists}/{len(verified)} foreign IDs still exist")
    print(f"Output: {OUTPUT}")

if __name__ == "__main__":
    main()
