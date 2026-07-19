#!/usr/bin/env python3
"""
fix_cf_worker.py — заменяет _check_gallery_api на HTML-based проверку
для работы через CF Worker (который не может ходить в /json).
"""
import sys
from pathlib import Path

SCANNER = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("pentest_site_gallery_scanner.py")
BAK = SCANNER.with_suffix(".py.cf_bak")
content = SCANNER.read_text(encoding="utf-8")
BAK.write_text(content, encoding="utf-8")
print(f"Backup: {BAK}")

# ===== Шаг 1: добавляем _check_gallery_html перед _check_gallery_api =====
html_func = '''
async def _check_gallery_html(gid: str, client: httpx.AsyncClient) -> tuple[bool, int]:
    """Проверка через HTML-страницу галереи (для CF Workers и прямого доступа).
    Быстрее чем парсить JSON, работает когда /json заблокирован."""
    try:
        url = f"{TARGET_SCHEME}://{TARGET_HOST}/gallery/{gid}"
        r = await client.get(url)
        status = r.status_code
        if status == 429:
            return False, 429
        if status != 200:
            return False, status
        text = r.text
        # Признаки НЕсуществующей галереи
        if "Content doesn\\'t exist" in text:
            return False, 404
        if "<title>Postimages — free image hosting</title>" in text and "image_url" not in text:
            return False, 404
        # Признаки существующей: image_url в JS, meta og:image, или gallery в URL
        if "image_url" in text or 'og:image' in text or f"/gallery/{gid}" in text:
            return True, status
        # Эвристика: если страница не пустая и нет явных признаков 404
        if len(text) > 500:
            return True, status
        return False, status
    except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError):
        return False, -1
    except Exception:
        return False, -1


'''

# Вставляем перед _check_gallery_api
marker = "async def _check_gallery_api(gid: str, client: httpx.AsyncClient) -> tuple[bool, int]:"
assert marker in content, "Cannot find _check_gallery_api"
content = content.replace(marker, html_func + marker, 1)
print("+ Added _check_gallery_html()")

# ===== Шаг 2: заменяем вызовы _check_gallery_api на _check_gallery_html в probe =====
# В функции _probe есть строка:
#   ok, status = await _check_gallery_api(gid, client)
# Заменяем ВСЕ вхождения
old_call = "await _check_gallery_api(gid, client)"
new_call = "await _check_gallery_html(gid, client)"
count = content.count(old_call)
content = content.replace(old_call, new_call)
print(f"+ Replaced {count} calls: _check_gallery_api → _check_gallery_html")

# ===== Шаг 3: в ref-проверках тоже меняем =====
# _check_gallery_api используется также в check_reference и других местах
# где gid передан как параметр
old_call2 = "await _check_gallery_api(ref_gid,"
new_call2 = "await _check_gallery_html(ref_gid,"
count2 = content.count(old_call2)
content = content.replace(old_call2, new_call2)
print(f"+ Replaced {count2} ref calls")

# Сохраняем
SCANNER.write_text(content, encoding="utf-8")

# Проверяем компиляцию
import py_compile
try:
    py_compile.compile(str(SCANNER), doraise=True)
    print("✓ Compiles OK")
except py_compile.PyCompileError as e:
    print(f"✗ COMPILE ERROR: {e}")
    BAK.rename(SCANNER)
    print("Restored from backup")
    sys.exit(1)

print(f"✓ Done. Run scanner with --cloudflare-worker-url to use HTML checks")
