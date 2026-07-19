#!/usr/bin/env python3
"""
Анализ структуры ID галерей: префикс-кластеры и тепловая карта суффиксов.

Гипотезы:
1. ID имеют локальную структуру — одинаковые длинные префиксы группируются
2. Суффиксы внутри одного префикса не случайны, а кластеризованы
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


def prefix_analysis(ids: list[str], prefix_len: int = 5):
    """Анализ префиксов: сколько ID на каждый префикс."""
    by_prefix: dict[str, int] = Counter()
    for gid in ids:
        by_prefix[gid[:prefix_len]] += 1

    # Распределение: сколько префиксов имеют N ID
    freq_dist: dict[int, int] = Counter()
    for count in by_prefix.values():
        freq_dist[count] += 1

    # Топ префиксов с наибольшим количеством ID
    top = by_prefix.most_common(30)

    return by_prefix, freq_dist, top


def suffix_heatmap(ids: list[str], target_prefix: str):
    """Тепловая карта суффиксов для конкретного префикса."""
    suffix_len = ID_LENGTH - len(target_prefix)
    suffix_counts: dict[str, int] = Counter()
    for gid in ids:
        if gid.startswith(target_prefix):
            suffix_counts[gid[len(target_prefix):]] += 1

    # Визуализация: для каждого возможного первого символа суффикса
    # сколько галерей найдено с этим символом
    char_hits: dict[str, int] = Counter()
    for suffix in suffix_counts:
        char_hits[suffix[0]] += 1

    return suffix_counts, char_hits


def full_suffix_scan(ids: list[str], prefix: str):
    """
    Полное сканирование пространства суффиксов для префикса.
    Показывает какие комбинации существуют и нет.
    """
    suffix_len = ID_LENGTH - len(prefix)
    existing = {gid[len(prefix):] for gid in ids if gid.startswith(prefix)}

    # Если suffix_len == 2: 62² = 3844 комбинации
    # Если suffix_len == 3: 62³ = 238328 (много для таблицы, но можно статистику)

    if suffix_len > 2:
        # Для больших суффиксов — только статистика
        total_space = 62 ** suffix_len
        hit_count = len(existing)
        print(f"\n  Префикс '{prefix}': найдено {hit_count} из {total_space} ({hit_count / total_space * 100:.1%})")

        # Распределение по первому символу суффикса
        first_char: dict[str, list[str]] = defaultdict(list)
        for s in existing:
            first_char[s[0]].append(s)
        print(f"  Распределение по первому символу суффикса:")
        for c in sorted(first_char, key=lambda x: len(first_char[x]), reverse=True):
            print(f"    '{c}': {len(first_char[c])} ID(s)")
        return

    # Для 2-символьных суффиксов — полная матрица
    rows = CHARSET[:36]  # 0-9, A-Z
    cols = CHARSET  # все 62

    matrix = {}
    for c1 in CHARSET:
        for c2 in CHARSET:
            suffix = c1 + c2
            matrix[suffix] = suffix in existing

    # Визуализация
    print(f"\n  Тепловая карта суффиксов для префикса '{prefix}'")
    print(f"  ■ = существует, · = нет, PREFIX = {prefix}")
    print()

    # Компактный вывод: блоки по 62 столбца, каждая строка — первый символ
    header = "  " + "".join(cols[i] for i in range(0, len(cols), 2))
    print(header)

    for r in rows:
        line = r + " "
        for c in cols:
            line += "■" if matrix.get(r + c) else "·"
        print(line)

    # Статистика
    hit_count = sum(1 for v in matrix.values() if v)
    total_space = len(CHARSET) * len(CHARSET)
    print(f"\n  Найдено: {hit_count}/{total_space} ({hit_count / total_space * 100:.1%})")


def prefix_sort_key(prefix: str) -> int:
    """Сортировка префиксов по base62 значению."""
    n = 0
    for c in prefix:
        n = n * 62 + CHAR_IDX.get(c, 0)
    return n


def main():
    ids = load_ids(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/unique_gids.txt"))
    print(f"Загружено ID: {len(ids)}")

    # ========== 1. Префикс-анализ ==========
    print("\n" + "=" * 70)
    print("1. ПРЕФИКС-АНАЛИЗ (prefix_len=5)")
    print("=" * 70)

    by_prefix, freq_dist, top = prefix_analysis(ids, prefix_len=5)

    print(f"Уникальных префиксов длины 5: {len(by_prefix)}")
    print(f"Из теоретических {62**5} ({62**5}) — заполнено {len(by_prefix)/(62**5)*100:.2f}%")
    print()

    print("Распределение: сколько префиксов имеют N ID:")
    for n, count in sorted(freq_dist.items()):
        print(f"  {n} ID: {count} префиксов")

    print(f"\nТоп-30 префиксов по количеству ID (отсортированы по base62):")
    # Сортируем по base62 для читаемости
    for prefix, count in sorted(top, key=lambda x: prefix_sort_key(x[0])):
        print(f"  {prefix}** → {count} ID(s)")

    # ========== 2. Только префиксы с ≥3 ID ==========
    print("\n" + "=" * 70)
    print("2. ПЛОДОРОДНЫЕ ПРЕФИКСЫ (≥3 ID на prefix_len=5)")
    print("=" * 70)
    fertile = [(p, c) for p, c in by_prefix.items() if c >= 3]
    print(f"Всего 'плодородных' префиксов: {len(fertile)} из {len(by_prefix)}")
    for prefix, count in sorted(fertile, key=lambda x: prefix_sort_key(x[0]))[:40]:
        examples = [gid for gid in ids if gid.startswith(prefix)][:5]
        print(f"  {prefix}** → {count} ID(s)  примеры: {', '.join(examples)}")

    # ========== 3. Анализ prefix_len=6 ==========
    print("\n" + "=" * 70)
    print("3. ПРЕФИКС-АНАЛИЗ (prefix_len=6, суффикс=1 символ)")
    print("=" * 70)

    by_prefix6, freq_dist6, top6 = prefix_analysis(ids, prefix_len=6)
    print(f"Уникальных префиксов длины 6: {len(by_prefix6)}")
    print(f"Из теоретических {62**6} ({62**6}) — заполнено {len(by_prefix6)/(62**6)*100:.6f}%")

    # Префиксы с ≥2 ID (значит суффикс варьируется)
    multi = [(p, c) for p, c in by_prefix6.items() if c >= 2]
    print(f"\nПрефиксов с ≥2 ID (вариация последнего символа): {len(multi)}")
    for prefix, count in sorted(multi, key=lambda x: prefix_sort_key(x[0]))[:30]:
        examples = [gid for gid in ids if gid.startswith(prefix)]
        suffixes = sorted(set(gid[6:] for gid in examples))
        print(f"  {prefix}[{''.join(suffixes)}] → {count} ID(s)")

    # ========== 4. Тепловая карта для примера ==========
    print("\n" + "=" * 70)
    print("4. ТЕПЛОВАЯ КАРТА СУФФИКСОВ (примеры)")
    print("=" * 70)

    # Найдём префикс с максимальным количеством ID для демонстрации
    if fertile:
        best_prefix = max(fertile, key=lambda x: x[1])[0]
        print(f"\nПрефикс с макс. количеством ID: {best_prefix}")
        examples = sorted(set(gid for gid in ids if gid.startswith(best_prefix)))
        print(f"  Все галереи с префиксом {best_prefix}: {examples}")

        # Суффиксы
        suffix_set = sorted(gid[len(best_prefix):] for gid in examples)
        print(f"  Суффиксы: {suffix_set}")

        # Проверка гипотезы кластеризации: считаем расстояние между соседними суффиксами
        if len(suffix_set) >= 3:
            print("\n  Анализ промежутков между суффиксами:")
            for i in range(len(suffix_set) - 1):
                s1, s2 = suffix_set[i], suffix_set[i + 1]
                # Расстояние в base62
                d1 = CHAR_IDX[s1[0]] * 62 + CHAR_IDX[s1[1]] if len(s1) >= 2 else CHAR_IDX[s1[0]]
                d2 = CHAR_IDX[s2[0]] * 62 + CHAR_IDX[s2[1]] if len(s2) >= 2 else CHAR_IDX[s2[0]]
                gap = d2 - d1 - 1  # количество пропущенных
                print(f"    {s1} → {s2}: gap={gap}")

    # ========== 5. Статистика: распределение префиксов по hit-ratio ==========
    print("\n" + "=" * 70)
    print("5. АНАЛИЗ ПРОСТРАНСТВА SUFFIX (prefix_len=5, suffix_len=2)")
    print("=" * 70)

    print("Теоретическое пространство на префикс: 62² = 3844")
    print(f"Всего префиксов: {len(by_prefix)}")
    total_hits = sum(by_prefix.values())
    print(f"Всего ID: {total_hits}")
    print(f"Средняя плотность на префикс: {total_hits / len(by_prefix):.1f} ID")
    print(f"Средняя заполненность: {total_hits / (len(by_prefix) * 3844) * 100:.2f}%")

    # На каких префиксах плотность максимальна?
    print("\nТоп-20 префиксов по заполненности (hits/3844):")
    density = [(p, c, c / 3844 * 100) for p, c in by_prefix.items()]
    for prefix, count, pct in sorted(density, key=lambda x: -x[1])[:20]:
        examples = sorted(gid[len(prefix):] for gid in ids if gid.startswith(prefix))
        range_str = f"{examples[0]}..{examples[-1]}" if examples else "—"
        print(f"  {prefix}** → {count} ID(s) ({pct:.1f}%)  суффиксы: [{', '.join(examples[:8])}{'...' if len(examples) > 8 else ''}]")


if __name__ == "__main__":
    main()
