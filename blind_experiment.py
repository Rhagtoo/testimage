#!/usr/bin/env python3
"""
Эксперимент: бан по IP или по HTTP-сессии?
============================================
Гипотеза: после серии 404 целевой сайт «ослепляет» источник.
Вопрос: бан привязан к IP (смена прокси помогает) или к HTTP-сессии
(cookies/keep-alive/TLS — тогда достаточно нового клиента)?

Протокол:
  1. REF (200 ожидаем) — baseline
  2. MISS #1 (404 ожидаем)
  3. REF → ? (всё ещё 200 или уже ослеп?)
  4. MISS #2
  5. REF → ?
  6. MISS #3
  7. REF → ? (момент срабатывания защиты)
  8. Если ослеп — закрываем клиент, создаём НОВЫЙ без cookies
  9. REF → ? (200 = бан по сессии, 404 = бан по IP)

Вывод: JSON с результатами каждого шага.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import string
import sys
import time
from pathlib import Path

import httpx

# ── config ──────────────────────────────────────────────────────
TARGET_HOST = "pentest_site.com"
TARGET_SCHEME = "https"
JSON_URL = f"{TARGET_SCHEME}://{TARGET_HOST}/json"
REF_GID = "y3tXqH0"  # эталонная галерея (должна существовать)
CHARSET = string.ascii_letters + string.digits
ID_LENGTH = 7
REQUEST_TIMEOUT = 12.0

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)

BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json, text/plain, */*",
    "Referer": f"{TARGET_SCHEME}://{TARGET_HOST}/",
}


# ── helpers ─────────────────────────────────────────────────────
def random_gid() -> str:
    return "".join(random.choices(CHARSET, k=ID_LENGTH))


def make_client(proxy: str | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        proxy=proxy,
        headers=BASE_HEADERS,
        timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=5.0),
        follow_redirects=True,
        http2=False,
    )


async def probe(client: httpx.AsyncClient, gid: str) -> dict:
    """Проверяет галерею через JSON API. Возвращает {status, ok, error, images_count}."""
    params = {"action": "list", "page": 1, "album": gid}
    t0 = time.monotonic()
    try:
        r = await client.get(JSON_URL, params=params)
        elapsed = time.monotonic() - t0
        result = {
            "gid": gid,
            "status": r.status_code,
            "elapsed_ms": round(elapsed * 1000),
            "headers": dict(r.headers),
        }
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
        elif r.status_code == 429:
            result["ok"] = False
            result["error"] = "429_rate_limited"
        else:
            result["ok"] = False
            result["error"] = f"status_{r.status_code}"
        return result
    except httpx.TimeoutException:
        return {"gid": gid, "status": -1, "ok": False, "error": "timeout", "elapsed_ms": round((time.monotonic() - t0) * 1000)}
    except Exception as exc:
        return {"gid": gid, "status": -2, "ok": False, "error": f"exception:{type(exc).__name__}", "elapsed_ms": round((time.monotonic() - t0) * 1000)}


def summarize(steps: list[dict]) -> dict:
    """Сводка: на каком шаге и после скольких miss произошло ослепление."""
    ref_ok_count = 0
    miss_count = 0
    blind_at_miss = None

    for i, step in enumerate(steps):
        label = step.get("label", "")
        gid = step.get("gid", "")
        ok = step.get("ok", False)
        if label == "REF":
            if ok:
                ref_ok_count += 1
            else:
                blind_at_miss = miss_count
                break
        elif label.startswith("MISS"):
            miss_count += 1

    return {
        "total_steps": len(steps),
        "misses_before_blind": blind_at_miss,
        "ref_checks_passed": ref_ok_count,
    }


# ── main ────────────────────────────────────────────────────────
async def run_experiment(
    proxy: str | None = None,
    max_misses: int = 10,
) -> dict:
    steps: list[dict] = []
    report: dict = {
        "proxy": proxy or "direct",
        "ref_gid": REF_GID,
        "max_misses": max_misses,
        "steps": steps,
    }

    # ── фаза 1: REF → MISS → REF → ... до ослепления ──
    client = make_client(proxy)
    miss_gids: list[str] = []

    # baseline REF
    step = await probe(client, REF_GID)
    step["label"] = "REF_BASELINE"
    steps.append(step)
    print(f"  [1] REF_BASELINE: status={step['status']}, ok={step['ok']}")

    if not step["ok"]:
        # референс мёртв с самого начала — нечего тестировать
        await client.aclose()
        report["error"] = "ref_dead_at_start"
        return report

    # цикл MISS → REF
    for i in range(1, max_misses + 1):
        miss_gid = random_gid()
        miss_gids.append(miss_gid)

        # MISS
        step = await probe(client, miss_gid)
        step["label"] = f"MISS_{i}"
        steps.append(step)
        expected = "404" if step["status"] == 404 else f"{step['status']}"
        print(f"  [{len(steps)}] MISS_{i} ({miss_gid}): status={step['status']} (expect 404)")

        if step["status"] == 429:
            print("  → rate limited, ждём 2s...")
            await asyncio.sleep(2.0)

        # Небольшая пауза между запросами (реалистичный сценарий)
        await asyncio.sleep(0.3)

        # REF после MISS
        step = await probe(client, REF_GID)
        step["label"] = f"REF_AFTER_MISS_{i}"
        steps.append(step)
        print(f"  [{len(steps)}] REF_AFTER_MISS_{i}: status={step['status']}, ok={step['ok']}")

        if not step["ok"]:
            print(f"\n  ⚠ ОСЛЕПЛЕНИЕ после {i} miss(ов)!")
            report["blind_after_misses"] = i
            break
    else:
        print(f"\n  ✓ Не ослепло после {max_misses} miss — защита не обнаружена")
        report["blind_after_misses"] = None

    # ── фаза 2: новый клиент (без cookies/keep-alive) ──
    print("\n  ── Фаза 2: новый AsyncClient (без cookies) ──")
    await client.aclose()
    await asyncio.sleep(0.5)

    fresh_client = make_client(proxy)
    step = await probe(fresh_client, REF_GID)
    step["label"] = "REF_FRESH_CLIENT"
    steps.append(step)
    print(f"  [{len(steps)}] REF_FRESH_CLIENT: status={step['status']}, ok={step['ok']}")

    if step["ok"]:
        report["verdict"] = "BAN_BY_SESSION"
        print("\n  ✅ Вердикт: БАН ПО HTTP-СЕССИИ (новый клиент → ref снова 200)")
    else:
        report["verdict"] = "BAN_BY_IP"
        print(f"\n  ❌ Вердикт: БАН ПО IP (новый клиент → ref всё ещё {step['status']})")

    await fresh_client.aclose()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Эксперимент: бан по IP или по сессии?")
    parser.add_argument("--proxy", help="SOCKS5 proxy URL (socks5://host:port)")
    parser.add_argument("--max-misses", type=int, default=10, help="Макс. miss-запросов (default: 10)")
    parser.add_argument("--output", "-o", default="blind_experiment.json", help="Файл результата (JSON)")
    args = parser.parse_args()

    print(f"Цель: {TARGET_SCHEME}://{TARGET_HOST}")
    print(f"REF:  {REF_GID}")
    print(f"Прокси: {args.proxy or 'direct'}")
    print(f"Макс. miss: {args.max_misses}")
    print()

    report = asyncio.run(run_experiment(proxy=args.proxy, max_misses=args.max_misses))

    # сохраняем
    out_path = Path(args.output)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nРезультат: {out_path}")

    # краткая сводка
    summary = summarize(report["steps"])
    print(f"Сводка: blind_after={report.get('blind_after_misses')}, verdict={report.get('verdict', 'N/A')}")


if __name__ == "__main__":
    main()
