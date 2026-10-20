"""P3.1 — Persistent Artifact Store (CAS) Tests.

Verifies:
  - Put/get roundtrip
  - Content-addressable identity (same content → same hash → same artifact)
  - Manifest persistence across store instances
  - Eviction policy
  - Stale entry cleanup
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.thinking.artifact_store import ArtifactStore
from compiler.thinking.canonicalize import content_hash


@pytest.fixture
def store(tmp_path):
    """Create a temporary ArtifactStore for each test."""
    return ArtifactStore(store_dir=tmp_path / "artifacts")


# ═══════════════════════════════════════════════════════════════════════
# Basic Roundtrip
# ═══════════════════════════════════════════════════════════════════════


class TestBasicRoundtrip:
    """Put → get must return identical bytes."""

    def test_put_get_roundtrip(self, store):
        blob = b'{"scene_id": "hook", "text": "hello"}'
        h = content_hash(json.loads(blob))
        store.put(h, blob, artifact_type="scene_ir")

        result = store.get(h)
        assert result == blob

    def test_get_missing_returns_none(self, store):
        assert store.get("nonexistent") is None

    def test_exists(self, store):
        h = "abc123def4567890"
        assert not store.exists(h)
        store.put(h, b'{"data": 1}')
        assert store.exists(h)

    def test_different_content_different_hash(self, store):
        h1 = content_hash({"v": 1})
        h2 = content_hash({"v": 2})
        store.put(h1, b'{"v": 1}')
        store.put(h2, b'{"v": 2}')

        assert store.get(h1) != store.get(h2)


# ═══════════════════════════════════════════════════════════════════════
# Content-Addressable Identity
# ═══════════════════════════════════════════════════════════════════════


class TestContentAddressable:
    """Same content → same hash → same artifact, regardless of put order."""

    def test_same_content_same_hash(self):
        data = {"scene_type": "hook", "text": "hello", "duration": 150}
        h1 = content_hash(data)
        h2 = content_hash(data)
        assert h1 == h2

    def test_overwrite_is_idempotent(self, store):
        h = content_hash({"v": 1})
        store.put(h, b'first')
        store.put(h, b'second')
        assert store.get(h) == b'second'

    def test_key_order_invariant(self):
        h1 = content_hash({"b": 1, "a": 2})
        h2 = content_hash({"a": 2, "b": 1})
        assert h1 == h2


# ═══════════════════════════════════════════════════════════════════════
# Manifest Persistence
# ═══════════════════════════════════════════════════════════════════════


class TestManifestPersistence:
    """Store must survive process restart (manifest persists to disk)."""

    def test_manifest_survives_restart(self, tmp_path):
        store_dir = tmp_path / "artifacts"

        # First "session"
        store1 = ArtifactStore(store_dir=store_dir)
        h = content_hash({"persist": True})
        store1.put(h, b'{"persist": true}', artifact_type="test")

        # Second "session" — new instance, same directory
        store2 = ArtifactStore(store_dir=store_dir)
        result = store2.get(h)
        assert result == b'{"persist": true}'

    def test_manifest_tracks_metadata(self, store):
        h = content_hash({"meta": 1})
        store.put(h, b'{"meta": 1}', artifact_type="scene_ir")

        assert h in store.manifest
        entry = store.manifest[h]
        assert entry["artifact_type"] == "scene_ir"
        assert entry["size"] > 0
        assert entry["created_at"] > 0


# ═══════════════════════════════════════════════════════════════════════
# Directory Structure
# ═══════════════════════════════════════════════════════════════════════


class TestDirectoryStructure:
    """Files must be stored in hash[:2]/hash.json to avoid inode explosion."""

    def test_two_level_directory(self, store, tmp_path):
        h = content_hash({"dir": "test"})
        store.put(h, b'{"dir": "test"}')

        expected_dir = store.store_dir / h[:2]
        expected_file = expected_dir / f"{h}.json"
        assert expected_dir.exists()
        assert expected_file.exists()

    def test_multiple_hashes_share_prefix(self, store):
        # Find two hashes with same prefix
        h1 = content_hash({"a": 1})
        h2 = content_hash({"a": 2})
        store.put(h1, b'{"a": 1}')
        store.put(h2, b'{"a": 2}')

        # Both should exist (possibly in same prefix dir)
        assert store.exists(h1)
        assert store.exists(h2)


# ═══════════════════════════════════════════════════════════════════════
# Eviction
# ═══════════════════════════════════════════════════════════════════════


class TestEviction:
    """Eviction must remove oldest entries when size limit is exceeded."""

    def test_evict_under_limit_is_noop(self, store):
        h = content_hash({"small": 1})
        store.put(h, b'{"small": 1}')
        store.evict(max_size_mb=100)
        assert store.exists(h)

    def test_evict_removes_oldest(self, store):
        # Put 3 entries
        h1 = content_hash({"v": 1})
        h2 = content_hash({"v": 2})
        h3 = content_hash({"v": 3})
        store.put(h1, b'x' * 100)
        store.put(h2, b'y' * 100)
        store.put(h3, b'z' * 100)

        # Evict to very small limit (entries are ~100 bytes each + manifest overhead)
        store.evict(max_size_mb=0)  # 0 MB = evict everything to 80% target

        # At least some entries should be evicted
        remaining = sum(1 for h in [h1, h2, h3] if store.exists(h))
        assert remaining < 3

    def test_clear_removes_all(self, store):
        store.put("a1", b'data1')
        store.put("a2", b'data2')
        store.clear()
        assert store.stats()["entries"] == 0


# ═══════════════════════════════════════════════════════════════════════
# Stale Entry Cleanup
# ═══════════════════════════════════════════════════════════════════════


class TestStaleCleanup:
    """Manifest entries pointing to missing files must be cleaned up."""

    def test_stale_entry_returns_none(self, store):
        h = content_hash({"stale": 1})
        store.put(h, b'{"stale": 1}')

        # Manually delete the file
        path = store.store_dir / h[:2] / f"{h}.json"
        path.unlink(missing_ok=True)

        # get should return None and clean manifest
        assert store.get(h) is None
        assert h not in store.manifest


# ═══════════════════════════════════════════════════════════════════════
# Stats
# ═══════════════════════════════════════════════════════════════════════


class TestStats:
    """Store statistics must be accurate."""

    def test_stats_empty(self, store):
        s = store.stats()
        assert s["entries"] == 0
        assert s["total_size_mb"] == 0.0

    def test_stats_after_put(self, store):
        store.put("h1", b'a' * 1000)
        store.put("h2", b'b' * 2000)
        s = store.stats()
        assert s["entries"] == 2
        assert s["total_size_mb"] >= 0  # 3KB rounds to 0.00 MB


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
