"""
Discovery Engine v2 — production-ready.
Оптимизирован, с персистентностью, транспортом и метриками.

Слои:
  DiscoveryEngine    — реестр CandidateSource, события
  ExpansionScheduler — A* PriorityQueue
  TransportScheduler — blind budget, ротация
  ClusterStore       — SQLite персистентность
"""
from __future__ import annotations

import array
import heapq
import json
import logging
import math
import os
import random
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any

log = logging.getLogger("discovery")

# ═══════════════════════════════════════════════════════════════
# Константы
# ═══════════════════════════════════════════════════════════════

CHARSET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
CHAR_IDX: dict[str, int] = {c: i for i, c in enumerate(CHARSET)}
ID_LENGTH = 7
PREFIX_LEN = 5
SUFFIX_LEN = ID_LENGTH - PREFIX_LEN  # 2
SUFFIX_SPACE = 62 ** SUFFIX_LEN  # 3844

# Предвычисленные суффиксы: suffix_index → (first_char, suffix_str)
_SUFFIX_BY_IDX: list[tuple[str, str]] = []
for _fc in CHARSET:
    for _sc in CHARSET:
        _SUFFIX_BY_IDX.append((_fc, _fc + _sc))
assert len(_SUFFIX_BY_IDX) == SUFFIX_SPACE

# Предвычисленные массивы для fast path: suffix_index → список соседей с тем же first_char
_NEIGHBORS_BY_FIRST_CHAR: dict[str, list[int]] = {}
for _fc in CHARSET:
    _NEIGHBORS_BY_FIRST_CHAR[_fc] = [
        i for i, (c, _) in enumerate(_SUFFIX_BY_IDX) if c == _fc
    ]

# ═══════════════════════════════════════════════════════════════
# Fast BitSet — array('Q') для 3844 бит = 61 × 64-bit word
# ═══════════════════════════════════════════════════════════════

_BITSET_WORDS = (SUFFIX_SPACE + 63) // 64  # 61


@dataclass(slots=True)
class FastBitSet:
    """BitSet на array('Q') — ~10x быстрее bytearray в Python."""

    _words: array.array = field(default_factory=lambda: array.array("Q", [0] * _BITSET_WORDS))

    def set(self, idx: int) -> None:
        word = idx >> 6
        self._words[word] |= 1 << (idx & 63)

    def is_set(self, idx: int) -> bool:
        word = idx >> 6
        return bool(self._words[word] & (1 << (idx & 63)))

    def count_set(self) -> int:
        return sum(w.bit_count() for w in self._words)

    def count_clear(self) -> int:
        return SUFFIX_SPACE - self.count_set()

    def clear_indices(self) -> list[int]:
        """Непротестированные индексы."""
        out = []
        for wi, w in enumerate(self._words):
            if w == 0xFFFFFFFFFFFFFFFF:
                continue
            base = wi * 64
            for bit in range(64):
                idx = base + bit
                if idx >= SUFFIX_SPACE:
                    break
                if not (w & (1 << bit)):
                    out.append(idx)
        return out


# ═══════════════════════════════════════════════════════════════
# Bayesian Posterior
# ═══════════════════════════════════════════════════════════════

@dataclass(slots=True)
class BayesianPosterior:
    alpha_prior: float = 1.0
    beta_prior: float = 1.0
    hits: int = 0
    misses: int = 0

    @property
    def expected_hit_rate(self) -> float:
        a = self.alpha_prior + self.hits
        b = self.beta_prior + self.misses
        return a / (a + b)

    @property
    def total(self) -> int:
        return self.hits + self.misses

    def update(self, hit: bool) -> None:
        if hit:
            self.hits += 1
        else:
            self.misses += 1

    def to_dict(self) -> dict:
        return {"hits": self.hits, "misses": self.misses}

    @classmethod
    def from_dict(cls, d: dict) -> "BayesianPosterior":
        return cls(hits=d.get("hits", 0), misses=d.get("misses", 0))


# ═══════════════════════════════════════════════════════════════
# ClusterState
# ═══════════════════════════════════════════════════════════════

class ClusterState(Enum):
    SINGLETON = auto()
    ACTIVE = auto()
    PAUSED = auto()
    EXHAUSTED = auto()
    DEAD = auto()


# ═══════════════════════════════════════════════════════════════
# ClusterScore — метрики для дашборда
# ═══════════════════════════════════════════════════════════════

