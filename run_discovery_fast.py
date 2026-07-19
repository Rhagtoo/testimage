#!/usr/bin/env python3
"""Discovery Runner — Workers + AdGuard SOCKS5 + авто-ротация локаций."""
import json, time, re, random, string, subprocess, httpx, logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [disc] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("disc")

SOCKS = "socks5://127.0.0.1:1080"
WORKERS = json.loads(Path("/mnt/c/Users/Rhagtoo/POSTIMG/worker_ref.json").read_text())["workers"]
REFS = Path(__file__).parent / "steady_scan_results.jsonl"
IMG_RE = re.compile(r'i\.postimg\.cc/[^"\s<>]+')
BASE62 = string.digits + string.ascii_letters

# Локации AdGuard (короткие имена для CLI)
LOCATIONS = ["Riga", "Tallinn", "Stockholm", "Amsterdam", "Copenhagen", "Brussels",
             "Oslo", "Milan", "Prague", "Helsinki", "Frankfurt", "Marseille",
             "Warsaw", "London", "Berlin", "Madrid", "Dublin", "Zurich",
             "Vienna", "Paris", "Rome", "Bratislava", "Bucharest", "Istanbul"]

def connect_adguard(location):
    """Подключить AdGuard к локации. Возвращает True если успешно."""
    try:
        r = subprocess.run(["adguardvpn-cli", "connect", "-l", location],
                          capture_output=True, text=True, timeout=15)
        return "Successfully Connected" in r.stdout
    except:
        return False

def check_ref():
    """REF через Workers + SOCKS5."""
    for w in random.sample(WORKERS, min(5, len(WORKERS))):
        try:
            r = httpx.get(f"{w['url']}/gallery/y3tXqH0",
                          headers={"X-Key": w["secret"]}, proxy=SOCKS, timeout=10)
            if r.status_code == 200:
                return True
        except:
            continue
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

def rand_id():
    return ''.join(random.choice(BASE62) for _ in range(7))

def main():
    log.info(f"Workers: {len(WORKERS)}")

    # Стартовая локация
    loc_idx = 0
    current_loc = LOCATIONS[loc_idx]
    if not connect_adguard(current_loc):
        log.error(f"Не могу подключиться к {current_loc}")
        return

    # Ждём живой канал
    for attempt in range(1, 30):
        if check_ref():
            log.info(f"✅ {current_loc}: Workers живы, старт!")
            break
        log.warning(f"⏸ {current_loc}: слепые (попытка {attempt})")
        # Меняем локацию
        loc_idx = (loc_idx + 1) % len(LOCATIONS)
        current_loc = LOCATIONS[loc_idx]
        connect_adguard(current_loc)
        time.sleep(3)
    else:
        log.error("Все локации слепые :(")
        return

    probes = hits = wi = blinds = 0
    start = time.monotonic()

    try:
        while probes < 100000:
            if probes > 0 and probes % 30 == 0:
                if not check_ref():
                    blinds += 1
                    loc_idx = (loc_idx + 1) % len(LOCATIONS)
                    current_loc = LOCATIONS[loc_idx]
                    log.warning(f"😵 BLIND #{blinds} → переключаю на {current_loc}")
                    connect_adguard(current_loc)
                    time.sleep(5)
                    if not check_ref():
                        log.warning(f"   {current_loc} тоже слепая, ещё раз")
                        loc_idx = (loc_idx + 1) % len(LOCATIONS)
                        current_loc = LOCATIONS[loc_idx]
                        connect_adguard(current_loc)
                        time.sleep(5)
                    continue

            gid = rand_id()
            w = WORKERS[wi % len(WORKERS)]; wi += 1
            st, cnt = probe(w, gid)
            probes += 1

            if st == "hit":
                hits += 1
                with open(REFS, "a") as f:
                    f.write(json.dumps({"ts": time.time(), "gid": gid, "hit": True,
                                        "images": cnt, "w": w["url"].split("//")[1].split(".")[0],
                                        "loc": current_loc}) + "\n")
                log.info(f"✅ HIT {gid} ({cnt} imgs) [{current_loc}]")

            if probes % 100 == 0:
                e = time.monotonic() - start
                log.info(f"[{probes}] {hits}h {probes/e:.1f}rps "
                         f"blinds={blinds} loc={current_loc}")

    except KeyboardInterrupt: pass
    finally:
        log.info(f"DONE: {probes}p {hits}h {blinds}b in {(time.monotonic()-start)/60:.0f}m")

if __name__ == "__main__":
    main()
