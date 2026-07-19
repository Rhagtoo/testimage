#!/usr/bin/env python3
"""
auto_upload_cdp.py — авто-заливка через открытый Chrome (CDP порт 19222)
Создаёт canvas-изображения и upload'ит их через fetch() из контекста браузера.
Cloudflare не блокирует — весь трафик идёт внутри браузера.

Usage:
  python3 auto_upload_cdp.py [--count 10] [--delay 1.0] [--cdp-port 19222] [--gallery-file gallery_ids.txt]
"""

import asyncio
import json
import sys
import time
import argparse
import httpx
import websockets
from dataclasses import dataclass, field
from typing import Optional

# Цвета для разнообразия canvas
COLORS = [
    (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
    (255, 0, 255), (0, 255, 255), (128, 0, 128), (255, 165, 0),
    (0, 128, 0), (128, 0, 0), (0, 0, 128), (128, 128, 0),
    (255, 192, 203), (0, 255, 127), (100, 149, 237),
]

def _make_upload_js(label, guestkey, w=200, h=200, r=255, g=0, b=0):
    """Generate JS for canvas upload. No .format() — uses string concatenation."""
    return f"""(async() => {{
    const canvas = document.createElement('canvas');
    canvas.width = {w}; canvas.height = {h};
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = 'rgb({r},{g},{b})';
    ctx.fillRect(0, 0, {w}, {h});
    ctx.fillStyle = '#fff';
    ctx.font = '20px monospace';
    ctx.fillText('{label}', 10, 30);

    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));

    const formData = new FormData();
    formData.append('source', blob, 'img_{label}.png');
    formData.append('action', 'upload');
    formData.append('key', '{guestkey}');
    formData.append('format', 'json');

    const resp = await fetch('/json', {{
        method: 'POST',
        body: formData,
        headers: {{ 'X-Requested-With': 'XMLHttpRequest' }}
    }});
    const data = await resp.json();
    const galleryId = data.gallery || data.id || (data.url && data.url.split('/').slice(-2)[0]) || JSON.stringify(data);
    return {{ galleryId, status: resp.status, data }};
}})()"""

GALLERY_ADD_JS = """(async() => {{
    const formData = new FormData();
    formData.append('gallery', '{gallery}');
    formData.append('optsize', '0');
    formData.append('expire', '0');
    formData.append('url', '{image_url}');
    formData.append('numfiles', '{numfiles}');
    formData.append('upload_session', '{upload_session}');

    const resp = await fetch('/json', {{
        method: 'POST',
        body: formData,
        headers: {{ 'X-Requested-With': 'XMLHttpRequest' }}
    }});
    const text = await resp.text();
    return {{ status: resp.status, body: text }};
}})()"""


@dataclass
class UploadResult:
    gallery_id: Optional[str] = None
    image_url: Optional[str] = None
    status: int = 0
    error: Optional[str] = None
    label: str = ""

    def ok(self) -> bool:
        return self.gallery_id is not None and self.status == 200


class ChromeUploader:
    def __init__(self, cdp_port: int = 19222, guestkey: str = "3877d5eb451bfe6ec8060544192bab23"):
        self.cdp_url = f"http://127.0.0.1:{cdp_port}"
        self.guestkey = guestkey
        self.gallery_ids: list[str] = []
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._next_id = 1

    async def connect(self) -> bool:
        """Подключаемся к Chrome CDP, создаём вкладку на postimg.cc."""
        try:
            r = httpx.get(f"{self.cdp_url}/json/version", timeout=5)
            version = r.json()
            print(f"Chrome: {version.get('Browser', '?')}")
        except Exception as e:
            print(f"Cannot connect to Chrome CDP on {self.cdp_url}: {e}")
            return False

        # Проверяем есть ли уже вкладка с postimg.cc
        r = httpx.get(f"{self.cdp_url}/json", timeout=5)
        tabs = r.json()
        target = None
        for t in tabs:
            url = t.get("url", "")
            if "postimg.cc" in url or "postimages.org" in url:
                if "devtools" not in url:
                    target = t
                    print(f"Found existing tab: {url[:60]}")
                    break

        if not target:
            # Создаём новую вкладку
            r = httpx.put(f"{self.cdp_url}/json/new?https://postimg.cc/", timeout=10)
            target = r.json()
            print(f"New tab: {target.get('id', '?')[:20]}")

        ws_url = target["webSocketDebuggerUrl"].replace("localhost", "127.0.0.1")

        try:
            self.ws = await websockets.connect(ws_url, max_size=10_000_000, ping_interval=30)
            print(f"Connected to: {target.get('title', 'tab')[:40]}")
            return True
        except Exception as e:
            print(f"WebSocket error: {e}")
            return False

    async def _send_cmd(self, method: str, params: dict = None) -> dict:
        cmd_id = self._next_id
        self._next_id += 1
        msg = {"id": cmd_id, "method": method}
        if params:
            msg["params"] = params
        await self.ws.send(json.dumps(msg))

        # Читаем ответ (пропускаем сетевые события)
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=30)
            data = json.loads(raw)
            if data.get("id") == cmd_id:
                return data.get("result", {})
            # Игнорируем другие события

    async def navigate(self, url: str = "https://postimg.cc/"):
        """Навигация на страницу."""
        await self._send_cmd("Page.navigate", {"url": url})
        await asyncio.sleep(2)  # ждём загрузки

    async def execute_js(self, expression: str) -> dict:
        """Выполняет JS в контексте страницы."""
        result = await self._send_cmd("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })
        return result

    async def upload_one(self, label: str, w: int = 200, h: int = 200) -> UploadResult:
        """Создаёт canvas + upload через fetch."""
        color_idx = hash(label) % len(COLORS)
        r, g, b = COLORS[color_idx]

        js = _make_upload_js(label=label, guestkey=self.guestkey, w=w, h=h, r=r, g=g, b=b)

        try:
            result = await self.execute_js(js)
            value = result.get("result", {}).get("value", {})
            if not value:
                # Может быть exception
                exc = result.get("exceptionDetails", {})
                err_text = exc.get("text", "?") if exc else "no value"
                return UploadResult(error=str(err_text)[:200], label=label, status=-1)

            gallery_id = value.get("galleryId", "")
            status = value.get("status", 0)
            data = value.get("data", {})

            # galleryId может быть JSON строкой (в случае ошибки)
            if gallery_id.startswith("{"):
                gallery_id = ""

            if not gallery_id and isinstance(data, dict):
                gallery_id = data.get("gallery", data.get("id", ""))
                # Может быть URL
                if not gallery_id and "url" in data:
                    parts = data["url"].split("/")
                    if len(parts) >= 2:
                        gallery_id = parts[-2]

            return UploadResult(
                gallery_id=gallery_id if gallery_id else None,
                image_url=data.get("url") if isinstance(data, dict) else None,
                status=status,
                label=label,
            )
        except Exception as e:
            return UploadResult(error=str(e), label=label, status=-1)

    async def upload_batch(self, count: int, delay: float = 1.0) -> list[UploadResult]:
        """Загружает count изображений."""
        results: list[UploadResult] = []

        # Навигация на postimg.cc (нужно для кук)
        await self.navigate("https://postimg.cc/")

        print(f"Uploading {count} images with {delay}s delay...")
        for i in range(count):
            label = f"img{i:03d}_{int(time.time())}"
            result = await self.upload_one(label)
            results.append(result)

            if result.ok():
                self.gallery_ids.append(result.gallery_id)
                print(f"  [{i+1}/{count}] ✓ {result.gallery_id} ({result.image_url or 'no url'})")
            else:
                print(f"  [{i+1}/{count}] ✗ {result.error or 'unknown error'} (status={result.status})")

            if i < count - 1 and delay > 0:
                await asyncio.sleep(delay)

        return results

    async def close(self):
        if self.ws:
            await self.ws.close()

    async def __aenter__(self):
        ok = await self.connect()
        if not ok:
            raise RuntimeError("CDP connection failed")
        return self

    async def __aexit__(self, *args):
        await self.close()


async def main():
    parser = argparse.ArgumentParser(description="Auto-upload via Chrome CDP")
    parser.add_argument("--count", type=int, default=10, help="Number of uploads (default: 10)")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between uploads in seconds (default: 1.0)")
    parser.add_argument("--cdp-port", type=int, default=19222, help="Chrome remote debugging port (default: 19222)")
    parser.add_argument("--gallery-file", type=str, default="generated_gallery_ids.txt", help="Output file for gallery IDs")
    parser.add_argument("--guestkey", type=str, default="3877d5eb451bfe6ec8060544192bab23", help="GUESTKEY")
    args = parser.parse_args()

    async with ChromeUploader(cdp_port=args.cdp_port, guestkey=args.guestkey) as uploader:
        results = await uploader.upload_batch(count=args.count, delay=args.delay)

        # Сортируем: успешные сверху
        ok_results = [r for r in results if r.ok()]
        fail_results = [r for r in results if not r.ok()]

        print(f"\n{'='*50}")
        print(f"Done: {len(ok_results)}/{len(results)} succeeded")
        print(f"{'='*50}")

        # Сохраняем ID
        with open(args.gallery_file, "w") as f:
            for r in ok_results:
                f.write(f"{r.gallery_id}\n")
                print(f"  {r.gallery_id}")

        if ok_results:
            print(f"\nSaved {len(ok_results)} IDs to {args.gallery_file}")


if __name__ == "__main__":
    asyncio.run(main())