@dataclass(slots=True)
class ClusterScore:
    prefix5: str
    discovered: int          # сколько ID известно
    tight: bool              # tight-кластер?
    tested: int              # сколько суффиксов проверено
    remaining: int           # сколько осталось
    p_hit: float             # текущая P(hit)
    expected_new: float      # ожидаемое количество новых находок
    ev: float                # expected value для scheduler
    state: str               # ClusterState.name

    def to_dict(self) -> dict:
        return {
            "prefix": self.prefix5,
            "discovered": self.discovered,
            "tight": self.tight,
            "tested": self.tested,
            "remaining": self.remaining,
            "p_hit": round(self.p_hit, 4),
            "expected_new": round(self.expected_new, 2),
            "ev": round(self.ev, 6),
            "state": self.state,
        }


# ═══════════════════════════════════════════════════════════════
# CandidateSource ABC
# ═══════════════════════════════════════════════════════════════

class CandidateSource(ABC):
    source_id: str

    @property
    @abstractmethod
    def expected_value(self) -> float:
        ...

    @abstractmethod
    def next_candidate(self) -> str | None:
        ...

    @abstractmethod
    def on_hit(self, gid: str) -> None:
        ...

    @abstractmethod
    def on_miss(self, gid: str) -> None:
        ...

    @property
    @abstractmethod
    def is_exhausted(self) -> bool:
        ...

    @property
    @abstractmethod
    def source_type(self) -> str:
        ...


# ═══════════════════════════════════════════════════════════════
# PrefixCluster — с FastBitSet и байесовским EV
# ═══════════════════════════════════════════════════════════════

