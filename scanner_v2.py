#!/usr/bin/env python3
"""
Scanner v2 — multi-port AdGuard pool, proper oracle, detailed logging.

Pool: 8 AdGuard SOCKS5 ports → 8 independent CF colos.
Oracle: size_download > 28073 → EXISTS (works in soft-ban).
Ref check: every BATCH=5 probes per port; blind → instant port switch.
Logging: JSONL with timestamps, colo, egress IP, probe results.
"""
import subprocess, time, sys, json, os, random, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

# ── Config ──────────────────────────────────────────────
PORTS = [1081,1082,1083,1084,1085,1087,1088,1089,1090,1091,1094,1096,1097,1098,1099,1100,1101,1102,1103,1104,1105,1106,1107,1111,1113,1114,1115,1116,1117,1118,1119,1120]  # 32 fresh
HOST = "testimage.cc"
REF = "y3tXqH0"
ANCHORS_FILE = "/home/rhagtoo/testimage/anchor_ids.txt"
BATCH = 5           # probes per port before ref check
RADIUS_START = 10   # initial radius around each anchor
RADIUS_MAX = 50     # max radius
RADIUS_STEP = 10    # expand by this each pass
PROBE_DELAY = 0.1   # seconds between probes on same port
CONCURRENCY = 25    # max concurrent workers

# Working locations (all non-EU that succeeded)
LOCATIONS = [
    "tokyo", "seoul", "singapore", "sydney", "mumbai", "toronto", "chicago",
    "miami", "vancouver", "bangkok", "taipei", "jakarta", "manila", "dubai",
    "johannesburg", "bogota", "lima", "santiago", "atlanta", "denver", "seattle",
    "phoenix", "cairo", "istanbul", "moscow", "kathmandu", "hanoi", "shanghai",
    "astana", "auckland", "riga", "tallinn",
]

# base62
CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
CV = {c: i for i, c in enumerate(CHARS)}

# ── Logging ─────────────────────────────────────────────
LOG_PATH = Path(f"/home/rhagtoo/testimage/scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl")
log_lock = threading.Lock()

def log_event(event_type: str, **data):
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        **data,
    }
    with log_lock:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

# ── Helpers ─────────────────────────────────────────────
def b2i(s: str) -> int:
    v = 0
    for c in s:
        v = v * 62 + CV[c]
    return v

def i2b(v: int) -> str:
    if v == 0:
        return "0"
    s = []
    while v > 0:
        s.append(CHARS[v % 62])
        v //= 62
    return "".join(reversed(s))

def curl(url: str, port: int, timeout: int = 8) -> tuple[int, int]:
    """Returns (http_code, size_download) or (0, 0) on failure."""
    cmd = (
        f'curl -s --max-time {timeout} -o /dev/null '
        f'-w "%{{http_code}} %{{size_download}}" '
        f'--socks5 127.0.0.1:{port} "{url}"'
    )
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout + 2)
        parts = r.stdout.strip().split()
        if len(parts) >= 2:
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return 0, 0

def classify(http: int, size: int) -> str:
    """Oracle: classify probe result."""
    if http == 200:
        return "EXISTS"
    if http in (404, 403) and size > 28073:
        return "EXISTS_BANNED"
    if http in (404, 403) and size == 28073:
        return "NEVER"
    if http == 0:
        return "CONN_FAIL"
    return f"UNKNOWN({http},{size})"

