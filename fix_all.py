#!/usr/bin/env python3
"""fix_all.py — все правки одним скриптом."""
from pathlib import Path

SCANNER = Path("pentest_site_gallery_scanner.py")
BAK = SCANNER.with_suffix(".py.bak2")
orig = SCANNER.read_text()
BAK.write_text(orig)
print(f"Backup: {BAK}")
content = orig

# 1. Добавить _check_gallery_html
html_func = '''
async def _check_gallery_html(gid: str, client: httpx.AsyncClient) -> tuple[bool, int]:
    """Проверка через HTML-страницу (для CF Worker, где /json заблокирован)."""
    try:
        base = PING_URL.rstrip("/") if PING_URL != _DEFAULT_PING_URL else f"{TARGET_SCHEME}://{TARGET_HOST}"
        url = f"{base}/gallery/{gid}"
        r = await client.get(url)
        if r.status_code == 429:
            return False, 429
        if r.status_code != 200:
            return False, r.status_code
        text = r.text
        if "Content doesn\\'t exist" in text:
            return False, 404
        if "image_url" in text or "og:image" in text:
            return True, r.status_code
        if len(text) > 800:
            return True, r.status_code
        return False, r.status_code
    except Exception:
        return False, -1


'''
marker = "async def _check_gallery_api(gid: str, client: httpx.AsyncClient) -> tuple[bool, int]:"
content = content.replace(marker, html_func + marker, 1)
print("+ Added _check_gallery_html()")

# 2. Заменить вызовы _check_gallery_api → _check_gallery_html в probe
for old_call in ["await _check_gallery_api(gid, client)", "await _check_gallery_api(ref_gid,"]:
    cnt = content.count(old_call)
    if cnt:
        new_call = old_call.replace("_check_gallery_api", "_check_gallery_html")
        content = content.replace(old_call, new_call)
        print(f"+ Replaced {cnt} calls: {old_call[:40]}...")

# 3. Добавить X-Key в BASE_HEADERS при использовании CF Worker
old_log = 'log.info("Using Cloudflare Worker API: %s", cf_worker_url)'
new_log = '''log.info("Using Cloudflare Worker API: %s", cf_worker_url)
    BASE_HEADERS["X-Key"] = cf_worker_key'''
if old_log in content:
    content = content.replace(old_log, new_log, 1)
    print("+ X-Key added to BASE_HEADERS")
else:
    # Может уже применено
    if 'BASE_HEADERS["X-Key"]' in content:
        print("+ X-Key already in BASE_HEADERS")
    else:
        print("! WARNING: X-Key log line not found")

SCANNER.write_text(content)

import py_compile
try:
    py_compile.compile(str(SCANNER), doraise=True)
    print("✓ Compiles OK")
except py_compile.PyCompileError as e:
    print(f"✗ ERROR: {e}")
    BAK.rename(SCANNER)
