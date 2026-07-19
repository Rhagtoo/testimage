#!/usr/bin/env python3
"""
Discovery Engine — demo v2: pre-seed кластеры реальными ID,
затем симулируем экспансию.
"""
import sys
import random
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
    print(f"Ground truth: {len(all_ids)} ID")
    print(f"Пространство: {62**7:,}")

    # ═══════════════════════════════════════════════════════════════
    # Pre-seed: берём 1000 случайных ID из ground truth
    # и «притворяемся», что нашли их через upload-based discovery
    # ═══════════════════════════════════════════════════════════════
    random.seed(42)
    seed_ids = random.sample(sorted(all_ids), 1000)

    engine = DiscoveryEngine()
    for gid in seed_ids:
        engine.on_gallery_found(gid)

    stats = engine.stats
    print(f"\n{'='*60}")
    print(f"PRE-SEED: 1,000 ID из upload-based discovery")
    print(f"{'='*60}")
    print(f"  Кластеров: {engine.cluster_count}")
    print(f"  Активных (≥2 ID): {stats['active_clusters']}")
    print(f"  Синглтонов: {stats['singleton_clusters']}")
    print(f"  Tight: {stats['tight_clusters']}")

    # Покажем топ-5 активных кластеров
    active = engine.active_clusters()
    active.sort(key=lambda c: c.expected_value, reverse=True)
    print(f"\n  Топ-5 кластеров по EV:")
    for c in active[:5]:
        print(f"    {c.prefix5}**  ids={c.total_discovered}  tight={c.is_tight}  "
              f"EV={c.expected_value:.4f}  frontier={c.remaining_candidates}  "
              f"P(hit)={c.posterior.expected_hit_rate:.3f}")

    # ═══════════════════════════════════════════════════════════════
    # Экспансия: 500 запросов через Scheduler
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"ЭКСПАНСИЯ: 500 запросов через Scheduler")
    print(f"(бюджет 5 MISS → ротация транспорта)")
    print(f"{'='*60}")

    engine.update_transport(TransportHealth(blind=False, miss_budget_remaining=5))

    found = []
    misses = 0
    rotations = 0
    consecutive_miss = 0
    max_checks = 500

    # Пошаговая детализация первых 30 запросов
    detailed = 30

    for i in range(max_checks):
        result = engine.next_candidate()
        if result is None:
            print(f"  [STOP] Scheduler exhausted после {i} запросов")
            break

        gid, source_id = result
        source_type = source_id.split(":")[0]

        if gid in all_ids:
            # Проверяем — этот ID уже в seed или нет
            is_new = gid not in seed_ids
            if is_new:
                found.append(gid)
            engine.on_expansion_hit(source_id, gid)
            if i < detailed:
                tag = "NEW" if is_new else "SEED"
                print(f"  [{i:3d}] HIT  [{tag}] {gid}  src={source_id}  ev={engine.get_cluster_for_gid(gid).expected_value:.3f}" if engine.get_cluster_for_gid(gid) else f"  [{i:3d}] HIT  [{tag}] {gid}  src={source_id}")
            consecutive_miss = 0
        else:
            engine.on_expansion_miss(source_id, gid)
            misses += 1
            consecutive_miss += 1
            if i < detailed:
                print(f"  [{i:3d}] MISS      {gid}  src={source_id}  streak={consecutive_miss}")

        if consecutive_miss >= 5:
            rotations += 1
            if i < detailed:
                print(f"  [{i:3d}] >>> ROTATE transport (5 consecutive MISS)")
            consecutive_miss = 0
            engine.update_transport(TransportHealth(blind=False, miss_budget_remaining=5))

    print(f"\n  --- Результаты ---")
    print(f"  Запросов: {max_checks}")
    print(f"  Найдено НОВЫХ: {len(found)}")
    print(f"  MISS: {misses}")
    print(f"  Ротаций транспорта: {rotations}")
    if found:
        print(f"  Hit rate (новые): {len(found)/max_checks*100:.1f}%")
        print(f"  MISS / новую находку: {misses/len(found):.1f}")
    else:
        print(f"  Hit rate (новые): 0%")

    # ═══════════════════════════════════════════════════════════════
    # Сравнение: случайный поиск
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"СРАВНЕНИЕ: случайный поиск (500 запросов)")
    print(f"{'='*60}")

    random.seed(999)
    random_found = 0
    for _ in range(500):
        gid = "".join(random.choices(CHARSET, k=ID_LENGTH))
        if gid in all_ids and gid not in seed_ids:
            random_found += 1

    print(f"  Найдено новых: {random_found}")
    print(f"  Hit rate: {random_found/500*100:.4f}%")

    expansion_rate = len(found) / max_checks * 100 if found else 0
    random_rate = random_found / 500 * 100

    print(f"\n  {'='*50}")
    print(f"  ИТОГ:")
    print(f"    Случайный поиск:     {random_rate:.4f}% hit rate")
    print(f"    Кластерная экспансия: {expansion_rate:.1f}% hit rate")
    if random_rate > 0:
        print(f"    Улучшение:            ×{expansion_rate/random_rate:,.0f}")
    else:
        print(f"    Улучшение:            ∞ (случайный поиск нашёл 0)")

    # ═══════════════════════════════════════════════════════════════
    # Статистика кластеров после экспансии
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'='*60}")
    print(f"СТАТИСТИКА КЛАСТЕРОВ ПОСЛЕ ЭКСПАНСИИ")
    print(f"{'='*60}")

    final = engine.stats
    exhausted = sum(
        1 for c in engine._clusters.values()
        if c.state == ClusterState.EXHAUSTED
    )
    dead = sum(
        1 for c in engine._clusters.values()
        if c.state == ClusterState.DEAD
    )
    active_clusters = engine.active_clusters()

    print(f"  Кластеров всего:     {engine.cluster_count}")
    print(f"  Активных:            {len(active_clusters)}")
    print(f"  Exhausted:           {exhausted}")
    print(f"  Dead:                {dead}")
    print(f"  Синглтонов:          {final['singleton_clusters']}")
    print(f"  Tight:               {final['tight_clusters']}")

    if active_clusters:
        active_clusters.sort(key=lambda c: c.expected_value, reverse=True)
        print(f"\n  Топ-5 активных кластеров:")
        for c in active_clusters[:5]:
            print(f"    {c.prefix5}**  ids={c.total_discovered}  tight={c.is_tight}  "
                  f"EV={c.expected_value:.4f}  frontier={c.remaining_candidates}  "
                  f"P(hit)={c.posterior.expected_hit_rate:.3f}  "
                  f"hits={c.posterior.hits} misses={c.posterior.misses}")

    # Покажем один exhausted кластер
    exhausted_clusters = [
        c for c in engine._clusters.values()
        if c.state == ClusterState.EXHAUSTED
    ]
    if exhausted_clusters:
        c = exhausted_clusters[0]
        print(f"\n  Пример exhausted кластера:")
        print(f"    {c.prefix5}**  ids={c.total_discovered}  total_tested={c.tested.count_set()}")
        discovered_suffixes = sorted(gid[5:] for gid in c.discovered_ids)
        print(f"    Суффиксы: {discovered_suffixes}")


if __name__ == "__main__":
    main()
