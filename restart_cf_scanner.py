#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import auto_cycle as ac

ROOT = Path(__file__).resolve().parent
cmd = ac.build_scan_cmd(
    trusted=False,
    priority_file=ROOT / "scan_clusters_fresh.txt",
    priority_limit=0,
    workers_trusted=0,
    workers_blind=50,
    probe_timeout=4,
    output=ROOT / "found_counter_scan.txt",
    persistent=True,
    ref_before_combat=True,
    cloudflare_worker_api=True,
)
with open(ROOT / "counter_scan_current.log", "a", encoding="utf-8", newline="\n") as log:
    log.write(f"\n--- cf-worker restart {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        env=ac._subprocess_env(),
    )
print("started", proc.pid)
time.sleep(2)
print("poll", proc.poll())