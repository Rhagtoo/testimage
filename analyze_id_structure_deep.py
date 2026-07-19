#!/usr/bin/env python3
"""
Глубокий анализ: паттерны суффиксов и гипотеза локальной структуры.
"""
import sys
from collections import Counter, defaultdict
from pathlib import Path

CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
CHAR_IDX = {c: i for i, c in enumerate(CHARSET)}
ID_LENGTH = 7


def load_ids(path: Path) -> list[str]:
    ids = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if len(line) == ID_LENGTH and all(c in CHAR_IDX for c in line):
            ids.append(line)
    return ids


def suffix_to_int(suffix: str) -> int:
    """base62 → int для суффикса произвольной длины."""
    n = 0
    for c in suffix:
        n = n * 62 + CHAR_IDX[c]
    return n


def int_to_suffix(n: int, length: int) -> str:
    """int → base62 суффикс фиксированной длины."""
    chars = []
    for _ in range(length):
        n, r = divmod(n, 62)
        chars.append(CHARSET[r])
    return "".join(reversed(chars))


def main():
    ids = load_ids(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/unique_gids.txt"))
    print(f"Загружено ID: {len(ids)}")

    # Группировка по prefix_len=5
    by_prefix: dict[str, list[str]] = defaultdict(list)
    for gid in ids:
        by_prefix[gid[:5]].append(gid)

    # ========== 1. Анализ: фиксирован ли первый символ суффикса? ==========
    print("=" * 70)
    print("1. ГИПОТЕЗА: ПЕРВЫЙ СИМВОЛ СУФФИКСА ФИКСИРОВАН?")
    print("   (prefix_len=5, suffix_len=2)")
    print("=" * 70)

    multi_prefixes = [(p, gids) for p, gids in by_prefix.items() if len(gids) >= 3]
    print(f"Префиксов с ≥3 ID: {len(multi_prefixes)}")

    same_first_char = 0
    two_clusters = 0
    scattered = 0
    cluster_examples = {1: [], 2: [], 3: []}

    for prefix, gids in multi_prefixes:
        suffix_first_chars = Counter(g[5] for g in gids)
        unique_first = len(suffix_first_chars)

        if unique_first == 1:
            same_first_char += 1
            if len(cluster_examples[1]) < 3:
                cluster_examples[1].append((prefix, [(g[5:], g) for g in sorted(gids)]))
        elif unique_first == 2:
            two_clusters += 1
            if len(cluster_examples[2]) < 3:
                cluster_examples[2].append((prefix, [(g[5:], g) for g in sorted(gids)]))
        else:
            scattered += 1
            if len(cluster_examples[3]) < 3:
                cluster_examples[3].append((prefix, [(g[5:], g) for g in sorted(gids)]))

    total = same_first_char + two_clusters + scattered
    print(f"\n  Один первый символ (tight cluster):     {same_first_char:>5} ({same_first_char/total*100:5.1f}%)")
    print(f"  Два кластера (разные первые символы):   {two_clusters:>5} ({two_clusters/total*100:5.1f}%)")
    print(f"  Разбросаны (≥3 разных первых символов): {scattered:>5} ({scattered/total*100:5.1f}%)")

    print("\n  Примеры 'один первый символ' (tight):")
    for prefix, items in cluster_examples[1]:
        print(f"    {prefix} → {[s for s,_ in items]}")

    print("\n  Примеры 'два кластера':")
    for prefix, items in cluster_examples[2]:
        print(f"    {prefix} → {[s for s,_ in items]}")

    if cluster_examples[3]:
        print("\n  Примеры 'разбросаны':")
        for prefix, items in cluster_examples[3]:
            print(f"    {prefix} → {[s for s,_ in items]}")

    # ========== 2. Gap-распределение для tight-кластеров ==========
    print("\n" + "=" * 70)
    print("2. GAP-АНАЛИЗ: РАССТОЯНИЯ МЕЖДУ СОСЕДНИМИ СУФФИКСАМИ")
    print("   (только префиксы с ≥3 ID и одним первым символом)")
    print("=" * 70)

    all_gaps: list[int] = []
    all_suffix_first_chars: dict[str, int] = Counter()

    for prefix, gids in by_prefix.items():
        if len(gids) < 3:
            continue

        # Только tight кластеры
        first_chars = {g[5] for g in gids}
        if len(first_chars) > 1:
            continue

        suffix_ints = sorted(suffix_to_int(g[5:]) for g in gids)
        for i in range(len(suffix_ints) - 1):
            gap = suffix_ints[i + 1] - suffix_ints[i] - 1  # -1 = пропущенные между
            all_gaps.append(gap)

        for g in gids:
            all_suffix_first_chars[g[5]] += 1

    print(f"  Tight-кластеров (≥3 ID, один первый символ): {same_first_char}")
    print(f"  Всего gap-значений: {len(all_gaps)}")

    if all_gaps:
        import statistics
        print(f"  Средний gap: {statistics.mean(all_gaps):.1f}")
        print(f"  Медианный gap: {statistics.median(all_gaps):.0f}")
        print(f"  Мин/Макс gap: {min(all_gaps)}/{max(all_gaps)}")
        print(f"  std dev: {statistics.stdev(all_gaps):.1f}")

        # Гистограмма gap'ов
        gap_freq = Counter(all_gaps)
        print(f"\n  Гистограмма gap'ов (топ-20):")
        for gap, count in sorted(gap_freq.items())[:20]:
            bar = "█" * min(count, 60)
            print(f"    gap={gap:>4d}: {count:>4d} {bar}")
        print(f"    ... и ещё {len(gap_freq)-20} уникальных значений")

    # ========== 3. Анализ prefix_len=4 (3-char suffix) ==========
    print("\n" + "=" * 70)
    print("3. ПРЕФИКС-АНАЛИЗ (prefix_len=4, suffix_len=3)")
    print("=" * 70)

    by_prefix4: dict[str, list[str]] = defaultdict(list)
    for gid in ids:
        by_prefix4[gid[:4]].append(gid)

    multi4 = [(p, gids) for p, gids in by_prefix4.items() if len(gids) >= 3]
    print(f"Уникальных префиксов: {len(by_prefix4)}")
    print(f"Префиксов с ≥3 ID: {len(multi4)}")

    # Сколько ID у топ префиксов
    top4 = sorted(multi4, key=lambda x: -len(x[1]))[:20]
    for prefix, gids in top4:
        suffixes = sorted(g[4:] for g in gids)
        first_char_variance = len({s[0] for s in suffixes})
        tag = "✓ tight" if first_char_variance == 1 else f"{first_char_variance} clusters"
        print(f"  {prefix}*** → {len(gids)} ID(s) [{tag}] суффиксы: [{', '.join(suffixes[:8])}{'...' if len(suffixes) > 8 else ''}]")

    # ========== 4. Самое важное: проверка локальности ==========
    print("\n" + "=" * 70)
    print("4. КЛЮЧЕВОЙ ТЕСТ: ЛОКАЛЬНАЯ СТРУКТУРА ПРОСТРАНСТВА ID")
    print("   Если ID локальны → соседние base62 ID тоже существуют")
    print("=" * 70)

    all_ids_set = set(ids)

    # Проверяем гипотезу: для каждого найденного ID проверяем ±1, ±2, ±3 в base62
    neighborhood_hits = {1: 0, 2: 0, 3: 0, 5: 0, 10: 0}
    neighborhood_total = {1: 0, 2: 0, 3: 0, 5: 0, 10: 0}

    # Берём выборку в 5000 ID для скорости
    import random
    sample = random.sample(ids, min(5000, len(ids)))

    for gid in sample:
        center = suffix_to_int(gid)
        for radius, _ in neighborhood_hits.items():
            for delta in range(-radius, radius + 1):
                if delta == 0:
                    continue
                candidate_int = center + delta
                if candidate_int < 0 or candidate_int >= 62**7:
                    continue
                candidate = int_to_suffix(candidate_int, 7)
                neighborhood_total[radius] += 1
                if candidate in all_ids_set:
                    neighborhood_hits[radius] += 1

    print(f"  Выборка: {len(sample)} ID")
    print()
    print(f"  {'Радиус':>8} {'Проверено':>10} {'Найдено':>8} {'Hit rate':>10}")
    print(f"  {'-'*8} {'-'*10} {'-'*8} {'-'*10}")
    for radius in sorted(neighborhood_hits):
        total = neighborhood_total[radius]
        hits = neighborhood_hits[radius]
        rate = hits / total * 100 if total else 0
        print(f"  {'±'+str(radius):>8} {total:>10} {hits:>8} {rate:>9.2f}%")

    # Ожидаемая случайная плотность
    overall_density = len(ids) / (62**7)
    print(f"\n  Ожидаемая случайная плотность: {overall_density*100:.8f}%")
    print(f"  Если hit rate >> ожидаемой плотности → ID ЛОКАЛЬНЫ (не случайны)")

    # ========== 5. Статистика "плодородности" для разных длин префикса ==========
    print("\n" + "=" * 70)
    print("5. ПОРОГ ПЛОДОРОДНОСТИ: КАКОЙ PREFIX_LEN ОПТИМАЛЕН?")
    print("=" * 70)

    for plen in range(3, 7):
        by_p = defaultdict(list)
        for gid in ids:
            by_p[gid[:plen]].append(gid)

        total_p = len(by_p)
        multi_p = sum(1 for gids in by_p.values() if len(gids) >= 2)
        fertile_p = sum(1 for gids in by_p.values() if len(gids) >= 3)
        max_ids = max(len(gids) for gids in by_p.values()) if by_p else 0

        suffix_len = ID_LENGTH - plen
        suffix_space = 62 ** suffix_len

        print(f"  prefix_len={plen} (suffix={suffix_len} chars, space={suffix_space}):")
        print(f"    уникальных префиксов: {total_p}")
        print(f"    с ≥2 ID: {multi_p} ({multi_p/total_p*100:.1f}%)")
        print(f"    с ≥3 ID: {fertile_p} ({fertile_p/total_p*100:.1f}%)")
        print(f"    макс ID на префикс: {max_ids}")


if __name__ == "__main__":
    main()
