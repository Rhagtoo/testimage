#!/usr/bin/env python3
"""Сканирование кластера: перебор всех 3844 суффиксов префикса (прямой доступ)."""
import asyncio, httpx, json, time, sys

CHARSET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
TARGET = "https://postimg.cc"
CONCURRENT = 12
PREFIX = sys.argv[1] if len(sys.argv) > 1 else "Cnqcy"
GUESTKEY = "3877d5eb451bfe6ec8060544192bab23"

found = []
total = 0

async def probe(suffix):
    gid = PREFIX + suffix
    cl = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=4.0), http2=False,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                 "Cookie": f"GUESTKEY=***",
                 "Referer": f"{TARGET}/"})
    try:
        r = await cl.get(f"{TARGET}/json",
            params={"action": "list", "page": 1, "album": gid})
        if r.status_code == 200:
            d = r.json()
            if not d.get("error"):
                return {"gid": gid, "found": True, "images": len(d.get("images", []))}
        elif r.status_code == 429:
            return {"gid": gid, "found": False, "rate_limited": True}
        return {"gid": gid, "found": False}
    except:
        return {"gid": gid, "found": False}
    finally:
        await cl.aclose()

async def scan():
    global total
    sem = asyncio.Semaphore(CONCURRENT)
    t0 = time.time()

    async def bounded(c1, c2):
        global total
        async with sem:
            r = await probe(c1 + c2)
            total += 1
            if total % 200 == 0:
                elapsed = time.time() - t0
                print(f"  [{total}/{62*62}] {total/elapsed:.1f} rps, found={len(found)}", flush=True)
            if r["found"]:
                found.append(r)
                print(f"  ✅ {r['gid']}: {r['images']} imgs", flush=True)
            elif r.get("rate_limited"):
                print(f"  ⚠ 429 — пауза 3s", flush=True)
                await asyncio.sleep(3)
            return r

    tasks = [bounded(c1, c2) for c1 in CHARSET for c2 in CHARSET]
    await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = time.time() - t0
    print(f"\n=== {PREFIX}?? ===")
    print(f"Проверено: {total}/{62*62}")
    print(f"Найдено: {len(found)}")
    print(f"Время: {elapsed:.1f}s ({total/elapsed:.1f} rps)")
    for f in found:
        print(f"  {f['gid']}: {f['images']} images")
    
    json.dump({"prefix": PREFIX, "found": len(found), "total": total, "elapsed_s": elapsed, "galleries": found},
              open(f"cluster_{PREFIX}.json", "w"), indent=2)
    print(f"\nСохранено: cluster_{PREFIX}.json")
    return found

found = asyncio.run(scan())
