#!/usr/bin/env python3
"""
Stage 1.2: Multi-IP oracle probe.
Probes every test-set ID through multiple AdGuard locations.
Records: HTTP code, size, CF-Ray, cookies.

Input: oracle_testset/{exists_verified,never_generated,foreign_verified}.jsonl
Output: oracle_testset/probe_results.jsonl
"""
import subprocess, json, sys, time
from datetime import datetime, timezone
from pathlib import Path

HOST = "postimg.cc"
REF = "y3tXqH0"
TEST_DIR = "/home/rhagtoo/testimage/oracle_testset"
OUTPUT = f"{TEST_DIR}/probe_results.jsonl"

INPUTS = [
    f"{TEST_DIR}/exists_verified.jsonl",
    f"{TEST_DIR}/never_generated.jsonl",
    f"{TEST_DIR}/foreign_verified.jsonl",
]

# Working AdGuard ports with locations
PORTS_LOCS = {
    1080: "Milan",        1081: "Frankfurt",   1082: "Stockholm",
    1083: "Amsterdam",    1084: "Paris",       1085: "London",
    1088: "Riga",         1089: "Prague",      1091: "Warsaw",
    1092: "Copenhagen",   1093: "Zurich",      1096: "Berlin",
    1097: "Luxembourg",   1098: "Brussels",    1099: "Rome",
    1100: "Bratislava",   1101: "Manchester",  1104: "Belgrade",
    1107: "Kyiv",         1109: "Bucharest",
}

def curl_full(url, port):
    """Returns (http_code, size, headers_dict)"""
    cmd = f'curl -s --max-time 10 -D - -o /dev/null --socks5 127.0.0.1:{port} "{url}"'
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=12)
        lines = r.stdout.split('\n')
        http_code = 0
        headers = {}
        for line in lines:
            if line.startswith('HTTP/'):
                http_code = int(line.split()[1]) if len(line.split()) > 1 else 0
            elif ':' in line and not line.startswith('{'):
                k, v = line.split(':', 1)
                headers[k.strip().lower()] = v.strip()
        # Get size from content-length
        size = int(headers.get('content-length', 0))
        return http_code, size, headers
    except:
        return 0, 0, {}

def classify(http, size):
    if http == 200: return "EXISTS"
    if http in (404, 403) and size > 28073: return "EXISTS_BANNED"
    if http in (404, 403) and size == 28073: return "NEVER"
    if http == 0: return "CONN_FAIL"
    return f"OTHER_{http}_{size}"

def check_ref(port):
    http, size, _ = curl_full(f"https://{HOST}/gallery/{REF}", port)
    return classify(http, size) in ("EXISTS", "EXISTS_BANNED"), classify(http, size)

def load_testset():
    """Load all test set IDs with labels"""
    ids = []
    for path in INPUTS:
        if not Path(path).exists():
            print(f"  WARN: {path} not found, skipping")
            continue
        with open(path) as f:
            for line in f:
                entry = json.loads(line.strip())
                ids.append(entry)
    return ids

def main():
    print("=== Stage 1.2: Multi-IP Oracle Probe ===\n")
    
    # Load test set
    test_ids = load_testset()
    print(f"Test set: {len(test_ids)} IDs")
    n_exists = sum(1 for t in test_ids if t["label"] == "KNOWN_EXISTS")
    n_never = sum(1 for t in test_ids if t["label"] == "KNOWN_NEVER")
    n_foreign = sum(1 for t in test_ids if "FOREIGN" in t["label"])
    print(f"  KNOWN_EXISTS: {n_exists}")
    print(f"  KNOWN_NEVER: {n_never}")
    print(f"  FOREIGN: {n_foreign}")
    
    # Limit NEVER to avoid excessive runtime (keep all exists + foreign + 200 never)
    never_ids = [t for t in test_ids if t["label"] == "KNOWN_NEVER"]
    other_ids = [t for t in test_ids if t["label"] != "KNOWN_NEVER"]
    if len(never_ids) > 200:
        import random
        random.seed(42)
        never_ids = random.sample(never_ids, 200)
        print(f"  (sampled {len(never_ids)} NEVER for probe efficiency)")
    
    probe_ids = other_ids + never_ids
    print(f"  Will probe: {len(probe_ids)} IDs × {len(PORTS_LOCS)} locations = {len(probe_ids) * len(PORTS_LOCS)} total\n")
    
    # Probe
    results = []
    n = 0
    
    for port, loc in sorted(PORTS_LOCS.items()):
        # Verify port is alive
        alive, cls = check_ref(port)
        if not alive:
            print(f"  :{port} ({loc}) → ref={cls} SKIP")
            continue
        
        print(f"── {loc} (:{port}) — ref={cls} ──", flush=True)
        
        for entry in probe_ids:
            gid = entry["gid"]
            label = entry["label"]
            
            http, size, headers = curl_full(f"https://{HOST}/gallery/{gid}", port)
            cls = classify(http, size)
            time.sleep(0.3)  # rate limit
            
            result = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "gid": gid,
                "label": label,
                "location": loc,
                "port": port,
                "http": http,
                "size": size,
                "cls": cls,
                "cf_ray": headers.get("cf-ray", ""),
                "cf_cache_status": headers.get("cf-cache-status", ""),
                "server": headers.get("server", ""),
                "set_cookie": headers.get("set-cookie", "")[:120],
            }
            results.append(result)
            n += 1
            
            status = "✓" if cls in ("EXISTS", "EXISTS_BANNED") else " "
            if n % 100 == 0:
                print(f"  [{n}/{len(probe_ids)*len(PORTS_LOCS)}] {gid} → {http}/{size}B {cls} {status}", flush=True)
        
        # Ref check after location
        _, post_cls = check_ref(port)
        print(f"  Done {loc} — ref check: {post_cls}\n")
    
    # Write output
    Path(OUTPUT).parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    
    print(f"\nDone: {len(results)} probes across {len(PORTS_LOCS)} locations")
    print(f"Output: {OUTPUT}")

if __name__ == "__main__":
    main()
