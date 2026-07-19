#!/usr/bin/env python3
"""Интеграция Discovery Engine v2 — чистые патчи с правильными отступами."""

import sys
from pathlib import Path

PATCHES = [
    # 1. Import
    (
        'try:\n    import socksio as _socksio  # noqa: F401',
        'try:\n    from discovery_engine import (\n        DiscoveryEngine, create_engine_for_scanner,\n    )\n    DISCOVERY_AVAILABLE = True\nexcept ImportError:\n    DISCOVERY_AVAILABLE = False\n\ntry:\n    import socksio as _socksio  # noqa: F401',
    ),
    # 2. Scanner.__init__ — add self.discovery
    (
        '        self._priority_ids: deque[str] = priority_ids if priority_ids is not None else deque()\n        self._priority_queued: set[str] = set(priority_ids) if priority_ids else set()',
        '        self._priority_ids: deque[str] = priority_ids if priority_ids is not None else deque()\n        self._priority_queued: set[str] = set(priority_ids) if priority_ids else set()\n        self.discovery: DiscoveryEngine | None = None\n        if DISCOVERY_AVAILABLE:\n            self.discovery = create_engine_for_scanner(\n                db_path=Path("discovery_state.db"),\n                blind_threshold=5,\n            )\n            self.discovery.load()',
    ),
    # 3. enqueue_seed_bundle — seed new galleries
    (
        '        self._upload_seed_gids.add(gid)\n        candidates = [gid] if include_seed else []\n        candidates.extend(_suffix_mutate_ids(gid, suffix_len))',
        '        self._upload_seed_gids.add(gid)\n        candidates = [gid] if include_seed else []\n        candidates.extend(_suffix_mutate_ids(gid, suffix_len))\n        if self.discovery:\n            self.discovery.on_gallery_found(gid)',
    ),
    # 4. Worker: discovery hit/miss hook — right before _mark_tried
    (
        '                    self._mark_tried(gid)\n\n            pending: set[asyncio.Task] = set()',
        '                    if self.discovery:\n                        cluster = self.discovery.get_cluster_for_gid(gid)\n                        if cluster and cluster.state.name == "ACTIVE":\n                            if ok:\n                                self.discovery.on_probe_hit(cluster.source_id, gid)\n                            else:\n                                self.discovery.on_probe_miss(cluster.source_id, gid)\n                    self._mark_tried(gid)\n\n            pending: set[asyncio.Task] = set()',
    ),
    # 5. Discovery candidates before idgen.next()
    (
        '                    else:\n                        gid = self.idgen.next()',
        '                    elif self.discovery:\n                        result = self.discovery.next_candidate()\n                        if result:\n                            gid, _src = result\n                        else:\n                            gid = self.idgen.next()\n                    else:\n                        gid = self.idgen.next()',
    ),
    # 6. Dashboard in heartbeat
    (
        '                            self._write_persistent_status(idle=True)\n                            reload = await self._wait_reload()',
        '                            self._write_persistent_status(idle=True)\n                            if self.discovery:\n                                self.discovery.write_dashboard(Path("discovery_dashboard.json"))\n                            reload = await self._wait_reload()',
    ),
    # 7. Flush on shutdown
    (
        '        if isinstance(self.idgen, IDGen):\n            self.idgen._flush_persist()\n        if self._proxy_stats:',
        '        if isinstance(self.idgen, IDGen):\n            self.idgen._flush_persist()\n        if self.discovery:\n            saved = self.discovery.flush()\n            log.info("[discovery] flushed %d clusters on shutdown", saved)\n        if self._proxy_stats:',
    ),
]


def main():
    scanner_path = sys.argv[1] if len(sys.argv) > 1 else "pentest_site_gallery_scanner.py"
    path = Path(scanner_path)
    if not path.exists():
        print(f"ERROR: {path} not found")
        sys.exit(1)

    content = path.read_text(encoding="utf-8")

    # Verify all patterns exist
    print("Checking patterns...")
    all_ok = True
    for i, (old, _) in enumerate(PATCHES):
        count = content.count(old)
        if count == 1:
            print(f"  {i+1}. ✓ (1 match)")
        else:
            print(f"  {i+1}. ✗ ({count} matches)")
            all_ok = False

    if not all_ok:
        print("\nABORTED: some patterns don't match. Scanner NOT modified.")
        sys.exit(1)

    # Backup
    bak = path.with_suffix(".py.discovery_bak")
    bak.write_text(content, encoding="utf-8")
    print(f"\nBackup: {bak}")

    # Apply
    for i, (old, new) in enumerate(PATCHES):
        if new in content:
            print(f"  {i+1}. ⏭ already applied")
            continue
        content = content.replace(old, new, 1)
        print(f"  {i+1}. ✓ applied")

    path.write_text(content, encoding="utf-8")

    # Verify compilation
    import py_compile
    try:
        py_compile.compile(str(path), doraise=True)
        print(f"\n✓ Scanner compiles successfully!")
    except py_compile.PyCompileError as e:
        print(f"\n✗ Compilation error: {e}")
        print("Restoring from backup...")
        bak.rename(path)
        sys.exit(1)


if __name__ == "__main__":
    main()
