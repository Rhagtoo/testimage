#!/usr/bin/env python3
"""
Метрика для prefix_len=5: экономика экспансии с фиксированным
первым символом суффикса.
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

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
    print(f"Всего ID: {len(all_ids)}")

    # Группируем по prefix5
    by_prefix5: dict[str, set[str]] = defaultdict(set)
    for gid in all_ids:
        by_prefix5[gid[:5]].add(gid)

    total_prefix5 = len(by_prefix5)
    print(f"Уникальных prefix5: {total_prefix5}")

    # ========== 1. Размеры кластеров prefix5 ==========
    print("\n" + "=" * 70)
    print("1. РАЗМЕРЫ КЛАСТЕРОВ PREFIX5")
    print("=" * 70)

    size_dist = Counter()
    for gids in by_prefix5.values():
        size_dist[len(gids)] += 1

    print(f"  {'Размер':>6} {'Кластеров':>10} {'%':>8} {'Кумул.%':>8}")
    print(f"  {'-'*6} {'-'*10} {'-'*8} {'-'*8}")
    cumul = 0
    for size in sorted(size_dist):
        count = size_dist[size]
        pct = count / total_prefix5 * 100
        cumul += pct
        bar = "█" * min(count // 20, 60) if count >= 20 else ""
        print(f"  {size:>6} {count:>10} {pct:>7.1f}% {cumul:>7.1f}%  {bar}")

    # ========== 2. КЛЮЧЕВАЯ МЕТРИКА: экспансия с фикс. первым символом ==========
    print("\n" + "=" * 70)
    print("2. ЭКСПАНСИЯ С ФИКС. ПЕРВЫМ СИМВОЛОМ СУФФИКСА")
    print("   Для каждого prefix5: берём суффикс найденного ID")
    print("   Фиксируем первый символ, проверяем 61 вариант второго")
    print("=" * 70)

    # Для каждого prefix5 с хотя бы 1 ID
    extra_by_first_char: dict[int, list[int]] = defaultdict(list)
    # extra_by_first_char[first_char_of_suffix] → список кол-ва найденных доп. ID

    total_extra = 0
    total_checked = 0
    total_clusters_with_any = 0
    singleton_clusters = 0

    for prefix, existing_ids in by_prefix5.items():
        if len(existing_ids) == 1:
            singleton_clusters += 1

        # Для КАЖДОГО найденного ID в кластере — симулируем экспансию
        for gid in existing_ids:
            suffix = gid[5:]  # 2 chars
            first_char = suffix[0]
            second_char = suffix[1]

            # Все варианты с тем же первым символом (61 вариант, исключая исходный)
            candidates = {prefix + first_char + c for c in CHARSET if c != second_char}

            # Сколько из них реально существуют?
            found = candidates & all_ids
            extra = len(found)  # дополнительных галерей

            extra_by_first_char[len(existing_ids)].append(extra)
            total_extra += extra
            total_checked += 61

            if extra > 0:
                total_clusters_with_any += 1

    print(f"\n  Синглтон-кластеров: {singleton_clusters} ({singleton_clusters/total_prefix5*100:.1f}%)")
    print(f"  Кластеров где хоть одна экспансия дала >0: {total_clusters_with_any}")
    print(f"  Всего проверок (симулировано): {total_checked}")
    print(f"  Всего доп. галерей найдено: {total_extra}")
    print(f"  MISS / доп. галерея: {total_checked/total_extra:.1f}" if total_extra else "  MISS: N/A (ничего не найдено)")

    # ========== 3. Распределение доп. находок ==========
    print("\n" + "=" * 70)
    print("3. РАСПРЕДЕЛЕНИЕ: СКОЛЬКО ДОП. ID ПРИ ЭКСПАНСИИ 61 ВАРИАНТА?")
    print("=" * 70)

    # Собираем все результаты экспансии
    all_expansions: list[int] = []
    for gids in by_prefix5.values():
        for gid in gids:
            suffix = gid[5:]
            first_char = suffix[0]
            candidates = {gid[:5] + first_char + c for c in CHARSET if c != suffix[1]}
            found = len(candidates & all_ids)
            all_expansions.append(found)

    extra_counter = Counter(all_expansions)
    total_expansions = len(all_expansions)

    print(f"\n  Всего экспансий: {total_expansions}")
    print(f"\n  {'Найдено доп.':>12} {'Экспансий':>10} {'%':>8}")
    print(f"  {'-'*12} {'-'*10} {'-'*8}")
    for extra in sorted(extra_counter)[:20]:
        count = extra_counter[extra]
        pct = count / total_expansions * 100
        bar = "█" * min(int(pct), 60)
        print(f"  {extra:>12} {count:>10} {pct:>7.1f}%  {bar}")

    if len(extra_counter) > 20:
        print(f"  ... и ещё {len(extra_counter)-20} значений")

    avg_extra = sum(all_expansions) / total_expansions

    # ========== 4. Стратегия: экспансия только для НЕ-синглтонов ==========
    print("\n" + "=" * 70)
    print("4. УМНАЯ СТРАТЕГИЯ: ЭКСПАНСИЯ ТОЛЬКО ПОСЛЕ 2-ГО НАЙДЕННОГО ID")
    print("   (не тратим бюджет на синглтон-кластеры)")
    print("=" * 70)

    # Сценарий: мы уже нашли ≥2 ID в кластере (например, через случайный скан).
    # Теперь для каждого из них делаем экспансию 61 варианта.
    smart_extra = []
    smart_checked = 0
    smart_total_extra = 0

    for prefix, existing_ids in by_prefix5.items():
        if len(existing_ids) < 2:
            continue

        for gid in existing_ids:
            suffix = gid[5:]
            first_char = suffix[0]
            candidates = {prefix + first_char + c for c in CHARSET if c != suffix[1]}
            found = len(candidates & all_ids)
            smart_extra.append(found)
            smart_checked += 61
            smart_total_extra += found

    print(f"\n  Кластеров с ≥2 ID: {sum(1 for gids in by_prefix5.values() if len(gids) >= 2)}")
    print(f"  Экспансий: {len(smart_extra)}")
    print(f"  Проверок: {smart_checked}")
    print(f"  Доп. галерей найдено: {smart_total_extra}")
    if smart_total_extra:
        print(f"  MISS / доп. галерея: {smart_checked/smart_total_extra:.1f}")
    print(f"  Среднее доп. на экспансию: {sum(smart_extra)/len(smart_extra):.2f}" if smart_extra else "")

    smart_counter = Counter(smart_extra)
    print(f"\n  {'Найдено доп.':>12} {'Экспансий':>10} {'%':>8}")
    print(f"  {'-'*12} {'-'*10} {'-'*8}")
    for extra in sorted(smart_counter)[:15]:
        count = smart_counter[extra]
        pct = count / len(smart_extra) * 100
        bar = "█" * min(int(pct), 60)
        print(f"  {extra:>12} {count:>10} {pct:>7.1f}%  {bar}")

    # ========== 5. Только для tight-кластеров ==========
    print("\n" + "=" * 70)
    print("5. ИДЕАЛЬНАЯ СТРАТЕГИЯ: ЭКСПАНСИЯ ТОЛЬКО TIGHT-КЛАСТЕРОВ")
    print("   (≥2 ID, все суффиксы с одним первым символом)")
    print("=" * 70)

    tight_extra = []
    tight_checked = 0
    tight_total_extra = 0

    for prefix, existing_ids in by_prefix5.items():
        if len(existing_ids) < 2:
            continue
        first_chars = {g[5] for g in existing_ids}
        if len(first_chars) > 1:
            continue  # не tight

        for gid in existing_ids:
            suffix = gid[5:]
            first_char = suffix[0]
            candidates = {prefix + first_char + c for c in CHARSET if c != suffix[1]}
            found = len(candidates & all_ids)
            tight_extra.append(found)
            tight_checked += 61
            tight_total_extra += found

    print(f"\n  Tight-кластеров (≥2 ID, один первый символ): {sum(1 for gids in by_prefix5.values() if len(gids)>=2 and len({g[5] for g in gids})==1)}")
    print(f"  Экспансий: {len(tight_extra)}")
    print(f"  Проверок: {tight_checked}")
    print(f"  Доп. галерей найдено: {tight_total_extra}")
    if tight_total_extra:
        print(f"  MISS / доп. галерея: {tight_checked/tight_total_extra:.1f}")
    if tight_extra:
        print(f"  Среднее доп. на экспансию: {sum(tight_extra)/len(tight_extra):.2f}")

    tight_counter = Counter(tight_extra)
    print(f"\n  {'Найдено доп.':>12} {'Экспансий':>10} {'%':>8}")
    print(f"  {'-'*12} {'-'*10} {'-'*8}")
    for extra in sorted(tight_counter)[:10]:
        count = tight_counter[extra]
        pct = count / len(tight_extra) * 100 if tight_extra else 0
        bar = "█" * min(int(pct), 60)
        print(f"  {extra:>12} {count:>10} {pct:>7.1f}%  {bar}")

    # ========== 6. Сводная таблица ==========
    print("\n" + "=" * 70)
    print("6. СВОДКА: СРАВНЕНИЕ СТРАТЕГИЙ")
    print("=" * 70)

    strategies = [
        ("Полный брутфорс (все 61)", total_checked, total_extra, total_expansions),
    ]
    if smart_extra:
        strategies.append(("≥2 ID в кластере", smart_checked, smart_total_extra, len(smart_extra)))
    if tight_extra:
        strategies.append(("Tight-кластеры", tight_checked, tight_total_extra, len(tight_extra)))

    print(f"\n  {'Стратегия':<30} {'MISS/доп.':>10} {'Hit rate':>10} {'Проверок':>10}")
    print(f"  {'-'*30} {'-'*10} {'-'*10} {'-'*10}")
    for name, checked, found, count in strategies:
        miss_per = checked / found if found else float('inf')
        hit_rate = found / checked * 100 if checked else 0
        print(f"  {name:<30} {miss_per:>10.1f} {hit_rate:>9.1f}% {checked:>10}")

    print(f"\n  Для сравнения — случайный поиск:")
    random_density = len(all_ids) / (62**7)
    random_hit_rate = random_density * 100
    random_miss_per = 1 / random_density if random_density else float('inf')
    print(f"  {'Случайный перебор':<30} {random_miss_per:>10.0f} {random_hit_rate:>9.8f}%")


if __name__ == "__main__":
    main()
