#!/usr/bin/env python3
"""
Scanner — multi-port round-robin gallery discovery.
Uses multiple SOCKS5 proxy ports, each with a different VPN location.
3 probes via "chain A", then 3 via "chain B" (6 total per port), then switch.

Usage:
  1. Set up SOCKS5 proxy ports (see setup_ports.sh)
  2. Create anchor_ids.txt with known gallery IDs
  3. Set TARGET_HOST and REF_GALLERY below
  4. Run: python3 scanner.py

Requires: curl, adguardvpn-cli (or adapt rotate_port for your proxy)
"""
import subprocess, time, sys, json, random, threading
from datetime import datetime, timezone
from pathlib import Path

# ── CONFIG ──────────────────────────────────────────────
TARGET_HOST = "postimg.cc"          # Target host to scan
REF_GALLERY = "y3tXqH0"            # Known existing gallery for ref checks
ANCHORS_FILE = "anchor_ids.txt"     # File with known gallery IDs (one per line)
BASE_PORT = 1080                    # First SOCKS5 port
NUM_PORTS = 30                      # Number of ports
PROBES_PER_PORT = 3                 # Probes per batch
BATCHES_PER_PORT = 2                # Batches per port (chain A + chain B = 6 total)
NUM_TARGETS = 1000                  # IDs to scan from seed

CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
CV = {c: i for i, c in enumerate(CHARS)}

PORTS = list(range(BASE_PORT, BASE_PORT + NUM_PORTS))
HOST = TARGET_HOST
REF = REF_GALLERY

# ── base62 helpers ──────────────────────────────────────
def b2i(s):
    v = 0
    for c in s: v = v * 62 + CV[c]
    return v

def i2b(v):
    if v == 0: return '0'
    s = []
    while v > 0: s.append(CHARS[v % 62]); v //= 62
    return ''.join(reversed(s))

def generate_targets(seed, count):
    seed_int = b2i(seed)
    targets = []
    for i in range(1, count + 1):
        gid = i2b(seed_int + i)
        if len(gid) == 7:
            targets.append(gid)
    return targets

# ── HTTP helpers ────────────────────────────────────────
def curl(url, port, timeout=10):
    cmd = f'curl -s --max-time {timeout} -o /dev/null -w "%{{http_code}} %{{size_download}}" --socks5 127.0.0.1:{port} "{url}"'
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout+2)
        parts = r.stdout.strip().split()
        if len(parts) >= 2:
            return int(parts[0]), int(parts[1])
    except:
        pass
    return 0, 0

def classify(http, size):
    if http == 200: return "EXISTS"
    if http in (404, 403) and size > 28073: return "EXISTS_BANNED"
    if http in (404, 403) and size == 28073: return "NEVER"
    if http == 0: return "CONN_FAIL"
    return f"UNKNOWN"

def check_ref(port):
    time.sleep(0.2)
    try:
        http, size = curl(f"https://{HOST}/gallery/{REF}", port)
        return classify(http, size), http, size
    except Exception as e:
        print(f"  ! check_ref :{port} error: {e}", flush=True)
        return "CONN_FAIL", 0, 0

def probe(gid, port):
    time.sleep(0.2)
    http, size = curl(f"https://{HOST}/gallery/{gid}", port)
    return classify(http, size), http, size

# ── Proxy rotation ──────────────────────────────────────
def rotate_port(port):
    """Rotate proxy on given port. Adapt this for your proxy setup."""
    home_dir = f"/tmp/proxy_port_{port}"
    try:
        r = subprocess.run(
            f'HOME="{home_dir}" adguardvpn-cli connect -y',
            shell=True, capture_output=True, text=True, timeout=20
        )
        return "Successfully Connected" in (r.stdout + r.stderr)
    except Exception as e:
        print(f"  ! rotate :{port} failed: {e}", flush=True)
        return False