@dataclass(slots=True)
class PrefixCluster(CandidateSource):
    """Кластер галерей с общим 5-символьным префиксом."""

    prefix5: str = ""
    source_id: str = field(init=False, default="")

    # Обнаруженные ID
    discovered_ids: set[str] = field(default_factory=set)

    # Протестированные суффиксы (FastBitSet, 3844 бита)
    tested: FastBitSet = field(default_factory=FastBitSet)

    # Очередь кандидатов (индексы суффиксов, не строки — для скорости)
    frontier: deque[int] = field(default_factory=deque)

    # Политика: tight_first (индексы для первого символа) или все
    _frontier_phase: int = 0  # 0=tight first-char, 1=other first-chars
    _frontier_tight_char: str = ""

    # Распределение первых символов найденных суффиксов
    suffix_histogram: dict[str, int] = field(default_factory=dict)

    # Байесовская оценка
    posterior: BayesianPosterior = field(default_factory=BayesianPosterior)

    # Состояние
    state: ClusterState = ClusterState.SINGLETON

    # Статистика для scheduler
    total_miss_streak: int = 0

    # Dirty-флаг для персистентности
    _dirty: bool = False

    def __post_init__(self) -> None:
        self.source_id = f"cluster:{self.prefix5}"

    # ──── CandidateSource interface ────

    @property
    def expected_value(self) -> float:
        if self.state in (ClusterState.EXHAUSTED, ClusterState.DEAD, ClusterState.SINGLETON):
            return 0.0
        if not self.frontier:
            return 0.0
        return self.posterior.expected_hit_rate

    def next_candidate(self) -> str | None:
        if not self.frontier:
            if self.state == ClusterState.ACTIVE:
                self._transition_to_exhausted_or_dead()
            return None
        idx = self.frontier[0]
        suffix = _SUFFIX_BY_IDX[idx][1]
        return self.prefix5 + suffix

    def on_hit(self, gid: str) -> None:
        suffix = gid[PREFIX_LEN:]
        self.discovered_ids.add(gid)
        idx = CHAR_IDX[suffix[0]] * 62 + CHAR_IDX[suffix[1]]
        self.tested.set(idx)
        self.posterior.update(hit=True)

        if self.frontier and self.frontier[0] == idx:
            self.frontier.popleft()

        fc = suffix[0]
        self.suffix_histogram[fc] = self.suffix_histogram.get(fc, 0) + 1
        self.total_miss_streak = 0
        self._dirty = True
        self._update_state()

    def on_miss(self, gid: str) -> None:
        suffix = gid[PREFIX_LEN:]
        idx = CHAR_IDX[suffix[0]] * 62 + CHAR_IDX[suffix[1]]
        self.tested.set(idx)
        self.posterior.update(hit=False)

        if self.frontier and self.frontier[0] == idx:
            self.frontier.popleft()

        self.total_miss_streak += 1
        self._dirty = True
        self._update_state()

    @property
    def is_exhausted(self) -> bool:
        return self.state in (ClusterState.EXHAUSTED, ClusterState.DEAD)

    @property
    def source_type(self) -> str:
        return "prefix_cluster"

    # ──── Cluster-specific ────

    def add_seed(self, gid: str) -> None:
        """Добавить ID как seed (найденный извне экспансии)."""
        if gid in self.discovered_ids:
            return

        suffix = gid[PREFIX_LEN:]
        self.discovered_ids.add(gid)
        idx = CHAR_IDX[suffix[0]] * 62 + CHAR_IDX[suffix[1]]
        self.tested.set(idx)
        self.suffix_histogram[suffix[0]] = self.suffix_histogram.get(suffix[0], 0) + 1
        self._dirty = True

        if self.state == ClusterState.SINGLETON and len(self.discovered_ids) >= 2:
            self._activate()
        elif self.state == ClusterState.PAUSED:
            self._activate()

    def _activate(self) -> None:
        """Построить frontier и перейти в ACTIVE."""
        discovered_suffixes = {gid[PREFIX_LEN:] for gid in self.discovered_ids}
        discovered_indices = {
            CHAR_IDX[s[0]] * 62 + CHAR_IDX[s[1]] for s in discovered_suffixes
        }

        # Фаза 1: tight — варианты с доминирующим первым символом
        if self.suffix_histogram:
            dominant_char = max(self.suffix_histogram, key=self.suffix_histogram.get)
            self._frontier_tight_char = dominant_char
            tight_indices = [
                i for i in _NEIGHBORS_BY_FIRST_CHAR[dominant_char]
                if i not in discovered_indices and not self.tested.is_set(i)
            ]
            random.shuffle(tight_indices)
            self.frontier = deque(tight_indices)
        else:
            self._frontier_tight_char = ""

        # Фаза 2 (будет добавлена при исчерпании фазы 1)
        self._frontier_phase = 0
        self.state = ClusterState.ACTIVE

    def _activate_phase2(self) -> None:
        """Добавить в frontier остальные первые символы."""
        discovered_suffixes = {gid[PREFIX_LEN:] for gid in self.discovered_ids}
        discovered_indices = {
            CHAR_IDX[s[0]] * 62 + CHAR_IDX[s[1]] for s in discovered_suffixes
        }

        # Сортируем по убыванию частоты в histogram
        other_chars = sorted(
            [c for c in CHARSET if c != self._frontier_tight_char],
            key=lambda c: self.suffix_histogram.get(c, 0),
            reverse=True,
        )

        phase2 = []
        for fc in other_chars:
            for i in _NEIGHBORS_BY_FIRST_CHAR[fc]:
                if i not in discovered_indices and not self.tested.is_set(i):
                    phase2.append(i)

        random.shuffle(phase2)
        self.frontier = deque(phase2)
        self._frontier_phase = 1

    def _transition_to_exhausted_or_dead(self) -> None:
        if self._frontier_phase == 0 and self._frontier_tight_char:
            # Tight-фаза исчерпана → переходим в фазу 2
            self._activate_phase2()
            if self.frontier:
                self.state = ClusterState.ACTIVE
                return

        if len(self.discovered_ids) > 1:
            self.state = ClusterState.EXHAUSTED
        else:
            self.state = ClusterState.DEAD
        self._dirty = True

    def _update_state(self) -> None:
        if self.state == ClusterState.SINGLETON and len(self.discovered_ids) >= 2:
            self._activate()
        elif self.state == ClusterState.ACTIVE:
            if not self.frontier and self.tested.count_set() >= SUFFIX_SPACE:
                self._transition_to_exhausted_or_dead()

    @property
    def remaining_candidates(self) -> int:
        return len(self.frontier)

    @property
    def total_discovered(self) -> int:
        return len(self.discovered_ids)

    @property
    def is_tight(self) -> bool:
        if len(self.discovered_ids) < 2:
            return False
        return len({gid[PREFIX_LEN] for gid in self.discovered_ids}) == 1

    def get_score(self, transport_health: float = 1.0) -> ClusterScore:
        """Метрики для дашборда."""
        expected_new = self.expected_value * (self.remaining_candidates or 1)
        return ClusterScore(
            prefix5=self.prefix5,
            discovered=self.total_discovered,
            tight=self.is_tight,
            tested=self.tested.count_set(),
            remaining=self.remaining_candidates,
            p_hit=self.posterior.expected_hit_rate,
            expected_new=expected_new,
            ev=self.expected_value * transport_health * math.log(self.remaining_candidates + 1),
            state=self.state.name,
        )

    # ──── Serialization ────

    def to_row(self) -> tuple:
        """Для SQLite: (prefix5, discovered_json, tested_bytes, posterior_json,
        histogram_json, frontier_json, frontier_phase, tight_char, state, miss_streak)."""
        return (
            self.prefix5,
            json.dumps(sorted(self.discovered_ids)),
            self.tested._words.tobytes(),
            json.dumps(self.posterior.to_dict()),
            json.dumps(self.suffix_histogram),
            json.dumps(list(self.frontier)),
            self._frontier_phase,
            self._frontier_tight_char,
            self.state.name,
            self.total_miss_streak,
        )

    @classmethod
    def from_row(cls, row: tuple) -> "PrefixCluster":
        (
            prefix5, discovered_json, tested_bytes, posterior_json,
            histogram_json, frontier_json, frontier_phase, tight_char,
            state_name, miss_streak,
        ) = row

        cluster = cls(prefix5=prefix5)
        cluster.source_id = f"cluster:{prefix5}"

        # discovered
        cluster.discovered_ids = set(json.loads(discovered_json))

        # tested bits
        words = array.array("Q")
        words.frombytes(tested_bytes)
        if len(words) < _BITSET_WORDS:
            words.extend([0] * (_BITSET_WORDS - len(words)))
        cluster.tested._words = words

        # posterior
        posterior_dict = json.loads(posterior_json)
        cluster.posterior = BayesianPosterior.from_dict(posterior_dict)

        # histogram
        cluster.suffix_histogram = json.loads(histogram_json)

        # frontier
        cluster.frontier = deque(json.loads(frontier_json))

        # phase/char/state/streak
        cluster._frontier_phase = frontier_phase
        cluster._frontier_tight_char = tight_char
        cluster.state = ClusterState[state_name]
        cluster.total_miss_streak = miss_streak

        return cluster


