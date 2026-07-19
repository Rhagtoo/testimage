#!/usr/bin/env python3
"""
auto_create_galleries_cdp.py — Создаёт галереи через Chrome CDP + файловый upload canvas'ов.
Полный цикл: создать галерею → загрузить фото → записать ID.

Использует CDP Page.navigate + Runtime.evaluate + DOM.setFileInputFiles.
"""
import asyncio
import json
import time
import argparse
import httpx
import websockets
import struct
import zlib
import tempfile
from pathlib import Path


def make_png(w=200, h=200, r=255, g=0, b=0):
    """Создаёт PNG в памяти."""
    raw = (b'\x00' + struct.pack('>3B', r, g, b)) * w
    raw_data = raw * h
    def chunk(ctype, data):
        c = ctype + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
    ihdr = struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(raw_data)) + chunk(b'IEND', b'')


class GalleryCreator:
    def __init__(self, cdp_port=19222):
        self.cdp = f"http://127.0.0.1:{cdp_port}"
        self.ws = None
        self._next_id = 1
        self.results = []

    async def connect(self):
        r = httpx.get(f"{self.cdp}/json/version", timeout=5)
        ver = r.json()
        print(f"Chrome: {ver.get('Browser', '?')}")

        # Создаём новую вкладку на postimg.cc
        r = httpx.put(f"{self.cdp}/json/new?https://postimg.cc/", timeout=10)
        tab = r.json()
        ws_url = tab["webSocketDebuggerUrl"].replace("localhost", "127.0.0.1")
        self.ws = await websockets.connect(ws_url, max_size=20_000_000, ping_interval=30)
        print(f"Tab: {tab.get('id', '?')[:20]} {tab.get('url', '?')[:50]}")
        await asyncio.sleep(2)  # ждём загрузки страницы
        return True

    async def _cdp(self, method, params=None):
        cid = self._next_id
        self._next_id += 1
        msg = {"id": cid, "method": method}
        if params: msg["params"] = params
        await self.ws.send(json.dumps(msg))
        while True:
            resp = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=30))
            if resp.get("id") == cid:
                return resp.get("result", {})

    async def navigate(self, url):
        await self._cdp("Page.navigate", {"url": url})
        await asyncio.sleep(2)

    async def eval_js(self, expr):
        result = await self._cdp("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": True,
        })
        exc = result.get("exceptionDetails")
        if exc:
            return None, exc.get("text", "?")
        return result.get("result", {}).get("value"), None

    async def create_one(self, index: int) -> str | None:
        """Создаёт галерею + загружает canvas."""
        label = f"auto{int(time.time())%100000}_{index}"
        color = [(255,0,0),(0,255,0),(0,0,255),(255,255,0),(255,0,255),
                 (0,255,255),(255,165,0),(128,0,128),(0,128,0),(255,192,203)][index%10]
        r, g, b = color

        # 1. Создать галерею
        create_js = f"""async()=>{{
            let f=new FormData();f.set('action','add');f.set('name','{label}');
            let r=await fetch('/json',{{method:'POST',body:f,headers:{{'X-Requested-With':'XMLHttpRequest'}}}});
            let d=await r.json();
            return (d.url_html||'').split('/').pop()||'';
        }}"""
        val, err = await self.eval_js(f"({create_js})()")
        if err:
            print(f"  [{index}] create error: {err[:80]}")
            return None
        gid = (val or "").strip()
        if not gid or len(gid) < 5:
            print(f"  [{index}] bad gid: {gid}")
            return None

        # 2. Перейти на страницу галереи и загрузить фото
        await self.navigate(f"https://postimg.cc/gallery/{gid}")
        await asyncio.sleep(1.5)

        # Создаём PNG через canvas
        png = make_png(200, 200, r, g, b)

        # 3. Ищем file input на странице и заполняем его
        # Используем DOM.getDocument для поиска input[type=file]
        doc = await self._cdp("DOM.getDocument", {"depth": -1})
        root = doc.get("root", {})

        # Ищем file input
        file_node = await self._cdp("DOM.querySelector", {
            "nodeId": root.get("nodeId"),
            "selector": "input[type=file]"
        })
        node_id = file_node.get("nodeId", 0)

        if node_id:
            # Сохраняем PNG во временный файл (CDP требует путь к файлу)
            tmp_path = Path(tempfile.gettempdir()) / f"upload_{index}.png"
            tmp_path.write_bytes(png)

            # Устанавливаем файл в input через CDP
            await self._cdp("DOM.setFileInputFiles", {
                "files": [str(tmp_path)],
                "nodeId": node_id,
            })

            # Ждём авто-upload
            await asyncio.sleep(2)

            # Проверяем результат — загрузилось ли фото
            check_js = """async()=>{
                let r=await fetch('/json?action=list&page=1&album='+window.location.pathname.split('/').pop());
                let d=await r.json();
                return (d.images||[]).length;
            }"""
            val2, err2 = await self.eval_js(f"({check_js})()")
            count = val2 if val2 is not None else 0
            print(f"  [{index}] {gid} ({count} photos)")
        else:
            # Нет file input — добавляем через URL как fallback
            print(f"  [{index}] {gid} (no file input, trying URL upload...)")
            add_js = f"""async()=>{{
                let f=new FormData();
                f.set('gallery','{gid}');f.set('optsize','0');f.set('expire','0');
                f.set('url','https://i.postimg.cc/ZY12yJ5h/photo-5305713284646378373-x.jpg');
                f.set('numfiles','1');
                f.set('upload_session',Date.now()+'.'+Math.random().toString(36).slice(2,18));
                let r=await fetch('https://postimages.org/json',{{method:'POST',body:f,
                    headers:{{'X-Requested-With':'XMLHttpRequest'}}}});
                return await r.json();
            }}"""
            val3, err3 = await self.eval_js(f"({add_js})()")
            print(f"  [{index}] URL upload: {json.dumps(val3)[:100]}")

        return gid

    async def run(self, count=10, delay=0.5):
        print(f"Creating {count} galleries...")
        for i in range(count):
            try:
                gid = await self.create_one(i)
                if gid:
                    self.results.append(gid)
            except Exception as e:
                print(f"  [{i}] ERROR: {e}")
            if i < count - 1 and delay > 0:
                await asyncio.sleep(delay)

        return self.results

    async def close(self):
        if self.ws:
            await self.ws.close()

    async def __aenter__(self): await self.connect(); return self
    async def __aexit__(self, *a): await self.close()


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--delay", type=float, default=1.0)
    p.add_argument("--cdp-port", type=int, default=19222)
    p.add_argument("--output", type=str, default="cdp_gallery_ids.txt")
    args = p.parse_args()

    async with GalleryCreator(cdp_port=args.cdp_port) as gc:
        results = await gc.run(count=args.count, delay=args.delay)

    print(f"\n{'='*40}")
    print(f"Created: {len(results)}/{args.count}")
    if results:
        Path(args.output).write_text("\n".join(results))
        print(f"Saved to {args.output}")
        for gid in results:
            print(f"  {gid}")


if __name__ == "__main__":
    asyncio.run(main())
