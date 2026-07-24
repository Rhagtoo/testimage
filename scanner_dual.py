#!/usr/bin/env python3
"""
Dual scanner — 2 independent threads, each with its own port pool.
Scanner A: ports 1080-1109, targets 1-500
Scanner B: ports 1110-1139, targets 501-1000
"""
import subprocess, time, sys, json, threading
from datetime import datetime, timezone
from pathlib import Path

# ── Config ──────────────────────────────────────────────
TARGET_HOST = "testimage.cc"
REF = "y3tXqH0"
SEED = "MWjqnfs"
NUM_TARGETS = 10000

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
    return [i2b(seed_int + i) for i in range(1, count + 1) if len(i2b(seed_int + i)) == 7]

def curl(url, port, timeout=10):
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
    return "UNKNOWN"

def check_ref(port):
    time.sleep(0.2)
    try:
        http, size = curl(f"https://{TARGET_HOST}/gallery/{REF}", port)
        return classify(http, size), http, size
    except: return "CONN_FAIL", 0, 0

def probe(gid, port):
    time.sleep(0.2)
    http, size = curl(f"https://{TARGET_HOST}/gallery/{gid}", port)
    return classify(http, size), http, size

def rotate_port(port):
    home_dir = f"/tmp/proxy_port_{port}"
    try:
        r = subprocess.run(f'HOME="{home_dir}" adguardvpn-cli connect -y',
                          shell=True, capture_output=True, text=True, timeout=20)
        return "Successfully Connected" in (r.stdout + r.stderr)
    except: return False

# ── Scanner thread ──────────────────────────────────────

