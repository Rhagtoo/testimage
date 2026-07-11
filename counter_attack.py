#!/usr/bin/env python3
"""
Обогащение CSV серверным временем из JSONL, оценка rate счётчика, предсказание + окно ID.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

CHARSET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
M = 62**7
TRIED_FILE = Path("tried_galleries.txt")


def gid_from_line(line: str) -> str | None:
    line = line.strip()
    if not line:
        return None
    tok = line.split()[0].split("/")[-1].rstrip("/")
    if len(tok) == 7 and tok.isalnum():
        return tok
    return None


def load_tried_gids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    seen: set[str] = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if gid := gid_from_line(line):
                seen.add(gid)
    return seen


def parse_server_ms(rec: dict) -> float | None:
    it = rec.get("id_timing") or {}
    for key in ("server_resp_date_utc", "id_ready_utc", "client_resp_recv_utc"):
        raw = it.get(key)
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return dt.timestamp() * 1000.0
        except (ValueError, TypeError):
            continue
    for post in rec.get("upload_posts") or []:
        raw = post.get("server_resp_date_utc")
        if raw:
            try:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                return dt.timestamp() * 1000.0
            except (ValueError, TypeError):
                pass
    return None


def load_intel(paths: list[Path]) -> dict[str, float]:
    out: dict[str, float] = {}
    for path in paths:
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                gid = rec.get("gid")
                if not gid:
                    continue
                ms = parse_server_ms(rec)
                if ms is not None:
                    out[gid] = ms
    return out


def base62_to_gid(n: int) -> str | None:
    if n < 0:
        return None
    chars: list[str] = []
    x = n
    for _ in range(7):
        x, r = divmod(x, 62)
        chars.append(CHARSET[r])
    if x != 0:
        return None
    return "".join(reversed(chars))


def enrich(csv_path: Path, intel_paths: list[Path], out_path: Path) -> list[dict]:
    intel = load_intel(intel_paths)
    rows: list[dict] = []
    with open(csv_path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            gid = row["gid"]
            row["base62_int"] = int(row["base62"])
            row["server_time_ms"] = intel.get(gid)
            rows.append(row)

    matched = [r for r in rows if r["server_time_ms"] is not None]
    matched.sort(key=lambda r: float(r["server_time_ms"]))

    prev_b: int | None = None
    for r in matched:
        if prev_b is not None:
            r["real_delta"] = r["base62_int"] - prev_b
        else:
            r["real_delta"] = ""
        prev_b = r["base62_int"]

    fieldnames = list(rows[0].keys()) if rows else []
    for extra in ("base62_int", "server_time_ms", "real_delta"):
        if extra not in fieldnames:
            fieldnames.append(extra)

    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(matched)

    return matched


def analyze(rows: list[dict]) -> dict:
    if len(rows) < 2:
        return {}
    deltas = [int(r["real_delta"]) for r in rows[1:] if r.get("real_delta") != ""]
    first, last = rows[0], rows[-1]
    span_s = (float(last["server_time_ms"]) - float(first["server_time_ms"])) / 1000.0
    total = int(last["base62_int"]) - int(first["base62_int"])
    rate = total / span_s if span_s > 0 else 0.0
    mono = all(int(rows[i]["base62_int"]) >= int(rows[i - 1]["base62_int"]) for i in range(1, len(rows)))
    neg = sum(1 for d in deltas if d < 0)
    pos = sum(1 for d in deltas if d > 0)
    small = sum(1 for d in deltas if 0 < d < 100)
    return {
        "n": len(rows),
        "span_s": span_s,
        "total_delta": total,
        "rate_per_s": rate,
        "monotonic": mono,
        "pos_deltas": pos,
        "neg_deltas": neg,
        "small_pos_deltas": small,
        "median_delta": statistics.median(deltas) if deltas else 0,
        "mean_delta": statistics.mean(deltas) if deltas else 0,
        "last_gid": last["gid"],
        "last_base62": int(last["base62_int"]),
        "last_server_ms": float(last["server_time_ms"]),
        "first_gid": first["gid"],
        "first_base62": int(first["base62_int"]),
    }


def predict_counter(stats: dict, now_ms: float | None = None) -> dict:
    now_ms = now_ms if now_ms is not None else time.time() * 1000.0
    elapsed_s = (now_ms - stats["last_server_ms"]) / 1000.0
    predicted = int(stats["last_base62"] + stats["rate_per_s"] * elapsed_s)
    return {
        "now_ms": now_ms,
        "elapsed_s": elapsed_s,
        "predicted_base62": predicted,
        "predicted_gid": base62_to_gid(predicted),
        "rate_per_s": stats["rate_per_s"],
    }


def window_ids(center: int, half: int) -> list[str]:
    out: list[str] = []
    for n in range(center - half, center + half + 1):
        if 0 <= n < M:
            g = base62_to_gid(n)
            if g:
                out.append(g)
    return out


def time_gap_clusters(
    rows: list[dict],
    *,
    gap_sec: float = 10.0,
    min_size: int = 2,
) -> list[list[dict]]:
    """Группы upload ID с интервалом server_time <= gap_sec."""
    sorted_rows = sorted(rows, key=lambda r: float(r["server_time_ms"]))
    out: list[list[dict]] = []
    current: list[dict] = []
    prev_ms: float | None = None

    for row in sorted_rows:
        ms = float(row["server_time_ms"])
        if prev_ms is None or (ms - prev_ms) / 1000.0 <= gap_sec:
            current.append(row)
        else:
            if len(current) >= min_size:
                out.append(current)
            current = [row]
        prev_ms = ms

    if len(current) >= min_size:
        out.append(current)
    return out


def cluster_window_ids(
    cluster_groups: list[list[dict]],
    *,
    half: int = 80,
    max_spread: int = 500,
) -> list[str]:
    """±half вокруг кластера; при огромном spread — по каждому gid отдельно."""
    seen: set[str] = set()
    ordered: list[str] = []

    def add_range(lo: int, hi: int) -> None:
        for n in range(lo - half, hi + half + 1):
            if 0 <= n < M:
                g = base62_to_gid(n)
                if g and g not in seen:
                    seen.add(g)
                    ordered.append(g)

    for items in cluster_groups:
        vals = [int(x["base62_int"]) for x in items]
        lo, hi = min(vals), max(vals)
        if hi - lo <= max_spread:
            add_range(lo, hi)
        else:
            for v in vals:
                add_range(v, v)
    return ordered


def prefix_clusters(rows: list[dict], prefix_len: int = 6) -> list[tuple[str, list[dict]]]:
    from collections import defaultdict

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["gid"][:prefix_len]].append(r)
    out: list[tuple[str, list[dict]]] = []
    for pre, items in groups.items():
        if len(items) >= 2:
            items.sort(key=lambda x: float(x["server_time_ms"]))
            vals = [int(x["base62_int"]) for x in items]
            if max(vals) - min(vals) < 500:
                out.append((pre, items))
    out.sort(key=lambda x: max(int(i["base62_int"]) for i in x[1]) - min(int(i["base62_int"]) for i in x[1]))
    return out


def build_scan_windows(
    rows: list[dict],
    *,
    half: int = 500,
    cluster_half: int = 80,
) -> list[str]:
    """Окна вокруг каждого ID + tight prefix-кластеров."""
    seen: set[str] = set()
    ordered: list[str] = []

    def add_center(center: int) -> None:
        for n in range(center - half, center + half + 1):
            if 0 <= n < M:
                g = base62_to_gid(n)
                if g and g not in seen:
                    seen.add(g)
                    ordered.append(g)

    for r in rows:
        add_center(int(r["base62_int"]))

    for _, items in prefix_clusters(rows):
        lo = min(int(x["base62_int"]) for x in items)
        hi = max(int(x["base62_int"]) for x in items)
        for n in range(lo - cluster_half, hi + cluster_half + 1):
            if 0 <= n < M:
                g = base62_to_gid(n)
                if g and g not in seen:
                    seen.add(g)
                    ordered.append(g)

    return ordered


def main() -> None:
    ap = argparse.ArgumentParser(description="Counter intel: enrich, rate, predict")
    ap.add_argument("--csv", default="upload_burst_9334.csv")
    ap.add_argument("--intel", action="append", default=[])
    ap.add_argument("--out", default="enriched_counter.csv")
    ap.add_argument("--window", type=int, default=2000, help="±N вокруг predict (если monotonic)")
    ap.add_argument("--window-out", default="counter_window_ids.txt")
    ap.add_argument("--scan-half", type=int, default=500, help="±N вокруг каждого известного ID")
    ap.add_argument("--scan-out", default="scan_windows.txt")
    ap.add_argument("--scan-recent", type=int, default=80, help="сколько последних ID для recent-окна")
    ap.add_argument("--recent-half", type=int, default=200, help="±N для recent-окна")
    ap.add_argument("--clusters-out", default="scan_clusters.txt")
    ap.add_argument("--cluster-half", type=int, default=80, help="±N вокруг кластера")
    ap.add_argument("--time-gap", type=float, default=10.0, help="макс. сек между соседями в time-кластере")
    ap.add_argument(
        "--fresh-out",
        default="scan_clusters_fresh.txt",
        help="окна только вокруг последних upload ID",
    )
    ap.add_argument(
        "--fresh-count",
        type=int,
        default=120,
        help="сколько последних upload ID для fresh-окна",
    )
    ap.add_argument(
        "--tried-file",
        default=str(TRIED_FILE),
        help="пробованные ID — исключить из fresh (default tried_galleries.txt)",
    )
    ap.add_argument(
        "--fresh-cap",
        type=int,
        default=800,
        help="макс. untried ID в fresh за цикл (0=без лимита)",
    )
    args = ap.parse_args()

    csv_path = Path(args.csv)
    intel_paths = [Path(p) for p in args.intel] or [
        Path("upload_burst_9334.jsonl"),
        Path("upload_intel.jsonl"),
    ]
    out_path = Path(args.out)

    rows = enrich(csv_path, intel_paths, out_path)
    stats = analyze(rows)

    print(f"enriched → {out_path}  matched={stats.get('n', 0)}")
    if not stats:
        return

    print(f"span={stats['span_s']:.0f}s  total_delta={stats['total_delta']:,}")
    print(f"rate={stats['rate_per_s']:,.2f} galleries/sec")
    print(f"monotonic_by_server_time={stats['monotonic']}")
    print(f"deltas: pos={stats['pos_deltas']} neg={stats['neg_deltas']} small(0<d<100)={stats['small_pos_deltas']}")
    print(f"median_delta={stats['median_delta']:,.0f}  mean_delta={stats['mean_delta']:,.0f}")
    print(f"anchor: {stats['last_gid']} base62={stats['last_base62']:,}")

    prefix_cl = prefix_clusters(rows)
    time_cl = time_gap_clusters(rows, gap_sec=args.time_gap)
    print(f"\nprefix6 tight clusters: {len(prefix_cl)}")
    for pre, items in prefix_cl[:8]:
        gids = [x["gid"] for x in items]
        spread = max(int(x["base62_int"]) for x in items) - min(int(x["base62_int"]) for x in items)
        print(f"  {pre}: spread={spread} -> {gids}")

    print(f"time-gap clusters (≤{args.time_gap}s): {len(time_cl)}")
    for items in time_cl[:5]:
        gids = [x["gid"] for x in items]
        t0 = float(items[0]["server_time_ms"]) / 1000.0
        t1 = float(items[-1]["server_time_ms"]) / 1000.0
        print(f"  span={t1 - t0:.1f}s n={len(gids)} -> {gids[:4]}{'...' if len(gids) > 4 else ''}")

    if args.scan_half > 0:
        scan_ids = build_scan_windows(rows, half=args.scan_half)
        Path(args.scan_out).write_text("\n".join(scan_ids) + "\n", encoding="utf-8")
        print(f"\nfull scan ±{args.scan_half}: {len(scan_ids)} IDs → {args.scan_out}")

    # Полный скан: prefix6 (проверенный режим, ~5–8k ID)
    prefix_groups = [items for _, items in prefix_cl]
    cluster_ids = cluster_window_ids(prefix_groups, half=args.cluster_half)
    Path(args.clusters_out).write_text("\n".join(cluster_ids) + "\n", encoding="utf-8")
    print(f"cluster scan ±{args.cluster_half}: {len(cluster_ids)} IDs → {args.clusters_out}")

    # Fresh: ±cluster_half вокруг каждого из последних N upload (новые якоря, без random)
    fresh_rows = rows[-args.fresh_count :] if args.fresh_count > 0 else []
    fresh_ids: list[str] = []
    seen_f: set[str] = set()
    for r in fresh_rows:
        c = int(r["base62_int"])
        for n in range(c - args.cluster_half, c + args.cluster_half + 1):
            if 0 <= n < M:
                g = base62_to_gid(n)
                if g and g not in seen_f:
                    seen_f.add(g)
                    fresh_ids.append(g)
    if args.tried_file:
        tried = load_tried_gids(Path(args.tried_file))
        if tried:
            before = len(fresh_ids)
            fresh_ids = [g for g in fresh_ids if g not in tried]
            print(
                f"fresh untried: {len(fresh_ids)}/{before} "
                f"(skip {before - len(fresh_ids)} из {args.tried_file})",
            )
    if args.fresh_cap > 0 and len(fresh_ids) > args.fresh_cap:
        fresh_ids = fresh_ids[-args.fresh_cap :]
        print(f"fresh cap: {len(fresh_ids)} newest untried (limit {args.fresh_cap})")
    if args.fresh_out:
        Path(args.fresh_out).write_text(
            ("\n".join(fresh_ids) + "\n") if fresh_ids else "",
            encoding="utf-8",
        )
        print(f"fresh ±{args.cluster_half} x{len(fresh_rows)}: {len(fresh_ids)} IDs → {args.fresh_out}")

    recent = rows[-args.scan_recent :] if args.scan_recent > 0 else []
    recent_ids: list[str] = []
    seen_r: set[str] = set()
    for r in recent:
        c = int(r["base62_int"])
        for n in range(c - args.recent_half, c + args.recent_half + 1):
            if 0 <= n < M:
                g = base62_to_gid(n)
                if g and g not in seen_r:
                    seen_r.add(g)
                    recent_ids.append(g)
    recent_out = Path(args.clusters_out).with_name("scan_recent.txt")
    recent_out.write_text("\n".join(recent_ids) + "\n", encoding="utf-8")
    print(f"recent ±{args.recent_half} x{len(recent)}: {len(recent_ids)} IDs → {recent_out}")

    if stats["monotonic"] and stats["rate_per_s"] > 0:
        pred = predict_counter(stats)
        print(f"\npredict NOW: base62={pred['predicted_base62']:,} gid={pred['predicted_gid']}")
        print(f"  (+{pred['elapsed_s']:.0f}s since anchor, rate={pred['rate_per_s']:,.2f}/s)")
        if args.window > 0 and pred["predicted_gid"]:
            ids = window_ids(pred["predicted_base62"], args.window)
            Path(args.window_out).write_text("\n".join(ids) + "\n", encoding="utf-8")
            print(f"predict window ±{args.window} → {len(ids)} IDs → {args.window_out}")
    else:
        print(
            "\n⚠ linear predictor OFF: base62 НЕ монотонен по server_time "
            f"(neg={stats['neg_deltas']}, total_delta={stats['total_delta']:,}). "
            "Используй scan_windows.txt + aggressive-blind, не rate-экстраполяцию."
        )


if __name__ == "__main__":
    main()