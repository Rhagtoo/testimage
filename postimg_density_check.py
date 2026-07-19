#!/usr/bin/env python3
"""
Density check: verify all IDs between known anchor pairs.
Uses Content-Length oracle (28099=exists_banned, 28073=never, 57556=exists).
Auto-rotates AdGuard locations every N requests.
"""
import subprocess, time, sys, json, csv
from datetime import datetime

SOCKS = "127.0.0.1:1080"
HOST = "testimage.com"
REQUESTS_PER_IP = 18  # rotate before ban (~20 threshold)
DELAY = 2.5  # seconds between requests

CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
char_to_val = {c: i for i, c in enumerate(CHARS)}

def b62_to_int(s):
    v = 0
    for c in s: v = v * 62 + char_to_val[c]
    return v

def int_to_b62(v):
    if v == 0: return '0'
    s = []
    while v > 0:
        s.append(CHARS[v % 62])
        v //= 62
    return ''.join(reversed(s))

LOCATIONS = [
    "tokyo", "singapore", "sydney", "newyork", "miami",
    "london", "amsterdam", "frankfurt", "paris", "madrid",
    "milan", "stockholm", "warsaw", "vienna", "zurich"
]
loc_idx = 0

def rotate_adguard():
    global loc_idx
    loc = LOCATIONS[loc_idx % len(LOCATIONS)]
    loc_idx += 1
    print(f"\n>>> Switching AdGuard to {loc.upper()}...")
    sys.stdout.flush()
    subprocess.run(["adguardvpn-cli", "connect", "-l", loc], 
                   capture_output=True, timeout=20)
    time.sleep(4)

def curl_gallery(gid):
    """Check one gallery ID. Returns (http_code, body_size, oracle)"""
    time.sleep(DELAY)
    url = f"https://{HOST}/gallery/{gid}"
    cmd = f'curl -s -o /dev/null -w "\\nHTTP:%{{http_code}}\\nSIZE:%{{size_download}}" --socks5 {SOCKS} "{url}"'
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
    
    http_code = body_size = "?"
    for line in r.stdout.strip().split('\n'):
        if line.startswith('HTTP:'): http_code = line.split(':', 1)[1]
        elif line.startswith('SIZE:'): body_size = line.split(':', 1)[1]
    
    oracle = "?"
    if http_code == '200':
        oracle = "EXISTS"
    elif body_size == '28099':
        oracle = "EXISTS_BANNED"
    elif body_size == '28073':
        oracle = "NEVER"
    
    return http_code, body_size, oracle


def find_anchor_pairs(csv_path, min_delta=5, max_delta=30):
    """Find consecutive ID pairs with given delta range"""
    pairs = []
    prev_gid = prev_int = None
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            gid = row.get('gid', '').strip()
            if len(gid) != 7: continue
            try: gid_int = b62_to_int(gid)
            except: continue
            if prev_int and min_delta <= gid_int - prev_int <= max_delta:
                pairs.append((prev_gid, gid, gid_int - prev_int, prev_int, gid_int))
            prev_gid = gid; prev_int = gid_int
    return pairs


def main():
    csv_path = "/mnt/c/Users/Rhagtoo/POSTIMG/enriched_counter.csv"
    pairs = find_anchor_pairs(csv_path)
    print(f"Found {len(pairs)} anchor pairs (delta 5-30)")
    
    # Select first 5 pairs to test
    test_pairs = pairs[:5]
    
    results = []
    request_count = 0
    
    # Ensure AdGuard is connected
    rotate_adguard()
    
    for pair_idx, (gid_a, gid_b, delta, int_a, int_b) in enumerate(test_pairs):
        # Generate all intermediate IDs
        between = [int_to_b62(int_a + i) for i in range(1, delta)]
        
        print(f"\n{'='*60}")
        print(f"Pair {pair_idx+1}/{len(test_pairs)}: {gid_a} → {gid_b} (delta={delta}, {len(between)} IDs to check)")
        print(f"{'='*60}")
        
        # Check anchor A
        if request_count > 0 and request_count % REQUESTS_PER_IP == 0:
            rotate_adguard()
        
        code, size, oracle = curl_gallery(gid_a)
        request_count += 1
        print(f"  [{request_count:3d}] ANCHOR_A {gid_a:8s} → HTTP={code} SIZE={size}B {oracle}")
        results.append({"pair": pair_idx, "type": "anchor_a", "gid": gid_a, "http": code, "size": size, "oracle": oracle})
        
        # Check intermediate IDs
        exists_count = 0
        for gid in between:
            if request_count > 0 and request_count % REQUESTS_PER_IP == 0:
                rotate_adguard()
            
            code, size, oracle = curl_gallery(gid)
            request_count += 1
            
            if oracle in ("EXISTS", "EXISTS_BANNED"):
                exists_count += 1
                marker = "✓" if oracle == "EXISTS" else "⚠"
            else:
                marker = "✗"
            
            print(f"  [{request_count:3d}] MIDDLE   {gid:8s} → HTTP={code} SIZE={size}B {oracle} {marker}")
            results.append({"pair": pair_idx, "type": "middle", "gid": gid, "http": code, "size": size, "oracle": oracle})
        
        # Check anchor B
        if request_count > 0 and request_count % REQUESTS_PER_IP == 0:
            rotate_adguard()
        
        code, size, oracle = curl_gallery(gid_b)
        request_count += 1
        print(f"  [{request_count:3d}] ANCHOR_B {gid_b:8s} → HTTP={code} SIZE={size}B {oracle}")
        results.append({"pair": pair_idx, "type": "anchor_b", "gid": gid_b, "http": code, "size": size, "oracle": oracle})
        
        density = exists_count / len(between) * 100 if between else 0
        print(f"  >>> Density: {exists_count}/{len(between)} = {density:.1f}%")
    
    # Summary
    print(f"\n{'='*60}")
    print(f"FINAL SUMMARY")
    print(f"{'='*60}")
    
    total_middle = sum(1 for r in results if r['type'] == 'middle')
    total_exists = sum(1 for r in results if r['type'] == 'middle' and r['oracle'] in ('EXISTS', 'EXISTS_BANNED'))
    
    print(f"Total requests: {request_count}")
    print(f"Middle IDs checked: {total_middle}")
    print(f"Found existing: {total_exists} ({total_exists/total_middle*100:.1f}%)" if total_middle else "N/A")
    
    # Save results
    out_path = f"/home/rhagtoo/density_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"Results saved to: {out_path}")


if __name__ == "__main__":
    main()
