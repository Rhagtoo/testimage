#!/usr/bin/env python3
"""Добавляет HTML-fallback в _check_gallery_api когда JSON API возвращает 403."""

import sys
from pathlib import Path

SCANNER = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("pentest_site_gallery_scanner.py")
content = SCANNER.read_text()

# Ищем функцию _check_gallery_api и добавляем fallback в неё
# Находим конец тела функции (последний return перед пустой строкой)

old = '''async def _check_gallery_api(gid: str, client: httpx.AsyncClient) -> tuple[bool, int]:
    """PENTEST_SITE JSON API: 200+images=found, 404=miss (как gallery-dl)."""
    params = {"action": "list", "page": 1, "album": gid}
    for attempt in range(3):
        try:
            r = await client.get(PENTEST_SITE_JSON_URL, params=params)
            if r.status_code == 429:
                await asyncio.sleep(min((attempt + 1) * 3.0, 15.0))
                continue
            if r.status_code in (403, 404):
                return False, r.status_code
            if r.status_code != 200:
                continue
            try:
                data = r.json()
            except Exception:
                continue
            images = data.get("images") or []
            if images:
                return True, r.status_code
            return False, r.status_code
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError):
            continue
        except Exception:
            return False, -1
    return False, -1'''

# Проверяем что old существует единожды
count = content.count(old)
if count == 0:
    print("ERROR: _check_gallery_api not found!")
    # Попробуем найти функцию
    idx = content.find("async def _check_gallery_api")
    if idx >= 0:
        snippet = content[idx:idx+200]
        print(f"Found at offset {idx}:")
        print(repr(snippet[:200]))
    sys.exit(1)
elif count > 1:
    print(f"ERROR: {count} matches (expected 1)")
    sys.exit(1)

new = '''async def _check_gallery_api(gid: str, client: httpx.AsyncClient) -> tuple[bool, int]:
    """PENTEST_SITE JSON API: 200+images=found, 404=miss (как gallery-dl).
    При 403 — fallback на HTML-страницу галереи (для CF Workers)."""
    params = {"action": "list", "page": 1, "album": gid}
    for attempt in range(3):
        try:
            r = await client.get(PENTEST_SITE_JSON_URL, params=params)
            if r.status_code == 429:
                await asyncio.sleep(min((attempt + 1) * 3.0, 15.0))
                continue
            if r.status_code == 403:
                # HTML fallback: CF Workers не могут ходить в /json
                return await _check_gallery_page(gid, client)
            if r.status_code == 404:
                return False, r.status_code
            if r.status_code != 200:
                continue
            try:
                data = r.json()
            except Exception:
                continue
            images = data.get("images") or []
            if images:
                return True, r.status_code
            return False, r.status_code
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError):
            continue
        except Exception:
            return False, -1
    return False, -1


async def _check_gallery_page(gid: str, client: httpx.AsyncClient) -> tuple[bool, int]:
    """Проверка через HTML-страницу галереи (fallback когда /json заблокирован).
    Ищем image_url в JavaScript шаблоне."""
    try:
        url = f"{TARGET_SCHEME}://{TARGET_HOST}/gallery/{gid}"
        r = await client.get(url)
        if r.status_code != 200:
            return False, r.status_code
        text = r.text
        # Признаки существующей галереи:
        # - "image_url" в JS
        # - НЕ "Content doesn't exist"
        # - НЕ "Режим не поддерживается"
        if "Content doesn't exist" in text:
            return False, 404
        if "Режим не поддерживается" in text and "image_url" not in text:
            return False, 404
        if "image_url" in text or "/gallery/" + gid in text:
            return True, r.status_code
        # Если страница загрузилась но контент неясен — считаем что галерея есть
        return "postimg" in text.lower() or "pentest_site" in text.lower(), r.status_code
    except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError):
        return False, -1
    except Exception:
        return False, -1'''

# Применяем
if old in content:
    content = content.replace(old, new, 1)
    SCANNER.write_text(content)
    print("✓ HTML fallback added to _check_gallery_api")
else:
    print("ERROR: pattern not matched")
    sys.exit(1)
