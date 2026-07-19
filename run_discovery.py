#!/usr/bin/env python3
"""Discovery Engine — через AdGuard SOCKS5 → CF Workers (HTML, X-Key)."""
import sys, os, json, time, re, httpx, logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from discovery_engine import create_engine_for_scanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [discovery] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("run_discovery")

DB_PATH = Path(__file__).parent / "discovery_state.db"
WORKER_REF = Path("/mnt/c/Users/Rhagtoo/POSTIMG/worker_ref.json")
REFS_PATH = Path(__file__).parent / "steady_scan_results.jsonl"
SOCKS_PROXY = "socks5://127.0.0.1:1080"

IMG_RE = re.compile(r'i\.postimg\.cc/[^"\s<>]+')

def load_workers():
    data = json.loads(WORKER_REF.read_text())
    return data.get("workers", data)

def check_ref(workers):
    """REF через Workers + SOCKS5."""
    for w in workers[:10]:
        try:
            r = httpx.get(f"{w['url']}/gallery/y3tXqH0",
                          headers={"X-Key": w.get("secret", "")},
                          proxy=SOCKS_PROXY, timeout=10)
            if r.status_code == 200:
                return True, w
        except:
            continue
    return False, None

def probe_gallery(worker, gid):
    """Проверка галереи через CF Worker (HTML)."""
    try:
        r = httpx.get(f"{worker['url']}/gallery/{gid}",
                       headers={"X-Key": worker.get("secret", "")},
                       proxy=SOCKS_PROXY, timeout=10,
                       follow_redirects=True)
        if r.status_code == 404:
            return "miss", 0
        if r.status_code != 200:
            return "error", 0
        images = IMG_RE.findall(r.text)
        if images:
            return "hit", len(set(images))
        return "empty", 0
    except:
        return "error", 0

def main():
    workers = load_workers()
    log.info(f"Loaded {len(workers)} workers, SOCKS5={SOCKS_PROXY}")

    engine = create_engine_for_scanner(db_path=str(DB_PATH))
    engine.load()
    log.info(f"Loaded {engine.cluster_count} clusters")

    scores = engine.get_scores()
    active = sum(1 for s in scores if s.state == "ACTIVE")
    log.info(f"Active={active} Total={engine.cluster_count}")

    # Ждём
    attempt = 0
    while True:
        ok, w = check_ref(workers)
        if ok:
            log.info(f"✅ Worker {w['url'].split('//')[1].split('.')[0]} жив! Начинаем")
            break
        attempt += 1
        if attempt == 1:
            log.info("⏸ Workers слепые. Жду 60s...")
        elif attempt % 5 == 0:
            log.info(f"⏸ [{attempt}] всё ещё...")
        time.sleep(60)

    probes = 0
    hits = 0
    start = time.monotonic()
    max_probes = 20000
    report_interval = 100
    blind_wait = 120
    worker_idx = 0

    try:
        while probes < max_probes:
            if probes % 50 == 0:
                ok, w = check_ref(workers)
                engine.on_ref_status(ok)
                if not ok:
                    log.warning(f"⏸ Слепота на {probes}. Жду {blind_wait}s...")
                    time.sleep(blind_wait)
                    continue

            result = engine.next_candidate()
            if result is None:
                log.info("Нет кандидатов. Закончили.")
                break

            gid, src = result
            w = workers[worker_idx % len(workers)]
            worker_idx += 1

            status, count = probe_gallery(w, gid)
            probes += 1

            if status == "hit":
                engine.on_probe_hit(src, gid)
                engine.on_gallery_found(gid)
                hits += 1
                line = json.dumps({"ts": time.time(), "gid": gid, "prefix": gid[:5],
                                   "hit": True, "images": count,
                                   "worker": w["url"].split("//")[1].split(".")[0]})
                with open(REFS_PATH, "a") as f:
                    f.write(line + "\n")
                log.info(f"✅ HIT {gid} ({count} imgs)")
            elif status == "empty":
                engine.on_gallery_found(gid)
                engine.on_probe_miss(src, gid)
            else:
                engine.on_probe_miss(src, gid)

            if probes % report_interval == 0:
                elapsed = time.monotonic() - start
                rps = probes / elapsed
                scores = engine.get_scores()
                active = sum(1 for s in scores if s.state == "ACTIVE")
                log.info(f"[{probes}/{max_probes}] {hits}h, {rps:.1f}rps, "
                         f"act={active} cl={engine.cluster_count}")

    except KeyboardInterrupt:
        log.info("Interrupted")
    finally:
        engine.flush()
        elapsed = time.monotonic() - start
        log.info(f"Done: {probes} probes in {elapsed/60:.1f}m, {hits} hits")
        engine.write_dashboard(Path(__file__).parent / "discovery_dashboard.json")

if __name__ == "__main__":
    main()
