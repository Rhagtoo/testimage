#!/usr/bin/env python3
"""Эксперимент: token bucket или жёсткий счётчик?
MISS с паузами 60s → если бан после тех же 5-7 miss → счётчик.
Если нужно 20+ miss → token bucket с восстановлением."""
import asyncio, httpx, random, string, json, time, os
from pathlib import Path

WORKER = "https://postimg-diag.rhagtoo.workers.dev"
SECRET = open("/mnt/c/Users/Rhagtoo/cf_worker/secret.txt").read().strip()
GK = "3877d5eb451bfe6ec8060544192bab23"
REF = "y3tXqH0"
CHARSET = string.ascii_letters + string.digits
PAUSE = 60  # секунд между MISS

def gid(): return "".join(random.choices(CHARSET, k=7))

async def probe(label, gid_ref):
    cl = httpx.AsyncClient(timeout=httpx.Timeout(14.0, connect=6.0), http2=False)
    try:
        t0 = time.monotonic()
        r = await cl.get(f"{WORKER}/json",
            params={"action": "list", "page": 1, "album": gid_ref},
            headers={"X-Key": SECRET, "Cookie": f"GUESTKEY={GK}",
                     "User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        elapsed = time.monotonic() - t0
        d = {
            "label": label, "ts": time.time(), "elapsed_ms": round(elapsed*1000),
            "status": r.status_code,
            "cf_ray": r.headers.get("X-Debug-CF-Ray", ""),
            "cf_colo": r.headers.get("X-Debug-CF-Colo", ""),
            "cache": r.headers.get("X-Debug-Cache-Status", ""),
            "origin": r.headers.get("X-Debug-Origin-Status", ""),
        }
        if r.status_code == 200:
            j = r.json()
            d["ok"] = not j.get("error")
            d["images"] = len(j.get("images", []))
        else:
            d["ok"] = False
            d["body"] = r.text[:120]
        return d
    except Exception as e:
        return {"label": label, "error": type(e).__name__, "ts": time.time()}
    finally:
        await cl.aclose()

async def main():
    results = []
    t0 = time.time()

    r = await probe("REF_BASELINE", REF)
    results.append(r)
    print(f"[{r['elapsed_ms']}ms] REF_BASELINE: status={r['status']}, "
          f"ok={r.get('ok')}, cf_ray={r.get('cf_ray','?')[:20]}")

    if not r.get("ok"):
        print("❌ REF dead at start")
        return results

    for i in range(1, 30):
        print(f"\n⏳ Пауза {PAUSE}s перед MISS_{i}...", end=" ", flush=True)
        for remaining in range(PAUSE, 0, -10):
            await asyncio.sleep(10)
            print(f"{remaining}s", end=" ", flush=True)
        print()

        r = await probe(f"MISS_{i}", gid())
        results.append(r)
        elapsed_m = (time.time() - t0) / 60
        icon = "✓" if r.get("ok") else ""
        print(f"[{elapsed_m:.1f}min] MISS_{i}: status={r['status']} {icon}")

        r = await probe(f"REF_AFTER_{i}", REF)
        results.append(r)
        elapsed_m = (time.time() - t0) / 60
        icon = "✓" if r.get("ok") else "✗"
        print(f"[{elapsed_m:.1f}min] REF_AFTER_{i}: status={r['status']} {icon}, "
              f"cf_ray={r.get('cf_ray','?')[:20]}")

        if not r.get("ok"):
            print(f"\n⚠ ОСЛЕПЛЕНИЕ после {i} miss с паузами {PAUSE}s!")
            print(f"   Всего прошло: {elapsed_m:.1f} мин")
            break
    else:
        print(f"\n✅ НЕ ослепло после 30 miss с паузами — чистый token bucket с быстрым восстановлением")

    return results

results = asyncio.run(main())
out = Path("/tmp/blind_pause_result.json")
out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
print(f"\nСохранено: {out}")
