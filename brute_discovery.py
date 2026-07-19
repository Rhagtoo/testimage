#!/usr/bin/env python3
"""Brute-force вокруг созданных галерей — Workers + AdGuard ротация."""
import json, time, re, random, subprocess, httpx, logging, string
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [b] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("b")

SOCKS = "socks5://127.0.0.1:1080"
WORKERS = json.loads(Path("/mnt/c/Users/Rhagtoo/POSTIMG/worker_ref.json").read_text())["workers"]
REFS = Path(__file__).parent / "steady_scan_results.jsonl"
IMG_RE = re.compile(r'i\.postimg\.cc/[^"\s<>]+')
BASE62 = string.digits + string.ascii_letters
LOCS = ["Riga","Tallinn","Stockholm","Amsterdam","Copenhagen","Brussels",
        "Oslo","Milan","Prague","Helsinki","Frankfurt","Marseille",
        "Warsaw","London","Berlin","Madrid","Dublin","Zurich",
        "Vienna","Paris","Rome","Bratislava","Bucharest","Istanbul"]

# Сиды
SEEDS = set(line.strip() for line in open(Path(__file__).parent / "created_galleries.txt") if line.strip())
PREFIXES = sorted(set(g[:5] for g in SEEDS))
SUFFIXES = [a+b for a in BASE62 for b in BASE62]

log.info(f"Seeds: {len(SEEDS)}, prefixes: {len(PREFIXES)}")

def connect(loc):
    subprocess.run(["adguardvpn-cli","connect","-l",loc], capture_output=True, timeout=15)

def check_ref():
    for w in random.sample(WORKERS, min(5, len(WORKERS))):
        try:
            r = httpx.get(f"{w['url']}/gallery/y3tXqH0",
                          headers={"X-Key": w["secret"]}, proxy=SOCKS, timeout=10)
            if r.status_code == 200: return True
        except: pass
    return False

def probe(w, gid):
    try:
        r = httpx.get(f"{w['url']}/gallery/{gid}",
                       headers={"X-Key": w["secret"]}, proxy=SOCKS, timeout=10)
        if r.status_code == 404: return "miss", 0
        if r.status_code != 200: return "err", 0
        imgs = IMG_RE.findall(r.text)
        return ("hit", len(set(imgs))) if imgs else ("empty", 0)
    except: return "err", 0

# Стартовая локация
loc = None
for loc in LOCS:
    connect(loc); time.sleep(2)
    if check_ref():
        log.info(f"✅ {loc}")
        break
    log.info(f"  {loc} blind")
else:
    log.error("All locations blind!"); exit(1)

probes = hits = wi = blinds = 0
start = time.monotonic()

for prefix in PREFIXES:
    log.info(f"Prefix {prefix}** (3844 candidates)")
    for suffix in SUFFIXES:
        gid = prefix + suffix
        if gid in SEEDS: continue

        # Ref check + rotation every 30 probes
        if probes > 0 and probes % 30 == 0 and not check_ref():
            blinds += 1
            old = loc
            for loc in LOCS:
                if loc == old: continue
                log.warning(f"😵 BLIND #{blinds} ({old} → {loc})")
                connect(loc); time.sleep(4)
                if check_ref(): break
            else:
                log.error("All blind!"); break

        w = WORKERS[wi % len(WORKERS)]; wi += 1
        st, cnt = probe(w, gid)
        probes += 1

        if st == "hit":
            hits += 1
            with open(REFS, "a") as f:
                f.write(json.dumps({"ts": time.time(), "gid": gid, "hit": True,
                                    "images": cnt, "loc": loc}) + "\n")
            log.info(f"✅ HIT {gid} ({cnt}) [{loc}]")

        if probes % 100 == 0:
            e = time.monotonic() - start
            log.info(f"[{probes}] {hits}h {probes/e:.1f}rps {blinds}b {loc}")

    log.info(f"Prefix {prefix} done")

e = time.monotonic() - start
log.info(f"DONE: {probes}p {hits}h {blinds}b in {e/60:.0f}m")
