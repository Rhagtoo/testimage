#!/usr/bin/env python3
"""
Hybrid scanner: multi-threaded adaptive-radius scan around known anchors.
Combines smart anchor targeting + parallel probe + adaptive radius expansion.
Oracle: 200=EXISTS, 28099B=EXISTS_BANNED, 28073B=NEVER.
"""
import subprocess, time, sys, json, random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# ── Config ──────────────────────────────────────────────
SOCKS = "127.0.0.1:1080"
HOST = "testimage.com"
CONCURRENCY = 25
REF = "XFkkFgw"
REF_CHECK_EVERY = 40
ANCHORS_FILE = "/home/rhagtoo/anchor_ids.txt"
INITIAL_RADIUS = 10    # start with ±10
MAX_RADIUS = 50        # expand to ±50 if nothing found
RADIUS_STEP = 10       # expand by 10 each pass

CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
CV = {c: i for i, c in enumerate(CHARS)}

LOCATIONS = [
    "riga", "tallinn", "copenhagen", "helsinki", "stockholm", "zurich",
    "warsaw", "frankfurt", "prague", "marseille", "london", "oslo",
    "vienna", "brussels", "milan", "palermo", "amsterdam", "paris",
    "berlin", "vilnius", "rome", "luxembourg", "manchester", "chisinau",
    "kyiv", "zagreb", "madrid", "dublin", "cairo", "bucharest",
    "istanbul", "belgrade", "sofia", "bratislava", "lisbon", "athens",
    "barcelona", "telaviv", "budapest", "nicosia",
    "newyork", "boston", "toronto", "atlanta", "montreal",
    "miami", "chicago", "dallas", "denver", "seattle",
    "vancouver", "mexicocity", "phoenix", "lasvegas",
    "siliconvalley", "losangeles", "bogota", "lima",
    "santiago", "saopaulo", "buenosaires",
    "moscow", "dubai", "mumbai", "kathmandu", "johannesburg",
    "hanoi", "taipei", "jakarta", "singapore", "bangkok",
    "manila", "shanghai", "hongkong", "astana",
    "tokyo", "seoul", "sydney", "auckland",
]
loc_idx = 0

# ── Helpers ─────────────────────────────────────────────
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
    cmd = f'curl -s --max-time 5 -o /dev/null -w "%{{http_code}} %{{size_download}}" --socks5 {SOCKS} "{url}"'
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=7)
        parts = r.stdout.strip().split()
        if len(parts) < 2: return None
        http, size = parts[0], parts[1]
        if http == '200': return 'EXISTS'
        if size == '28099': return 'EXISTS_BANNED'
        if size == '28073': return 'NEVER'
        return None
    except:
        return None

def check_ref():
    r = probe(REF)
    if r == 'EXISTS': return True, '200=FRESH'
    if r == 'EXISTS_BANNED': return True, '28099B=ORACLE'
    if r == 'NEVER': return False, '28073B=HARD_BAN'
    return False, f'?({r})'

def load_anchors():
    anchors = []
    with open(ANCHORS_FILE) as f:
        for line in f:
            gid = line.strip()
            if len(gid) == 7:
                anchors.append(gid)
    return anchors

# ── Main ────────────────────────────────────────────────
def main():
    start = datetime.now()
    anchors = load_anchors()
    random.shuffle(anchors)
    anchor_set = set(anchors)
    
    found_new = set()
    checked = 0
    anchors_done = 0
    
    print("=" * 55)
    print("HYBRID SCANNER — adaptive radius")
    print(f"Anchors: {len(anchors)}  Initial radius: ±{INITIAL_RADIUS}")
    print(f"Max radius: ±{MAX_RADIUS}  Step: {RADIUS_STEP}")
    print(f"Concurrency: {CONCURRENCY}  Oracle: 200/28099B=EXISTS  28073B=NEVER")
    print("=" * 55)
    
    rotate()
    ok, state = check_ref()
    sys.stdout.write(f"Init ref: {state} {'✓' if ok else '✗'}\n"); sys.stdout.flush()
    if not ok:
        for _ in range(5):
            rotate()
            ok, state = check_ref()
            if ok: break
        if not ok:
            print("Cannot establish oracle."); return
    
    for anchor in anchors:
        anchors_done += 1
        anchor_int = b2i(anchor)
        
        # Adaptive radius: start small, expand if nothing found
        for radius in range(INITIAL_RADIUS, MAX_RADIUS + 1, RADIUS_STEP):
            # Generate neighbor IDs for this radius band
            # Skip IDs already in anchors or found_new
            neighbors = []
            for offset in range(-radius, radius + 1):
                if offset == 0: continue
                # Only include offsets in the current "band" (outside previous radius)
                prev_radius = radius - RADIUS_STEP
                if abs(offset) > prev_radius:
                    gid = i2b(anchor_int + offset)
                    if gid not in anchor_set and gid not in found_new:
                        neighbors.append(gid)
            
            if not neighbors:
                continue
            
            # Probe in parallel
            band_found = []
            with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
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
                        band_found.append(gid)
                        marker = "✓" if result == 'EXISTS' else "⚠"
                        sys.stdout.write(f"\n  [{marker}] NEW: {gid} (near {anchor}, radius ±{radius})\n")
                        sys.stdout.flush()
            
            # If we found something in this band, don't expand further
            if band_found:
                break
        
        # Progress
        if anchors_done % 25 == 0:
            elapsed = (datetime.now() - start).total_seconds()
            rps = checked / elapsed if elapsed > 0 else 0
            sys.stdout.write(f"\r  anchors={anchors_done}/{len(anchors)} checked={checked} found={len(found_new)} rps={rps:.1f}  ")
            sys.stdout.flush()
        
        # Ref check
        if anchors_done % REF_CHECK_EVERY == 0:
            ok, state = check_ref()
            if not ok:
                sys.stdout.write(f"\n⚠ Oracle fail at anchor {anchors_done}. Rotating...\n")
                sys.stdout.flush()
                rotate()
                for _ in range(3):
                    ok, state = check_ref()
                    if ok: break
                    rotate()
                sys.stdout.write(f"  Ref: {state} {'✓' if ok else '✗'}\n")
                sys.stdout.flush()
    
    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n\n{'=' * 55}")
    print(f"SCAN COMPLETE — {elapsed:.0f}s")
    print(f"Anchors: {anchors_done}  Checked: {checked}  New: {len(found_new)}")
    print(f"Rate: {checked/elapsed:.1f} req/s")
    print(f"{'=' * 55}")
    
    if found_new:
        for gid in sorted(found_new):
            print(f"  {gid}")
        path = f"/home/rhagtoo/hybrid_found_{start.strftime('%Y%m%d_%H%M%S')}.json"
        with open(path, 'w') as f:
            json.dump({"timestamp": start.isoformat(), "found": sorted(found_new), "checked": checked}, f, indent=2)

if __name__ == "__main__":
    main()
