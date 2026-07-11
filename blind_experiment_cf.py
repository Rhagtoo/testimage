#!/usr/bin/env python3
"""
Эксперимент #2: cookie-гипотеза через CF Worker.
=================================================
Проверяет: бан по IP Cloudflare или по GUESTKEY (cookie-сессии)?

Worker пересылает GUESTKEY → origin видит одну сессию.
После N×404 сессия flagged → все запросы этой сессии = 404.

Тест:
  Фаза A: фиксированный GUESTKEY → ref → miss×N → ref. Ждём ослепления.
  Фаза B: НОВЫЙ GUESTKEY (без смены Worker) → ref.
           200 = бан по cookie-сессии.
           404 = бан по IP (Cloudflare egress IP).

Запуск: python3 blind_experiment_cf.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import string
import sys
import time
from pathlib import Path
import uuid

import httpx

# ── config ──────────────────────────────────────────────────────
# CF Worker endpoint (без X-Key — forbidden)
CF_WORKER_URL = os.environ.get(
    "CF_WORKER_URL",
    "https://postimg-ref.rhagtoo2.workers.dev",
)
CF_SECRET = os.environ.get("CF_SECRET", "")
REF_GID = os.environ.get("REF_GID", "y3tXqH0")
CHARSET = string.ascii_letters + string.digits
ID_LENGTH = 7
REQUEST_TIMEOUT = 14.0

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)

# ── helpers ─────────────────────────────────────────────────────
def random_gid() -> str:
    return "".join(random.choices(CHARSET, k=ID_LENGTH))


def random_guestkey() -> str:
    return uuid.uuid4().hex[:32]


def make_worker_client(guestkey: str | None = None) -> httpx.AsyncClient:
    """Клиент к CF Worker — X-Key обязателен."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://postimg.cc/",
        "X-Key": CF_SECRET,
    }
    if guestkey:
        headers["X-Guestkey"] = guestkey
        headers["Cookie"] = f"GUESTKEY={guestkey}"

    return httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=6.0),
        follow_redirects=True,
        http2=False,
    )


async def probe(client: httpx.AsyncClient, gid: str) -> dict:
    """Проверяет галерею через CF Worker JSON API."""
    params = {"action": "list", "page": 1, "album": gid}
    t0 = time.monotonic()
    try:
        r = await client.get(f"{CF_WORKER_URL}/json", params=params)
        elapsed = time.monotonic() - t0
        result = {
            "gid": gid,
            "status": r.status_code,
            "elapsed_ms": round(elapsed * 1000),
        }
        if r.status_code == 403:
            result["ok"] = False
            result["error"] = "403_forbidden"
            return result
        if r.status_code == 429:
            result["ok"] = False
            result["error"] = "429_rate_limited"
            return result
        if r.status_code == 200:
            try:
                data = r.json()
            except Exception:
                result["ok"] = False
                result["error"] = "json_parse_failed"
                return result
            err = data.get("error")
            if err:
                result["ok"] = False
                result["error"] = f"api_error:{err.get('code', '?')}"
            else:
                images = data.get("images") or data.get("data", {}).get("images") or []
                result["ok"] = True
                result["images_count"] = len(images) if isinstance(images, list) else 0
        elif r.status_code == 404:
            result["ok"] = False
            result["error"] = "404"
        else:
            result["ok"] = False
            result["error"] = f"status_{r.status_code}"
        return result
    except httpx.TimeoutException:
        return {"gid": gid, "status": -1, "ok": False, "error": "timeout", "elapsed_ms": round((time.monotonic() - t0) * 1000)}
    except Exception as exc:
        return {"gid": gid, "status": -2, "ok": False, "error": f"exception:{type(exc).__name__}", "elapsed_ms": round((time.monotonic() - t0) * 1000)}


