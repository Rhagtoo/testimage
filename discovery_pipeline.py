#!/usr/bin/env python3
"""
discovery_pipeline.py — полный пайплайн:
1. Скрапинг DuckDuckGo → seed ID
2. Проверка через CF Worker → живые галереи
3. Brute-force префикс-6 → все галереи сессии
"""
import httpx, re, time, sys, json, string
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

DDG_URL = "https://html.duckduckgo.com/html/?q=site:postimg.cc/gallery&s={offset}"
WORKER_URL = sys.argv[1] if len(sys.argv) > 1 else "https://postimg-ref1.rhagtoo2.workers.dev"
WORKER_KEY = sys.argv[2] if len(sys.argv) > 2 else "ncG7NaK_cZqkl08xXG0V152T0LYkM5LX"
BASE62 = string.digits + string.ascii_letters
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/150.0.0.0",
    "X-Key": WORKER_KEY,
}

def scrape_ddg(max_pages=5):
    """Скрапим DuckDuckGo HTML-выдачу."""
    found = set()
    for page in range(max_pages):
        url = DDG_URL.format(offset=page * 30)
        try:
            r = httpx.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=15)
            ids = set(re.findall(r'postimg\.cc/gallery/([a-zA-Z0-9]{5,8})', r.text))
            new = ids - found
            found |= ids
            print(f"  DDG page {page}: +{len(new)} new (total {len(found)})")
            if not ids:
                break
            time.sleep(1.5)
        except Exception as e:
            print(f"  DDG page {page}: ERR {e}")
            break
    return sorted(found)

def check_gallery(gid):
    """Проверка галереи через CF Worker HTML."""
    try:
        r = httpx.get(f"{WORKER_URL}/gallery/{gid}", headers=HEADERS, timeout=10)
        return gid, r.status_code
    except:
        return gid, -1

def brute_prefix6(prefix6):
    """Брутим все 62 последних символа для префикса-6."""
    results = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(check_gallery, prefix6 + c): c for c in BASE62}
        for f in as_completed(futures):
            gid, code = f.result()
            if code == 200:
                results.append(gid)
    return sorted(results)

def main():
    print("=== Phase 1: DuckDuckGo scraping ===\n")
    seeds = scrape_ddg(max_pages=5)
    print(f"\nScraped {len(seeds)} candidate IDs")

    if not seeds:
        print("No seeds found. Exiting.")
        return

    # Сохраняем seeds
    Path("/tmp/discovery_seeds.txt").write_text("\n".join(seeds))

    print("\n=== Phase 2: Verify seeds via CF Worker ===\n")
    live = []
    batch_size = 20
    for i in range(0, len(seeds), batch_size):
        batch = seeds[i:i+batch_size]
        with ThreadPoolExecutor(max_workers=10) as ex:
            futures = {ex.submit(check_gallery, gid): gid for gid in batch}
            for f in as_completed(futures):
                gid, code = f.result()
                if code == 200:
                    live.append(gid)
        print(f"  [{i+1}-{min(i+batch_size, len(seeds))}]: {len(live)} live so far")
        time.sleep(0.5)

    print(f"\nLive galleries: {len(live)}/{len(seeds)}")
    for gid in live:
        print(f"  {gid}")

    if not live:
        return

    # Сохраняем живые
    Path("/tmp/discovery_live.txt").write_text("\n".join(live))

    print("\n=== Phase 3: Brute-force prefix-6 clusters ===\n")
    all_found = set(live)
    for gid in live:
        prefix6 = gid[:6]
        cluster = brute_prefix6(prefix6)
        new = set(cluster) - all_found
        if new:
            all_found |= set(cluster)
            print(f"  {prefix6}*: +{len(new)} new → {sorted(new)}")

    total_new = len(all_found) - len(live)
    print(f"\n=== RESULTS ===")
    print(f"Seeds scraped: {len(seeds)}")
    print(f"Live verified: {len(live)}")
    print(f"After brute-force: {len(all_found)} total (+{total_new})")

    Path("/tmp/discovery_all.txt").write_text("\n".join(sorted(all_found)))
    Path("/tmp/discovery_report.json").write_text(json.dumps({
        "seeds": seeds,
        "live": live,
        "all_found": sorted(all_found),
        "total": len(all_found),
    }, indent=2))
    print(f"\nSaved to /tmp/discovery_all.txt and /tmp/discovery_report.json")


if __name__ == "__main__":
    main()
