#!/usr/bin/env python3
"""
POSTIMG Discovery Pipeline — единый запуск всего цикла.

Использование:
  python3 pipeline.py [--config config.json]

Что делает:
  1. Проверяет AdGuard VPN (подключает если надо)
  2. Проверяет SESSIONKEY (создаёт галереи-сиды если надо)
  3. Запускает brute-force вокруг сидов через Workers + авто-ротация AdGuard
  4. Сохраняет состояние, переживает отключения и баны
  5. Работает пока не остановят (Ctrl+C) или не кончатся префиксы
"""
import json, time, re, random, subprocess, httpx, logging, string, sys
from pathlib import Path
from datetime import datetime, timezone

# ══════════════════════════════════════════════════
# Конфиг
# ══════════════════════════════════════════════════

DIR = Path(__file__).parent
CONFIG_PATH = DIR / "pipeline_config.json"
WORKER_REF = Path("/mnt/c/Users/Rhagtoo/POSTIMG/worker_ref.json")
STATE_PATH = DIR / "pipeline_state.json"
SEEDS_PATH = DIR / "created_galleries.txt"
RESULTS_PATH = DIR / "steady_scan_results.jsonl"
LOG_PATH = DIR / "pipeline.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [pipeline] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_PATH)]  # только в файл (фон)
)
log = logging.getLogger("pipeline")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

IMG_RE = re.compile(r'i\.postimg\.cc/[^"\s<>]+')
BASE62 = string.digits + string.ascii_letters
SUFFIXES = list(BASE62)  # только последний символ (62 варианта)

# ══════════════════════════════════════════════════
# Состояние
# ══════════════════════════════════════════════════

def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"created_seeds": [], "completed_prefixes": [],
            "total_probes": 0, "total_hits": 0, "total_blinds": 0,
            "started_at": None}

def save_state(state):
    state["updated_at"] = datetime.now().isoformat()
    STATE_PATH.write_text(json.dumps(state, indent=2))

# ══════════════════════════════════════════════════
# AdGuard
# ══════════════════════════════════════════════════

def adguard_status():
    try:
        r = subprocess.run(["adguardvpn-cli", "status"], capture_output=True, text=True, timeout=5)
        return "Connected" in r.stdout or "connected" in r.stdout
    except: return False

def adguard_connect(loc):
    try:
        r = subprocess.run(["adguardvpn-cli", "connect", "-l", loc],
                          capture_output=True, text=True, timeout=15)
        return "Successfully Connected" in r.stdout
    except: return False

def adguard_ensure(cfg):
    """Убедиться что AdGuard подключён."""
    if adguard_status():
        log.info("AdGuard VPN уже подключён")
        return True
    for loc in cfg["adguard"]["locations"]:
        log.info(f"Подключаю AdGuard → {loc}")
        if adguard_connect(loc):
            log.info(f"AdGuard подключён: {loc}")
            return True
    log.error("Не могу подключить AdGuard ни к одной локации")
    return False

# ══════════════════════════════════════════════════
# Cookies / SESSIONKEY
# ══════════════════════════════════════════════════

def check_session(cfg):
    """Проверяет рабочий ли SESSIONKEY."""
    try:
        r = httpx.post("https://postimg.cc/json",
                        data={"action": "add", "name": "test_cookie"},
                        cookies=cfg["cookies"],
                        headers={"x-requested-with": "XMLHttpRequest",
                                 "origin": "https://postimg.cc",
                                 "referer": "https://postimg.cc/"},
                        timeout=10)
        ok = "url_html" in r.text and r.status_code == 200
        if ok:
            log.info("SESSIONKEY рабочий ✅")
        else:
            log.warning(f"SESSIONKEY не работает: {r.text[:100]}")
        return ok
    except Exception as e:
        log.error(f"Ошибка проверки SESSIONKEY: {e}")
        return False

def create_galleries(cfg, count=20):
    """Создаёт галереи через API."""
    log.info(f"Создаю {count} галерей...")
    gids = []
    for i in range(count):
        try:
            r = httpx.post("https://postimg.cc/json",
                          data={"action": "add", "name": f"auto_{int(time.time())%100000}_{i}"},
                          cookies=cfg["cookies"],
                          headers={"x-requested-with": "XMLHttpRequest",
                                   "origin": "https://postimg.cc",
                                   "referer": "https://postimg.cc/"},
                          timeout=15)
            d = r.json()
            gid = d.get("url_html", "").split("/")[-1] if d.get("url_html") else None
            if gid:
                gids.append(gid)
                log.info(f"  [{i+1}/{count}] {gid}")
            else:
                log.warning(f"  [{i+1}/{count}] FAIL: {d}")
        except Exception as e:
            log.error(f"  [{i+1}/{count}] ERROR: {e}")
        time.sleep(0.3 + random.random() * 0.5)

    if gids:
        existing = set()
        if SEEDS_PATH.exists():
            existing = set(line.strip() for line in open(SEEDS_PATH) if line.strip())
        all_ids = sorted(existing | set(gids))
        SEEDS_PATH.write_text("\n".join(all_ids) + "\n")
        log.info(f"Создано: {len(gids)} (всего сидов: {len(all_ids)})")
    return gids