# ═══════════════════════════════════════════════════════════════
# RandomSource
# ═══════════════════════════════════════════════════════════════

@dataclass(slots=True)
class RandomSource(CandidateSource):
    source_id: str = "random"
    _total: int = field(default=0, init=False)
    _hits: int = field(default=0, init=False)

    @property
    def expected_value(self) -> float:
        if self._total == 0:
            return 2.55e-8
        return (self._hits + 1) / (self._total + 2)

    def next_candidate(self) -> str:
        return "".join(random.choices(CHARSET, k=ID_LENGTH))

    def on_hit(self, gid: str) -> None:
        self._total += 1
        self._hits += 1

    def on_miss(self, gid: str) -> None:
        self._total += 1

    @property
    def is_exhausted(self) -> bool:
        return False

    @property
    def source_type(self) -> str:
        return "random"


# ═══════════════════════════════════════════════════════════════
# TransportHealth
# ═══════════════════════════════════════════════════════════════

@dataclass
class TransportHealth:
    blind: bool = False
    miss_budget_remaining: int = 5
    latency_ms: float = 500.0
    transport_type: str = "unknown"

    @property
    def health(self) -> float:
        if self.blind:
            return 0.0
        if self.miss_budget_remaining <= 0:
            return 0.0
        latency_penalty = min(1.0, 200.0 / max(self.latency_ms, 1.0))
        budget_factor = self.miss_budget_remaining / 5.0
        return latency_penalty * budget_factor


# ═══════════════════════════════════════════════════════════════
# TransportScheduler — обёртка над реальным транспортом
# ═══════════════════════════════════════════════════════════════