# ── Main ────────────────────────────────────────────────
def main(seed_override=None):
    start = datetime.now()
    log_path = Path(f"scan_{start.strftime('%Y%m%d_%H%M%S')}.jsonl")
    log_lock = threading.Lock()
    
    def log_event(etype, **data):
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "type": etype, **data}
        with log_lock:
            with open(log_path, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    
    print(f"Scanner — {start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Ports: {BASE_PORT}-{BASE_PORT+NUM_PORTS-1} ({NUM_PORTS} total)")
    print(f"Batches: {BATCHES_PER_PORT} x {PROBES_PER_PORT} probes")
    print(f"Log: {log_path}")
    
    # Step 1: Check ports
    print("\n── Checking ports...", flush=True)
    alive_ports = []
    for port in PORTS:
        cls, http, size = check_ref(port)
        ok = cls in ("EXISTS", "EXISTS_BANNED")
        icon = "v" if ok else "x"
        if ok or cls == "NEVER":
            alive_ports.append(port)
        if port % 5 == 0 or port == PORTS[-1]:
            sys.stdout.write(f"\r  :{port} -> {icon} {cls}  ")
            sys.stdout.flush()
    print(f"\n  Alive: {len(alive_ports)}/{len(PORTS)}")
    
    if not alive_ports:
        print("No ports! Set up proxies first.")
        return
    
    log_event("scan_start", ports=len(alive_ports), probes_per_port=PROBES_PER_PORT)
    
    # Step 2: Pick seed
    print("\n── Picking seed...", flush=True)
    if seed_override:
        seed = seed_override
    else:
        with open(ANCHORS_FILE) as f:
            anchors = [l.strip() for l in f if len(l.strip()) == 7 and all(c in CV for c in l.strip())]
        seed = random.choice(anchors)
    print(f"  Seed: {seed}")
    log_event("seed_selected", gid=seed)
    
    # Step 3: Generate targets
    targets = generate_targets(seed, NUM_TARGETS)
    print(f"  Targets: {len(targets)} ({targets[0]} -> {targets[-1]})")
    log_event("targets_generated", count=len(targets), first=targets[0], last=targets[-1])
    
    # Step 4: Round-robin scan
    total_batches = BATCHES_PER_PORT
    print(f"\n── Scanning ({total_batches}x{PROBES_PER_PORT} per port)...", flush=True)
    
    checked = 0
    found_set = set()
    port_idx = 0
    total_rotated = 0
    target_idx = 0
    
    while target_idx < len(targets):
        # Find next alive port
        port = None
        attempts = 0
        while port is None:
            p = alive_ports[port_idx]
            port_idx = (port_idx + 1) % len(alive_ports)
            
            cls, http, size = check_ref(p)
            if cls in ("EXISTS", "EXISTS_BANNED"):
                port = p
                break
            
            attempts += 1
            if attempts % 5 == 0:
                log_event("port_blind_skip", port=p, cls=cls)
            
            if attempts >= len(alive_ports):
                log_event("all_blind", target_idx=target_idx, attempts=attempts)
                print(f"\n  ! All ports blind at target {target_idx}. Waiting 30s...", flush=True)
                time.sleep(30)
                attempts = 0
        
        # 2 batches x 3 probes = 6 IDs per port
        port_blind = False
        for batch in range(BATCHES_PER_PORT):
            if port_blind:
                break
            chain = "A" if batch == 0 else "B"
            for probe_num in range(PROBES_PER_PORT):
                if target_idx >= len(targets):
                    break
                gid = targets[target_idx]
                target_idx += 1
                
                cls, _, _ = check_ref(port)
                if cls not in ("EXISTS", "EXISTS_BANNED"):
                    log_event("port_blind_before", port=port, target_idx=target_idx, chain=chain)
                    port_blind = True
                    break
                
                cls, http, size = probe(gid, port)
                checked += 1
                log_event("probe", gid=gid, port=port, http=http, size=size, cls=cls, chain=chain)
                
                if cls in ("EXISTS", "EXISTS_BANNED"):
                    found_set.add(gid)
                    log_event("found", gid=gid, port=port, cls=cls, chain=chain)
                    print(f"\n  FOUND: {gid} on :{port} ({cls})", flush=True)
                
                cls, _, _ = check_ref(port)
                if cls not in ("EXISTS", "EXISTS_BANNED"):
                    log_event("port_blind_after", port=port, target_idx=target_idx, chain=chain)
                    port_blind = True
                    break
        
        if port_blind:
            if rotate_port(port):
                total_rotated += 1
                log_event("port_rotated", port=port, total=total_rotated)
                time.sleep(2)
        
        if target_idx % 50 == 0:
            elapsed = (datetime.now() - start).total_seconds()
            rps = checked / elapsed if elapsed > 0 else 0
            msg = (f"\r  [{target_idx}/{len(targets)}] checked={checked} "
                   f"found={len(found_set)} rps={rps:.1f} "
                   f"rotated={total_rotated}")
            sys.stdout.write(msg)
            sys.stdout.flush()
            log_event("progress", targets_done=target_idx, checked=checked,
                     found=len(found_set), rps=round(rps,1), rotated=total_rotated)
    
    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n\nDONE — {elapsed:.0f}s  checked={checked}  "
          f"found={len(found_set)}  rotated={total_rotated}")
    log_event("scan_complete", checked=checked, found=len(found_set),
             elapsed_s=round(elapsed,1), rotated=total_rotated)
    
    if found_set:
        path = Path(f"found_{start.strftime('%Y%m%d_%H%M%S')}.json")
        with open(path, "w") as f:
            json.dump({"seed": seed, "found": sorted(found_set), "checked": checked}, f, indent=2)
        print(f"Results: {path}")

if __name__ == "__main__":
    import sys
    seed = sys.argv[1] if len(sys.argv) > 1 else None
    main(seed_override=seed)