# ── AdGuard Port Pool ───────────────────────────────────
class PortPool:
    """Manages 8 AdGuard SOCKS5 ports with health tracking and rotation."""

    def __init__(self, ports: list[int]):
        self.ports: dict[int, dict] = {}
        self.lock = threading.Lock()
        self.loc_idx = 0

        for port in ports:
            self.ports[port] = {
                "status": "fresh",       # fresh | blind | reconnecting | dead
                "fail_streak": 0,
                "location": "unknown",
                "colo": "unknown",
                "banned_at": None,
                "total_probes": 0,
                "total_miss": 0,
            }

    def probe(self, gid: str, port: int) -> tuple[str, int, int]:
        """Probe a gallery ID through given port. Returns (class, http, size)."""
        url = f"https://{HOST}/gallery/{gid}"
        time.sleep(PROBE_DELAY)  # pace requests
        http, size = curl(url, port)
        cls = classify(http, size)
        with self.lock:
            self.ports[port]["total_probes"] += 1
            if cls in ("NEVER", "CONN_FAIL"):
                self.ports[port]["total_miss"] += 1
        return cls, http, size

    def check_ref(self, port: int) -> tuple[bool, str, int, int]:
        """Check reference gallery. Returns (ok, cls, http, size)."""
        cls, http, size = self.probe(REF, port)
        ok = cls in ("EXISTS", "EXISTS_BANNED")
        return ok, cls, http, size

    def rotate_port(self, port: int, reason: str = "blind"):
        """Reconnect a port to a new AdGuard location in background."""
        with self.lock:
            if self.ports[port]["status"] == "reconnecting":
                return  # already rotating
            self.ports[port]["status"] = "reconnecting"
            self.ports[port]["banned_at"] = datetime.now().isoformat()

        def _rotate():

            # Pick a new location
            with self.lock:
                loc = LOCATIONS[self.loc_idx % len(LOCATIONS)]
                self.loc_idx += 1

            log_event("port_rotate_start", port=port, location=loc, reason=reason)

            # Reconnect
            data_dir = f"/tmp/adg{port - 1080}"
            try:
                subprocess.run(
                    ["adguardvpn-cli", "disconnect"],
                    env={**os.environ, "XDG_DATA_HOME": data_dir},
                    capture_output=True, timeout=10,
                )
                time.sleep(1)
                subprocess.run(
                    ["adguardvpn-cli", "connect", "-l", loc],
                    env={**os.environ, "XDG_DATA_HOME": data_dir},
                    capture_output=True, timeout=20,
                )
                time.sleep(3)  # let tunnel stabilize

                # Verify
                ok, cls, http, size = self.check_ref(port)
                with self.lock:
                    if ok:
                        self.ports[port]["status"] = "fresh"
                        self.ports[port]["fail_streak"] = 0
                        self.ports[port]["total_probes"] = 0
                        self.ports[port]["location"] = loc
                    else:
                        self.ports[port]["status"] = "blind"
                log_event("port_rotate_done", port=port, location=loc,
                          ref_ok=ok, ref_cls=cls, ref_http=http, ref_size=size)
            except Exception as e:
                with self.lock:
                    self.ports[port]["status"] = "dead"
                log_event("port_rotate_error", port=port, location=loc, error=str(e))

        t = threading.Thread(target=_rotate, daemon=True)
        t.start()

    def get_fresh(self) -> int | None:
        """Get a fresh (non-blind) port, or None if all are blind/reconnecting."""
        with self.lock:
            fresh = [p for p, s in self.ports.items() if s["status"] == "fresh"]
            if fresh:
                return fresh[0]
        return None

    def detect_colo(self, port: int) -> tuple[str, str]:
        """Detect CF colo and egress IPv4 for a port. Returns (colo, ipv4)."""
        try:
            result = subprocess.run(
                f'curl -x socks5h://127.0.0.1:{port} -s --max-time 8 '
                f'https://testimage-diag.rhagtoo.workers.dev/ip',
                shell=True, capture_output=True, text=True, timeout=10
            )
            data = json.loads(result.stdout)
            colo = data.get("cfEntry", {}).get("colo", "?")
            ips = data.get("egressIp", "").split(", ")
            ipv4 = [ip for ip in ips if ":" not in ip]
            return colo, ipv4[0] if ipv4 else "?"
        except Exception:
            return "?", "?"

    def mark_blind(self, port: int):
        """Mark port as blind after consecutive ref failures."""
        with self.lock:
            self.ports[port]["fail_streak"] += 1
            if self.ports[port]["fail_streak"] >= 3:
                self.ports[port]["status"] = "blind"
                log_event("port_blind", port=port,
                          fail_streak=self.ports[port]["fail_streak"],
                          total_probes=self.ports[port]["total_probes"])

    def mark_fresh(self, port: int):
        """Reset port fail streak (ref passed)."""
        with self.lock:
            if self.ports[port]["fail_streak"] > 0:
                self.ports[port]["fail_streak"] = 0
                if self.ports[port]["status"] == "blind":
                    self.ports[port]["status"] = "fresh"
                    log_event("port_recovered", port=port)

    def stats(self) -> dict:
        with self.lock:
            return {
                str(p): dict(s) for p, s in self.ports.items()
            }