class TransportScheduler:
    """
    Управляет бюджетом MISS для реального транспорта.

    Интегрируется со Scanner:
      - При каждом ref-check обновляет blind-статус
      - При каждом probe-результате обновляет бюджет
      - Выдаёт TransportHealth для ExpansionScheduler
    """

    def __init__(self, blind_threshold: int = 5) -> None:
        self.blind_threshold = blind_threshold
        self._consecutive_miss = 0
        self._health = TransportHealth(blind=False, miss_budget_remaining=blind_threshold)
        self._rotation_count = 0
        self._lock = threading.Lock()

    @property
    def health(self) -> TransportHealth:
        with self._lock:
            return self._health

    def on_ref_ok(self, transport_type: str = "direct") -> None:
        """REF галерея жива — транспорт зрячий."""
        with self._lock:
            self._health.blind = False
            self._health.miss_budget_remaining = self.blind_threshold
            self._health.transport_type = transport_type
            self._consecutive_miss = 0

    def on_ref_blind(self) -> None:
        """REF галерея недоступна — транспорт ослеплён."""
        with self._lock:
            self._health.blind = True
            self._health.miss_budget_remaining = 0

    def on_hit(self) -> None:
        """Успешный probe."""
        with self._lock:
            self._consecutive_miss = 0
            self._health.miss_budget_remaining = max(
                self._health.miss_budget_remaining,
                self.blind_threshold,
            )

    def on_miss(self) -> None:
        """Неуспешный probe."""
        with self._lock:
            self._consecutive_miss += 1
            self._health.miss_budget_remaining = max(
                0,
                self.blind_threshold - self._consecutive_miss,
            )

    def rotate(self, new_transport_type: str = "rotated") -> None:
        """Ротация транспорта."""
        with self._lock:
            self._rotation_count += 1
            self._consecutive_miss = 0
            self._health.blind = False
            self._health.miss_budget_remaining = self.blind_threshold
            self._health.transport_type = new_transport_type

    @property
    def should_rotate(self) -> bool:
        with self._lock:
            return self._consecutive_miss >= self.blind_threshold

    @property
    def is_blind(self) -> bool:
        with self._lock:
            return self._health.blind

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "blind": self._health.blind,
                "budget": self._health.miss_budget_remaining,
                "consecutive_miss": self._consecutive_miss,
                "rotations": self._rotation_count,
                "transport": self._health.transport_type,
            }


# ═══════════════════════════════════════════════════════════════
# ExpansionScheduler
# ═══════════════════════════════════════════════════════════════

@dataclass(slots=True)
class _ScheduleEntry:
    priority: float
    seq: int
    source_id: str

    def __lt__(self, other: "_ScheduleEntry") -> bool:
        if self.priority != other.priority:
            return self.priority > other.priority
        return self.seq < other.seq


class ExpansionScheduler:
    """A* PriorityQueue планировщик."""

    def __init__(self) -> None:
        self._sources: dict[str, CandidateSource] = {}
        self._heap: list[_ScheduleEntry] = []
        self._seq = 0
        self._transport_health = TransportHealth()
        self._lock = threading.Lock()

    def register(self, source: CandidateSource) -> None:
        with self._lock:
            self._sources[source.source_id] = source
            self._push(source.source_id)

    def unregister(self, source_id: str) -> None:
        with self._lock:
            self._sources.pop(source_id, None)

    def update_transport(self, health: TransportHealth) -> None:
        with self._lock:
            self._transport_health = health
            self._heap.clear()
            self._seq = 0
            for sid in list(self._sources):
                self._push(sid)

    def next(self) -> str | None:
        with self._lock:
            self._clean_exhausted()
            if not self._heap:
                return None

            entry = heapq.heappop(self._heap)
            source = self._sources.get(entry.source_id)
            if not source or source.is_exhausted:
                return self.next()

            candidate = source.next_candidate()
            if candidate is None:
                return self.next()

            self._push(source.source_id)
            return candidate

    def _priority(self, source: CandidateSource) -> float:
        ev = source.expected_value
        th = self._transport_health.health
        if hasattr(source, "remaining_candidates"):
            rem = source.remaining_candidates  # type: ignore
        else:
            rem = 1
        return ev * th * math.log(rem + 1)

    def _push(self, source_id: str) -> None:
        source = self._sources.get(source_id)
        if not source or source.is_exhausted:
            return
        self._seq += 1
        heapq.heappush(
            self._heap,
            _ScheduleEntry(self._priority(source), self._seq, source_id),
        )

    def _clean_exhausted(self) -> None:
        self._heap = [
            e for e in self._heap
            if e.source_id in self._sources
            and not self._sources[e.source_id].is_exhausted
        ]
        heapq.heapify(self._heap)

    def notify_hit(self, source_id: str, gid: str) -> None:
        with self._lock:
            source = self._sources.get(source_id)
            if source:
                source.on_hit(gid)
            self._push(source_id)

    def notify_miss(self, source_id: str, gid: str) -> None:
        with self._lock:
            source = self._sources.get(source_id)
            if source:
                source.on_miss(gid)
            self._push(source_id)

    def get_scores(self) -> list[ClusterScore]:
        """Метрики для дашборда (только кластеры)."""
        scores = []
        with self._lock:
            th = self._transport_health.health
            for source in self._sources.values():
                if isinstance(source, PrefixCluster):
                    scores.append(source.get_score(th))
        scores.sort(key=lambda s: s.ev, reverse=True)
        return scores

    def to_dict(self) -> dict:
        with self._lock:
            active = sum(
                1 for s in self._sources.values()
                if not s.is_exhausted and s.expected_value > 0
            )
            clusters = sum(
                1 for s in self._sources.values()
                if s.source_type == "prefix_cluster"
            )
            singleton = sum(
                1 for s in self._sources.values()
                if isinstance(s, PrefixCluster) and s.state == ClusterState.SINGLETON
            )
            return {
                "total_sources": len(self._sources),
                "clusters": clusters,
                "active": active,
                "singletons": singleton,
                "heap_size": len(self._heap),
            }


