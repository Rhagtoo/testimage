#!/usr/bin/env python3
"""
Stage 4: Check arithmetic patterns around bv68s9M (found at seed+18 from bv68s94).
Tests: +36, +54, +72, +18*N, Gray-code neighbors, bit-flip neighbors.
Uses AdGuard pool for probes.

Output: oracle_testset/pattern_check.jsonl
"""
import subprocess, json, sys, time
from datetime import datetime, timezone
from pathlib import Path

CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
CV = {c: i for i, c in enumerate(CHARS)}
SEED = "bv68s94"
FIND = "bv68s9M"
HOST = "postimg.cc"
OUTPUT = "/home/rhagtoo/testimage/oracle_testset/pattern_check.jsonl"

# Working AdGuard ports
PORTS = [1080,1081,1082,1083,1084,1085,1088,1089,1091,1092,1093,1096,1097,1098,1099,1100,1101,1104,1107,1109]

def b2i(s):
    v = 0
    for c in s: v = v * 62 + CV[c]
    return v

def i2b(v):
    if v == 0: return '0'
    s = []
    while v > 0: s.append(CHARS[v % 62]); v //= 62
    return ''.join(reversed(s))

def curl(gid, port):
    cmd = f'curl -s --max-time 10 -o /dev/null -w "%{{http_code}} %{{size_download}}" --socks5 127.0.0.1:{port} "https://{HOST}/gallery/{gid}"'
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=12)
        parts = r.stdout.strip().split()
        if len(parts) >= 2: return int(parts[0]), int(parts[1])
    except: pass
    return 0, 0

def classify(http, size):
    if http == 200: return "EXISTS"
    if http in (404,403) and size > 28073: return "EXISTS_BANNED"
    if http in (404,403) and size == 28073: return "NEVER"
    if http == 0: return "CONN_FAIL"
    return f"HTTP{http}_{size}"

def probe(gid, port, description):
    time.sleep(0.3)
    http, size = curl(gid, port)
    cls = classify(http, size)
    result = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "gid": gid, "http": http, "size": size, "cls": cls,
        "port": port, "description": description
    }
    print(f"  {description:30s} {gid:8s} :{port} → {http}/{size}B {cls}")
    return result

def main():
    seed_int = b2i(SEED)
    find_int = b2i(FIND)
    delta = find_int - seed_int
    print(f"Seed: {SEED} (int={seed_int})")
    print(f"Find: {FIND} (int={find_int})")
    print(f"Delta: +{delta}")
    print()

    results = []
    port_idx = 0

    def next_port():
        nonlocal port_idx
        p = PORTS[port_idx % len(PORTS)]
        port_idx += 1
        return p

    # 1. Arithmetic progression: seed + 18*N for N=2..10
    print("── Arithmetic progression (+18*N) ──")
    for n in range(2, 11):
        gid = i2b(seed_int + delta * n)
        if len(gid) == 7:
            results.append(probe(gid, next_port(), f"seed+{delta*n} (N={n})"))
    print()

    # 2. Check the find itself through multiple ports
    print("── Ref check: bv68s9M itself ──")
    for port in PORTS[:3]:
        results.append(probe(FIND, port, "bv68s9M ref"))
    print()

    # 3. Gray-code neighbors
    print("── Gray code neighbors ──")
    def to_gray(n):
        return n ^ (n >> 1)
    def from_gray(g):
        n = g
        while g := g >> 1:
            n ^= g
        return n

    find_gray = to_gray(find_int)
    for i in range(1, 6):
        neighbor_gray = find_gray + i
        gid = i2b(from_gray(neighbor_gray))
        if len(gid) == 7:
            results.append(probe(gid, next_port(), f"gray+{i}"))
        neighbor_gray = find_gray - i
        if neighbor_gray >= 0:
            gid = i2b(from_gray(neighbor_gray))
            if len(gid) == 7:
                results.append(probe(gid, next_port(), f"gray-{i}"))
    print()

    # 4. Bit-flip neighbors (42 bits)
    print("── Single bit-flip neighbors ──")
    for bit in range(42):
        flipped = find_int ^ (1 << bit)
        gid = i2b(flipped)
        if len(gid) == 7 and gid != FIND:
            http, size = curl(gid, next_port())
            cls = classify(http, size)
            time.sleep(0.15)
            result = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "gid": gid, "http": http, "size": size, "cls": cls,
                "port": PORTS[(port_idx-1) % len(PORTS)],
                "description": f"bit-flip-{bit}"
            }
            results.append(result)
            if cls != "NEVER":
                print(f"  bit-flip-{bit:2d} {gid:8s} → {http}/{size}B {cls} ← !!!")
    print()

    # Write output
    Path(OUTPUT).parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    n_found = sum(1 for r in results if r["cls"] in ("EXISTS", "EXISTS_BANNED"))
    print(f"\nDone: {len(results)} probes, {n_found} interesting")
    print(f"Output: {OUTPUT}")

if __name__ == "__main__":
    main()