# ── Scanner ─────────────────────────────────────────────
def load_anchors(path: str) -> list[str]:
    anchors = []
    with open(path) as f:
        for line in f:
            gid = line.strip()
            if len(gid) == 7 and all(c in CV for c in gid):
                anchors.append(gid)
    return anchors

def generate_neighbors(anchor_int: int, radius: int, radius_step: int,
                       current_radius: int, anchor_set: set, found_set: set) -> list[str]:
    """Generate neighbor IDs for the current radius band."""
    prev = current_radius - radius_step
    neighbors = []
    for offset in range(-radius, radius + 1):
        if offset == 0:
            continue
        if abs(offset) <= prev:
            continue  # already scanned in previous band
        gid = i2b(anchor_int + offset)
        if gid not in anchor_set and gid not in found_set:
            neighbors.append(gid)
    return neighbors

def scan_anchor(anchor: str, anchor_int: int, anchor_set: set,
                pool: PortPool, found_set: set, checked: list, lock: threading.Lock):
    """Scan all radius bands around one anchor using available ports."""
    for radius in range(RADIUS_START, RADIUS_MAX + 1, RADIUS_STEP):
        neighbors = generate_neighbors(anchor_int, radius, RADIUS_STEP,
                                       radius, anchor_set, found_set)
        if not neighbors:
            continue

        band_found = []
        # Process in batches
        for i in range(0, len(neighbors), BATCH):
            batch = neighbors[i:i + BATCH]

            # Get a fresh port with backoff wait
            port = None
            for attempt in range(30):
                port = pool.get_fresh()
                if port is not None:
                    break
                if attempt == 0:
                    log_event("waiting_port", anchor=anchor, radius=radius)
                time.sleep(min(2 + attempt * 0.5, 10))
            
            if port is None:
                log_event("anchor_skipped", anchor=anchor, radius=radius)
                return

            # Probe batch
            for gid in batch:
                cls, http, size = pool.probe(gid, port)
                with lock:
                    checked[0] += 1

                if cls in ("EXISTS", "EXISTS_BANNED"):
                    band_found.append(gid)
                    found_set.add(gid)
                    log_event("found", gid=gid, near=anchor, radius=radius,
                              port=port, cls=cls, colo=pool.ports[port].get("colo", "?"))

            # Ref check after batch
            ok, ref_cls, ref_http, ref_size = pool.check_ref(port)
            if not ok:
                pool.mark_blind(port)
                pool.rotate_port(port, reason=f"ref_{ref_cls}")
            else:
                pool.mark_fresh(port)

        if band_found:
            # Found something in this band → don't expand further for this anchor
            break

