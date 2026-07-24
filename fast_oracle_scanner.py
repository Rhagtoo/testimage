#!/usr/bin/env python3
"""
Fast oracle scanner — embraces bans, uses size oracle to see through them.
Periodic ref-check ensures oracle is alive (ref=28099B → ok, ref=28073B → rotate IP).
"""
import subprocess, time, sys, json, random, itertools
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

SOCKS = "127.0.0.1:1080"
HOST = "testimage.cc"
CONCURRENCY = 40
REF_CHECK_EVERY = 50  # check ref every N requests
ORACLE_VALID = "28099"  # EXISTS_BANNED size
ORACLE_NEVER = "28073"  # NEVER_EXISTED size

CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
CV = {c: i for i, c in enumerate(CHARS)}

LOCATIONS = [
    "stockholm", "helsinki", "lisbon", "oslo",
    "sydney", "newyork", "miami",
    "london", "amsterdam", "frankfurt", "paris", "madrid",
    "milan", "warsaw", "vienna", "zurich",
    "tokyo", "singapore",  # known-bad, last resort
]
loc_idx = 0

def rotate():
    global loc_idx
    loc = LOCATIONS[loc_idx % len(LOCATIONS)]
    loc_idx += 1
    print(f"\n🔄 {loc.upper()} ", end="", flush=True)
    subprocess.run(["adguardvpn-cli", "connect", "-l", loc], capture_output=True, timeout=20)
    time.sleep(4)

def probe(gid):
    """Returns 'EXISTS','EXISTS_BANNED','NEVER' or None on error"""
    url = f"https://{HOST}/gallery/{gid}"
    cmd = f'curl -s --max-time 8 -o /dev/null -w "%{{http_code}} %{{size_download}}" --socks5 {SOCKS} "{url}"'
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        parts = r.stdout.strip().split()
        if len(parts) < 2: return None
        http, size = parts[0], parts[1]
        if http == '200': return 'EXISTS'
        if size == ORACLE_VALID: return 'EXISTS_BANNED'
        if size == ORACLE_NEVER: return 'NEVER'
        return None  # unexpected
    except:
        return None

def check_ref(ref="XFkkFgw"):
    """Check if oracle is alive: ref should return EXISTS or EXISTS_BANNED"""
    r = probe(ref)
    return r in ('EXISTS', 'EXISTS_BANNED'), r

def gen_random_id():
    """Generate random 7-char base62 ID"""
    return ''.join(random.choices(CHARS, k=7))

def main():
    start = datetime.now()
    found = []
    checked = 0
    oracle_checks = 0
    oracle_fails = 0
    
    print("=" * 55)
    print("FAST ORACLE SCANNER")
    print(f"Concurrency: {CONCURRENCY}  Ref-check: every {REF_CHECK_EVERY}")
    print(f"Oracle: {ORACLE_VALID}B=EXISTS_BANNED  {ORACLE_NEVER}B=NEVER")
    print("=" * 55)
    
    rotate()
    
    # Verify initial oracle state
    ok, state = check_ref()
    print(f"Initial ref check: {state} {'✓' if ok else '✗'}")
    if not ok:
        print("Ref not visible — rotating...")
        rotate()
        ok, state = check_ref()
        print(f"Retry ref check: {state} {'✓' if ok else '✗'}")
    
    if not ok:
        print("Cannot establish oracle. Aborting.")
        return
    
    batch = []
    
    while checked < 5000:  # safety limit
        # Generate batch of random IDs
        batch_ids = [gen_random_id() for _ in range(CONCURRENCY)]
        
        # Parallel probe
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
            futures = {ex.submit(probe, gid): gid for gid in batch_ids}
            for future in as_completed(futures):
                gid = futures[future]
                try:
                    result = future.result()
                except:
                    result = None
                
                checked += 1
                
                if result in ('EXISTS', 'EXISTS_BANNED'):
                    found.append(gid)
                    marker = "✓" if result == 'EXISTS' else "⚠"
                    print(f"\n  [ {marker} ] {gid} (total found: {len(found)})", flush=True)
                
                # Progress
                if checked % 100 == 0:
                    elapsed = (datetime.now() - start).total_seconds()
                    rps = checked / elapsed if elapsed > 0 else 0
                    print(f"\r  [{checked}] found={len(found)} rps={rps:.1f} ", end="", flush=True)
                
                # Ref check
                if checked % REF_CHECK_EVERY == 0:
                    oracle_checks += 1
                    ok, state = check_ref()
                    if not ok:
                        oracle_fails += 1
                        print(f"\n⚠ Oracle FAIL: ref={state} at {checked} requests. Rotating...", flush=True)
                        rotate()
                        ok, state = check_ref()
                        if not ok:
                            print(f"⚠ Still dead: ref={state}. Rotating again...", flush=True)
                            rotate()
                            ok, state = check_ref()
                        if ok:
                            print(f"✓ Oracle restored: ref={state}", flush=True)
                        else:
                            print(f"✗ Cannot restore oracle. Pausing 30s...", flush=True)
                            time.sleep(30)
                            rotate()
    
    # Final summary
    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n\n{'=' * 55}")
    print(f"SCAN COMPLETE")
    print(f"Time: {elapsed:.0f}s  Checked: {checked}  Found: {len(found)}")
    print(f"Rate: {checked/elapsed:.1f} req/s")
    print(f"Oracle checks: {oracle_checks}  Fails: {oracle_fails}")
    print(f"{'=' * 55}")
    
    if found:
        print("Found IDs:")
        for gid in found:
            print(f"  {gid}")
        
        with open(f"/home/rhagtoo/oracle_found_{start.strftime('%Y%m%d_%H%M%S')}.json", 'w') as f:
            json.dump({"timestamp": start.isoformat(), "found": found, "checked": checked}, f, indent=2)

if __name__ == "__main__":
    main()
