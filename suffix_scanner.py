#!/usr/bin/env python3
"""
Suffix brute-force scanner: given a gallery ID, brute-force last 2 characters.
Usage: python3 suffix_scanner.py <GALLERY_ID>
Example: python3 suffix_scanner.py m46dMKg
"""
import subprocess, time, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

SOCKS = "127.0.0.1:1080"
HOST = "testimage.com"
CONCURRENCY = 30
REF = "XFkkFgw"
REF_CHECK_EVERY = 100

CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

LOCATIONS = [
    # Europe (fresh pool)
    "riga", "tallinn", "copenhagen", "helsinki", "stockholm", "zurich",
    "warsaw", "frankfurt", "prague", "marseille", "london", "oslo",
    "vienna", "brussels", "milan", "palermo", "amsterdam", "paris",
    "berlin", "vilnius", "rome", "luxembourg", "manchester", "chisinau",
    "kyiv", "zagreb", "madrid", "dublin", "cairo", "bucharest",
    "istanbul", "belgrade", "sofia", "bratislava", "lisbon", "athens",
    "barcelona", "telaviv", "budapest", "nicosia",
    # Americas
    "newyork", "boston", "toronto", "atlanta", "montreal",
    "miami", "chicago", "dallas", "denver", "seattle",
    "vancouver", "mexicocity", "phoenix", "lasvegas",
    "siliconvalley", "losangeles", "bogota", "lima",
    "santiago", "saopaulo", "buenosaires",
    # Asia-Pacific
    "moscow", "dubai", "mumbai", "kathmandu", "johannesburg",
    "hanoi", "taipei", "jakarta", "singapore", "bangkok",
    "manila", "shanghai", "hongkong", "astana",
    "tokyo", "seoul", "sydney", "auckland",
]
loc_idx = 0

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
    # Return (ok, state_detail)
    if r == 'EXISTS': return True, '200=FRESH'
    if r == 'EXISTS_BANNED': return True, '28099B=ORACLE'
    if r == 'NEVER': return False, '28073B=HARD_BAN'
    return False, f'UNKNOWN({r})'

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 suffix_scanner.py <GALLERY_ID>")
        sys.exit(1)
    
    gid = sys.argv[1]
    if len(gid) != 7:
        print(f"Error: expected 7-char gallery ID, got '{gid}'")
        sys.exit(1)
    
    prefix = gid[:5]
    start = datetime.now()
    
    print("=" * 55)
    print(f"SUFFIX SCANNER — prefix: {prefix}XX")
    print(f"Space: 62² = 3844 IDs to check")
    print(f"Oracle: 200=EXISTS  28099B=EXISTS_BANNED  28073B=NEVER")
    print("=" * 55)
    
    rotate()
    ok, state = check_ref()
    sys.stdout.write(f"Init ref: {state} {'✓' if ok else '✗'}\n"); sys.stdout.flush()
    if not ok:
        for _ in range(3):
            rotate()
            ok, state = check_ref()
            if ok: break
        if not ok:
            print("Cannot establish oracle. Aborting.")
            return
    
    # Generate all suffix combinations
    suffixes = [a + b for a in CHARS for b in CHARS]
    found = []
    checked = 0
    
    print(f"\nScanning {len(suffixes)} suffixes with {CONCURRENCY} threads...\n")
    
    # Process in batches
    batch_size = CONCURRENCY
    for i in range(0, len(suffixes), batch_size):
        batch = suffixes[i:i+batch_size]
        batch_ids = [prefix + suffix for suffix in batch]
        
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
            futures = {ex.submit(probe, g): g for g in batch_ids}
            for future in as_completed(futures):
                g = futures[future]
                try:
                    result = future.result()
                except:
                    result = None
                checked += 1
                
                if result in ('EXISTS', 'EXISTS_BANNED'):
                    found.append(g)
                    marker = "✓" if result == 'EXISTS' else "⚠"
                    sys.stdout.write(f"\n  [{marker}] {g}  ({result})\n")
                    sys.stdout.flush()
        
        # Progress
        elapsed = (datetime.now() - start).total_seconds()
        rps = checked / elapsed if elapsed > 0 else 0
        eta = (len(suffixes) - checked) / rps if rps > 0 else 0
        sys.stdout.write(f"\r  {checked}/{len(suffixes)} ({checked*100/len(suffixes):.0f}%) found={len(found)} rps={rps:.1f} eta={eta:.0f}s  ")
        sys.stdout.flush()
        
        # Ref check
        if checked % REF_CHECK_EVERY == 0:
            ok, state = check_ref()
            if not ok:
                sys.stdout.write(f"\n⚠ Oracle fail. Rotating...\n")
                sys.stdout.flush()
                rotate()
                ok, state = check_ref()
                if not ok: rotate(); ok, state = check_ref()
                sys.stdout.write(f"  Ref: {state} {'✓' if ok else '✗'}\n")
                sys.stdout.flush()
    
    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n\n{'=' * 55}")
    print(f"DONE in {elapsed:.0f}s")
    print(f"Checked: {checked}  Found: {len(found)}")
    if found:
        print("Found IDs:")
        for g in found:
            print(f"  {g}")
    print("=" * 55)

if __name__ == "__main__":
    main()