# ═══════════════════════════════════════════════════════════════
# ClusterStore — SQLite персистентность
# ═══════════════════════════════════════════════════════════════

class ClusterStore:
    """SQLite-хранилище состояния кластеров."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._init_schema()
        return self._conn

    def _init_schema(self) -> None:
        conn = self._conn
        if conn is None:
            return
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clusters (
                prefix5 TEXT PRIMARY KEY,
                discovered_json TEXT NOT NULL,
                tested_bytes BLOB NOT NULL,
                posterior_json TEXT NOT NULL,
                histogram_json TEXT NOT NULL DEFAULT '{}',
                frontier_json TEXT NOT NULL DEFAULT '[]',
                frontier_phase INTEGER NOT NULL DEFAULT 0,
                tight_char TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT 'SINGLETON',
                miss_streak INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.commit()

    def save_cluster(self, cluster: PrefixCluster) -> None:
        """UPSERT одного кластера."""
        conn = self._get_conn()
        row = cluster.to_row()
        with self._lock:
            conn.execute(
                """INSERT OR REPLACE INTO clusters
                   (prefix5, discovered_json, tested_bytes, posterior_json,
                    histogram_json, frontier_json, frontier_phase,
                    tight_char, state, miss_streak)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                row,
            )
            conn.commit()
            cluster._dirty = False

    def save_dirty(self, clusters: dict[str, PrefixCluster]) -> int:
        """Сохранить все dirty-кластеры. Возвращает количество сохранённых."""
        conn = self._get_conn()
        saved = 0
        rows = []
        for cluster in clusters.values():
            if cluster._dirty:
                rows.append(cluster.to_row())
                cluster._dirty = False
                saved += 1
        if rows:
            with self._lock:
                conn.executemany(
                    """INSERT OR REPLACE INTO clusters
                       (prefix5, discovered_json, tested_bytes, posterior_json,
                        histogram_json, frontier_json, frontier_phase,
                        tight_char, state, miss_streak)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                conn.commit()
        return saved

    def load_all(self) -> dict[str, PrefixCluster]:
        """Загрузить все кластеры из БД."""
        conn = self._get_conn()
        clusters: dict[str, PrefixCluster] = {}
        with self._lock:
            for row in conn.execute("SELECT * FROM clusters"):
                cluster = PrefixCluster.from_row(row)
                clusters[cluster.prefix5] = cluster
        return clusters

    def load_singleton_prefixes(self) -> set[str]:
        """Загрузить только префиксы синглтон-кластеров (для быстрой проверки)."""
        conn = self._get_conn()
        with self._lock:
            rows = conn.execute(
                "SELECT prefix5 FROM clusters WHERE state='SINGLETON'"
            ).fetchall()
        return {r[0] for r in rows}

    def delete_cluster(self, prefix5: str) -> None:
        conn = self._get_conn()
        with self._lock:
            conn.execute("DELETE FROM clusters WHERE prefix5=?", (prefix5,))
            conn.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        conn = self._get_conn()
        with self._lock:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, value),
            )
            conn.commit()

    def stats(self) -> dict:
        conn = self._get_conn()
        with self._lock:
            total = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
            active = conn.execute(
                "SELECT COUNT(*) FROM clusters WHERE state='ACTIVE'"
            ).fetchone()[0]
            singleton = conn.execute(
                "SELECT COUNT(*) FROM clusters WHERE state='SINGLETON'"
            ).fetchone()[0]
        return {"total": total, "active": active, "singletons": singleton}

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None


# ═══════════════════════════════════════════════════════════════
# DiscoveryEngine
# ═══════════════════════════════════════════════════════════════

class DiscoveryEngine:
    """
    Центральный движок поиска.

    Интеграция со Scanner:
      scanner.discovery.on_gallery_found(gid)     # при находке
      scanner.discovery.on_probe_hit(gid)         # при успешном probe
      scanner.discovery.on_probe_miss(gid)        # при неуспешном probe
      scanner.discovery.transport.on_ref_ok()     # при ref=200
      scanner.discovery.transport.on_ref_blind()  # при ref≠200
      gid, src = scanner.discovery.next_candidate() # следующий ID
    """

    def __init__(
        self,
        store_path: Path | None = None,
        blind_threshold: int = 5,
    ) -> None:
        self._clusters: dict[str, PrefixCluster] = {}
        self._scheduler = ExpansionScheduler()
        self._random_source: RandomSource | None = None
        self.transport = TransportScheduler(blind_threshold=blind_threshold)
        self._store = ClusterStore(store_path) if store_path else None
        self._loaded = False
        self._save_interval = 60.0  # секунд
        self._last_save = time.monotonic()

    # ──── Загрузка из БД ────

    def load(self) -> int:
        """Загрузить состояние из БД. Возвращает количество кластеров."""
        if not self._store:
            return 0
        self._clusters = self._store.load_all()
        for cluster in self._clusters.values():
            if cluster.state not in (ClusterState.EXHAUSTED, ClusterState.DEAD, ClusterState.SINGLETON):
                self._scheduler.register(cluster)
        self._loaded = True
        log.info(
            "discovery: loaded %d clusters from %s",
            len(self._clusters), self._store.db_path,
        )
        return len(self._clusters)

    def save(self, force: bool = False) -> int:
        """Сохранить dirty-кластеры. Возвращает количество сохранённых."""
        if not self._store:
            return 0
        now = time.monotonic()
        if not force and now - self._last_save < self._save_interval:
            return 0
        saved = self._store.save_dirty(self._clusters)
        self._last_save = now
        if saved:
            log.debug("discovery: saved %d clusters", saved)
        return saved

    # ──── События: новая галерея найдена ────

    def on_gallery_found(self, gid: str) -> PrefixCluster:
        """Галерея найдена (любым способом: upload, scan, etc)."""
        prefix5 = gid[:PREFIX_LEN]

        if prefix5 in self._clusters:
            cluster = self._clusters[prefix5]
        else:
            cluster = PrefixCluster(prefix5=prefix5)
            self._clusters[prefix5] = cluster

        old_state = cluster.state
        cluster.add_seed(gid)

        if old_state == ClusterState.SINGLETON and cluster.state == ClusterState.ACTIVE:
            self._scheduler.register(cluster)
            log.info(
                "[discovery] cluster %s** activated: %d IDs, tight=%s",
                prefix5, cluster.total_discovered, cluster.is_tight,
            )

        self._maybe_save()
        return cluster

    # ──── События: результат probe ────

    def on_probe_hit(self, source_id: str, gid: str) -> None:
        """Probe нашёл галерею — обновляем кластер."""
        self._scheduler.notify_hit(source_id, gid)
        self._maybe_save()

    def on_probe_miss(self, source_id: str, gid: str) -> None:
        """Probe не нашёл — только обновляем кластер, не трогаем транспорт."""
        self._scheduler.notify_miss(source_id, gid)
        self._maybe_save()

    def on_ref_status(self, ok: bool) -> None:
        """Обновить транспорт из сканера (вызывается при ref-check)."""
        if ok:
            self.transport.on_ref_ok()
        else:
            self.transport.on_ref_blind()
        self._scheduler.update_transport(self.transport.health)

    # ──── Получение кандидатов ────

    def next_candidate(self) -> tuple[str, str] | None:
        """Следующий ID для проверки. Returns (gid, source_id) или None."""
        self._scheduler.update_transport(self.transport.health)

        gid = self._scheduler.next()
        if gid is None:
            # Fallback: случайный ID
            if self._random_source is None:
                self._random_source = RandomSource()
                self._scheduler.register(self._random_source)
            gid = self._random_source.next_candidate()
            return gid, "random"

        prefix5 = gid[:PREFIX_LEN]
        cluster = self._clusters.get(prefix5)
        if cluster and cluster.state == ClusterState.ACTIVE:
            source_id = cluster.source_id
        else:
            source_id = "random"
            if self._random_source is None:
                self._random_source = RandomSource()
                self._scheduler.register(self._random_source)

        return gid, source_id

    # ──── Запросы ────

    def get_cluster(self, prefix5: str) -> PrefixCluster | None:
        return self._clusters.get(prefix5)

    def get_cluster_for_gid(self, gid: str) -> PrefixCluster | None:
        """Получить кластер по полному 7-символьному ID."""
        return self._clusters.get(gid[:PREFIX_LEN])

    def get_scores(self) -> list[ClusterScore]:
        return self._scheduler.get_scores()

    @property
    def cluster_count(self) -> int:
        return len(self._clusters)

    # ──── Персистентность ────

    def _maybe_save(self) -> None:
        self.save(force=False)

    def flush(self) -> int:
        """Принудительное сохранение всех dirty."""
        return self.save(force=True)

    # ──── Дашборд ────

    def dashboard(self) -> dict:
        """Полная статистика для scan_status.json / логов."""
        scores = self.get_scores()
        top_n = scores[:10]
        return {
            "transport": self.transport.to_dict(),
            "scheduler": self._scheduler.to_dict(),
            "total_clusters": self.cluster_count,
            "top_clusters": [s.to_dict() for s in top_n],
            "total_top_ev": sum(s.ev for s in top_n),
            "db_stats": self._store.stats() if self._store else {},
        }

    def write_dashboard(self, path: Path) -> None:
        """Записать дашборд в JSON файл."""
        data = self.dashboard()
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════
# Интеграция со Scanner
# ═══════════════════════════════════════════════════════════════


def create_engine_for_scanner(
    db_path: str | Path | None = None,
    blind_threshold: int = 5,
) -> DiscoveryEngine:
    """Создать DiscoveryEngine с персистентностью."""
    store_path = Path(db_path) if db_path else None
    engine = DiscoveryEngine(store_path=store_path, blind_threshold=blind_threshold)
    if store_path and store_path.exists():
        engine.load()
    return engine


# ═══════════════════════════════════════════════════════════════
# CLI для тестирования
# ═══════════════════════════════════════════════════════════════

def _cli_load_ids(path: str) -> set[str]:
    ids = set()
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if len(line) == ID_LENGTH and all(c in CHAR_IDX for c in line):
            ids.add(line)
    return ids


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    all_ids = _cli_load_ids(sys.argv[1] if len(sys.argv) > 1 else "/tmp/unique_gids.txt")
    print(f"Ground truth: {len(all_ids)} ID")

    random.seed(42)
    seed = set(random.sample(sorted(all_ids), 10_000))

    engine = create_engine_for_scanner(db_path="/tmp/discovery_test.db")
    # Очищаем БД для чистого теста
    if engine._store:
        engine._store._get_conn().execute("DELETE FROM clusters")
        engine._store._get_conn().commit()

    t0 = time.monotonic()
    for gid in seed:
        engine.on_gallery_found(gid)
    print(f"Seed: {len(seed)} IDs, clusters={engine.cluster_count} ({time.monotonic()-t0:.2f}s)")

    scores = engine.get_scores()
    active = sum(1 for s in scores if s.state == "ACTIVE")
    tight = sum(1 for s in scores if s.tight)
    print(f"Active: {active}, Tight: {tight}")

    # Экспансия
    engine.transport.on_ref_ok("direct")
    found = 0
    misses = 0
    rotations = 0
    N = 5000

    t0 = time.monotonic()
    for i in range(N):
        result = engine.next_candidate()
        if result is None:
            break
        gid, src = result
        if gid in all_ids:
            is_new = gid not in seed
            if is_new:
                found += 1
            engine.on_probe_hit(src, gid)
        else:
            misses += 1
            engine.on_probe_miss(src, gid)
            if engine.transport.should_rotate:
                rotations += 1
                engine.transport.rotate("rotated")

    elapsed = time.monotonic() - t0
    print(f"\nExpansion: {N} probes in {elapsed:.2f}s ({N/elapsed:.0f} probes/s)")
    print(f"Found new: {found}, Misses: {misses}, Rotations: {rotations}")
    print(f"Hit rate: {found/N*100:.2f}%")
    if found:
        print(f"MISS/find: {misses/found:.1f}")

    # Сохраняем и показываем дашборд
    engine.flush()
    print(f"\nDashboard: {json.dumps(engine.dashboard(), indent=2)}")
