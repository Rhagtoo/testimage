#!/usr/bin/env python3
"""
Scanner v3 — radius 1000, AdGuard auto-rotation.
When port goes blind, cycles to next VPN location.
"""
import subprocess, time, sys, json, random, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# ── Config ──────────────────────────────────────────────
PORT = 1080            # AdGuard SOCKS port
HOST = "postimg.cc"
REF = "y3tXqH0"
ANCHORS_FILE = "/home/rhagtoo/testimage/anchor_ids.txt"
BATCH = 1
PROBE_DELAY = 0.2
ROTATE_COOLDOWN = 5   # seconds between rotations

ADGUARD_LOCATIONS = [
    # APAC (different CF edges, less likely under wave ban)
    "TOKYO", "SINGAPORE", "SYDNEY", "MUMBAI",
    # Americas
    "NEW YORK", "CHICAGO", "LOS ANGELES", "MIAMI", "TORONTO", "SAO PAULO",
    # Europe (likely under wave ban, but try anyway)
    "HELSINKI", "STOCKHOLM", "OSLO", "COPENHAGEN",
    "LONDON", "AMSTERDAM", "FRANKFURT", "PARIS", "ZURICH",
    "MILAN", "MADRID", "VIENNA", "PRAGUE", "WARSAW",
    "BRUSSELS", "RIGA", "TALLINN", "VILNIUS",
    "BUCHAREST", "SOFIA", "ATHENS", "LISBON",
    "DUBLIN", "LUXEMBOURG",
    # More APAC/other
    "HONG KONG", "SEOUL", "TAIPEI", "BANGKOK",
    "JAKARTA", "MANILA", "DUBAI", "ISTANBUL",
    "TEL AVIV", "JOHANNESBURG", "MEXICO CITY",
    "BOGOTA", "SANTIAGO", "BUENOS AIRES",
]

# base62
CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
CV = {c: i for i, c in enumerate(CHARS)}

LOG_PATH = Path(f"/home/rhagtoo/testimage/scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")
log_lock = threading.Lock()

def log_event(event_type: str, **data):
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "type": event_type, **data}
    with log_lock:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def b2i(s): 
    v = 0
    for c in s: v = v * 62 + CV[c]
    return v
def i2b(v):
    if v == 0: return '0'
    s = []
    while v > 0: s.append(CHARS[v % 62]); v //= 62
    return ''.join(reversed(s))

def curl(url, port, timeout=8):
    cmd = f'curl -s --max-time {timeout} -o /dev/null -w "%{{http_code}} %{{size_download}}" --socks5 127.0.0.1:{port} "{url}"'
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout+2)
        parts = r.stdout.strip().split()
        if len(parts) >= 2: return int(parts[0]), int(parts[1])
    except: pass
    return 0, 0

def classify(http, size):
    if http == 200: return "EXISTS"
    if http in (404, 403) and size > 28073: return "EXISTS_BANNED"
    if http in (404, 403) and size == 28073: return "NEVER"
    if http == 0: return "CONN_FAIL"
    return f"UNKNOWN"

def probe(gid):
    time.sleep(PROBE_DELAY)
    http, size = curl(f"https://{HOST}/gallery/{gid}", PORT)
    return classify(http, size), http, size

def check_ref():
    cls, http, size = probe(REF)
    return cls in ("EXISTS", "EXISTS_BANNED"), cls, http, size

def load_anchors(path):
    anchors = []
    with open(path) as f:
        for line in f:
            gid = line.strip()
            if len(gid) == 7 and all(c in CV for c in gid):
                anchors.append(gid)
    return anchors

# ── AdGuard rotation ────────────────────────────────────
rotation_lock = threading.Lock()
rotation_idx = [0]
blind_count = [0]
rotate_count = [0]

def rotate_adguard():
    """Cycle AdGuard to next location. Returns True if successful."""
    with rotation_lock:
        location = ADGUARD_LOCATIONS[rotation_idx[0]]
        rotation_idx[0] = (rotation_idx[0] + 1) % len(ADGUARD_LOCATIONS)
    
    r = subprocess.run(
        f'adguardvpn-cli connect -l "{location}" -y',
        shell=True, capture_output=True, text=True, timeout=20
    )
    ok = "Successfully Connected" in (r.stdout + r.stderr)
    
    with rotation_lock:
        rotate_count[0] += 1
    
    log_event("adguard_rotate", location=location, ok=ok, total=rotate_count[0])
    
    if ok:
        time.sleep(2)  # Let connection stabilize
    return ok

def ensure_fresh():
    """Try rotating until port is fresh. Returns True if fresh, False if exhausted."""
    for _ in range(5):  # max 5 rotation attempts per call
        ok, cls, http, size = check_ref()
        if ok:
            return True
        log_event("port_blind", cls=cls, http=http, size=size)
        with rotation_lock: blind_count[0] += 1
        
        if not rotate_adguard():
            time.sleep(ROTATE_COOLDOWN)
    return False  # exhausted rotation attempts

