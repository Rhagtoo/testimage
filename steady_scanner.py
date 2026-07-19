#!/usr/bin/env python3
"""
steady_scanner.py — v3: SOCKS5 + CF Workers с worker health tracking.
REF через workers (не напрямую), перекрёстная проверка при фейле, изоляция слепых workers.
"""
import asyncio
import httpx
import json
import sqlite3
import time
import signal
import random
from pathlib import Path
from collections import defaultdict

# ═══════════════════════ Config ═══════════════════════
RPS = 2.0
INTERVAL = 1.0 / RPS
REF_GID = "y3tXqH0"
REF_INTERVAL = 6
MAX_MISS_STREAK = 16       # больше толерантность — workers могут слепнуть
COOLDOWN_BLIND = 120
MAX_REF_FAIL = 4           # больше попыток — cross-check нескольких workers
WORKER_BLIND_COOLDOWN = 300  # 5 минут изоляции слепого worker'а

DB_PATH = Path(__file__).parent / "discovery_state.db"
STATE_PATH = Path(__file__).parent / "steady_scan_state.json"
RESULTS_PATH = Path(__file__).parent / "steady_scan_results.jsonl"

SECRET = "ncG7Na…M5LX"
GUESTKEY = "3877d5…ab23"
WORKERS = [
    "https://postimg-ref.rhagtoo2.workers.dev",
    "https://postimg-ref1.rhagtoo2.workers.dev",
    "https://postimg-ref2.rhagtoo2.workers.dev",
    "https://postimg-ref3.rhagtoo2.workers.dev",
    "https://postimg-ref4.rhagtoo2.workers.dev",
    "https://postimg-ref5.rhagtoo2.workers.dev",
    "https://postimg-ref1.anadar.workers.dev",
    "https://postimg-ref2.anadar.workers.dev",
    "https://postimg-ref3.anadar.workers.dev",
]
SOCKS_PROXY = "socks5://127.0.0.1:1080"

CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

shutdown = False
stats = {"requests": 0, "hits": 0, "misses": 0, "refs": 0, "ref_fails": 0, "blinds": 0, "started": time.time()}

# Worker health tracking
worker_blind_until = {}  # worker_url -> timestamp when it recovers
worker_fail_streak = defaultdict(int)


def get_live_worker():
    """Выбирает случайного живого worker'а."""
    now = time.time()
    live = [w for w in WORKERS if worker_blind_until.get(w, 0) < now]
    if not live:
        # Все слепые — сбрасываем и пробуем любой
        worker_blind_until.clear()
        live = list(WORKERS)
    return random.choice(live)


def mark_worker_blind(worker):
    """Изолирует worker на WORKER_BLIND_COOLDOWN секунд."""
    worker_blind_until[worker] = time.time() + WORKER_BLIND_COOLDOWN
    worker_fail_streak[worker] = 0
    live = sum(1 for w in WORKERS if worker_blind_until.get(w, 0) < time.time())
    print(f"  🚫 {worker.split('.')[0].split('/')[-1]} blinded for {WORKER_BLIND_COOLDOWN}s ({live}/{len(WORKERS)} live)")


def mark_worker_ok(worker):
    worker_fail_streak[worker] = 0
    # Снимаем изоляцию досрочно
    worker_blind_until.pop(worker, None)


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"prefix": None, "last_ref_ok": True}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state))


