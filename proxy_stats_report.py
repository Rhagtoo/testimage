#!/usr/bin/env python3
"""Отчёт по прокси: запросы, blind, combat-404, баны, outage — из логов и proxy_stats.json."""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_LOGS = [
    ROOT / "counter_scan_current.log",
    ROOT / "counter_scan.log",
]
PROXY_LINE_RE = re.compile(
    r"^\s*(\d{2}:\d{2}:\d{2})\s+(\S+:\d+)\s+req=(\d+)\s+404=(\d+)\s+blind=(\d+)\s+"
    r"err=(\d+)\s+\((\d+)%\)\s+avg=(\d+)ms\s+outage=([\d.]+)s(?:\s+DOWN)?$",
)
SUMMARY_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}) \[proxy-stats\] req=(\d+) err=(\d+) blind=(\d+) "
    r"active=(\d+)/(\d+) idle=(\d+) degraded=(\d+)"
)
COMBAT_BAN_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}) \[combat-budget\] (\S+): (\d+) miss за (\d+)s → cooldown (\d+)s"
)
WORST_RE = re.compile(
    r"^\s*(\d{2}:\d{2}:\d{2})\s+(\S+:\d+)\s+err=(\d+) \((\d+)%\) streak=(\d+) outage=([\d.]+)s"
)


@dataclass
class ProxyRow:
    label: str
    requests: int = 0
    miss_404: int = 0
    blind: int = 0
    errors: int = 0
    err_pct: int = 0
    avg_ms: int = 0
    outage_s: float = 0.0
    down: bool = False
    last_ts: str = ""
    first_ts: str = ""
    combat_bans: list[str] = field(default_factory=list)
    worst_err: int = 0
    worst_streak: int = 0

    @property
    def ref_ok(self) -> int:
        return max(0, self.requests - self.blind)

    @property
    def blind_pct(self) -> float:
        return 100.0 * self.blind / self.requests if self.requests else 0.0

    @property
    def combat_pct(self) -> float:
        """Доля запросов дошедших до combat (не blind ref)."""
        return 100.0 * (self.miss_404 + (1 if self.ref_ok > self.miss_404 else 0)) / self.requests if self.requests else 0.0


def _parse_file(
    path: Path,
    proxies: dict[str, ProxyRow],
    summaries: list[dict],
    combat_bans: list[dict],
) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
            m = SUMMARY_RE.match(line.strip())
            if m:
                summaries.append({
                    "ts": m.group(1),
                    "file": path.name,
                    "req": int(m.group(2)),
                    "err": int(m.group(3)),
                    "blind": int(m.group(4)),
                    "active": int(m.group(5)),
                    "total": int(m.group(6)),
                })
                continue
            m = COMBAT_BAN_RE.match(line.strip())
            if m:
                ban = {
                    "ts": m.group(1),
                    "label": m.group(2),
                    "misses": int(m.group(3)),
                    "window_s": int(m.group(4)),
                    "cooldown_s": int(m.group(5)),
                }
                combat_bans.append(ban)
                row = proxies.setdefault(ban["label"], ProxyRow(label=ban["label"]))
                row.combat_bans.append(m.group(1))
                continue
            m = PROXY_LINE_RE.match(line)
            if m:
                ts, label = m.group(1), m.group(2)
                row = proxies.setdefault(label, ProxyRow(label=label))
                if not row.first_ts:
                    row.first_ts = ts
                row.last_ts = ts
                row.requests = int(m.group(3))
                row.miss_404 = int(m.group(4))
                row.blind = int(m.group(5))
                row.errors = int(m.group(6))
                row.err_pct = int(m.group(7))
                row.avg_ms = int(m.group(8))
                row.outage_s = float(m.group(9))
                row.down = line.rstrip().endswith("DOWN")
                continue
            m = WORST_RE.match(line)
            if m:
                label = m.group(2)
                row = proxies.setdefault(label, ProxyRow(label=label))
                row.worst_err = max(row.worst_err, int(m.group(3)))
                row.worst_streak = max(row.worst_streak, int(m.group(5)))


def _last_snapshot_from_current(path: Path) -> tuple[dict[str, ProxyRow], dict | None]:
    """Последний блок [proxy-stats] топ нагрузки из counter_scan_current.log."""
    if not path.exists():
        return {}, None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    # ищем последний summary и следующие proxy-строки до пустой/файл:
    last_summary: dict | None = None
    last_idx = -1
    for i, line in enumerate(lines):
        m = SUMMARY_RE.match(line.strip())
        if m:
            last_summary = {
                "ts": m.group(1),
                "file": path.name,
                "req": int(m.group(2)),
                "err": int(m.group(3)),
                "blind": int(m.group(4)),
                "active": int(m.group(5)),
                "total": int(m.group(6)),
            }
            last_idx = i
    if last_summary is None:
        return {}, None
    rows: dict[str, ProxyRow] = {}
    for line in lines[last_idx + 1:]:
        if "[proxy-stats]" in line:
            break
        m = PROXY_LINE_RE.match(line)
        if not m:
            continue
        ts, label = m.group(1), m.group(2)
        row = ProxyRow(label=label, first_ts=ts, last_ts=ts)
        row.requests = int(m.group(3))
        row.miss_404 = int(m.group(4))
        row.blind = int(m.group(5))
        row.errors = int(m.group(6))
        row.err_pct = int(m.group(7))
        row.avg_ms = int(m.group(8))
        row.outage_s = float(m.group(9))
        row.down = line.rstrip().endswith("DOWN")
        rows[label] = row
    return rows, last_summary