# ══════════════════════════════════════════════════
# Workers
# ══════════════════════════════════════════════════

WORKERS = None
SOCKS = None

def load_workers():
    global WORKERS, SOCKS
    cfg = json.loads(CONFIG_PATH.read_text())
    SOCKS = f"socks5://127.0.0.1:{cfg['adguard']['socks_port']}"
    WORKERS = json.loads(WORKER_REF.read_text()).get("workers", [])

def check_ref(loc="?"):
    """Проверка канала ТОЛЬКО через Workers. Без прямого fallback.
    Прямой доступ к postimg.cc маскирует слепоту Workers — это баг.
    Если Workers слепнут → нужна ротация AdGuard, а не прямой обход."""
    ref = "y3tXqH0"
    alive = 0
    sample = random.sample(WORKERS, min(5, len(WORKERS)))
    wnames = []
    for w in sample:
        wn = w['url'].split('//')[1].split('.')[0]
        try:
            r = httpx.get(f"{w['url']}/gallery/{ref}",
                          headers={"X-Key": w["secret"]}, proxy=SOCKS, timeout=10)
            if r.status_code == 200:
                alive += 1
                wnames.append(f"{wn}✅")
            else:
                wnames.append(f"{wn}❌")
        except:
            wnames.append(f"{wn}💀")
    ok = alive > 0
    log.info(f"[{loc}] ref: {alive}/{len(sample)} {'✅' if ok else '❌'} [{', '.join(wnames)}]")
    return ok

def probe(gid, loc="?"):
    w = random.choice(WORKERS)
    wn = w['url'].split('//')[1].split('.')[0]
    try:
        r = httpx.get(f"{w['url']}/gallery/{gid}",
                       headers={"X-Key": w["secret"]}, proxy=SOCKS, timeout=10)
        if r.status_code == 404: return "miss", 0, wn
        if r.status_code != 200: return "err", 0, wn
        imgs = IMG_RE.findall(r.text)
        cnt = len(set(imgs))
        if cnt:
            log.info(f"🎯 HIT {gid} ({cnt}img) [{loc}] via {wn}")
            return "hit", cnt, wn
        return "empty", 0, wn
    except:
        return "err", 0, wn

# ══════════════════════════════════════════════════
# Главный цикл
# ══════════════════════════════════════════════════