def main():
    start = datetime.now()
    print(f"Scanner v2 — {start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Log: {LOG_PATH}")
    print(f"Ports: {PORTS}")
    print(f"Oracle: size_download > 28073 → EXISTS")
    print("=" * 50)

    # Load anchors
    anchors = load_anchors(ANCHORS_FILE)
    random.shuffle(anchors)
    anchor_set = set(anchors)
    print(f"Anchors: {len(anchors)}")

    # Init pool
    pool = PortPool(PORTS)

    # Verify ALL ports are fresh on startup
    print("\nInitializing ports...")
    fresh = []
    for port in PORTS:
        ok, cls, http, size = pool.check_ref(port)
        pool.ports[port]["location"] = f"port{port}"
        if ok:
            fresh.append(port)
            pool.mark_fresh(port)
            print(f"  :{port} → {cls} ✓")
        else:
            print(f"  :{port} → {cls} ({http},{size}B) ✗ — rotating")
            pool.rotate_port(port, reason="startup_fail")
    
    # Wait for rotations
    print(f"\n  Fresh: {len(fresh)}/{len(PORTS)}. Waiting for rotations...")
    time.sleep(15)
    
    print("\nAfter rotation:")
    for port in PORTS:
        ok, cls, http, size = pool.check_ref(port)
        pool.mark_fresh(port) if ok else pool.mark_blind(port)
        icon = "✓" if ok else "✗"
        print(f"  :{port} → {cls} (HTTP {http}, {size}B) {icon}")

    log_event("scan_start", anchors=len(anchors), ports=PORTS,
              radius_start=RADIUS_START, radius_max=RADIUS_MAX)

    # Scan
    found_set = set()
    checked = [0]  # mutable counter for thread-safe increment
    lock = threading.Lock()
    anchors_done = 0

    print(f"\n{'=' * 50}")
    print("SCANNING...")
    print(f"{'=' * 50}")

    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        futures = {}
        # Submit initial batch
        for anchor in anchors[:len(PORTS)]:
            if anchor in anchor_set:
                anchor_int = b2i(anchor)
                f = executor.submit(scan_anchor, anchor, anchor_int, anchor_set,
                                    pool, found_set, checked, lock)
                futures[f] = anchor

        remaining = anchors[len(PORTS):]
        while futures:
            done = None
            for f in as_completed(futures):
                done = f
                break

            if done:
                del futures[done]
                anchors_done += 1

                # Progress
                if anchors_done % 50 == 0:
                    elapsed = (datetime.now() - start).total_seconds()
                    rps = checked[0] / elapsed if elapsed > 0 else 0
                    stats = pool.stats()
                    fresh_ports = sum(1 for s in stats.values() if s["status"] == "fresh")
                    sys.stdout.write(
                        f"\r  anchors={anchors_done}/{len(anchors)} "
                        f"checked={checked[0]} found={len(found_set)} "
                        f"rps={rps:.1f} ports_fresh={fresh_ports}/{len(PORTS)}  "
                    )
                    sys.stdout.flush()
                    log_event("progress", anchors_done=anchors_done,
                              total_anchors=len(anchors), checked=checked[0],
                              found=len(found_set), rps=round(rps, 1),
                              pool_stats=stats)

                # Submit next anchor
                if remaining:
                    anchor = remaining.pop(0)
                    anchor_int = b2i(anchor)
                    f = executor.submit(scan_anchor, anchor, anchor_int, anchor_set,
                                        pool, found_set, checked, lock)
                    futures[f] = anchor

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\n\n{'=' * 50}")
    print(f"SCAN COMPLETE — {elapsed:.0f}s ({elapsed/3600:.1f}h)")
    print(f"Anchors: {anchors_done}  Checked: {checked[0]}  New: {len(found_set)}")
    print(f"Rate: {checked[0]/elapsed:.1f} req/s")
    print(f"Log: {LOG_PATH}")
    print(f"{'=' * 50}")

    log_event("scan_complete", anchors_done=anchors_done, checked=checked[0],
              found=len(found_set), elapsed_s=round(elapsed, 1),
              rps=round(checked[0] / elapsed, 1))

    # Save results
    if found_set:
        results_path = Path(f"/home/rhagtoo/testimage/found_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(results_path, "w") as f:
            json.dump({
                "timestamp": start.isoformat(),
                "elapsed_s": elapsed,
                "checked": checked[0],
                "found": sorted(found_set),
            }, f, indent=2, ensure_ascii=False)
        print(f"Results: {results_path}")

        for gid in sorted(found_set):
            print(f"  {gid}")

if __name__ == "__main__":
    main()
