#!/usr/bin/env python3
"""Финальный эксперимент: ослепление через diagnostic Worker с полной диагностикой."""
import asyncio, httpx, uuid, random, string, json, time, os

WORKER = "https://postimg-diag.rhagtoo.workers.dev"
SECRET = open("/mnt/c/Users/Rhagtoo/cf_worker/secret.txt").read().strip()
GUESTKEY_BASE = "3877d5eb451bfe6ec8060544192bab23"
REF = "y3tXqH0"
CHARSET = string.ascii_letters + string.digits

def gid(): return "".join(random.choices(CHARSET, k=7))

async def probe(label, gid_ref, guestkey):
    cl = httpx.AsyncClient(timeout=httpx.Timeout(14.0, connect=6.0), http2=False)
    try:
        r = await cl.get(
            f"{WORKER}/json",
            params={"action": "list", "page": 1, "album": gid_ref},
            headers={
                "X-Key": SECRET,
                "Cookie": f"GUESTKEY={guestkey}",
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
            },
        )
        diag = {
            "label": label,
            "status": r.status_code,
            "cf_ray": r.headers.get("X-Debug-CF-Ray", ""),
            "cf_colo": r.headers.get("X-Debug-CF-Colo", ""),
            "cache_status": r.headers.get("X-Debug-Cache-Status", ""),
            "origin_status": r.headers.get("X-Debug-Origin-Status", ""),
            "server": r.headers.get("X-Debug-Server", ""),
            "age": r.headers.get("X-Debug-Age", ""),
            "latency_ms": r.headers.get("X-Debug-Latency-Ms", ""),
        }
        if r.status_code == 200:
            d = r.json()
            diag["ok"] = not d.get("error")
            diag["images"] = len(d.get("images", []))
        else:
            diag["ok"] = False
            diag["body"] = r.text[:120]
        return diag
    except Exception as e:
        return {"label": label, "error": str(type(e).__name__)}
    finally:
        await cl.aclose()

async def main():
    results = []
    gk_a = uuid.uuid4().hex[:32]  # random guestkey

    # Phase A: REF baseline
    r = await probe("REF_BASELINE", REF, gk_a)
    results.append(r)
    print(f"[1] REF_BASELINE: status={r['status']}, ok={r.get('ok')}, "
          f"images={r.get('images')}, cf_ray={r.get('cf_ray','?')[:20]}, "
          f"colo={r.get('cf_colo')}, cache={r.get('cache_status')}")

    if not r.get("ok"):
        print("❌ REF dead at start — abort")
        return results

    # Phase B: MISS loop until blind
    for i in range(1, 15):
        # MISS
        r = await probe(f"MISS_{i}", gid(), gk_a)
        results.append(r)
        print(f"[{len(results)}] MISS_{i}: status={r['status']}, "
              f"cf_ray={r.get('cf_ray','?')[:20]}, colo={r.get('cf_colo')}, "
              f"origin={r.get('origin_status')}")
        await asyncio.sleep(0.3)

        # REF check
        r = await probe(f"REF_AFTER_{i}", REF, gk_a)
        results.append(r)
        icon = "✓" if r.get("ok") else "✗"
        print(f"[{len(results)}] REF_AFTER_{i}: status={r['status']} {icon}, "
              f"cf_ray={r.get('cf_ray','?')[:20]}, "
              f"colo={r.get('cf_colo')}, cache={r.get('cache_status')}, "
              f"origin={r.get('origin_status')}")

        if not r.get("ok"):
            print(f"\n⚠ ОСЛЕПЛЕНИЕ после {i} miss!")
            break

    return results

results = asyncio.run(main())
print(f"\nВсего шагов: {len(results)}")
Path("/tmp/blind_diag_result.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
print("Сохранено: /tmp/blind_diag_result.json")

# Summary
blind_step = next((r for r in results if r["label"].startswith("REF_AFTER") and not r.get("ok")), None)
if blind_step:
    print(f"\nКлючевые данные при ослеплении:")
    print(f"  CF-Ray:    {blind_step.get('cf_ray')}")
    print(f"  CF-Colo:   {blind_step.get('cf_colo')}")
    print(f"  Cache:     {blind_step.get('cache_status')}")
    print(f"  Origin:    {blind_step.get('origin_status')}")
    print(f"  Server:    {blind_step.get('server')}")
    print(f"  Age:       {blind_step.get('age')}")