async def run_experiment(max_misses: int = 10) -> dict:
    """Фаза A: фикс. GUESTKEY → ослепление. Фаза B: новый GUESTKEY → ref."""
    steps: list[dict] = []
    report: dict = {
        "worker": CF_WORKER_URL,
        "ref_gid": REF_GID,
        "max_misses": max_misses,
        "steps": steps,
    }

    if not CF_SECRET:
        print("❌ CF_SECRET не задан. Экспортируйте: export CF_SECRET='...'")
        report["error"] = "no_secret"
        return report

    guestkey_a = random_guestkey()
    print(f"GUESTKEY_A = {guestkey_a[:16]}...")

    # ── Фаза A: фиксированный GUESTKEY ──
    client = make_worker_client(guestkey=guestkey_a)

    # baseline REF
    step = await probe(client, REF_GID)
    step["label"] = "REF_BASELINE"
    step["guestkey"] = guestkey_a[:16]
    steps.append(step)
    print(f"  [1] REF_BASELINE: status={step['status']}, ok={step.get('ok')}, "
          f"images={step.get('images_count', '?')}")

    if not step.get("ok"):
        await client.aclose()
        report["error"] = f"ref_dead_at_start:{step.get('error')}"
        return report

    blind_at = None
    for i in range(1, max_misses + 1):
        miss_gid = random_gid()

        step = await probe(client, miss_gid)
        step["label"] = f"MISS_{i}"
        steps.append(step)
        print(f"  [{len(steps)}] MISS_{i} ({miss_gid}): status={step['status']}")

        if step["status"] == 429:
            print("  → rate limited, ждём 3s...")
            await asyncio.sleep(3.0)

        await asyncio.sleep(0.4)

        # REF check
        step = await probe(client, REF_GID)
        step["label"] = f"REF_AFTER_MISS_{i}"
        steps.append(step)
        ok_str = "✓" if step.get("ok") else "✗"
        print(f"  [{len(steps)}] REF_AFTER_MISS_{i}: status={step['status']} {ok_str}")

        if not step.get("ok"):
            blind_at = i
            print(f"\n  ⚠ ОСЛЕПЛЕНИЕ после {i} miss(ов) с GUESTKEY_A!")
            break
    else:
        print(f"\n  ✓ Не ослепло после {max_misses} miss. Защита не обнаружена или порог выше.")

    report["blind_after_misses"] = blind_at

    # ── Фаза B: НОВЫЙ GUESTKEY (тот же Worker) ──
    print("\n  ── Фаза B: новый GUESTKEY, тот же Worker ──")
    await client.aclose()
    await asyncio.sleep(0.5)

    guestkey_b = random_guestkey()
    print(f"  GUESTKEY_B = {guestkey_b[:16]}...")
    fresh_client = make_worker_client(guestkey=guestkey_b)

    step = await probe(fresh_client, REF_GID)
    step["label"] = "REF_NEW_GUESTKEY"
    step["guestkey"] = guestkey_b[:16]
    steps.append(step)
    print(f"  [{len(steps)}] REF_NEW_GUESTKEY: status={step['status']}, ok={step.get('ok')}")

    report["phase_b"] = {"status": step["status"], "ok": step.get("ok")}

    if step.get("ok"):
        report["verdict"] = "BAN_BY_COOKIE_SESSION"
        print("\n  ✅ Вердикт: БАН ПО COOKIE-СЕССИИ (новый GUESTKEY → ref снова 200)")
        await fresh_client.aclose()
        return report

    print(f"\n  → Бан НЕ по cookie. Проверяем другие гипотезы...")
    await fresh_client.aclose()
    await asyncio.sleep(0.5)

    # ── Фаза C: ДРУГОЙ Worker (из того же конфига) + новый GUESTKEY ──
    second_worker = os.environ.get("CF_WORKER_URL_2", "")
    if second_worker:
        print(f"\n  ── Фаза C: другой Worker ──")
        guestkey_c = random_guestkey()
        print(f"  Worker: {second_worker}")
        cl3 = make_worker_client(guestkey=guestkey_c)
        step3 = await probe(cl3, REF_GID)
        step3["label"] = "REF_OTHER_WORKER"
        step3["worker"] = second_worker
        step3["guestkey"] = guestkey_c[:16]
        steps.append(step3)
        print(f"  [{len(steps)}] REF_OTHER_WORKER: status={step3['status']}, ok={step3.get('ok')}")
        report["phase_c"] = {"worker": second_worker, "status": step3["status"], "ok": step3.get("ok")}
        await cl3.aclose()

        if step3.get("ok"):
            report["verdict"] = "BAN_BY_SINGLE_WORKER_IP"
            print("\n  ✅ Вердикт: БАН ПО IP КОНКРЕТНОГО WORKER (другой Worker → ref 200)")
            return report

    # ── Фаза D: ждём cooldown, проверяем исходный Worker ──
    cooldown = 120
    print(f"\n  ── Фаза D: ждём {cooldown}s (IP_BAN_COOLDOWN) ──")
    for remaining in range(cooldown, 0, -15):
        print(f"  ... {remaining}s", end="\r")
        await asyncio.sleep(15)
    print(f"  ... 0s  ")

    guestkey_d = random_guestkey()
    cl4 = make_worker_client(guestkey=guestkey_d)
    step4 = await probe(cl4, REF_GID)
    step4["label"] = "REF_AFTER_COOLDOWN"
    step4["guestkey"] = guestkey_d[:16]
    steps.append(step4)
    print(f"  [{len(steps)}] REF_AFTER_COOLDOWN: status={step4['status']}, ok={step4.get('ok')}")
    report["phase_d"] = {"cooldown_s": cooldown, "status": step4["status"], "ok": step4.get("ok")}
    await cl4.aclose()

    if step4.get("ok"):
        report["verdict"] = "BAN_BY_IP_TEMPORARY"
        print(f"\n  ✅ Вердикт: ВРЕМЕННЫЙ БАН ПО IP ({cooldown}s cooldown → ref снова 200)")
    else:
        report["verdict"] = "BAN_BY_CF_RANGE_WAVE"
        print(f"\n  ❌ Вердикт: БАН CF-ДИАПАЗОНА ВОЛНОЙ (cooldown {cooldown}s не помог, другие Workers тоже)")

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Эксперимент #2: cookie-гипотеза через CF Worker")
    parser.add_argument("--max-misses", type=int, default=10)
    parser.add_argument("--output", "-o", default="blind_cf_result.json")
    args = parser.parse_args()

    if not CF_SECRET:
        print("❌ Нужен CF_SECRET.", file=sys.stderr)
        print("   export CF_SECRET='...'", file=sys.stderr)
        print("   export CF_WORKER_URL='https://...'  # опционально", file=sys.stderr)
        sys.exit(1)

    print(f"Worker: {CF_WORKER_URL}")
    print(f"REF:    {REF_GID}")
    print(f"Макс. miss: {args.max_misses}")
    print()

    report = asyncio.run(run_experiment(max_misses=args.max_misses))

    out_path = Path(args.output)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nПолный результат: {out_path}")


if __name__ == "__main__":
    main()