def main():
    start = datetime.now()
    print(f"Scanner v3 — {start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Port: {PORT}  Batch: {BATCH}  Log: {LOG_PATH}")
    print(f"Locations: {len(ADGUARD_LOCATIONS)}  Radius: 1000")
    
    anchors = load_anchors(ANCHORS_FILE)
    random.shuffle(anchors)
    anchor_set = set(anchors)
    print(f"Anchors: {len(anchors)}")
    
    # Initial freshness check (max 10 rotations)
    print("Checking port...")
    ok, cls, http, size = check_ref()
    if not ok:
        print(f"  :{PORT} → {cls} ({http},{size}B) — rotating...")
        for attempt in range(10):
            if rotate_adguard():
                ok, cls, http, size = check_ref()
                if ok:
                    print(f"  :{PORT} → ✓ {cls} (attempt {attempt+1})")
                    break
        else:
            print(f"  ⚠ All initial rotations blind ({rotate_count[0]} total). Starting anyway...")
    else:
        print(f"  :{PORT} → ✓ {cls}")
    
    print(f"\nSCANNING...")
    log_event("scan_start", anchors=len(anchors), locations=len(ADGUARD_LOCATIONS), batch=BATCH)
    
    checked = [0]
    found_set = set()
    check_lock = threading.Lock()
    anchors_done = [0]
    fresh_lock = threading.Lock()  # serialize ensure_fresh calls
    
    def scan_anchor(anchor):
        print(f"[scan] {anchor}", flush=True)
        anchor_int = b2i(anchor)
        
        for radius in [1000]:
            prev = max(0, radius - 10)
            neighbors = []
            for offset in range(-radius, radius + 1):
                if offset == 0 or abs(offset) <= prev: continue
                gid = i2b(anchor_int + offset)
                if gid not in anchor_set and gid not in found_set:
                    neighbors.append(gid)
            
            if not neighbors: continue
            
            for i in range(0, len(neighbors), BATCH):
                batch = neighbors[i:i+BATCH]
                
                # Ensure port is fresh before probing
                ok, cls, _, _ = check_ref()
                if not ok:
                    with fresh_lock:
                        if not ensure_fresh():
                            time.sleep(10)  # wait for wave ban to pass
                            continue
                
                blind = False
                for gid in batch:
                    cls, http, size = probe(gid)
                    with check_lock: checked[0] += 1
                    if cls in ("EXISTS", "EXISTS_BANNED"):
                        found_set.add(gid)
                        log_event("found", gid=gid, near=anchor, radius=radius, cls=cls)
                
                # Ref check after batch — rotate if blind
                ok, _, _, _ = check_ref()
                if not ok:
                    blind = True
                    with fresh_lock:
                        ensure_fresh()
                    break  # switch to fresh location
        
        with check_lock: anchors_done[0] += 1
    
    with ThreadPoolExecutor(max_workers=1) as executor:
        futures = {}
        for a in anchors[:1]:
            futures[executor.submit(scan_anchor, a)] = a
        remaining = anchors[1:]
        
        while futures:
            done = next(as_completed(futures), None)
            if done:
                try: done.result()
                except Exception as e:
                    print(f"[ERROR] {e}", flush=True)
                    log_event("error", error=str(e))
                del futures[done]
                print(f"[done] anchor complete. anchors_done={anchors_done[0]}", flush=True)
                
                # Progress every 10 anchors
                if anchors_done[0] % 10 == 0:
                    elapsed = (datetime.now() - start).total_seconds()
                    rps = checked[0] / elapsed if elapsed > 0 else 0
                    msg = f"\r  anchors={anchors_done[0]}/{len(anchors)} checked={checked[0]} found={len(found_set)} rps={rps:.1f} rotates={rotate_count[0]} blinds={blind_count[0]}  "
                    sys.stdout.write(msg)
                    sys.stdout.flush()
                    log_event("progress", anchors_done=anchors_done[0], checked=checked[0], 
                             found=len(found_set), rps=round(rps,1), 
                             rotates=rotate_count[0], blinds=blind_count[0])
                
                if remaining:
                    futures[executor.submit(scan_anchor, remaining.pop(0))] = remaining[0] if remaining else None
    
    elapsed = (datetime.now() - start).total_seconds()
    print(f"\nDONE — {elapsed:.0f}s  checked={checked[0]}  found={len(found_set)}  rotates={rotate_count[0]}")
    log_event("scan_complete", checked=checked[0], found=len(found_set), 
             elapsed_s=round(elapsed,1), rotates=rotate_count[0], blinds=blind_count[0])
    
    if found_set:
        path = Path(f"/home/rhagtoo/testimage/found_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(path, "w") as f:
            json.dump({"found": sorted(found_set), "checked": checked[0]}, f, indent=2)
        print(f"Results: {path}")

if __name__ == "__main__":
    main()