def parse_logs(paths: list[Path], *, current: Path | None = None) -> tuple[dict[str, ProxyRow], list[dict], list[dict]]:
    proxies: dict[str, ProxyRow] = {}
    summaries: list[dict] = []
    combat_bans: list[dict] = []

    cur = current or (paths[0] if paths else None)
    snap_rows, snap_summary = _last_snapshot_from_current(cur) if cur else ({}, None)
    if snap_summary:
        summaries.append(snap_summary)
        proxies.update(snap_rows)

    for path in paths:
        _parse_file(path, proxies, summaries, combat_bans)

    return proxies, summaries, combat_bans


def load_json_snapshot(path: Path) -> dict[str, ProxyRow]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[str, ProxyRow] = {}
    for p in data.get("proxies", []):
        label = p.get("label", "")
        if not label:
            continue
        row = ProxyRow(label=label)
        row.requests = int(p.get("requests", 0))
        row.miss_404 = int(p.get("miss_404", 0))
        row.blind = int(p.get("proxy_blind", 0))
        row.errors = int(p.get("errors", 0))
        row.avg_ms = int(p.get("avg_ms", 0))
        row.outage_s = float(p.get("outage_s", 0))
        row.down = bool(p.get("outage_now", False))
        row.last_ts = str(data.get("updated_at", ""))
        out[label] = row
    return out


def merge_rows(
    log_rows: dict[str, ProxyRow],
    json_rows: dict[str, ProxyRow],
    *,
    prefer_log: bool = True,
) -> dict[str, ProxyRow]:
    if prefer_log and log_rows:
        merged = dict(log_rows)
        for label, row in json_rows.items():
            if label not in merged:
                merged[label] = row
        return merged
    merged = dict(json_rows)
    for label, row in log_rows.items():
        if label not in merged or row.requests >= merged[label].requests:
            merged[label] = row
    return merged


