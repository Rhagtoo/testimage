#!/usr/bin/env python3
"""
Автоцикл: counter_attack → scan → sleep.

Трёхуровневая зрячесть (по умолчанию):
  1. SOCKS trusted blast — если check_trusted_proxies >= порога
  2. CF Worker speed scan — если Workers зрячие (check_trusted_workers)
  3. Blind/wait — ref-before-combat без зрячих каналов (только свежее окно)

Burst-upload → выделенные SOCKS (как раньше).
Проверка зрячести SOCKS и CF Workers — параллельно каждый цикл (кэш по интервалу).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable
SCANNER = ROOT / "pentest_site_gallery_scanner.py"
COUNTER = ROOT / "counter_attack.py"
CHECK_TRUSTED = ROOT / "check_trusted_proxies.py"
CHECK_WORKERS = ROOT / "check_trusted_workers.py"
WORKERS_CONFIG = ROOT / "worker_ref.json"
LOCK_FILE = ROOT / "scanner.pid"
BURST_LOCK = ROOT / "burst.pid"
AUTO_CYCLE_LOCK = ROOT / "auto_cycle.pid"
SCAN_RELOAD_FILE = ROOT / "scan_reload.json"
SCAN_STATUS_FILE = ROOT / "scan_status.json"
STATE_FILE = ROOT / "auto_cycle_state.json"
SCAN_MODE_VERSION = "cf-worker-trusted-v5-13w"
REFERENCE_IDS_FILE = ROOT / "found_counter_scan.txt"
WORKER_REF_CONFIG = ROOT / "worker_ref.json"
DEFAULT_LOG = ROOT / "auto_cycle.log"
SCAN_CURRENT_LOG = ROOT / "counter_scan_current.log"
BURST_LOG = ROOT / "burst_upload.log"

log = logging.getLogger("auto_cycle")
_persistent_scanner: subprocess.Popen | None = None
_burst_proc: subprocess.Popen | None = None


def _parse_burst_proxies(spec: str) -> list[str]:
    return [p.strip() for p in spec.split(",") if p.strip()]


def _setup_logging(log_path: Path, verbose: bool) -> None:
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s [auto] %(message)s", datefmt="%H:%M:%S")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    try:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)
    except OSError as e:
        log.warning("не удалось открыть log-file %s: %s", log_path, e)


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, encoding="utf-8") as fh:
        return max(0, sum(1 for _ in fh) - 1)


def acquire_auto_cycle_lock() -> bool:
    for _ in range(2):
        try:
            fd = os.open(AUTO_CYCLE_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            return True
        except FileExistsError:
            try:
                pid = int(AUTO_CYCLE_LOCK.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                pid = 0
            if pid and _pid_alive(pid):
                print(f"auto_cycle уже запущен (PID {pid})", file=sys.stderr)
                return False
            AUTO_CYCLE_LOCK.unlink(missing_ok=True)
    return False


def release_auto_cycle_lock() -> None:
    AUTO_CYCLE_LOCK.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stop_scanner() -> None:
    if not LOCK_FILE.exists():
        return
    try:
        pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        LOCK_FILE.unlink(missing_ok=True)
        return
    if _pid_alive(pid):
        log.info("останавливаем сканер/upload PID %d", pid)
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                check=False,
            )
        else:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
        time.sleep(2)
    LOCK_FILE.unlink(missing_ok=True)


def run_counter_attack(csv: Path, intel: Path, fresh_count: int, fresh_cap: int) -> bool:
    cmd = [
        PYTHON,
        str(COUNTER),
        "--csv",
        str(csv),
        "--intel",
        str(intel),
        "--scan-half",
        "0",
        "--fresh-count",
        str(fresh_count),
        "--fresh-cap",
        str(fresh_cap),
    ]
    log.info("counter_attack: %s", " ".join(cmd))
    res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    if res.stdout:
        for line in res.stdout.strip().splitlines():
            log.info("  %s", line)
    if res.returncode != 0:
        log.warning("counter_attack exit=%s stderr=%s", res.returncode, res.stderr[:500])
        return False
    return True


def _parse_sighted_output(text: str, label: str) -> tuple[int, int, float]:
    m = re.search(r"SIGHTED=(\d+)\s+TOTAL=(\d+)\s+PCT=([\d.]+)", text)
    if m:
        return int(m.group(1)), int(m.group(2)), float(m.group(3))
    m2 = re.search(r"Зрячих[^:]*:\s*(\d+)\s*/\s*(\d+)", text)
    if m2:
        n, total = int(m2.group(1)), int(m2.group(2))
        pct = 100.0 * n / total if total else 0.0
        return n, total, pct
    log.warning("не распознан вывод %s: %s", label, text[:300])
    return 0, 0, 0.0


def check_sighted(concurrency: int, timeout: int) -> tuple[int, int, float]:
    cmd = [PYTHON, str(CHECK_TRUSTED), "-c", str(concurrency), "--machine"]
    log.info("check trusted proxies: concurrency=%d timeout=%ds", concurrency, timeout)
    try:
        res = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log.warning("check_trusted timeout %ds → считаем 0 зрячих", timeout)
        return 0, 215, 0.0
    text = (res.stdout or "") + (res.stderr or "")
    return _parse_sighted_output(text, "check_trusted")


def check_worker_sighted(timeout: int, concurrency: int = 13) -> tuple[int, int, float]:
    cmd = [
        PYTHON, str(CHECK_WORKERS), "--machine",
        "-c", str(concurrency),
        "--reference-ids", str(REFERENCE_IDS_FILE),
        "--workers-config", str(WORKERS_CONFIG),
    ]
    log.info("check trusted workers: timeout=%ds", timeout)
    try:
        res = subprocess.run(
            cmd, cwd=ROOT, capture_output=True, text=True, check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log.warning("check_workers timeout %ds → считаем 0 зрячих", timeout)
        return 0, 1, 0.0
    text = (res.stdout or "") + (res.stderr or "")
    return _parse_sighted_output(text, "check_workers")


def load_worker_ref_config(path: Path = WORKER_REF_CONFIG) -> tuple[str, str]:
    if not path.exists():
        return "", ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return "", ""
    url = str(data.get("url", "")).strip().rstrip("/")
    secret = str(data.get("secret", "")).strip()
    if not url or not secret:
        return "", ""
    return url, secret


def build_scan_cmd(
    *,
    trusted: bool,
    priority_file: Path,
    priority_limit: int,
    workers_trusted: int,
    workers_blind: int,
    probe_timeout: int,
    output: Path,
    persistent: bool = False,
    ref_before_combat: bool = True,
    cloudflare_worker_api: bool = True,
    trusted_worker_pool: bool = False,
    worker_ref_url: str = "",
    worker_ref_key: str = "",
    worker_ref_config: Path | None = None,
) -> list[str]:
    cmd = [
        PYTHON,
        "-u",
        str(SCANNER),
        "--proxies-only",
        "--priority-file",
        str(priority_file),
        "--priority-only",
        "--no-deferred-queue",
        "-n",
        "0",
        "-o",
        str(output),
        "--probe-timeout",
        str(probe_timeout),
    ]
    if ref_before_combat and not trusted:
        cmd.append("--ref-before-combat")
    else:
        cmd.append("--aggressive-blind")
    if trusted:
        cmd.extend(["-w", str(workers_trusted)])
        if priority_limit > 0:
            cmd.extend(["--priority-limit", str(priority_limit)])
    else:
        if not ref_before_combat:
            cmd.append("--no-trusted-proxy")
        if workers_blind > 0:
            cmd.extend(["-w", str(workers_blind)])
    if persistent:
        cmd.append("--persistent")
    cfg_path = worker_ref_config or WORKER_REF_CONFIG
    if trusted_worker_pool and ref_before_combat and not trusted:
        cmd.append("--trusted-worker-pool")
        cmd.extend(["--worker-ref-config", str(cfg_path)])
        cmd.extend(["--trusted-reference-ids", str(REFERENCE_IDS_FILE)])
        cmd.extend(["--worker-scan-concurrency", "4"])
    elif cloudflare_worker_api and ref_before_combat and not trusted:
        cf_url, cf_key = load_worker_ref_config(cfg_path)
        if cf_url and cf_key:
            cmd.extend(["--cloudflare-worker-url", cf_url])
            cmd.extend(["--cloudflare-worker-key", cf_key])
        elif worker_ref_url:
            cmd.extend(["--worker-ref-url", worker_ref_url])
            if worker_ref_key:
                cmd.extend(["--worker-ref-key", worker_ref_key])
    elif worker_ref_url:
        cmd.extend(["--worker-ref-url", worker_ref_url])
        if worker_ref_key:
            cmd.extend(["--worker-ref-key", worker_ref_key])
    if worker_ref_config is not None:
        cmd.extend(["--worker-ref-config", str(worker_ref_config)])
    return cmd


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _normalize_burst_log(path: Path = BURST_LOG) -> bool:
    """Склеивает legacy UTF-16 (PowerShell) и UTF-8 хвост в единый UTF-8 файл."""
    if not path.exists():
        return False
    raw = path.read_bytes()
    if len(raw) < 2:
        return False
    if not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return False

    utf8_start = raw.find(b"--- burst 20")
    if utf8_start < 0:
        utf8_start = len(raw)

    while utf8_start > 2 and raw[utf8_start - 1] in (0, 13, 10):
        utf8_start -= 1

    prefix = raw[:utf8_start]
    if len(prefix) % 2:
        prefix = prefix[:-1]

    if prefix.startswith(b"\xff\xfe"):
        head = prefix[2:].decode("utf-16-le", errors="replace")
    elif prefix.startswith(b"\xfe\xff"):
        head = prefix[2:].decode("utf-16-be", errors="replace")
    else:
        head = prefix.decode("utf-16-le", errors="replace")

    tail = raw[utf8_start:].decode("utf-8", errors="replace")
    text = head + tail

    backup = path.with_name(path.name + ".utf16.bak")
    if not backup.exists():
        backup.write_bytes(raw)

    path.write_text(text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
    log.info(
        "burst_upload.log: UTF-16 → UTF-8 (%d → %d байт, backup %s)",
        len(raw),
        path.stat().st_size,
        backup.name,
    )
    return True


def run_scan(
    cmd: list[str],
    max_seconds: int | None,
    log_path: Path,
    current_log: Path = SCAN_CURRENT_LOG,
) -> int:
    log.info("scan: %s", " ".join(cmd))
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    header = f"--- scan {stamp} ---\n"
    current_log.write_text(header, encoding="utf-8", newline="\n")
    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=_subprocess_env(),
    )
    assert proc.stdout is not None
    start = time.monotonic()
    with open(log_path, "a", encoding="utf-8", newline="\n") as archive, open(
        current_log, "a", encoding="utf-8", newline="\n",
    ) as live:
        archive.write(f"\n{header}")
        archive.flush()

        def pump() -> bool:
            line = proc.stdout.readline()
            if not line:
                return False
            archive.write(line)
            live.write(line)
            archive.flush()
            live.flush()
            print(line, end="", flush=True)
            return True

        while proc.poll() is None:
            if max_seconds and (time.monotonic() - start) >= max_seconds:
                log.warning("scan timeout %ds → terminate", max_seconds)
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return -2
            if not pump():
                time.sleep(0.1)
        while pump():
            pass
    return proc.wait() or 0


def maybe_regenerate_clusters(
    state: dict,
    csv: Path,
    intel: Path,
    min_new_rows: int,
    fresh_count: int,
    fresh_cap: int,
) -> bool:
    rows = _count_csv_rows(csv)
    prev = int(state.get("csv_rows", 0))
    if rows - prev < min_new_rows:
        log.info("counter_attack skip: csv %d (+%d < %d)", rows, rows - prev, min_new_rows)
        return False
    ok = run_counter_attack(csv, intel, fresh_count, fresh_cap)
    if ok:
        state["csv_rows"] = rows
        state["last_counter_attack"] = time.time()
    return ok


def _line_count(path: Path) -> int:
    if not path.exists() or path.stat().st_size == 0:
        return 0
    return len(path.read_text(encoding="utf-8").splitlines())


def _full_scan_due(state: dict, hours: float) -> bool:
    if hours <= 0:
        return True
    last = float(state.get("last_full_scan", 0))
    return time.time() - last >= hours * 3600


def _check_trusted_due(args: argparse.Namespace, state: dict) -> bool:
    if args.check_trusted:
        return True
    if args.check_trusted_interval_hours <= 0:
        return False
    last = float(state.get("last_check", 0))
    return time.time() - last >= args.check_trusted_interval_hours * 3600


def _check_workers_due(args: argparse.Namespace, state: dict) -> bool:
    if args.check_workers:
        return True
    interval = float(args.check_workers_interval)
    if interval <= 0:
        return False
    last = float(state.get("last_worker_check", 0))
    return time.time() - last >= interval


def scanner_is_alive() -> bool:
    if not LOCK_FILE.exists():
        return False
    try:
        pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return False
    return _pid_alive(pid)


def burst_is_alive() -> bool:
    if not BURST_LOCK.exists():
        return False
    try:
        pid = int(BURST_LOCK.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return False
    return _pid_alive(pid)


def _load_scan_status() -> dict | None:
    if not SCAN_STATUS_FILE.exists():
        return None
    try:
        return json.loads(SCAN_STATUS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def sleep_until_next_cycle(args: argparse.Namespace, state: dict) -> None:
    if not args.persistent_scanner:
        cycle_sec = float(state.get("last_cycle_seconds", 0))
        sleep_s = max(args.min_interval, args.interval - int(cycle_sec))
        log.info(
            "сон %ds до следующего цикла (цикл %.0fs, target %ds)…",
            sleep_s, cycle_sec, args.interval,
        )
        time.sleep(sleep_s)
        return

    t0 = time.monotonic()
    log.info("ждём idle сканера (min=%ds, max=%ds)…", args.min_interval, args.interval)
    while True:
        elapsed = time.monotonic() - t0
        if elapsed >= args.interval:
            log.info("сон: ceiling %ds — следующий цикл", args.interval)
            break
        st = _load_scan_status()
        if st and st.get("idle"):
            idle_age = time.time() - float(st.get("ts", 0))
            if idle_age >= args.min_interval and elapsed >= args.min_interval:
                tried = st.get("session_tried", "?")
                log.info(
                    "сканер idle %.0fs (session=%s) — следующий цикл",
                    idle_age, tried,
                )
                break
        time.sleep(1.0)


def write_reload_signal(
    batch: int,
    priority_file: Path,
    *,
    limit: int = 0,
) -> None:
    payload = {
        "batch": batch,
        "file": priority_file.name,
        "limit": limit,
        "ts": time.time(),
    }
    SCAN_RELOAD_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    try:
        SCAN_STATUS_FILE.write_text(
            json.dumps({
                "idle": False,
                "batch": batch,
                "ts": time.time(),
                "file": priority_file.name,
            }, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    log.info("reload signal: batch=%d file=%s (%d ID)", batch, priority_file.name, _line_count(priority_file))


def _cleanup_orphan_scanners() -> None:
    """Убивает все висячие pentest_site_gallery_scanner процессы, оставшиеся от предыдущих запусков."""
    # чистим lock-файлы чтобы stop_scanner не врал
    for lock in (LOCK_FILE, BURST_LOCK):
        lock.unlink(missing_ok=True)
    # убиваем через pkill
    for pattern in ["pentest_site_gallery_scanner.py", "check_trusted_workers.py"]:
        try:
            res = subprocess.run(
                ["pkill", "-f", pattern],
                capture_output=True, text=True, check=False,
            )
            if res.returncode == 0:
                time.sleep(0.5)
        except FileNotFoundError:
            log.debug("[cleanup] pkill not available, skip")


def start_persistent_scanner(cmd: list[str], log_path: Path) -> subprocess.Popen | None:
    global _persistent_scanner
    # Всегда убиваем старый сканер перед запуском нового — чтобы не плодились
    stop_scanner()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    header = f"--- persistent scanner {stamp} ---\n"
    SCAN_CURRENT_LOG.write_text(header, encoding="utf-8", newline="\n")
    log.info("persistent scanner: %s", " ".join(cmd))
    with open(log_path, "a", encoding="utf-8", newline="\n") as archive:
        archive.write(f"\n{header}")
    out_fh = open(SCAN_CURRENT_LOG, "a", encoding="utf-8", newline="\n")
    _persistent_scanner = subprocess.Popen(
        cmd,
        cwd=ROOT,
        stdout=out_fh,
        stderr=subprocess.STDOUT,
        env=_subprocess_env(),
    )
    time.sleep(4)
    if _persistent_scanner.poll() is not None:
        log.error("persistent scanner упал сразу, exit=%s", _persistent_scanner.returncode)
        _persistent_scanner = None
        return None
    log.info("persistent scanner PID %d", _persistent_scanner.pid)
    return _persistent_scanner


def ensure_burst(csv: Path, intel: Path, proxies: list[str], interval: float) -> None:
    global _burst_proc
    want = ",".join(proxies)
    if _burst_proc is not None and _burst_proc.poll() is None:
        if getattr(ensure_burst, "_proxy_spec", None) == want:
            return
        log.info("burst upload: перезапуск (прокси изменились)")
        _burst_proc.terminate()
        try:
            _burst_proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            _burst_proc.kill()
        BURST_LOCK.unlink(missing_ok=True)
    elif _burst_proc is None and burst_is_alive():
        prev = getattr(ensure_burst, "_proxy_spec", None)
        if prev is None or prev == want:
            log.info("burst upload уже запущен (PID %s)", BURST_LOCK.read_text(encoding="utf-8").strip())
            ensure_burst._proxy_spec = want  # type: ignore[attr-defined]
            return
        log.info("burst upload: перезапуск (прокси изменились, старый PID %s)", BURST_LOCK.read_text(encoding="utf-8").strip())
        stop_burst()
    _burst_proc = run_burst_upload(csv, intel, proxies, interval)
    ensure_burst._proxy_spec = want  # type: ignore[attr-defined]


def stop_burst() -> None:
    global _burst_proc
    if _burst_proc is not None and _burst_proc.poll() is None:
        _burst_proc.terminate()
        try:
            _burst_proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            _burst_proc.kill()
        _burst_proc = None
    if not BURST_LOCK.exists():
        return
    try:
        pid = int(BURST_LOCK.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        BURST_LOCK.unlink(missing_ok=True)
        return
    if _pid_alive(pid):
        log.info("останавливаем burst upload PID %d", pid)
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)
        else:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
    BURST_LOCK.unlink(missing_ok=True)


def run_burst_upload(
    csv: Path,
    intel: Path,
    proxies: list[str],
    interval: float,
) -> subprocess.Popen | None:
    proxy_spec = ",".join(proxies)
    n_workers = max(1, len(proxies))
    cmd = [
        PYTHON,
        "-u",
        str(SCANNER),
        "--proxies-only",
        "--chrome",
        "--upload-only-loop",
        "--mass-upload-workers",
        str(n_workers),
        "--upload-fixed-proxy",
        proxy_spec,
        "--upload-interval",
        str(interval),
        "--upload-images",
        "2",
        "--upload-dataset-csv",
        str(csv),
        "--upload-intel-file",
        str(intel),
        "--upload-fast-intel",
        "--chrome-timeout",
        "45",
    ]
    log.info("burst upload: proxies=%s workers=%d interval=%.0fs", proxy_spec, n_workers, interval)
    _normalize_burst_log(BURST_LOG)
    with open(BURST_LOG, "a", encoding="utf-8", newline="\n") as lf:
        lf.write(f"\n--- burst {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            stdout=lf,
            stderr=subprocess.STDOUT,
            env=_subprocess_env(),
        )
    BURST_LOCK.write_text(str(proc.pid), encoding="ascii")
    return proc


def cycle_once(args: argparse.Namespace, state: dict) -> None:
    t0 = time.monotonic()

    if args.regenerate_always or maybe_regenerate_clusters(
        state, Path(args.csv), Path(args.intel), args.min_new_rows, args.fresh_count, args.fresh_cap,
    ):
        fresh = Path(args.fresh_clusters)
        full = Path(args.clusters)
        log.info(
            "очереди: fresh=%d full=%d",
            len(fresh.read_text(encoding="utf-8").splitlines()) if fresh.exists() else 0,
            len(full.read_text(encoding="utf-8").splitlines()) if full.exists() else 0,
        )

    fresh_path = Path(args.fresh_clusters)
    full_path = Path(args.clusters)

    socks_due = _check_trusted_due(args, state)
    workers_due = _check_workers_due(args, state)
    if socks_due:
        sighted, total, pct = check_sighted(args.check_concurrency, args.check_timeout)
        state["last_sighted"] = sighted
        state["last_sighted_total"] = total
        state["last_sighted_pct"] = pct
        state["last_check"] = time.time()
        log.info("SOCKS зрячих: %d / %d (%.1f%%)", sighted, total, pct)
    else:
        sighted = int(state.get("last_sighted", 0))
        total = int(state.get("last_sighted_total", 215))
        pct = float(state.get("last_sighted_pct", 0.0))
        log.info(
            "check SOCKS: кэш %d/%d (интервал %.0fч)",
            sighted, total, args.check_trusted_interval_hours,
        )

    if workers_due:
        w_sighted, w_total, w_pct = check_worker_sighted(args.check_workers_timeout)
        state["last_worker_sighted"] = w_sighted
        state["last_worker_sighted_total"] = w_total
        state["last_worker_sighted_pct"] = w_pct
        state["last_worker_check"] = time.time()
        log.info("CF Workers зрячих: %d / %d (%.1f%%)", w_sighted, w_total, w_pct)
    else:
        w_sighted = int(state.get("last_worker_sighted", 0))
        w_total = int(state.get("last_worker_sighted_total", 1))
        w_pct = float(state.get("last_worker_sighted_pct", 0.0))
        log.info(
            "check Workers: кэш %d/%d (интервал %.0fс)",
            w_sighted, w_total, args.check_workers_interval,
        )

    trusted_mode = sighted >= args.trusted_threshold
    worker_mode = (
        not trusted_mode
        and w_sighted >= args.worker_threshold
        and args.cloudflare_worker_api
    )
    if trusted_mode:
        log.info("канал: SOCKS trusted blast (%d зрячих)", sighted)
    elif worker_mode:
        log.info("канал: CF Worker speed scan (%d зрячих Workers)", w_sighted)
    else:
        log.info(
            "канал: blind/wait (SOCKS %d/%d, Workers %d/%d)",
            sighted, total, w_sighted, w_total,
        )
    use_full = False
    if trusted_mode:
        priority = fresh_path
        if not priority.exists() or priority.stat().st_size == 0:
            priority = full_path
        if args.persistent_scanner and scanner_is_alive():
            stop_scanner()
        cmd = build_scan_cmd(
            trusted=True,
            priority_file=priority,
            priority_limit=args.trusted_limit,
            workers_trusted=args.trusted_workers,
            workers_blind=0,
            probe_timeout=args.trusted_probe_timeout,
            output=Path(args.output),
        )
        max_sec = args.trusted_max_seconds
        log.info(
            "режим TRUSTED BLAST: %s limit=%d max=%ds workers=%d",
            priority.name, args.trusted_limit, max_sec, args.trusted_workers,
        )
        rc = run_scan(cmd, max_sec, Path(args.scan_log))
        log.info("scan завершён exit=%s", rc)
    else:
        use_full = _full_scan_due(state, args.full_scan_hours)
        if use_full:
            priority = full_path
            state["last_full_scan"] = time.time()
            scan_kind = "FULL"
        else:
            priority = fresh_path
            if not priority.exists():
                priority = full_path
                scan_kind = "FULL(fallback)"
            elif _line_count(priority) == 0:
                scan_kind = "FRESH(empty)"
            else:
                scan_kind = "FRESH"
        if worker_mode:
            mode_label = "CF-WORKER-TRUSTED"
            scan_workers = args.worker_scan_workers
            scan_timeout = args.worker_probe_timeout
        elif args.ref_before_combat:
            mode_label = "REF-BEFORE-COMBAT"
            scan_workers = args.blind_workers
            scan_timeout = args.blind_probe_timeout
        else:
            mode_label = "NO-TRUSTED"
            scan_workers = args.blind_workers
            scan_timeout = args.blind_probe_timeout
        log.info(
            "режим %s %s: %s (%d ID)",
            mode_label, scan_kind, priority.name, _line_count(priority),
        )
        if args.persistent_scanner:
            base_cmd = build_scan_cmd(
                trusted=False,
                priority_file=priority,
                priority_limit=0,
                workers_trusted=0,
                workers_blind=scan_workers,
                probe_timeout=scan_timeout,
                output=Path(args.output),
                persistent=True,
                ref_before_combat=args.ref_before_combat,
                cloudflare_worker_api=args.cloudflare_worker_api,
                trusted_worker_pool=worker_mode,
            )
            if not scanner_is_alive():
                start_persistent_scanner(base_cmd, Path(args.scan_log))
            skip_reload = False
            if scan_kind == "FRESH(empty)":
                skip_reload = True
                log.info("persistent: fresh пуст (все ID уже tried) — пропуск reload")
            elif scan_kind == "FRESH" and priority.exists():
                mtime = priority.stat().st_mtime
                if (
                    state.get("last_reload_mtime") == mtime
                    and state.get("last_reload_file") == priority.name
                ):
                    skip_reload = True
                    log.info("persistent: %s не изменился — пропуск reload", priority.name)
            if skip_reload:
                rc = 0
            else:
                batch = int(state.get("reload_batch", 0)) + 1
                state["reload_batch"] = batch
                write_reload_signal(batch, priority.resolve())
                state["last_reload_mtime"] = priority.stat().st_mtime if priority.exists() else 0
                state["last_reload_file"] = priority.name
                rc = 0
                log.info("persistent: reload batch %d отправлен", batch)
        else:
            stop_scanner()
            cmd = build_scan_cmd(
                trusted=False,
                priority_file=priority,
                priority_limit=0,
                workers_trusted=0,
                workers_blind=scan_workers,
                probe_timeout=scan_timeout,
                output=Path(args.output),
                ref_before_combat=args.ref_before_combat,
                cloudflare_worker_api=args.cloudflare_worker_api,
                trusted_worker_pool=worker_mode,
            )
            max_sec = args.blind_max_seconds if args.blind_max_seconds > 0 else None
            rc = run_scan(cmd, max_sec, Path(args.scan_log))
            log.info("scan завершён exit=%s", rc)

    if trusted_mode:
        state["last_scan_mode"] = "trusted"
    elif worker_mode and use_full:
        state["last_scan_mode"] = "cf-worker-trusted-full"
    elif worker_mode:
        state["last_scan_mode"] = "cf-worker-trusted-fresh"
    elif args.ref_before_combat and use_full:
        state["last_scan_mode"] = "ref-before-combat-full"
    elif args.ref_before_combat:
        state["last_scan_mode"] = "ref-before-combat-fresh"
    elif use_full:
        state["last_scan_mode"] = "no-trusted-full"
    else:
        state["last_scan_mode"] = "no-trusted-fresh"
    state["last_scan_rc"] = rc
    state["last_cycle_seconds"] = round(time.monotonic() - t0, 1)
    state["cycles"] = int(state.get("cycles", 0)) + 1

    if args.restart_burst:
        burst_proxies = _parse_burst_proxies(args.burst_proxies)
        if args.persistent_scanner:
            ensure_burst(Path(args.csv), Path(args.intel), burst_proxies, args.burst_interval)
        else:
            run_burst_upload(Path(args.csv), Path(args.intel), burst_proxies, args.burst_interval)


def main() -> None:
    ap = argparse.ArgumentParser(description="Auto cycle: counter_attack → check → scan")
    ap.add_argument(
        "--interval",
        type=int,
        default=90,
        help="макс. пауза между циклами / ceiling для persistent (сек)",
    )
    ap.add_argument(
        "--min-interval",
        type=int,
        default=25,
        help="мин. пауза после idle сканера перед следующим reload (сек)",
    )
    ap.add_argument("--once", action="store_true", help="один цикл и выход")
    ap.add_argument("--trusted-threshold", type=int, default=50)
    ap.add_argument("--trusted-limit", type=int, default=500, help="макс ID для trusted blast")
    ap.add_argument("--trusted-workers", type=int, default=15)
    ap.add_argument("--trusted-probe-timeout", type=int, default=8)
    ap.add_argument("--trusted-max-seconds", type=int, default=300, help="стоп trusted через N с")
    ap.add_argument(
        "--ref-before-combat",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="inline ref перед боевым запросом (default on; зрячесть без check_trusted)",
    )
    ap.add_argument(
        "--cloudflare-worker-api",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="CF Worker канал для скана (default on; из worker_ref.json)",
    )
    ap.add_argument("--worker-threshold", type=int, default=1, help="мин. зрячих CF Workers для speed scan")
    ap.add_argument("--worker-scan-workers", type=int, default=20, help="воркеры при CF Worker speed scan")
    ap.add_argument("--worker-probe-timeout", type=int, default=4, help="таймаут probe для CF Worker scan")
    ap.add_argument(
        "--check-workers",
        action="store_true",
        help="принудительно проверить зрячих CF Workers в этом цикле",
    )
    ap.add_argument(
        "--check-workers-interval",
        type=float,
        default=30.0,
        help="интервал проверки зрячести CF Workers (сек, default 30)",
    )
    ap.add_argument(
        "--check-workers-timeout",
        type=int,
        default=120,
        help="таймаут check_trusted_workers (сек)",
    )
    ap.add_argument("--blind-workers", type=int, default=20, help="воркеры blind/ref-before-combat скана")
    ap.add_argument("--blind-probe-timeout", type=int, default=4)
    ap.add_argument("--blind-max-seconds", type=int, default=0, help="0 = до конца очереди")
    ap.add_argument("--check-concurrency", type=int, default=60)
    ap.add_argument("--check-timeout", type=int, default=120, help="таймаут check_trusted (сек)")
    ap.add_argument(
        "--check-trusted",
        action="store_true",
        help="принудительно проверить зрячих в этом цикле",
    )
    ap.add_argument(
        "--check-trusted-interval-hours",
        type=float,
        default=24.0,
        help="периодическая проверка зрячих (0=никогда, default 24ч)",
    )
    ap.add_argument(
        "--full-scan-hours",
        type=float,
        default=24.0,
        help="полный scan_clusters.txt раз в N часов (default 24)",
    )
    ap.add_argument("--csv", default="upload_burst_9334.csv")
    ap.add_argument("--intel", default="upload_burst_9334.jsonl")
    ap.add_argument("--clusters", default="scan_clusters.txt")
    ap.add_argument("--fresh-clusters", default="scan_clusters_fresh.txt")
    ap.add_argument("--output", default="found_counter_scan.txt")
    ap.add_argument("--scan-log", default="counter_scan.log")
    ap.add_argument("--log-file", default=str(DEFAULT_LOG))
    ap.add_argument("--min-new-rows", type=int, default=4, help="пересборка кластеров при +N upload")
    ap.add_argument(
        "--fresh-count",
        type=int,
        default=120,
        help="последних upload ID для fresh-окна counter_attack",
    )
    ap.add_argument(
        "--fresh-cap",
        type=int,
        default=2000,
        help="макс. untried ID в fresh за цикл",
    )
    ap.add_argument("--regenerate-always", action="store_true")
    ap.add_argument("--restart-burst", action="store_true", help="после скана снова запустить burst")
    ap.add_argument(
        "--persistent-scanner",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="долгоживущий сканер + reload (default on)",
    )
    ap.add_argument(
        "--burst-proxies",
        default=("196.16.5.108:9766,196.16.5.162:9460,196.16.5.204:9619,196.16.8.78:9264,196.16.5.42:9398,196.16.5.20:9066,196.16.8.27:9508,196.16.5.90:9479,196.16.8.113:9051,196.16.2.38:9348,196.16.5.154:9566,196.16.2.173:9820,196.16.5.95:9614,196.16.2.235:9142,196.16.5.215:9243"
        ),
        help="прокси upload через запятую (по одному на worker)",
    )
    ap.add_argument("--burst-interval", type=float, default=1.0)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    _setup_logging(Path(args.log_file), args.verbose)
    if not acquire_auto_cycle_lock():
        sys.exit(1)
    state = _load_state()

    log.info(
        "старт auto_cycle interval=%ds trusted>=%d worker>=%d check_workers=%.0fs persistent=%s once=%s",
        args.interval, args.trusted_threshold, args.worker_threshold,
        args.check_workers_interval, args.persistent_scanner, args.once,
    )

    # Убить всех сирот-сканеров от предыдущих запусков
    _cleanup_orphan_scanners()

    idle_priority = ROOT / ".scan_idle.txt"
    idle_priority.write_text("", encoding="utf-8")

    try:
        if args.restart_burst and args.persistent_scanner:
            ensure_burst(
                Path(args.csv), Path(args.intel),
                _parse_burst_proxies(args.burst_proxies), args.burst_interval,
            )
        if args.persistent_scanner:
            need_restart = (
                scanner_is_alive()
                and state.get("scan_mode_version") != SCAN_MODE_VERSION
            )
            if need_restart:
                log.info(
                    "перезапуск сканера: режим %s (был %s)",
                    SCAN_MODE_VERSION, state.get("scan_mode_version", "?"),
                )
                stop_scanner()
            if scanner_is_alive():
                log.info(
                    "persistent scanner уже запущен (PID %s)",
                    LOCK_FILE.read_text(encoding="utf-8").strip(),
                )
            else:
                boot_cmd = build_scan_cmd(
                    trusted=False,
                    priority_file=idle_priority,
                    priority_limit=0,
                    workers_trusted=0,
                    workers_blind=args.worker_scan_workers,
                    probe_timeout=args.worker_probe_timeout,
                    output=Path(args.output),
                    persistent=True,
                    ref_before_combat=args.ref_before_combat,
                    cloudflare_worker_api=args.cloudflare_worker_api,
                    trusted_worker_pool=args.cloudflare_worker_api,
                )
                start_persistent_scanner(boot_cmd, Path(args.scan_log))
                state["scan_mode_version"] = SCAN_MODE_VERSION
        while True:
            cycle_once(args, state)
            _save_state(state)
            if args.once:
                break
            sleep_until_next_cycle(args, state)
    except KeyboardInterrupt:
        log.info("прервано пользователем")
        _save_state(state)
        stop_scanner()
        stop_burst()
    finally:
        release_auto_cycle_lock()


if __name__ == "__main__":
    main()