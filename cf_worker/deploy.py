#!/usr/bin/env python3
"""Deploy pentest_site CF Workers via Cloudflare API (one or many scripts)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CF_DIR = Path(__file__).resolve().parent
WORKER_REF_JSON = ROOT / "worker_ref.json"
ACCOUNT_ID = "YOUR_CF_ACCOUNT_ID_OLD"
API = "https://api.cloudflare.com/client/v4"
DEFAULT_WORKERS = tuple(
    ["pentest_site-ref"] + [f"pentest_site-ref{i}" for i in range(2, 14)]
)


def worker_pool_names(n: int = 13) -> tuple[str, ...]:
    return tuple(["pentest_site-ref"] + [f"pentest_site-ref{i}" for i in range(2, n + 1)])


def cf_request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    token: str,
    **kwargs,
) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    r = client.request(method, f"{API}{path}", headers=headers, timeout=120.0, **kwargs)
    try:
        data = r.json()
    except Exception:
        r.raise_for_status()
        raise
    if not data.get("success", False):
        raise RuntimeError(f"CF API {method} {path}: {data.get('errors', data)}")
    return data


def _ensure_subdomain(client: httpx.Client, token: str) -> str:
    try:
        sub = cf_request(client, "GET", f"/accounts/{ACCOUNT_ID}/workers/subdomain", token=token)
    except RuntimeError:
        cf_request(
            client,
            "PUT",
            f"/accounts/{ACCOUNT_ID}/workers/subdomain",
            token=token,
            json={"subdomain": "user1"},
        )
        sub = cf_request(client, "GET", f"/accounts/{ACCOUNT_ID}/workers/subdomain", token=token)
    subdomain = sub.get("result", {}).get("subdomain") or ""
    if not subdomain:
        raise RuntimeError("workers.dev subdomain not found — open Workers in dashboard once")
    return subdomain


def deploy_script(client: httpx.Client, token: str, script_name: str, *, subdomain: str) -> str:
    worker_src = (CF_DIR / "src" / "worker.js").read_text(encoding="utf-8")
    worker_secret = (CF_DIR / "secret.txt").read_text(encoding="utf-8").strip()
    guestkey = "YOUR_GUESTKEY"

    metadata = {
        "main_module": "worker.js",
        "compatibility_date": "2024-11-01",
        "bindings": [
            {"type": "plain_text", "name": "GUESTKEY", "text": guestkey},
        ],
    }

    print(f"upload {script_name}...")
    files = {
        "metadata": (None, json.dumps(metadata), "application/json"),
        "worker.js": ("worker.js", worker_src.encode("utf-8"), "application/javascript+module"),
    }
    cf_request(
        client,
        "PUT",
        f"/accounts/{ACCOUNT_ID}/workers/scripts/{script_name}",
        token=token,
        files=files,
    )

    print(f"set SECRET on {script_name}...")
    cf_request(
        client,
        "PUT",
        f"/accounts/{ACCOUNT_ID}/workers/scripts/{script_name}/secrets",
        token=token,
        json={"name": "SECRET", "text": worker_secret, "type": "secret_text"},
    )

    cf_request(
        client,
        "POST",
        f"/accounts/{ACCOUNT_ID}/workers/scripts/{script_name}/subdomain",
        token=token,
        json={"enabled": True},
    )

    worker_url = f"https://{script_name}.{subdomain}.workers.dev"
    print(f"  -> {worker_url}")
    return worker_url


def _load_existing_endpoints() -> list[dict]:
    if not WORKER_REF_JSON.exists():
        return []
    try:
        cfg = json.loads(WORKER_REF_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    workers = cfg.get("workers")
    if isinstance(workers, list) and workers:
        return [w for w in workers if isinstance(w, dict) and w.get("url")]
    url = str(cfg.get("url", "")).strip()
    secret = str(cfg.get("secret", "")).strip()
    if url and secret:
        return [{"url": url, "secret": secret}]
    return []


def _merge_endpoints(existing: list[dict], new_eps: list[dict]) -> list[dict]:
    by_url: dict[str, dict] = {}
    for ep in existing + new_eps:
        url = str(ep.get("url", "")).strip().rstrip("/")
        secret = str(ep.get("secret", "")).strip()
        if url and secret:
            by_url[url] = {"url": url, "secret": secret}
    return list(by_url.values())


def deploy_workers(
    token: str,
    names: tuple[str, ...] | list[str] | None = None,
    *,
    merge: bool = False,
) -> list[dict]:
    import time

    script_names = list(names or DEFAULT_WORKERS)
    worker_secret = (CF_DIR / "secret.txt").read_text(encoding="utf-8").strip()
    new_eps: list[dict] = []

    with httpx.Client() as client:
        subdomain = _ensure_subdomain(client, token)
        for name in script_names:
            for attempt in range(4):
                try:
                    url = deploy_script(client, token, name, subdomain=subdomain)
                    new_eps.append({"url": url, "secret": worker_secret})
                    break
                except (httpx.ReadError, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                    if attempt >= 3:
                        raise
                    wait = 2.0 * (attempt + 1)
                    print(f"  retry {name} in {wait:.0f}s ({exc})")
                    time.sleep(wait)
                    client = httpx.Client()

    existing = _load_existing_endpoints() if merge else []
    endpoints = _merge_endpoints(existing, new_eps) if merge else new_eps

    cfg: dict = {}
    if WORKER_REF_JSON.exists():
        try:
            cfg = json.loads(WORKER_REF_JSON.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cfg = {}

    cfg["account_id"] = ACCOUNT_ID
    cfg["workers"] = endpoints
    if endpoints:
        cfg["url"] = endpoints[0]["url"]
        cfg["secret"] = endpoints[0]["secret"]
    cfg.pop("note", None)
    WORKER_REF_JSON.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"updated {WORKER_REF_JSON} ({len(endpoints)} workers total, +{len(new_eps)} new)")
    return endpoints


def _load_token(argv: list[str]) -> str:
    for arg in argv[1:]:
        if arg.startswith("-"):
            continue
        return arg.strip()
    for path in (
        CF_DIR / "cf_api_token.txt",
        ROOT / "cf_api_token.txt",
    ):
        if path.exists():
            tok = path.read_text(encoding="utf-8").strip()
            if tok:
                return tok
    return (
        os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
        or os.environ.get("CF_API_TOKEN", "").strip()
    )


def main() -> None:
    token = _load_token(sys.argv)
    if not token:
        print(
            "usage: deploy.py <CF_API_TOKEN>\n"
            "   or: CLOUDFLARE_API_TOKEN=... deploy.py\n"
            "   or: cf_worker/cf_api_token.txt",
            file=sys.stderr,
        )
        sys.exit(1)
    merge = "--merge" in sys.argv
    names = DEFAULT_WORKERS
    if "--only" in sys.argv:
        idx = sys.argv.index("--only")
        if idx + 1 < len(sys.argv):
            names = tuple(n.strip() for n in sys.argv[idx + 1].split(",") if n.strip())
    if "--count" in sys.argv:
        idx = sys.argv.index("--count")
        n = int(sys.argv[idx + 1]) if idx + 1 < len(sys.argv) else 13
        names = worker_pool_names(n)
    deploy_workers(token, names, merge=merge)
    print("DONE")


if __name__ == "__main__":
    main()