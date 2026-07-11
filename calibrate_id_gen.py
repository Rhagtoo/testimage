#!/usr/bin/env python3
"""
Калибровка ID-генератора: создаём галереи с паузами и записываем (timestamp, id).

Использование:
  1. Скопируй cookies из браузера (F12 → Application → Cookies → postimg.cc)
  2. Передай как cookie-строку:
     python3 calibrate_id_gen.py --cookies "PHPSESSID=...; GUESTKEY=...; ..."
  
  3. Или через файл:
     python3 calibrate_id_gen.py --cookies-file cookies.txt
  
  4. Или через Chrome CDP (автоматически извлечёт cookies из запущенного Chrome):
     python3 calibrate_id_gen.py --chrome

Режимы:
  --mode create   — создаёт N пустых галерей (без заливки) через веб-интерфейс
  --mode analyze  — только анализирует существующий лог
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

TARGET = "https://postimg.cc"
TARGET_ORG = "https://postimages.org"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)

LOG_FILE = Path("id_calibration.jsonl")
DEFAULT_COUNT = 20
DEFAULT_PAUSE = 2.0  # секунд между созданиями


# ── cookie helpers ───────────────────────────────────────────────
def parse_cookies(raw: str) -> dict[str, str]:
    """Парсит cookie-строку или Netscape-формат."""
    cookies: dict[str, str] = {}
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Netscape format: domain flag path secure expires name value
        if "\t" in line:
            parts = line.split("\t")
            if len(parts) >= 7:
                cookies[parts[5]] = parts[6]
            continue
        # Standard format: name=value; name2=value2
        for part in line.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                cookies[k.strip()] = v.strip()
    return cookies


async def extract_chrome_cookies(port: int = 9222) -> dict[str, str]:
    """Извлекает cookies из Chrome через CDP."""
    import websockets  # optional dependency

    cl = httpx.AsyncClient(timeout=httpx.Timeout(5.0))
    try:
        r = await cl.get(f"http://127.0.0.1:{port}/json")
        tabs = r.json()
    finally:
        await cl.aclose()

    postimg_tab = None
    for tab in tabs:
        url = tab.get("url", "")
        if "postimg.cc" in url or "postimages.org" in url:
            postimg_tab = tab
            break

    if not postimg_tab:
        raise RuntimeError(f"Не найдена вкладка postimg.cc (Chrome CDP port {port})")

    ws_url = postimg_tab["webSocketDebuggerUrl"]
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Network.getCookies"}))
        resp = json.loads(await ws.recv())
        cookies_raw = resp.get("result", {}).get("cookies", [])
        return {c["name"]: c["value"] for c in cookies_raw}


# ── HTTP helpers ─────────────────────────────────────────────────
def make_client(cookies: dict[str, str]) -> httpx.AsyncClient:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Origin": TARGET_ORG,
        "Referer": f"{TARGET}/files",
    }
    return httpx.AsyncClient(
        headers=headers,
        cookies=httpx.Cookies(cookies),
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=True,
        http2=False,
    )


async def create_gallery(client: httpx.AsyncClient, name: str = "") -> dict:
    """
    Создаёт пустую галерею через POST на /files (веб-интерфейс).
    Возвращает {success, gallery_id, gallery_url, error}.
    """
    t0 = time.time()
    result = {"ts": t0, "ts_iso": datetime.fromtimestamp(t0, tz=timezone.utc).isoformat()}

    # Шаг 1: GET /files чтобы получить CSRF-токен (если есть)
    r = await client.get(f"{TARGET}/files")
    csrf_token = ""
    csrf_match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', r.text)
    if csrf_match:
        csrf_token = csrf_match.group(1)
    # Ищем скрытые поля формы
    form_data: dict[str, str] = {}
    if csrf_token:
        form_data["csrf_token"] = csrf_token
    form_data["gallery_name"] = name or f"calib_{int(t0)}"

    # Шаг 2: POST для создания галереи
    r = await client.post(f"{TARGET}/files", data=form_data)
    result["status"] = r.status_code
    result["url_after"] = str(r.url)

    # Извлекаем gallery ID из редиректа или HTML
    gid_match = re.search(r"/gallery/([A-Za-z0-9]{7})", str(r.url) + r.text)
    if gid_match:
        result["gallery_id"] = gid_match.group(1)
        result["success"] = True
    else:
        # Может быть API-ответ в JSON
        try:
            data = r.json()
            gid = data.get("gallery_id") or data.get("id") or data.get("gid")
            if gid:
                result["gallery_id"] = gid
                result["success"] = True
        except Exception:
            result["success"] = False
            result["error"] = "no_gallery_id_found"
            result["body_preview"] = r.text[:300]

    result["elapsed_ms"] = round((time.time() - t0) * 1000)
    return result


# ── main ────────────────────────────────────────────────────────
async def run_calibration(
    cookies: dict[str, str],
    count: int = DEFAULT_COUNT,
    pause: float = DEFAULT_PAUSE,
) -> list[dict]:
    results: list[dict] = []
    client = make_client(cookies)

    print(f"Создаю {count} галерей с паузой {pause}s...")
    print(f"Лог: {LOG_FILE.resolve()}")
    print()

    for i in range(1, count + 1):
        name = f"calib_{int(time.time())}"
        print(f"[{i}/{count}] Создание '{name}'...", end=" ", flush=True)

        r = await create_gallery(client, name=name)
        r["index"] = i

        if r.get("success"):
            print(f"✅ {r['gallery_id']} ({r['elapsed_ms']}ms)")
        else:
            print(f"❌ {r.get('error', '?')} (status={r.get('status')})")
            if "body_preview" in r:
                print(f"    body: {r['body_preview'][:120]}")

        results.append(r)

        # Сохраняем в лог
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

        if i < count:
            print(f"    пауза {pause}s...")
            await asyncio.sleep(pause)

    await client.aclose()
    return results


def analyze_results(results: list[dict]) -> None:
    """Анализирует калибровочные данные."""
    valid = [r for r in results if r.get("success") and r.get("gallery_id")]
    if len(valid) < 2:
        print(f"\n⚠ Недостаточно данных: {len(valid)} успешных созданий")
        return

    print(f"\n{'='*60}")
    print(f"Анализ {len(valid)} точек")
    print(f"{'='*60}")

    # Вычисляем интервалы между ID (в base62-единицах) и временем
    from datetime import datetime, timezone

    def gid_to_int(gid: str) -> int:
        """Base62 → int."""
        CHARSET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        base = len(CHARSET)
        val = 0
        for c in gid:
            val = val * base + CHARSET.index(c)
        return val

    intervals: list[dict] = []
    for i in range(1, len(valid)):
        prev = valid[i - 1]
        curr = valid[i]
        t1 = datetime.fromisoformat(prev["ts_iso"])
        t2 = datetime.fromisoformat(curr["ts_iso"])
        dt = (t2 - t1).total_seconds()
        id1 = gid_to_int(prev["gallery_id"])
        id2 = gid_to_int(curr["gallery_id"])
        did = id2 - id1
        rate = did / dt if dt > 0 else 0
        intervals.append({
            "from": prev["gallery_id"],
            "to": curr["gallery_id"],
            "dt_s": round(dt, 3),
            "delta_id": did,
            "rate_ids_per_s": round(rate, 6),
        })
        print(f"  {prev['gallery_id']} → {curr['gallery_id']}: "
              f"Δid={did:+,d}, Δt={dt:.1f}s, rate={rate:.2f} id/s")

    rates = [inv["rate_ids_per_s"] for inv in intervals]
    if rates:
        import statistics
        avg_rate = statistics.mean(rates)
        std_rate = statistics.stdev(rates) if len(rates) > 1 else 0
        print(f"\n  Средний rate: {avg_rate:.4f} id/s (±{std_rate:.4f})")
        print(f"  Ожидаемый ID через 60s:  +{int(avg_rate * 60):,}")
        print(f"  Ожидаемый ID через 300s: +{int(avg_rate * 300):,}")

        # Сохраняем сводку
        summary = {
            "points": len(valid),
            "avg_rate_ids_per_s": avg_rate,
            "std_rate_ids_per_s": std_rate,
            "intervals": intervals,
        }
        Path("id_calibration_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\nСводка сохранена: id_calibration_summary.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Калибровка ID-генератора postimg.cc")
    parser.add_argument("--cookies", help="Cookie-строка (name=value; ...)")
    parser.add_argument("--cookies-file", help="Файл с cookies (Netscape или key=value)")
    parser.add_argument("--chrome", type=int, nargs="?", const=9222, help="Извлечь cookies из Chrome CDP (порт)")
    parser.add_argument("--count", "-n", type=int, default=DEFAULT_COUNT, help=f"Количество галерей (default: {DEFAULT_COUNT})")
    parser.add_argument("--pause", "-p", type=float, default=DEFAULT_PAUSE, help=f"Пауза между созданиями, сек (default: {DEFAULT_PAUSE})")
    parser.add_argument("--analyze", action="store_true", help="Только проанализировать существующий лог")
    args = parser.parse_args()

    # ── режим анализа ──
    if args.analyze:
        if not LOG_FILE.exists():
            print(f"❌ Лог {LOG_FILE} не найден")
            sys.exit(1)
        results = [json.loads(line) for line in LOG_FILE.read_text(encoding="utf-8").strip().splitlines() if line.strip()]
        analyze_results(results)
        return

    # ── получение cookies ──
    cookies: dict[str, str] = {}
    if args.cookies:
        cookies = parse_cookies(args.cookies)
    elif args.cookies_file:
        cookies = parse_cookies(Path(args.cookies_file).read_text(encoding="utf-8"))
    elif args.chrome:
        try:
            cookies = asyncio.run(extract_chrome_cookies(args.chrome))
        except ImportError:
            print("❌ Нужен модуль websockets: pip install websockets")
            sys.exit(1)
    else:
        print("❌ Укажи --cookies, --cookies-file или --chrome")
        sys.exit(1)

    if not cookies:
        print("❌ Не удалось извлечь cookies")
        sys.exit(1)

    print(f"Cookies: {len(cookies)} шт ({', '.join(list(cookies.keys())[:5])}...)")
    
    # Проверяем, что залогинены
    has_phpsessid = any("PHPSESSID" in k.upper() for k in cookies)
    if not has_phpsessid:
        print("⚠ Похоже, нет сессионной cookie (PHPSESSID). Создание галерей может не сработать.")

    print()
    results = asyncio.run(run_calibration(cookies, count=args.count, pause=args.pause))

    # ── анализ ──
    analyze_results(results)


if __name__ == "__main__":
    main()