def log_result(entry):
    with open(RESULTS_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def suffix_from_index(idx):
    return CHARSET[idx // 62] + CHARSET[idx % 62]


def load_clusters(db):
    rows = db.execute("""
        SELECT prefix5, discovered_json, posterior_json, histogram_json, 
               frontier_json, tight_char, miss_streak
        FROM clusters WHERE state='ACTIVE'
    """).fetchall()
    clusters = []
    for r in rows:
        frontier = json.loads(r[4]) if r[4] else []
        if not frontier:
            continue
        post = json.loads(r[2])
        tight = r[5]
        hits = post.get("hits", 0)
        misses = post.get("misses", 0)
        tight_boost = 1 if tight else 0
        clusters.append({
            "prefix": r[0], "frontier": frontier, "tight": tight,
            "known_count": len(json.loads(r[1])), "hits": hits, "misses": misses,
            "priority": (tight_boost, len(json.loads(r[1])), -misses, r[0]),
            "miss_streak": r[6] or 0,
        })
    clusters.sort(key=lambda c: c["priority"], reverse=True)
    return clusters


async def probe(client, gid):
    t0 = time.monotonic()
    worker = get_live_worker()
    try:
        r = await client.get(
            f"{worker}/json",
            params={"action": "list", "page": 1, "album": gid},
            headers={"X-Key": SECRET, "Cookie": f"GUESTKEY=***",
                     "User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        elapsed = time.monotonic() - t0
        status = r.status_code
        if status == 200:
            j = r.json()
            ok = not j.get("error")
            imgs = len(j.get("images", []))
            if ok:
                mark_worker_ok(worker)
            return {"ok": ok, "images": imgs, "status": status, "elapsed_ms": round(elapsed * 1000), "worker": worker}
        worker_fail_streak[worker] += 1
        if worker_fail_streak[worker] >= 3:
            mark_worker_blind(worker)
        return {"ok": False, "images": 0, "status": status, "elapsed_ms": round(elapsed * 1000), "worker": worker}
    except Exception as e:
        worker_fail_streak[worker] += 1
        if worker_fail_streak[worker] >= 2:
            mark_worker_blind(worker)
        return {"ok": False, "images": 0, "status": 0, "error": type(e).__name__,
                "elapsed_ms": round((time.monotonic() - t0) * 1000), "worker": worker}


async def check_ref(client):
    """REF через случайного worker'а. При фейле — cross-check других."""
    t0 = time.monotonic()
    worker = get_live_worker()
    try:
        r = await client.get(
            f"{worker}/json",
            params={"action": "list", "page": 1, "album": REF_GID},
            headers={"X-Key": SECRET, "Cookie": f"GUESTKEY=***",
                     "User-Agent": "Mozilla/5.0", "Accept": "application/json"},
        )
        elapsed = time.monotonic() - t0
        if r.status_code == 200:
            j = r.json()
            if not j.get("error") and len(j.get("images", [])) >= 2:
                mark_worker_ok(worker)
                return {"ok": True, "status": 200, "elapsed_ms": round(elapsed * 1000), "worker": worker}
        # Worker вернул не 200 или пустой ответ — cross-check
        worker_fail_streak[worker] += 1
        return await cross_check_ref(client, worker, elapsed)
    except Exception as e:
        worker_fail_streak[worker] += 1
        return await cross_check_ref(client, worker, round((time.monotonic() - t0) * 1000))


async def cross_check_ref(client, failed_worker, first_elapsed):
    """Перекрёстная проверка: пробуем ещё 2 workers с других аккаунтов."""
    candidates = [w for w in WORKERS if w != failed_worker and worker_blind_until.get(w, 0) < time.time()]
    random.shuffle(candidates)
    to_check = candidates[:3]  # проверяем до 3 других

    ok_found = False
    for w in to_check:
        try:
            r = await client.get(
                f"{w}/json",
                params={"action": "list", "page": 1, "album": REF_GID},
                headers={"X-Key": SECRET, "Cookie": f"GUESTKEY=***",
                         "User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            )
            if r.status_code == 200:
                j = r.json()
                if not j.get("error") and len(j.get("images", [])) >= 2:
                    # Нашли живой worker — первый был слепой
                    mark_worker_blind(failed_worker)
                    mark_worker_ok(w)
                    print(f"  ↳ cross-check: {failed_worker.split('.')[0].split('/')[-1]} blind, {w.split('.')[0].split('/')[-1]} ok")
                    return {"ok": True, "status": 200, "elapsed_ms": first_elapsed, "worker": w}
            worker_fail_streak[w] += 1
        except Exception:
            worker_fail_streak[w] += 1

    # Все проверенные workers мертвы
    for w in to_check:
        if worker_fail_streak[w] >= 2:
            mark_worker_blind(w)
    mark_worker_blind(failed_worker)

    live = sum(1 for w in WORKERS if worker_blind_until.get(w, 0) < time.time())
    if live == 0:
        return {"ok": False, "status": 0, "error": "ALL_WORKERS_BLIND", "elapsed_ms": first_elapsed}
    else:
        # Есть живые — просто этот конкретный worker ослеп, пробуем ещё
        return {"ok": True, "status": 200, "elapsed_ms": first_elapsed, "cross_checked": True}


def update_db(db, prefix, suffix_idx, gid, hit, result):
    r = db.execute(
        "SELECT discovered_json, posterior_json, histogram_json, frontier_json, tested_bytes, miss_streak FROM clusters WHERE prefix5=?",
        (prefix,),
    ).fetchone()
    if not r:
        return
    disc = json.loads(r[0])
    post = json.loads(r[1])
    hist = json.loads(r[2])
    frontier = json.loads(r[3]) if r[3] else []
    tested_blob = r[4]
    miss_streak = r[5] or 0

    if suffix_idx in frontier:
        frontier.remove(suffix_idx)

    import array
    words = array.array("Q")
    words.frombytes(tested_blob)
    word = suffix_idx >> 6
    bit = suffix_idx & 63
    words[word] |= 1 << bit

    if hit:
        post["hits"] = post.get("hits", 0) + 1
        miss_streak = 0
        if gid not in disc:
            disc.append(gid)
        suffix = suffix_from_index(suffix_idx)
        fc = suffix[0]
        hist[fc] = hist.get(fc, 0) + 1
    else:
        post["misses"] = post.get("misses", 0) + 1
        miss_streak += 1

    db.execute(
        """UPDATE clusters SET discovered_json=?, posterior_json=?, histogram_json=?,
           frontier_json=?, tested_bytes=?, miss_streak=? WHERE prefix5=?""",
        (json.dumps(disc), json.dumps(post), json.dumps(hist),
         json.dumps(frontier), words.tobytes(), miss_streak, prefix),
    )
    db.commit()


async def main():
    global shutdown
    if not DB_PATH.exists():
        print("❌ discovery_state.db not found")
        return

    db = sqlite3.connect(str(DB_PATH))
    db.execute("PRAGMA journal_mode=WAL")
    state = load_state()

    transport = httpx.AsyncHTTPTransport(proxy=SOCKS_PROXY)
    async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(12.0, connect=6.0), http2=False) as client:
        probe_count = 0
        ref_fail_streak = 0
        probe_miss_streak = 0
        last_ref_ok = True
        results_buffer = []

        print(f"🚀 Steady Scanner v3 — {RPS} rps via SOCKS5 + {len(WORKERS)} CF workers")
        print(f"   REF through workers with cross-check, blind at {MAX_MISS_STREAK} misses")

        while not shutdown:
            clusters = load_clusters(db)
            if not clusters:
                print("🎉 All clusters exhausted!")
                break

            cluster = clusters[0]
            prefix = cluster["prefix"]
            frontier = cluster["frontier"]

            if not frontier:
                db.execute("UPDATE clusters SET state='DONE' WHERE prefix5=?", (prefix,))
                db.commit()
                continue

            suffix_idx = frontier[0]
            gid = prefix + suffix_from_index(suffix_idx)

            need_ref = (probe_count > 0 and probe_count % REF_INTERVAL == 0) or not last_ref_ok

            if need_ref:
                result = await check_ref(client)
                stats["refs"] += 1
                if result.get("ok"):
                    ref_fail_streak = 0
                    probe_miss_streak = 0
                    last_ref_ok = True
                    probe_count = 0
                    extra = ""
                    if result.get("cross_checked"):
                        extra = " (cross-checked)"
                    print(f"🟢 REF ok ({result['elapsed_ms']}ms{extra})")
                else:
                    stats["ref_fails"] += 1
                    ref_fail_streak += 1
                    err = result.get("error", result.get("status"))
                    print(f"🔴 REF FAIL: {err} (streak={ref_fail_streak})")

                    if ref_fail_streak >= MAX_REF_FAIL:
                        print(f"💀 ALL WORKERS BLIND! Cooldown {COOLDOWN_BLIND}s...")
                        stats["blinds"] += 1
                        save_state(state)
                        await asyncio.sleep(COOLDOWN_BLIND)
                        ref_fail_streak = 0
                        # Сбрасываем изоляцию
                        worker_blind_until.clear()
                        continue
                    else:
                        last_ref_ok = True
                        await asyncio.sleep(3.0)

                await asyncio.sleep(INTERVAL)
                continue

            # Probe
            result = await probe(client, gid)
            probe_count += 1
            stats["requests"] += 1

            hit = result.get("ok") and result.get("images", 0) > 0

            if hit:
                stats["hits"] += 1
                probe_miss_streak = 0
                print(f"🔴 HIT: {gid} ({result['images']} imgs, {result['elapsed_ms']}ms)")
            else:
                stats["misses"] += 1
                probe_miss_streak += 1

            update_db(db, prefix, suffix_idx, gid, hit, result)

            entry = {"ts": time.time(), "gid": gid, "prefix": prefix, "hit": hit,
                     "images": result.get("images", 0), "elapsed_ms": result.get("elapsed_ms", 0)}
            results_buffer.append(entry)
            if len(results_buffer) >= 10:
                for e in results_buffer:
                    log_result(e)
                results_buffer.clear()

            save_state({"prefix": prefix, "last_ref_ok": last_ref_ok})

            # Периодический статус
            if probe_count % 30 == 0:
                live = sum(1 for w in WORKERS if worker_blind_until.get(w, 0) < time.time())
                elapsed = time.time() - stats["started"]
                rps = probe_count / max(elapsed, 0.1)
                print(f"  [{probe_count}] {stats['hits']} hits, {live}/{len(WORKERS)} workers live, {rps:.1f}rps")

            if probe_miss_streak >= MAX_MISS_STREAK:
                ref_result = await check_ref(client)
                stats["refs"] += 1
                if ref_result.get("ok"):
                    print(f"⚠️  {probe_miss_streak} misses but REF ok — continuing")
                    probe_miss_streak = 0
                    ref_fail_streak = 0
                else:
                    print(f"💀 BLIND after {probe_miss_streak} misses! Cooldown {COOLDOWN_BLIND}s...")
                    stats["blinds"] += 1
                    save_state(state)
                    await asyncio.sleep(COOLDOWN_BLIND)
                    probe_miss_streak = 0
                    ref_fail_streak = 0
                    continue

            await asyncio.sleep(INTERVAL)

        for e in results_buffer:
            log_result(e)

    db.close()
    elapsed = time.time() - stats["started"]
    print(f"\n{'='*50}")
    print(f"📊 Done: {elapsed/3600:.1f}h | {stats['requests']} probes | {stats['hits']} hits")
    print(f"   REFs: {stats['refs']} (fails: {stats['ref_fails']}) | Blinds: {stats['blinds']}")
    print(f"   RPS: {stats['requests']/max(elapsed,1):.2f}")


def handle(sig, frame):
    global shutdown
    print(f"\n🛑 Shutting down...")
    shutdown = True

signal.signal(signal.SIGINT, handle)
signal.signal(signal.SIGTERM, handle)

if __name__ == "__main__":
    asyncio.run(main())