def print_report(
    proxies: dict[str, ProxyRow],
    summaries: list[dict],
    combat_bans: list[dict],
    *,
    top: int,
    csv_path: Path | None,
) -> None:
    used = [r for r in proxies.values() if r.requests > 0]
    used.sort(key=lambda r: (-r.requests, r.label))
    idle = [r for r in proxies.values() if r.requests == 0]

    print("=" * 72)
    print("ОТЧЁТ ПО ПРОКСИ")
    print("=" * 72)

    cur_summaries = [s for s in summaries if s.get("file") == "counter_scan_current.log"]
    if cur_summaries:
        last = cur_summaries[-1]
        first = cur_summaries[0]
    elif summaries:
        last = summaries[-1]
        first = summaries[0]
    else:
        last = first = None

    if last:
        blind_pct = 100.0 * last["blind"] / last["req"] if last["req"] else 0
        combat_est = max(0, last["req"] - last["blind"])
        print(f"\nТекущая сессия — последний heartbeat ({last['ts']}, {last['file']}):")
        print(f"  HTTP req:      {last['req']}")
        print(f"  blind (ref):   {last['blind']} ({blind_pct:.1f}%)")
        print(f"  ref ok (≈):    {combat_est} ({100 - blind_pct:.1f}%)")
        print(f"  errors:        {last['err']}")
        print(f"  active:        {last['active']}/{last['total']}")
        if first and first is not last:
            b0 = 100.0 * first["blind"] / first["req"] if first["req"] else 0
            b1 = blind_pct
            print(f"\n  Динамика blind%: {b0:.0f}% ({first['ts']}) → {b1:.0f}% ({last['ts']})")

    if len(cur_summaries) >= 3:
        print(f"\n  История heartbeat (counter_scan_current.log):")
        for s in cur_summaries[-8:]:
            bp = 100.0 * s["blind"] / s["req"] if s["req"] else 0
            print(f"    {s['ts']}  req={s['req']:>6}  blind={s['blind']:>6} ({bp:5.1f}%)")

    json_ts = ""
    for r in proxies.values():
        if r.last_ts and "-" in r.last_ts:
            json_ts = r.last_ts
            break
    if json_ts:
        print(f"\nproxy_stats.json: {json_ts}")
    print(f"\nПрокси в детализации: {len(used)} (полный пул=215; в логе только топ-5/heartbeat)")
    print(f"Без запросов:       {len(idle)}")
    print(f"Combat-budget банов: {len(combat_bans)}")

    if last:
        total_req = last["req"]
        total_blind = last["blind"]
        total_err = last["err"]
        total_ref_ok = max(0, total_req - total_blind)
    else:
        total_req = sum(r.requests for r in used)
        total_blind = sum(r.blind for r in used)
        total_err = sum(r.errors for r in used)
        total_ref_ok = sum(r.ref_ok for r in used)

    total_combat_404 = sum(r.miss_404 for r in used)
    print(f"\nИтого по пулу:")
    print(f"  requests:   {total_req}")
    if total_req:
        print(f"  blind:      {total_blind} ({100*total_blind/total_req:.1f}%)")
        print(f"  ref ok:     {total_ref_ok} ({100*total_ref_ok/total_req:.1f}%)")
    print(f"  errors:     {total_err}")
    print(f"  combat 404: {total_combat_404} (в топ-строках лога)")

    sighted = [r for r in used if r.ref_ok > 0]
    combat_only = [r for r in used if r.miss_404 > 0]
    print(f"  в топ-логе ref≥1: {len(sighted)} прокси, combat_404≥1: {len(combat_only)}")

    print(f"\n--- ТОП-{top} по нагрузке ---")
    print(f"{'proxy':<24} {'req':>6} {'blind':>6} {'404':>5} {'ref%':>6} {'err':>4} {'avg':>6} {'out':>6} {'ban':>4}")
    for r in used[:top]:
        ref_pct = 100.0 * r.ref_ok / r.requests if r.requests else 0
        print(
            f"{r.label:<24} {r.requests:>6} {r.blind:>6} {r.miss_404:>5} {ref_pct:>5.0f}% "
            f"{r.errors:>4} {r.avg_ms:>5}ms {r.outage_s:>5.0f}s {len(r.combat_bans):>4}"
        )

    high_blind = sorted(used, key=lambda r: (-r.blind_pct, -r.requests))[:top]
    print(f"\n--- ТОП-{top} самые «слепые» (blind%) ---")
    for r in high_blind:
        print(f"  {r.label:<24} blind={r.blind}/{r.requests} ({r.blind_pct:.0f}%) combat_404={r.miss_404}")

    if combat_bans:
        print(f"\n--- Combat-budget cooldown (3 miss / 5 min) ---")
        for b in combat_bans[-20:]:
            print(f"  {b['ts']}  {b['label']:<22} {b['misses']} miss → cd {b['cooldown_s']}s")

    worst = sorted(used, key=lambda r: (-r.worst_err, -r.errors))[:top]
    bad = [r for r in worst if r.errors > 0 or r.worst_err > 0]
    if bad:
        print(f"\n--- Худшие по ошибкам ---")
        for r in bad:
            print(
                f"  {r.label:<24} err={r.errors} ({r.err_pct}%) "
                f"streak_max={r.worst_streak} outage={r.outage_s:.0f}s"
                f"{' DOWN' if r.down else ''}"
            )

    upload_labels = {"9334", "9930", "9502", "9068"}
    upload_rows = [r for r in used if any(p in r.label for p in upload_labels)]
    if upload_rows:
        print(f"\n--- Upload-прокси ---")
        for r in sorted(upload_rows, key=lambda x: -x.requests):
            print(
                f"  {r.label:<24} req={r.requests} blind={r.blind} "
                f"ref_ok={r.ref_ok} combat_404={r.miss_404}"
            )

    if csv_path:
        lines = ["proxy,requests,blind,miss_404,ref_ok,errors,avg_ms,outage_s,combat_bans,last_ts,first_ts"]
        for r in used:
            lines.append(
                f"{r.label},{r.requests},{r.blind},{r.miss_404},{r.ref_ok},"
                f"{r.errors},{r.avg_ms},{r.outage_s},{len(r.combat_bans)},"
                f"{r.last_ts},{r.first_ts}"
            )
        csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nCSV: {csv_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Статистика прокси из логов сканера")
    ap.add_argument("--logs", nargs="*", default=[str(p) for p in DEFAULT_LOGS])
    ap.add_argument("--json", default=str(ROOT / "proxy_stats.json"))
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--csv", default=str(ROOT / "proxy_stats_report.csv"))
    ap.add_argument("--no-csv", action="store_true")
    args = ap.parse_args()

    log_paths = [Path(p) for p in args.logs]
    current = ROOT / "counter_scan_current.log"
    log_rows, summaries, combat_bans = parse_logs(log_paths, current=current)
    json_rows = load_json_snapshot(Path(args.json))
    snap_rows, snap_sum = _last_snapshot_from_current(current)
    if snap_sum:
        summaries = [snap_sum] + [s for s in summaries if s is not snap_sum]
    json_fresh = False
    json_path = Path(args.json)
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            updated = data.get("updated_at", "")
            if updated:
                age = time.time() - time.mktime(time.strptime(updated, "%Y-%m-%d %H:%M:%S"))
                json_fresh = age < 600
                json_rows = load_json_snapshot(json_path)
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    if json_fresh and json_rows:
        merged = json_rows
    elif snap_rows:
        merged = dict(snap_rows)
    else:
        merged = merge_rows(log_rows, json_rows, prefer_log=True)

    if not merged and not summaries:
        print("Нет данных: логи пусты или не найдены.")
        return

    csv_path = None if args.no_csv else Path(args.csv)
    print_report(merged, summaries, combat_bans, top=args.top, csv_path=csv_path)


if __name__ == "__main__":
    main()