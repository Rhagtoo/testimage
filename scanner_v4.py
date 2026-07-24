#!/usr/bin/env python3
"""
Scanner v4 — 29 AdGuard ports, round-robin: 3 probes per port, then switch.
"""
import subprocess, time, sys, json, random, threading
from datetime import datetime, timezone
from pathlib import Path

# ── Config ──────────────────────────────────────────────
PORTS = list(range(1080, 1110))
HOST = "postimg.cc"
REF = "y3tXqH0"
ANCHORS_FILE = "/home/rhagtoo/testimage/anchor_ids.txt"
PROBES_PER_PORT = 3
BATCHES_PER_PORT = 2  # 3 probes via chain A, then 3 via chain B = 6 total
NUM_TARGETS = 1000

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
        print(f"  ⚠ check_ref :{port} error: {e}", flush=True)
        return "CONN_FAIL", 0, 0

def probe(gid, port):
    time.sleep(0.2)
    http, size = curl(f"https://{HOST}/gallery/{gid}", port)
    return classify(http, size), http, size

# ── AdGuard helpers ─────────────────────────────────────

def rotate_port(port):
    """Rotate a specific port's AdGuard to next location. Returns True if success, False on error."""
    home_dir = f"/tmp/ag_port_{port}"
    try:
        r = subprocess.run(
            f'HOME="{home_dir}" adguardvpn-cli connect -y',
            shell=True, capture_output=True, text=True, timeout=20
        )
        return "Successfully Connected" in (r.stdout + r.stderr)
    except Exception as e:
        print(f"  ⚠ rotate :{port} failed: {e}", flush=True)
        return False


def main():
    start = datetime.now()
    log_path = Path(f"/home/rhagtoo/testimage/scan_v4_{start.strftime('%Y%m%d_%H%M%S')}.jsonl")
    log_lock = threading.Lock()
    
    def log_event(etype, **data):
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "type": etype, **data}
        with log_lock:
            with open(log_path, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    
    print(f"Scanner v4 — {start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Ports: {PORTS[0]}-{PORTS[-1]} ({len(PORTS)} total)")
    print(f"Probes per port: {PROBES_PER_PORT}")
    print(f"Log: {log_path}")
    
    # Step 1: Check which ports can see the ref
    print("\n── Checking ports...", flush=True)
    alive_ports = []
    for port in PORTS:
        cls, http, size = check_ref(port)
        ok = cls in ("EXISTS", "EXISTS_BANNED")
        icon = "✓" if ok else "✗"
        if ok or cls == "NEVER":
            alive_ports.append(port)
        # Print every 5th to save space
        if port % 5 == 0 or port == PORTS[-1]:
            sys.stdout.write(f"\r  :{port} → {icon} {cls}  ")
            sys.stdout.flush()
    print(f"\n  Alive: {len(alive_ports)}/{len(PORTS)}")
    
    if not alive_ports:
        print("No ports! Run setup first.")
        return
    
    log_event("scan_start", ports=len(alive_ports), probes_per_port=PROBES_PER_PORT)
    
    # Step 2: Pick seed from anchors
    print("\n── Picking seed...", flush=True)
    anchors = []
    with open(ANCHORS_FILE) as f:
        for line in f:
            gid = line.strip()
            if len(gid) == 7 and all(c in CV for c in gid):
                anchors.append(gid)
    seed = random.choice(anchors)
    print(f"  Seed: {seed}")
    log_event("seed_selected", gid=seed)
    
    # Step 3: Generate targets
    targets = generate_targets(seed, NUM_TARGETS)
    print(f"  Targets: {len(targets)} ({targets[0]} → {targets[-1]})")
    log_event("targets_generated", count=len(targets), first=targets[0], last=targets[-1])
    
    # Step 4: Round-robin scan
    print(f"\n── Scanning ({PROBES_PER_PORT} IDs/port × {len(alive_ports)} ports)...", flush=True)
    
    checked = 0
    found_set = set()
    port_idx = 0
    total_rotated = 0
    target_idx = 0  # current position in targets list
    
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
                print(f"\n  ⚠ All ports blind at target {target_idx}. Waiting 30s...", flush=True)
                time.sleep(30)
                attempts = 0
        
        # 2 batches of 3 probes each (chain A → chain B) = 6 total per port
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
                
                # Check ref before probe
                cls, _, _ = check_ref(port)
                if cls not in ("EXISTS", "EXISTS_BANNED"):
                    log_event("port_blind_before", port=port, target_idx=target_idx, chain=chain)
                    port_blind = True
                    break
                
                # Probe!
                cls, http, size = probe(gid, port)
                checked += 1
                log_event("probe", gid=gid, port=port, http=http, size=size, cls=cls, chain=chain)
                
                if cls in ("EXISTS", "EXISTS_BANNED"):
                    found_set.add(gid)
                    log_event("found", gid=gid, port=port, cls=cls, chain=chain)
                    print(f"\n  🎯 FOUND: {gid} on :{port} ({cls})", flush=True)
                
                # Check ref after probe
                cls, _, _ = check_ref(port)
                if cls not in ("EXISTS", "EXISTS_BANNED"):
                    log_event("port_blind_after", port=port, target_idx=target_idx, chain=chain)
                    port_blind = True
                    break
        
        # Rotate if port went blind during this batch
        if port_blind:
            if rotate_port(port):
                total_rotated += 1
                log_event("port_rotated", port=port, total=total_rotated)
                time.sleep(2)
        
        # Progress every 50 targets
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
        path = Path(f"/home/rhagtoo/testimage/found_v4_{start.strftime('%Y%m%d_%H%M%S')}.json")
        with open(path, "w") as f:
            json.dump({"seed": seed, "found": sorted(found_set), "checked": checked}, f, indent=2)
        print(f"Results: {path}")

if __name__ == "__main__":
    main()