def run_scanner(name, ports, targets, start_idx, results):
    """Run scanner on given port pool and target range."""
    log_path = Path(f"scan_{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")
    log_lock = threading.Lock()
    start_time = datetime.now()
    
    def log(etype, **data):
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "type": etype, "scanner": name, **data}
        with log_lock:
            with open(log_path, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    
    print(f"[{name}] Starting with {len(ports)} ports, {len(targets)} targets")
    print(f"[{name}] Range: {targets[0]} -> {targets[-1]}")
    print(f"[{name}] Log: {log_path}")
    
    # Step 1: Filter alive ports
    alive = []
    for p in ports:
        cls, http, size = check_ref(p)
        ok = cls in ("EXISTS", "EXISTS_BANNED")
        if ok or cls == "NEVER":
            alive.append(p)
    print(f"[{name}] Alive ports: {len(alive)}/{len(ports)}")
    
    if not alive:
        print(f"[{name}] No ports! Aborting.")
        return
    
    log("scan_start", ports=len(alive), targets=len(targets))
    
    # Step 2: Scan
    checked = 0
    found_set = set()
    port_probes = {}  # port -> probe count for analytics
    port_info = {}    # port -> {location, ip}
    port_idx = 0
    total_rotated = 0
    ti = 0
    scan_start = time.time()
    
    while ti < len(targets):
        # Find alive port
        port = None
        attempts = 0
        while port is None:
            p = alive[port_idx]
            port_idx = (port_idx + 1) % len(alive)
            cls, http, size = check_ref(p)
            if cls in ("EXISTS", "EXISTS_BANNED"):
                port = p
                break
            attempts += 1
            if attempts % 5 == 0:
                log("port_blind_skip", port=p, cls=cls)
            if attempts >= len(alive):
                log("all_blind", target_idx=ti, attempts=attempts)
                print(f"[{name}] ! All ports blind at {ti}. Sleep 30s...")
                time.sleep(30)
                attempts = 0
        
        # 2 batches x 3 probes = 6 per port
        port_blind = False
        for batch in range(2):
            if port_blind: break
            chain = "A" if batch == 0 else "B"
            for _ in range(3):
                if ti >= len(targets): break
                gid = targets[ti]
                ti += 1
                
                # Ref check BEFORE probe
                probe_t0 = time.time()
                ref_cls, ref_http, ref_size = check_ref(port)
                ref_before_ms = int((time.time() - probe_t0) * 1000)
                ref_before_ok = ref_cls in ("EXISTS", "EXISTS_BANNED")
                if not ref_before_ok:
                    log("port_blind_before", port=port, target_idx=ti, chain=chain,
                        ref_ms=ref_before_ms, ref_cls=ref_cls, ref_http=ref_http)
                    port_blind = True
                    break
                
                # Probe
                probe_t0 = time.time()
                cls, http, size = probe(gid, port)
                probe_ms = int((time.time() - probe_t0) * 1000)
                checked += 1
                port_probes[port] = port_probes.get(port, 0) + 1
                log("probe", gid=gid, port=port, http=http, size=size, cls=cls, chain=chain,
                    duration_ms=probe_ms, ref_before_ok=True, ref_before_ms=ref_before_ms,
                    port_probe_num=port_probes[port])
                
                if cls in ("EXISTS", "EXISTS_BANNED"):
                    found_set.add(gid)
                    log("found", gid=gid, port=port, cls=cls, chain=chain,
                        http=http, size=size, duration_ms=probe_ms)
                    print(f"[{name}] FOUND: {gid} on :{port} ({cls}, {size}B, {probe_ms}ms)")
                
                # Ref check AFTER probe
                probe_t0 = time.time()
                ref_cls, ref_http, ref_size = check_ref(port)
                ref_after_ms = int((time.time() - probe_t0) * 1000)
                ref_after_ok = ref_cls in ("EXISTS", "EXISTS_BANNED")
                if not ref_after_ok:
                    log("port_blind_after", port=port, target_idx=ti, chain=chain,
                        ref_ms=ref_after_ms, ref_cls=ref_cls, ref_http=ref_http,
                        probe_ms=probe_ms, port_probe_num=port_probes.get(port, 0))
                    port_blind = True
                    break
        
        if port_blind:
            probe_t0 = time.time()
            ok = rotate_port(port)
            rot_ms = int((time.time() - probe_t0) * 1000)
            if ok:
                total_rotated += 1
                log("port_rotated", port=port, total=total_rotated, duration_ms=rot_ms)
                time.sleep(2)
            else:
                log("port_rotate_failed", port=port, duration_ms=rot_ms)
        
        # Progress
        if ti % 50 == 0 or (ti >= len(targets)):
            elapsed = time.time() - scan_start
            rps = checked / elapsed if elapsed > 0 else 0
            msg = f"[{name}] [{ti}/{len(targets)}] checked={checked} found={len(found_set)} rps={rps:.1f} rotated={total_rotated}"
            print(msg)
            sys.stdout.flush()
            log("progress", targets_done=ti, checked=checked,
                found=len(found_set), rps=round(rps,1), rotated=total_rotated,
                active_ports=len([p for p in alive if port_probes.get(p, 0) > 0]),
                top_ports=sorted([(p, c) for p, c in port_probes.items() if c > 0],
                                 key=lambda x: -x[1])[:5])
    
    elapsed = time.time() - scan_start
    print(f"\n[{name}] DONE — {elapsed:.0f}s checked={checked} found={len(found_set)} rotated={total_rotated}")
    log("scan_complete", checked=checked, found=len(found_set),
        elapsed_s=round(elapsed,1), rotated=total_rotated,
        port_probes={str(p): c for p, c in sorted(port_probes.items())},
        total_ports_used=len([p for p in alive if port_probes.get(p, 0) > 0]))
    
    results[name] = {"checked": checked, "found": found_set, "rotated": total_rotated, "log": str(log_path)}


# ── Main ────────────────────────────────────────────────

def main():
    print(f"Dual Scanner — seed: {SEED}")
    start = datetime.now()
    
    # Generate all targets
    all_targets = generate_targets(SEED, NUM_TARGETS)
    print(f"Total targets: {len(all_targets)}")
    
    # Split ports
    ports = list(range(1080, 1140))
    ports_a = ports[:30]  # 1080-1109
    ports_b = ports[30:]  # 1110-1139
    print(f"Ports A: {ports_a[0]}-{ports_a[-1]} ({len(ports_a)})")
    print(f"Ports B: {ports_b[0]}-{ports_b[-1]} ({len(ports_b)})")
    
    # Split targets
    mid = len(all_targets) // 2
    targets_a = all_targets[:mid]
    targets_b = all_targets[mid:]
    print(f"Targets A: {targets_a[0]} -> {targets_a[-1]} ({len(targets_a)})")
    print(f"Targets B: {targets_b[0]} -> {targets_b[-1]} ({len(targets_b)})")
    
    results = {}
    
    # Launch both scanners in parallel threads
    t_a = threading.Thread(target=run_scanner, args=("A", ports_a, targets_a, 0, results))
    t_b = threading.Thread(target=run_scanner, args=("B", ports_b, targets_b, len(targets_a), results))
    
    t_a.start()
    t_b.start()
    
    t_a.join()
    t_b.join()
    
    elapsed = (datetime.now() - start).total_seconds()
    total_checked = sum(r["checked"] for r in results.values())
    total_found = set()
    for r in results.values():
        total_found.update(r["found"])
    
    print(f"\n{'='*50}")
    print(f"DUAL SCAN COMPLETE — {elapsed:.0f}s")
    print(f"Scanner A: checked={results.get('A',{}).get('checked',0)} found={len(results.get('A',{}).get('found',set()))}")
    print(f"Scanner B: checked={results.get('B',{}).get('checked',0)} found={len(results.get('B',{}).get('found',set()))}")
    print(f"Total: checked={total_checked} found={len(total_found)}")
    if total_found:
        print(f"Galleries: {sorted(total_found)}")

if __name__ == "__main__":
    main()
