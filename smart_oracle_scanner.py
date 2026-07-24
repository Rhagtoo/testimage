#!/usr/bin/env python3
"""
Smart oracle scanner: scans around KNOWN-VALID anchor IDs (user-created galleries).
Oracle: 57556B/200=EXISTS, 28099B=EXISTS_BANNED, 28073B=NEVER.
Periodic ref-check ensures oracle alive. Auto-rotates AdGuard IPs.
"""
import subprocess, time, sys, json, random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

SOCKS = "127.0.0.1:1080"
HOST = "testimage.cc"
CONCURRENCY = 20
REF_CHECK_EVERY = 40
ANCHORS_FILE = "/home/rhagtoo/anchor_ids.txt"
SCAN_RADIUS = 5  # ±N neighbors per anchor

CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
CV = {c: i for i, c in enumerate(CHARS)}

LOCATIONS = [
    "brussels", "prague", "budapest", "bucharest", "sofia",
    "athens", "dublin", "copenhagen", "reykjavik", "tallinn",
    "stockholm", "helsinki", "lisbon", "oslo",
    "sydney", "newyork", "miami", "london", "amsterdam",
    "frankfurt", "paris", "madrid", "milan", "warsaw",
    "vienna", "zurich", "tokyo", "singapore",
]
loc_idx = 0

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

def rotate():
    global loc_idx
    loc = LOCATIONS[loc_idx % len(LOCATIONS)]
    loc_idx += 1
    sys.stdout.write(f"\n🔄 {loc.upper()} "); sys.stdout.flush()
    subprocess.run(["adguardvpn-cli", "connect", "-l", loc], capture_output=True, timeout=20)
    time.sleep(4)

def probe(gid):
    url = f"https://{HOST}/gallery/{gid}"
    cmd = f'curl -s --max-time 6 -o /dev/null -w "%{{http_code}} %{{size_download}}" --socks5 {SOCKS} "{url}"'
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
        parts = r.stdout.strip().split()
        if len(parts) < 2: return None
        http, size = parts[0], parts[1]
        if http == '200': return 'EXISTS'
        if size == '28099': return 'EXISTS_BANNED'
        if size == '28073': return 'NEVER'
        return None
    except:
        return None

def check_ref(ref="XFkkFgw"):
    r = probe(ref)
    return r in ('EXISTS', 'EXISTS_BANNED'), r

def load_anchors():
    anchors = []
    with open(ANCHORS_FILE) as f:
        for line in f:
            gid = line.strip()
            if len(gid) == 7:
                anchors.append(gid)
    return anchors

def main():
    start = datetime.now()
    anchors = load_anchors()
    random.shuffle(anchors)  # randomize order
    
    found_new = set()  # new IDs not in anchors
    checked = 0
    anchors_checked = 0
    
    print("=" * 55)
    print(f"SMART ORACLE SCANNER")
    print(f"Anchors: {len(anchors)}  Radius: ±{SCAN_RADIUS}  Concurrency: {CONCURRENCY}")
    print(f"Oracle: 200=EXISTS  28099B=EXISTS_BANNED  28073B=NEVER")
    print("=" * 55)
    
    rotate()
    ok, state = check_ref()
    sys.stdout.write(f"Init ref: {state} {'✓' if ok else '✗'}\n"); sys.stdout.flush()
    if not ok:
        for _ in range(3):
            rotate()
            ok, state = check_ref()
            sys.stdout.write(f"Retry ref: {state} {'✓' if ok else '✗'}\n"); sys.stdout.flush()
            if ok: break
        if not ok:
            print("Cannot establish oracle. Aborting.")
            return
    
    for anchor in anchors:
        anchors_checked += 1
        
        # Check anchor itself (is it still alive?)
        anchor_result = probe(anchor)
        checked += 1
        
        # Generate neighbor IDs
        anchor_int = b2i(anchor)
        neighbors = []
        for offset in range(-SCAN_RADIUS, SCAN_RADIUS + 1):
            if offset == 0: continue
            gid = i2b(anchor_int + offset)
            if gid not in anchors and gid not in found_new:
                neighbors.append(gid)
        
        # Probe neighbors in parallel
        with ThreadPoolExecutor(max_workers=min(CONCURRENCY, len(neighbors))) as ex:
            futures = {ex.submit(probe, gid): gid for gid in neighbors}
            for future in as_completed(futures):
                gid = futures[future]
                try:
                    result = future.result()
                except:
                    result = None
                checked += 1
                
                if result in ('EXISTS', 'EXISTS_BANNED'):
                    found_new.add(gid)
                    marker = "✓" if result == 'EXISTS' else "⚠"
                    sys.stdout.write(f"\n  [ {marker} ] NEW: {gid} (near {anchor}) total_found={len(found_new)}\n")
                    sys.stdout.flush()
        
        # Progress
        if anchors_checked % 50 == 0:
            elapsed = (datetime.now() - start).total_seconds()
            rps = checked / elapsed if elapsed > 0 else 0
            sys.stdout.write(f"\r  anchors={anchors_checked}/{len(anchors)} checked={checked} found={len(found_new)} rps={rps:.1f}  ")
            sys.stdout.flush()
        
        # Ref check every N anchors
        if anchors_checked % REF_CHECK_EVERY == 0:
            ok, state = check_ref()
            if not ok:
                sys.stdout.write(f"\n⚠ Oracle fail at anchor {anchors_checked}. Rotating...\n")
                sys.stdout.flush()
                rotate()
                ok, state = check_ref()
                if not ok:
                    rotate()
                    ok, state = check_ref()
                sys.stdout.write(f"  Ref: {state} {'✓' if ok else '✗'}\n")
                sys.stdout.flush()
    
    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n\n{'=' * 55}")
    print(f"SCAN COMPLETE")
    print(f"Time: {elapsed:.0f}s  Anchors: {anchors_checked}  Checked: {checked}")
    print(f"New IDs found: {len(found_new)}  Rate: {checked/elapsed:.1f} req/s")
    print(f"{'=' * 55}")
    
    if found_new:
        for gid in sorted(found_new):
            print(f"  {gid}")
        path = f"/home/rhagtoo/oracle_smart_{start.strftime('%Y%m%d_%H%M%S')}.json"
        with open(path, 'w') as f:
            json.dump({"timestamp": start.isoformat(), "anchors_used": anchors_checked, 
                       "checked": checked, "found": sorted(found_new)}, f, indent=2)
        print(f"Saved: {path}")

if __name__ == "__main__":
    main()