def main():
    log.info("=" * 50)
    log.info("POSTIMG Discovery Pipeline")
    log.info("=" * 50)

    if len(sys.argv) > 1:
        global CONFIG_PATH
        CONFIG_PATH = Path(sys.argv[1])
    cfg = json.loads(CONFIG_PATH.read_text())

    log.info("Шаг 1: AdGuard VPN")
    if not adguard_ensure(cfg):
        log.error("AdGuard не доступен. Выход.")
        return

    log.info("Шаг 2: Workers")
    load_workers()
    log.info(f"Загружено {len(WORKERS)} workers")

    log.info("Шаг 3: SESSIONKEY и сиды")
    if not check_session(cfg):
        log.error("SESSIONKEY нерабочий. Обнови cookies в pipeline_config.json")
        return

    existing_seeds = set()
    if SEEDS_PATH.exists():
        existing_seeds = set(line.strip() for line in open(SEEDS_PATH) if line.strip())
    log.info(f"Существующих сидов: {len(existing_seeds)}")

    if len(existing_seeds) < cfg["seeds"]["min_count"]:
        log.info(f"Мало сидов ({len(existing_seeds)} < {cfg['seeds']['min_count']}), создаю новые")
        create_galleries(cfg, cfg["seeds"]["batch_size"])
        existing_seeds = set(line.strip() for line in open(SEEDS_PATH) if line.strip())

    log.info("Шаг 4: Загрузка состояния")
    state = load_state()
    if state["started_at"] is None:
        state["started_at"] = datetime.now(timezone.utc).isoformat()
    start_ts = time.time()
    session_start_probes = state["total_probes"]  # для честного RPS текущей сессии
    log.info(f"Всего проб: {state['total_probes']}, хитов: {state['total_hits']}")

    all_prefixes = sorted(set(g[:5] for g in existing_seeds))
    remaining = [p for p in all_prefixes if p not in state["completed_prefixes"]]
    log.info(f"Префиксов всего: {len(all_prefixes)}, осталось: {len(remaining)}")

    if not remaining:
        log.info("Все префиксы просканированы. Создаю новые сиды...")
        create_galleries(cfg, cfg["seeds"]["batch_size"])
        existing_seeds = set(line.strip() for line in open(SEEDS_PATH) if line.strip())
        all_prefixes = sorted(set(g[:5] for g in existing_seeds))
        remaining = [p for p in all_prefixes if p not in state["completed_prefixes"]]

    log.info("Шаг 5: Поиск живого канала")
    locations = cfg["adguard"]["locations"]
    loc = None
    loc_start_ts = time.time()
    probes_this_loc = 0
    loc_stats = {}

    for loc in locations:
        if not adguard_connect(loc):
            continue
        time.sleep(3)
        if check_ref(loc):
            log.info(f"✅ Канал жив через {loc}")
            loc_start_ts = time.time()
            probes_this_loc = 0
            loc_stats.setdefault(loc, {"activations": 0, "total_probes": 0, "blinds": 0})
            loc_stats[loc]["activations"] += 1
            break
        log.info(f"  {loc} — слепой")
    else:
        log.error("Все локации слепые. Попробуйте позже.")
        return

    log.info("Шаг 6: Сканирование")
    log.info(f"Живая локация: {loc}, префиксов: {len(remaining)}")

    probes_since_check = 0
    probes_this_prefix = 0
    last_session_check = time.monotonic()
    blind_start = {}
    workers_alive = True

    try:
        for prefix in remaining[:cfg["scan"]["max_prefixes_per_run"]]:
            log.info(f"Префикс {prefix}** ({len(SUFFIXES)} кандидатов)")
            for suffix in SUFFIXES:
                gid = prefix + suffix
                if gid in existing_seeds:
                    continue

                if probes_since_check >= cfg["adguard"]["blind_threshold_probes"]:
                    probes_since_check = 0
                    if not check_ref(loc):
                        blind_start[loc] = time.time()
                        state["total_blinds"] += 1
                        dur = time.time() - loc_start_ts
                        s = loc_stats.setdefault(loc, {"activations": 0, "total_probes": 0, "blinds": 0})
                        s["total_probes"] += probes_this_loc
                        s["blinds"] += 1
                        log.info(f"❌ [{loc}] ослепла: {probes_this_loc} пробов за {dur:.0f}с (всего проб #{state['total_probes']})")
                        for new_loc in locations:
                            if new_loc == loc:
                                continue
                            adguard_connect(new_loc); time.sleep(4)
                            if check_ref(new_loc):
                                if new_loc in blind_start:
                                    blind_dur = time.time() - blind_start[new_loc]
                                    log.info(f"✅ [{new_loc}] разбанило! Была слепа {blind_dur/60:.1f}мин")
                                    del blind_start[new_loc]
                                else:
                                    log.info(f"✅ [{new_loc}] жив!")
                                loc = new_loc
                                loc_start_ts = time.time()
                                probes_this_loc = 0
                                ls = loc_stats.setdefault(loc, {"activations": 0, "total_probes": 0, "blinds": 0})
                                ls["activations"] += 1
                                break
                        else:
                            log.error("Все локации слепые! Жду 120s...")
                            time.sleep(120)

                st, cnt, wn = probe(gid, loc)
                state["total_probes"] += 1
                probes_since_check += 1
                probes_this_prefix += 1
                probes_this_loc += 1

                if st == "hit":
                    state["total_hits"] += 1
                    with open(RESULTS_PATH, "a") as f:
                        f.write(json.dumps({"ts": time.time(), "gid": gid,
                                            "hit": True, "images": cnt,
                                            "loc": loc, "worker": wn}) + "\n")

                if state["total_probes"] % cfg["scan"]["report_interval"] == 0:
                    save_state(state)
                    elapsed = time.time() - start_ts
                    rps = (state["total_probes"] - session_start_probes) / elapsed if elapsed > 0 else 0
                    log.info(f"[{state['total_probes']}p | {state['total_hits']}h | "
                             f"{rps:.2f}rps | blind={state['total_blinds']}] "
                             f"{loc} {prefix}({probes_this_prefix}/{len(SUFFIXES)}) "
                             f"loc:{probes_this_loc}p")

                if time.monotonic() - last_session_check > 3600:
                    if not check_session(cfg):
                        log.error("SESSIONKEY протух! Остановка.")
                        save_state(state)
                        return
                    last_session_check = time.monotonic()

            state["completed_prefixes"].append(prefix)
            save_state(state)
            elapsed = time.time() - start_ts
            session_rps = (state['total_probes'] - session_start_probes) / elapsed if elapsed > 0 else 0
            log.info(f"✅ {prefix} done: {probes_this_prefix}p | total {state['total_hits']}h | {session_rps:.2f}rps")
            probes_this_prefix = 0

    except KeyboardInterrupt:
        log.info("Остановлено пользователем")

    save_state(state)
    log.info(f"ГОТОВО: {state['total_probes']} проб, {state['total_hits']} хитов, "
             f"{state['total_blinds']} ослеплений")
    log.info(f"Префиксов завершено: {len(state['completed_prefixes'])}")

if __name__ == "__main__":
    while True:
        try:
            main()
        except KeyboardInterrupt:
            print("Остановлено пользователем")
            break
        print("Перезапуск через 5с...")
        time.sleep(5)
