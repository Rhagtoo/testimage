#!/usr/bin/env python3
"""
Oracle scanner: find valid gallery IDs using Content-Length oracle.
Oracle: 57556B=EXISTS, 28099B=EXISTS_BANNED, 28073B=NEVER.
Rotates AdGuard every REQUESTS_PER_IP to stay under ban threshold.
"""
import subprocess, time, sys, json, random
from datetime import datetime

# ── Config ──────────────────────────────────────────────
SOCKS = "127.0.0.1:1080"
HOST = "testimage.com"
REQUESTS_PER_IP = 15  # rotate before ban (~20 threshold)
REQUEST_DELAY = 1.8    # seconds between requests

CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
CV = {c: i for i, c in enumerate(CHARS)}

# AdGuard locations to cycle through
LOCATIONS = [
    "tokyo", "singapore", "sydney", "newyork", "miami",
    "london", "amsterdam", "frankfurt", "paris", "madrid",
    "milan", "stockholm", "warsaw", "vienna", "zurich",
    "oslo", "helsinki", "lisbon",
]
loc_idx = 0

# ── Base62 helpers ──────────────────────────────────────
def b2i(s):
    v = 0
    for c in s: v = v * 62 + CV[c]
    return v

def i2b(v):
    if v == 0: return '0'
    s = []
    while v > 0:
        s.append(CHARS[v % 62])
        v //= 62
    return ''.join(reversed(s))

# ── AdGuard rotation ────────────────────────────────────
def rotate_adguard():
    global loc_idx
    loc = LOCATIONS[loc_idx % len(LOCATIONS)]
    loc_idx += 1
    print(f"\n🔄 Switching to {loc.upper()}...")
    sys.stdout.flush()
    subprocess.run(["adguardvpn-cli", "connect", "-l", loc],
                   capture_output=True, timeout=20)
    time.sleep(4)

# ── Single request ─────────────────────────────────────
def probe(gid):
    """Returns 'EXISTS', 'EXISTS_BANNED', or 'NEVER'"""
    time.sleep(REQUEST_DELAY)
    url = f"https://{HOST}/gallery/{gid}"
    cmd = f'curl -s -o /dev/null -w "HTTP:%{{http_code}} SIZE:%{{size_download}}" --socks5 {SOCKS} "{url}"'
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=12)
    
    http = size = "?"
    for part in r.stdout.strip().split():
        if part.startswith('HTTP:'): http = part.split(':', 1)[1]
        elif part.startswith('SIZE:'): size = part.split(':', 1)[1]
    
    if http == '200':
        return 'EXISTS', size
    elif size == '28099':
        return 'EXISTS_BANNED', size
    elif size == '28073':
        return 'NEVER', size
    else:
        return f'UNKNOWN_{size}', size

# ── Scan range ─────────────────────────────────────────
def scan_range(start_gid, count, direction=1):
    """Scan `count` IDs starting from start_gid in given direction (+1 or -1)"""
    start_int = b2i(start_gid)
    results = []
    found = []
    req_count = 0
    
    for i in range(count):
        if req_count > 0 and req_count % REQUESTS_PER_IP == 0:
            rotate_adguard()
        
        gid = i2b(start_int + i * direction)
        result, size = probe(gid)
        req_count += 1
        
        marker = "✓" if result == 'EXISTS' else ("⚠" if result == 'EXISTS_BANNED' else "·")
        print(f"  [{req_count:4d}] {gid} {result:15s} {size}B {marker}")
        sys.stdout.flush()
        
        results.append({"gid": gid, "result": result, "size": size})
        if result in ('EXISTS', 'EXISTS_BANNED'):
            found.append(gid)
    
    return results, found

# ── Main ────────────────────────────────────────────────
def main():
    start_time = datetime.now()
    all_found = []
    
    # Known reference IDs (confirmed alive)
    refs = ["y3tXqH0", "XFkkFgw"]
    
    print("=" * 55)
    print("ORACLE SCANNER")
    print(f"Oracle: 57556B=EXISTS  28099B=EXISTS_BANNED  28073B=NEVER")
    print(f"Requests/IP: {REQUESTS_PER_IP}  Delay: {REQUEST_DELAY}s")
    print(f"Start refs: {refs}")
    print("=" * 55)
    
    # Connect to first location
    rotate_adguard()
    
    # Verify refs are accessible
    print("\n🔍 Verifying reference IDs...")
    for ref in refs:
        r, s = probe(ref)
        print(f"  REF {ref}: {r} ({s}B)")
        if r not in ('EXISTS', 'EXISTS_BANNED'):
            print(f"  ⚠ Unexpected ref status — may need fresh IP")
    
    # Phase 1: Scan ±50 around each ref
    print("\n📡 Phase 1: Scanning ±50 around references...")
    for ref in refs:
        print(f"\n  >>> Around {ref}:")
        
        # Forward scan (+1 to +50)
        print(f"  --- Forward (+1 to +50) ---")
        _, fwd = scan_range(ref, 50, direction=1)
        all_found.extend(fwd)
        
        # Backward scan (-1 to -50)
        print(f"  --- Backward (-1 to -50) ---")
        _, bwd = scan_range(ref, 50, direction=-1)
        all_found.extend(bwd)
    
    # Summary
    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n{'=' * 55}")
    print(f"SCAN COMPLETE")
    print(f"Time: {elapsed:.0f}s  Found: {len(all_found)} new IDs")
    print(f"{'=' * 55}")
    
    if all_found:
        print("New valid IDs:")
        for gid in all_found:
            print(f"  {gid}")
        
        # Save
        out = {
            "timestamp": start_time.isoformat(),
            "refs": refs,
            "found": all_found,
            "elapsed_s": elapsed,
        }
        path = f"/home/rhagtoo/oracle_scan_{start_time.strftime('%Y%m%d_%H%M%S')}.json"
        with open(path, 'w') as f:
            json.dump(out, f, indent=2)
        print(f"\nSaved: {path}")
    else:
        print("No new IDs found in scanned range.")

if __name__ == "__main__":
    main()
