#!/usr/bin/env python3
"""
Production-scale benchmark: 10K seed IDs → 5K expansions.
"""
import sys
import random
import time
from pathlib import Path

from discovery_engine import (
    DiscoveryEngine,
    TransportHealth,
    ClusterState,
    CHARSET,
    ID_LENGTH,
)


def load_ids(path: Path) -> set[str]:
    ids = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if len(line) == ID_LENGTH and all(c in CHARSET for c in line):
            ids.add(line)
    return ids


def main():
    all_ids = load_ids(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/unique_gids.txt"))

    # ═══════════════════════════════════════════════════════════════
    # Pre-seed: 10,000 ID
    # ═══════════════════════════════════════════════════════════════
    random.seed(42)
    seed_ids = set(random.sample(sorted(all_ids), 10_000))

    t0 = time.monotonic()
    engine = DiscoveryEngine()
    for gid in seed_ids:
        engine.on_gallery_found(gid)
    t_seed = time.monotonic() - t0

    stats = engine.stats
    active = engine.active_clusters()
    tight_active = [c for c in active if c.is_tight]

    print(f"SEED: {len(seed_ids)} ID за {t_seed*1000:.0f}ms")
    print(f"  Кластеров: {engine.cluster_count}")
    print(f"  Активных:  {len(active)}")
    print(f"  Tight:     {len(tight_active)}")
    print(f"  Синглтонов:{stats['singleton_clusters']}")

    # Распределение размеров активных кластеров
    from collections import Counter
    size_dist = Counter(c.total_discovered for c in active)
    print(f"  Распределение размеров активных кластеров:")
    for size, count in sorted(size_dist.items()):
        print(f"    {size} ID: {count} кластеров")

    # ═══════════════════════════════════════════════════════════════
    # Экспансия: 5,000 запросов
    # ═══════════════════════════════════════════════════════════════
    engine.update_transport(TransportHealth(blind=False, miss_budget_remaining=5))

    t0 = time.monotonic()
    found_new = []
    misses = 0
    rotations = 0
    consecutive_miss = 0
    max_checks = 5000

    for i in range(max_checks):
        result = engine.next_candidate()
        if result is None:
            break
        gid, source_id = result

        if gid in all_ids:
            is_new = gid not in seed_ids
            if is_new:
                found_new.append(gid)
            engine.on_expansion_hit(source_id, gid)
            consecutive_miss = 0
        else:
            engine.on_expansion_miss(source_id, gid)
            misses += 1
            consecutive_miss += 1

        if consecutive_miss >= 5:
            rotations += 1
            consecutive_miss = 0
            engine.update_transport(TransportHealth(blind=False, miss_budget_remaining=5))

    t_expand = time.monotonic() - t0

    # ═══════════════════════════════════════════════════════════════
    # Результаты
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"ЭКСПАНСИЯ: {max_checks} запросов за {t_expand*1000:.0f}ms")
    print(f"{'='*60}")
    print(f"  Найдено новых:    {len(found_new)}")
    print(f"  MISS:             {misses}")
    print(f"  Hit rate:         {len(found_new)/max_checks*100:.2f}%")
    print(f"  MISS / находку:   {misses/len(found_new):.1f}" if found_new else "  MISS / находку: N/A")
    print(f"  Ротаций:          {rotations}")

    # Сравнение
    random.seed(999)
    random_found = 0
    for _ in range(max_checks):
        gid = "".join(random.choices(CHARSET, k=ID_LENGTH))
        if gid in all_ids and gid not in seed_ids:
            random_found += 1

    expansion_rate = len(found_new) / max_checks * 100
    random_rate = random_found / max_checks * 100
    improvement = expansion_rate / random_rate if random_rate > 0 else float('inf')

    print(f"\n  СРАВНЕНИЕ:")
    print(f"    Случайный поиск:     {random_rate:.6f}% hit rate")
    print(f"    Кластерная экспансия: {expansion_rate:.2f}% hit rate")
    print(f"    Улучшение:            ×{improvement:,.0f}")

    # Статистика кластеров
    final_active = engine.active_clusters()
    exhausted = sum(1 for c in engine._clusters.values() if c.state == ClusterState.EXHAUSTED)
    dead = sum(1 for c in engine._clusters.values() if c.state == ClusterState.DEAD)

    print(f"\n  СТАТИСТИКА КЛАСТЕРОВ:")
    print(f"    Активных:   {len(final_active)}")
    print(f"    Exhausted:  {exhausted}")
    print(f"    Dead:       {dead}")

    # Топ-10 по EV
    final_active.sort(key=lambda c: c.expected_value, reverse=True)
    print(f"\n  Топ-10 кластеров по EV:")
    for c in final_active[:10]:
        print(f"    {c.prefix5}**  ids={c.total_discovered}  tight={c.is_tight}  "
              f"EV={c.expected_value:.4f}  P(hit)={c.posterior.expected_hit_rate:.4f}  "
              f"h/m={c.posterior.hits}/{c.posterior.misses}  "
              f"frontier={c.remaining_candidates}")


if __name__ == "__main__":
    main()
