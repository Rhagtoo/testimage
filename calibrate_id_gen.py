#!/usr/bin/env python3
"""
Калибровка ID-генератора через Chrome CDP.
Создаёт пустые галереи с паузами, логирует (timestamp, gallery_id).

Требования:
  - Chrome запущен с --remote-debugging-port=9222
  - Открыта вкладка https://postimg.cc/files (залогиненный аккаунт)
  - pip install websockets httpx
"""

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOG_FILE = Path("id_calibration.jsonl")


# ── CDP helpers ─────────────────────────────────────────────────
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Users\Rhagtoo\AppData\Local\Google\Chrome\Application\chrome.exe",
]


def launch_chrome(port: int = 9222):
    """Запускает Chrome с CDP (отдельный профиль)."""
    import subprocess, socket

    # Найти chrome.exe
    exe = None
    for p in CHROME_PATHS:
        if Path(p).exists():
            exe = p
            break
    if not exe:
        print("\u274c Chrome не найден. Проверь CHROME_PATHS в скрипте.")
        return False

    # Проверить, не запущен ли уже
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=1)
        s.close()
        print(f"Chrome уже слушает порт {port}")
        return True
    except OSError:
        pass

    print(f"Запускаю Chrome ({exe}) на порту {port}...")
    profile = Path("chrome_cdp_profile")
    profile.mkdir(exist_ok=True)
    subprocess.Popen(
        [exe, f"--remote-debugging-port={port}", f"--user-data-dir={profile.resolve()}",
         "--no-first-run", "--no-default-browser-check", "https://postimg.cc/files"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    # Ждём готовности порта
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1)
            s.close()
            print(f"Chrome готов (порт {port})")
            print("   Залогинься в аккаунт в открывшемся окне, затем нажми Enter...")
            input()
            return True
        except OSError:
            time.sleep(0.5)
    print("Chrome не запустился")
    return False


def discover_tab(port: int = 9222):
    """Находит WebSocket URL вкладки postimg.cc через Chrome CDP."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as resp:
            tabs = json.loads(resp.read().decode())
    except Exception as e:
        raise RuntimeError(f"Chrome CDP недоступен на порту {port}: {e}")

    for tab in tabs:
        url = tab.get("url", "")
        if "postimg.cc" in url or "postimages.org" in url:
            return tab.get("webSocketDebuggerUrl"), url

    raise RuntimeError(
        f"Вкладка postimg.cc не найдена. Открой https://postimg.cc/files в Chrome.\n"
        f"Найдено вкладок: {len(tabs)}"
    )


async def cdp_send(ws, method: str, params: dict | None = None) -> dict:
    """Отправляет CDP-команду и ждёт ответ."""
    msg_id = int(time.time() * 1000) % 100000
    await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    while True:
        resp = json.loads(await ws.recv())
        if resp.get("id") == msg_id:
            return resp


async def create_gallery_cdp(ws_url: str, name: str) -> str | None:
    """
    Создаёт галерею через CDP (клик по форме на странице /files).
    Возвращает gallery_id или None.
    """
    import websockets

    async with websockets.connect(ws_url, max_size=2**24) as ws:
        # Узнаём текущие галереи на странице
        r = await cdp_send(ws, "Runtime.evaluate", {
            "expression": "JSON.stringify(Array.from(document.querySelectorAll('[data-gallery]')).map(el => el.dataset.gallery))",
            "returnByValue": True,
        })
        before_ids = set(json.loads(r["result"].get("result", {}).get("value", "[]")))

        # Заполняем поле и кликаем кнопку
        await cdp_send(ws, "Runtime.evaluate", {
            "expression": (
                f"var f=document.querySelector('form[name=\"addgallery\"]');"
                f"f.querySelector('input[name=\"gallery_name\"]').value='{name}';"
                f"f.querySelector('button[type=\"submit\"]').click();"
            ),
        })

        # Ждём появления новой галереи (polling)
        for _ in range(20):
            await asyncio.sleep(0.5)
            r = await cdp_send(ws, "Runtime.evaluate", {
                "expression": "JSON.stringify(Array.from(document.querySelectorAll('[data-gallery]')).map(el => el.dataset.gallery))",
                "returnByValue": True,
            })
            after_ids = set(json.loads(r["result"].get("result", {}).get("value", "[]")))
            new_ids = after_ids - before_ids
            if new_ids:
                return new_ids.pop()

        return None


# ── main ────────────────────────────────────────────────────────
async def run_calibration(port: int, count: int, pause: float):
    import websockets

    ws_url, tab_url = discover_tab(port)
    print(f"Вкладка: {tab_url}")
    print(f"Создаю {count} галерей с паузой {pause}s...\n")

    results = []
    for i in range(1, count + 1):
        t0 = time.time()
        name = f"calib_{int(t0)}"
        print(f"[{i}/{count}] '{name}'...", end=" ", flush=True)

        try:
            gid = await create_gallery_cdp(ws_url, name)
        except Exception as e:
            gid = None
            print(f"❌ CDP error: {e}")

        elapsed = time.time() - t0
        record = {
            "index": i,
            "ts": t0,
            "ts_iso": datetime.fromtimestamp(t0, tz=timezone.utc).isoformat(),
            "gallery_id": gid,
            "success": gid is not None,
            "elapsed_ms": round(elapsed * 1000),
        }

        if gid:
            print(f"✅ {gid} ({elapsed:.1f}s)")
        else:
            print(f"❌ Не создалась ({elapsed:.1f}s)")

        results.append(record)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

        if i < count and gid:
            await asyncio.sleep(pause)

    return results


def analyze_results(results: list[dict]):
    valid = [r for r in results if r.get("success") and r.get("gallery_id")]
    if len(valid) < 2:
        print(f"\n⚠ Мало данных: {len(valid)} точек")
        return

    CHARSET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    base = len(CHARSET)

    def gid_to_int(gid: str) -> int:
        val = 0
        for c in gid:
            val = val * base + CHARSET.index(c)
        return val

    print(f"\n{'='*60}")
    print(f"Анализ {len(valid)} точек")
    print(f"{'='*60}")

    import statistics
    intervals = []
    for i in range(1, len(valid)):
        a, b = valid[i - 1], valid[i]
        dt = (datetime.fromisoformat(b["ts_iso"]) - datetime.fromisoformat(a["ts_iso"])).total_seconds()
        did = gid_to_int(b["gallery_id"]) - gid_to_int(a["gallery_id"])
        rate = did / dt if dt > 0 else 0
        intervals.append({"from": a["gallery_id"], "to": b["gallery_id"], "dt_s": round(dt, 3), "delta_id": did, "rate": round(rate, 6)})
        print(f"  {a['gallery_id']} → {b['gallery_id']}: Δid={did:+,d}, Δt={dt:.1f}s, rate={rate:.2f} id/s")

    rates = [inv["rate"] for inv in intervals]
    avg = statistics.mean(rates)
    std = statistics.stdev(rates) if len(rates) > 1 else 0
    print(f"\n  Средний rate: {avg:.4f} id/s (±{std:.4f})")
    print(f"  +60s:  +{int(avg * 60):,}  |  +300s: +{int(avg * 300):,}")

    Path("id_calibration_summary.json").write_text(
        json.dumps({"points": len(valid), "avg_rate": avg, "std_rate": std, "intervals": intervals}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nСводка: id_calibration_summary.json")


def main():
    parser = argparse.ArgumentParser(description="Калибровка ID-генератора через Chrome CDP")
    parser.add_argument("--port", type=int, default=9222, help="Chrome CDP порт (default: 9222)")
    parser.add_argument("--launch", action="store_true", help="Авто-запуск Chrome с CDP")
    parser.add_argument("--count", "-n", type=int, default=20, help="Количество галерей")
    parser.add_argument("--pause", "-p", type=float, default=3.0, help="Пауза между созданиями, сек")
    parser.add_argument("--analyze", action="store_true", help="Только анализ существующего лога")
    args = parser.parse_args()

    if args.launch:
        if not launch_chrome(args.port):
            sys.exit(1)

    if args.analyze:
        if not LOG_FILE.exists():
            print(f"❌ Лог {LOG_FILE} не найден")
            sys.exit(1)
        results = [json.loads(line) for line in LOG_FILE.read_text(encoding="utf-8").strip().splitlines() if line.strip()]
        analyze_results(results)
        return

    results = asyncio.run(run_calibration(args.port, args.count, args.pause))
    analyze_results(results)


if __name__ == "__main__":
    main()
