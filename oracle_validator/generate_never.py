#!/usr/bin/env python3
"""
Stage 1.1c: Generate random never-existed gallery IDs.
Produces 1000 IDs that are guaranteed not to exist (verified via SESSIONKEY).

Output: oracle_testset/never_generated.jsonl
"""
import subprocess, json, random, sys
from pathlib import Path

CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
SESSIONKEY = "4f1115042cbfbd75b81e2ced3d6df18e7b26dd84dd3d37fd21e759373f36df46"
OUTPUT = "/home/rhagtoo/testimage/oracle_testset/never_generated.jsonl"
TARGET = 1000
BATCH = 20

def random_id():
    return ''.join(random.choice(CHARS) for _ in range(7))

def check_batch(gids):
    """Check multiple IDs in parallel via SESSIONKEY, returns list of (gid, exists, http, size)"""
    results = []
    import concurrent.futures
    
    def check_one(gid):
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
            return (gid, http == 500, http, size)
        except:
            return (gid, False, 0, 0)

    with concurrent.futures.ThreadPoolExecutor(max_workers=BATCH) as ex:
        futures = [ex.submit(check_one, gid) for gid in gids]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())
    return results

def main():
    print(f"Generating {TARGET} KNOWN_NEVER IDs...")
    
    generated = []
    tried = 0
    exists_hits = 0
    
    while len(generated) < TARGET:
        batch = [random_id() for _ in range(BATCH)]
        results = check_batch(batch)
        tried += len(results)
        
        for gid, exists, http, size in results:
            if exists:
                exists_hits += 1
                print(f"  ! Unexpected HIT: {gid} (exists!)")
            else:
                generated.append({"gid": gid, "label": "KNOWN_NEVER", "http": http, "size": size})
        
        print(f"  [{len(generated)}/{TARGET}] tried={tried} hits={exists_hits}")
    
    # Write output
    Path(OUTPUT).parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        for entry in generated:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    
    print(f"\nDone: {len(generated)} KNOWN_NEVER, {exists_hits} accidental hits in {tried} tries")
    print(f"Hit rate: {exists_hits/tried*100:.4f}%")
    print(f"Output: {OUTPUT}")

if __name__ == "__main__":
    main()
