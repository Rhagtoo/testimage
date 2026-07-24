#!/usr/bin/env python3
"""
Scanner — radius-based approach. For each known gallery (anchor),
probes neighbors within a given radius using SOCKS5 proxy.
Auto-rotates VPN location when port goes blind.

Usage: python3 scanner_radius.py [seed_gallery]
"""
import subprocess, time, sys, json, random, threading
from datetime import datetime, timezone
from pathlib import Path

# ── CONFIG ──────────────────────────────────────────────
TARGET_HOST = "testimage.cc"
REF_GALLERY = "y3tXqH0"
ANCHORS_FILE = "anchor_ids.txt"
SOCKS_PORT = 1080
SCAN_RADIUS = 1000
BATCH = 1

CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
CV = {c: i for i, c in enumerate(CHARS)}

def b2i(s):
    v = 0
    for c in s: v = v * 62 + CV[c]
    return v
def i2b(v):
    if v == 0: return '0'
    s = []
    while v > 0: s.append(CHARS[v % 62]); v //= 62
    return ''.join(reversed(s))

def curl(url, timeout=8):
    cmd = f'curl -s --max-time {timeout} -o /dev/null -w "%{{http_code}} %{{size_download}}" --socks5 127.0.0.1:{SOCKS_PORT} "{url}"'
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout+2)
        parts = r.stdout.strip().split()
        return int(parts[0]), int(parts[1]) if len(parts) >= 2 else (0, 0)
    except: return 0, 0

def classify(http, size):
    if http == 200: return "EXISTS"
    if http in (404, 403) and size > 28073: return "EXISTS_BANNED"
    if http in (404, 403) and size == 28073: return "NEVER"
    if http == 0: return "CONN_FAIL"
    return "UNKNOWN"

def check_ref():
    http, size = curl(f"https://{TARGET_HOST}/gallery/{REF_GALLERY}")
    return classify(http, size) in ("EXISTS", "EXISTS_BANNED"), classify(http, size), http, size

def probe(gid):
    time.sleep(0.2)
    http, size = curl(f"https://{TARGET_HOST}/gallery/{gid}")
    return classify(http, size), http, size

def rotate_vpn():
    """Rotate to next VPN location. Adapt for your setup."""
    try:
        r = subprocess.run("adguardvpn-cli connect -y", shell=True, capture_output=True, text=True, timeout=20)
        return "Successfully Connected" in (r.stdout + r.stderr)
    except: return False

def main():
    log_path = Path(f"scan_radius_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")
    
    with open(ANCHORS_FILE) as f:
        anchors = [l.strip() for l in f if len(l.strip()) == 7]
    random.shuffle(anchors)
    anchor_set = set(anchors)
    print(f"Anchors: {len(anchors)}, Radius: {SCAN_RADIUS}")
    
    # Init
    ok, cls, _, _ = check_ref()
    if not ok:
        for _ in range(10):
            if rotate_vpn():
                ok, cls, _, _ = check_ref()
                if ok: break
            time.sleep(2)
    if not ok:
        print("Can't get fresh connection!"); return
    
    print(f"Port :{SOCKS_PORT} -> {cls}")
    
    checked = [0]
    found_set = set()
    lock = threading.Lock()
    
    def scan_anchor(anchor):
        ai = b2i(anchor)
        prev = max(0, SCAN_RADIUS - 10)
        neighbors = []
        for off in range(-SCAN_RADIUS, SCAN_RADIUS + 1):
            if off == 0 or abs(off) <= prev: continue
            gid = i2b(ai + off)
            if gid not in anchor_set and gid not in found_set:
                neighbors.append(gid)
        if not neighbors: return
        
        for i in range(0, len(neighbors), BATCH):
            batch = neighbors[i:i+BATCH]
            ok, _, _, _ = check_ref()
            if not ok:
                for _ in range(5):
                    if rotate_vpn(): break
                    time.sleep(5)
                else: time.sleep(10); continue
            
            for gid in batch:
                cls, http, size = probe(gid)
                with lock: checked[0] += 1
                if cls in ("EXISTS", "EXISTS_BANNED"):
                    found_set.add(gid)
                    entry = {"ts": datetime.now(timezone.utc).isoformat(), "type": "found", "gid": gid, "near": anchor}
                    with open(log_path, "a") as f: f.write(json.dumps(entry) + "\n")
            
            ok, _, _, _ = check_ref()
            if not ok:
                for _ in range(5):
                    if rotate_vpn(): break
                    time.sleep(2)
                break
    
    with open(log_path, "a") as f:
        f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "type": "scan_start", "anchors": len(anchors), "radius": SCAN_RADIUS}) + "\n")
    
    print("Scanning...")
    for i, a in enumerate(anchors):
        scan_anchor(a)
        if (i+1) % 50 == 0:
            print(f"\r  [{i+1}/{len(anchors)}] checked={checked[0]} found={len(found_set)}", end="", flush=True)
    
    print(f"\nDone. checked={checked[0]} found={len(found_set)}")
    if found_set:
        with open(f"found_radius_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json", "w") as f:
            json.dump(sorted(found_set), f)

if __name__ == "__main__":
    main()
