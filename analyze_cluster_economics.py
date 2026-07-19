#!/usr/bin/env python3
"""
КЛЮЧЕВАЯ МЕТРИКА: распределение соседей prefix_len=6.

Для каждого найденного prefix6 проверяем ВСЕ 61 альтернативу последнего символа
против полного набора из 89K ID (без API-запросов, локально).

Ответ на вопрос: «если я нашёл галерею с этим prefix6, сколько ещё галерей
с этим же prefix6 существует в природе?»
"""
import sys
import random
from collections import Counter, defaultdict
from pathlib import Path
from dataclasses import dataclass, field

CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
CHAR_IDX = {c: i for i, c in enumerate(CHARSET)}
ID_LENGTH = 7


def load_ids(path: Path) -> set[str]:
    ids = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if len(line) == ID_LENGTH and all(c in CHAR_IDX for c in line):
            ids.add(line)
    return ids


def main():
    all_ids = load_ids(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/unique_gids.txt"))
    print(f"Всего уникальных ID: {len(all_ids)}")
    print(f"Теоретическое пространство: {62**7:,}\n")

    # ========== 1. Полный скан prefix_len=6 ==========
    print("=" * 70)
    print("1. ПОЛНЫЙ СКАН PREFIX6: ВСЕ 61 АЛЬТЕРНАТИВА")
    print("   (локально, без API)")
    print("=" * 70)

    # Группируем по prefix6
    by_prefix6: dict[str, set[str]] = defaultdict(set)
    for gid in all_ids:
        by_prefix6[gid[:6]].add(gid)

    print(f"Уникальных prefix6: {len(by_prefix6)}")

    # Распределение: сколько ID на prefix6
    cluster_size_dist: Counter = Counter()
    for gids in by_prefix6.values():
        cluster_size_dist[len(gids)] += 1

    print(f"\nРаспределение размеров кластеров (prefix6):")
    print(f"  {'Размер':>6} {'Кластеров':>10} {'%':>8} {'Кумул.%':>8}")
    print(f"  {'-'*6} {'-'*10} {'-'*8} {'-'*8}")
    total = len(by_prefix6)
    cumul = 0
    for size in sorted(cluster_size_dist):
        count = cluster_size_dist[size]
        pct = count / total * 100
        cumul += pct
        print(f"  {size:>6} {count:>10} {pct:>7.1f}% {cumul:>7.1f}%")

    # ========== 2. Ключевой вопрос ==========
    print("\n" + "=" * 70)
    print("2. ЕСЛИ Я НАШЁЛ ГАЛЕРЕЮ С ЭТИМ PREFIX6,")
    print("   СКОЛЬКО ВСЕГО ГАЛЕРЕЙ С ЭТИМ PREFIX6 СУЩЕСТВУЕТ?")
    print("=" * 70)

    # Для каждого prefix6, который содержит хотя бы 1 ID,
    # подсчитываем общее количество ID с этим префиксом
    sizes = list(cluster_size_dist.elements())

    print(f"\n  Всего кластеров: {len(sizes)}")
    print(f"  Средний размер: {sum(sizes)/len(sizes):.2f}")
    print(f"  Медианный размер: {sorted(sizes)[len(sizes)//2]}")

    size_counter = Counter(sizes)
    print(f"\n  {'Кол-во ID в кластере':>22} {'Кластеров':>10} {'%':>8} {'Кумул.%':>8}")
    print(f"  {'-'*22} {'-'*10} {'-'*8} {'-'*8}")
    cumul = 0
    for size in sorted(size_counter):
        count = size_counter[size]
        pct = count / total * 100
        cumul += pct
        extra = size - 1  # дополнительных галерей сверх найденной
        bar = "█" * min(count, 60)
        print(f"  {f'{size} (найдено +{extra})':>22} {count:>10} {pct:>7.1f}% {cumul:>7.1f}%  {bar}")

    # ========== 3. Ожидаемая ценность кластера ==========
    print("\n" + "=" * 70)
    print("3. ОЖИДАЕМАЯ ЦЕННОСТЬ: СКОЛЬКО ДОПОЛНИТЕЛЬНЫХ ID")
    print("   Я НАЙДУ, ПРОСКАНИРОВАВ 61 ВАРИАНТ?")
    print("=" * 70)

    # Сколько доп. галерей мы бы нашли, просканировав все 61 вариант?
    extra_dist: Counter = Counter()
    for gids in by_prefix6.values():
        extra = len(gids) - 1  # минус уже известная
        extra_dist[extra] += 1

    print(f"\n  {'Доп. галерей':>12} {'Кластеров':>10} {'%':>8} {'Кумул.%':>8}")
    print(f"  {'-'*12} {'-'*10} {'-'*8} {'-'*8}")
    cumul = 0
    for extra in sorted(extra_dist):
        count = extra_dist[extra]
        pct = count / total * 100
        cumul += pct
        bar = "█" * min(count, 60)
        print(f"  {extra:>12} {count:>10} {pct:>7.1f}% {cumul:>7.1f}%  {bar}")

    # Ожидаемое значение
    weighted = sum(extra * count for extra, count in extra_dist.items())
    print(f"\n  Ожидаемое кол-во ДОПОЛНИТЕЛЬНЫХ галерей: {weighted/total:.2f}")
    print(f"  (при сканировании всех 61 вариантов на найденную галерею)")

    # ========== 4. Симуляция стратегий ==========
    print("\n" + "=" * 70)
    print("4. СИМУЛЯЦИЯ: СКОЛЬКО MISS НА 1 НАЙДЕННУЮ ГАЛЕРЕЮ?")
    print("=" * 70)

    # Стратегия: сканируем все 61 вариант, считаем найденные и miss
    total_misses = 0
    total_found = 0
    for gids in by_prefix6.values():
        extra = len(gids) - 1  # дополнительно найденные
        misses = 61 - extra     # мимо
        total_found += extra
        total_misses += misses

    total_clusters = len(by_prefix6)
    print(f"\n  Кластеров: {total_clusters}")
    print(f"  Дополнительно найдено: {total_found}")
    print(f"  Всего MISS: {total_misses}")
    print(f"  MISS / кластер: {total_misses/total_clusters:.1f}")
    print(f"  MISS / найденный ID: {total_misses/total_found:.1f}")
    print(f"  Hit rate при сканировании 61: {total_found/(total_found+total_misses)*100:.1f}%")

    # ========== 5. Бюджетирование: а что если лимит 5 MISS? ==========
    print("\n" + "=" * 70)
    print("5. БЮДЖЕТИРОВАНИЕ: СТРАТЕГИЯ «СКАНИРУЙ ПОКА MISS ≤ 5»")
    print("=" * 70)

    # Для каждого кластера симулируем: сканируем варианты,
    # останавливаемся после 5 подряд MISS
    found_with_budget = 0
    missed_due_to_budget = 0
    total_checked_budget = 0
    hits_before_stop: list[int] = []

    for gids in by_prefix6.values():
        existing = gids
        checked = 0
        miss_streak = 0
        found_extra = 0
        max_miss = 5

        # Сканируем все 61 вариант, но останавливаемся при 5 подряд MISS
        for last_char in CHARSET:
            candidate = list(gids)[0][:6] + last_char
            if candidate in existing:
                continue  # это уже известная галерея, не считаем

            checked += 1
            if candidate in all_ids:
                found_extra += 1
                miss_streak = 0
            else:
                miss_streak += 1

            if miss_streak >= max_miss:
                missed_due_to_budget += 61 - checked  # остальные не проверили
                break

        total_checked_budget += checked
        hits_before_stop.append(found_extra)

    print(f"\n  С лимитом {max_miss} подряд MISS:")
    print(f"  Проверено вариантов в среднем: {total_checked_budget/total_clusters:.1f}")
    print(f"  Найдено доп. галерей: {sum(hits_before_stop)}")
    print(f"  Пропущено из-за бюджета: {missed_due_to_budget}")
    print(f"  Эффективность: {sum(hits_before_stop)/(sum(hits_before_stop)+total_checked_budget)*100:.1f}% hit rate")

    counter_hits = Counter(hits_before_stop)
    print(f"\n  Распределение найденных доп. галерей (с бюджетом {max_miss}):")
    for h in sorted(counter_hits):
        print(f"    {h} доп.: {counter_hits[h]:>6} кластеров ({counter_hits[h]/total_clusters*100:.1f}%)")

    # ========== 6. Визуализация кластеров ==========
    print("\n" + "=" * 70)
    print("6. ПРИМЕРЫ КЛАСТЕРОВ")
    print("=" * 70)

    # Кластеры размера 5 (максимум в нашем датасете)
    max_size = max(cluster_size_dist)
    max_clusters = [(p, gids) for p, gids in by_prefix6.items() if len(gids) == max_size]
    print(f"\n  Кластеры максимального размера ({max_size}):")
    for prefix, gids in sorted(max_clusters)[:10]:
        chars = sorted(g[6] for g in gids)
        print(f"    {prefix}[{''.join(chars)}] → {sorted(gids)}")

    # Кластеры размера 4
    size4 = [(p, gids) for p, gids in by_prefix6.items() if len(gids) == 4]
    print(f"\n  Примеры кластеров размера 4 (всего {len(size4)}):")
    sample4 = random.sample(size4, min(5, len(size4)))
    for prefix, gids in sorted(sample4):
        chars = sorted(g[6] for g in gids)
        print(f"    {prefix}[{''.join(chars)}] → {sorted(gids)}")

    # Кластеры размера 3
    size3 = [(p, gids) for p, gids in by_prefix6.items() if len(gids) == 3]
    print(f"\n  Примеры кластеров размера 3 (всего {len(size3)}):")
    sample3 = random.sample(size3, min(5, len(size3)))
    for prefix, gids in sorted(sample3):
        chars = sorted(g[6] for g in gids)
        print(f"    {prefix}[{''.join(chars)}] → {sorted(gids)}")

    # ========== 7. Выводы для архитектуры ==========
    print("\n" + "=" * 70)
    print("7. ВЫВОДЫ ДЛЯ АРХИТЕКТУРЫ")
    print("=" * 70)

    e_extra = weighted / total_clusters
    miss_per_extra = (61 - e_extra) / e_extra if e_extra > 0 else float('inf')
    clusters_with_neighbors = sum(1 for gids in by_prefix6.values() if len(gids) >= 2)

    print(f"""
  На 89K найденных ID:

  • Кластеров с ≥2 ID (есть соседи): {clusters_with_neighbors} ({clusters_with_neighbors/total_clusters*100:.1f}%)
  • Кластеров ровно с 1 ID (одиночки):  {total_clusters - clusters_with_neighbors} ({(total_clusters-clusters_with_neighbors)/total_clusters*100:.1f}%)
  • Ожидаемое доп. галерей на кластер: {e_extra:.2f}
  • MISS на 1 доп. найденную галерею:   {miss_per_extra:.1f}

  Стратегия «проверить 61 вариант»:
  → hit rate {total_found/(total_found+total_misses)*100:.1f}%
  → окупается при бюджете > {miss_per_extra:.0f} MISS на кластер
""")


if __name__ == "__main__":
    main()
