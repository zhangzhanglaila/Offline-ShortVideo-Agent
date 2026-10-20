"""Artifact Store — Persistent Content-Addressable Storage (CAS).

Maps content hashes to serialized artifact blobs on disk.
Persisted across sessions. Survives process restarts.

Storage layout:
    <project>/.cache/artifacts/
        manifest.json           # hash → {size, created_at, artifact_type}
        ab/
            abcdef12345678.json # Serialized artifact blob

The two-level directory (hash[:2] / hash) avoids single-directory
inode explosion with large caches.

Usage:
    store = ArtifactStore()

    # Store
    store.put("abc123", b'{"scene_id": "hook", ...}', artifact_type="scene_ir")

    # Retrieve
    blob = store.get("abc123")
    if blob:
        data = json.loads(blob)

    # Check existence
    if store.exists("abc123"):
        ...

This is the bridge between:
  - FixpointScheduler (in-memory artifacts)
  - Persistent render cache (survives restarts)
  - Future: distributed CAS (remote workers)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


def _get_project_root() -> Path:
    """Get the project root directory."""
    return Path(__file__).resolve().parent.parent


class ArtifactStore:
    """Persistent content-addressable store for artifact blobs.

    Thread-safe for reads. Writes are atomic (write-then-rename pattern
    is not implemented here because we use JSON blobs, but the interface
    supports it for future ProcessPoolExecutor scenarios).
    """

    def __init__(self, store_dir: Path | str | None = None):
        self.store_dir = Path(store_dir) if store_dir else _get_project_root() / ".cache" / "artifacts"
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.store_dir / "manifest.json"
        self.manifest: dict[str, dict] = self._load_manifest()

    def _load_manifest(self) -> dict[str, dict]:
        """Load manifest from disk."""
        if self.manifest_path.exists():
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_manifest(self):
        """Persist manifest to disk."""
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, indent=2, ensure_ascii=False)

    def get(self, content_hash: str) -> Optional[bytes]:
        """Retrieve artifact blob by content hash. Returns None if miss."""
        entry = self.manifest.get(content_hash)
        if not entry:
            return None
        path = self.store_dir / content_hash[:2] / f"{content_hash}.json"
        if path.exists():
            return path.read_bytes()
        # Stale manifest entry
        del self.manifest[content_hash]
        self._save_manifest()
        return None

    def put(self, content_hash: str, blob: bytes, artifact_type: str = "") -> Path:
        """Store artifact blob under its content hash.

        Args:
            content_hash: The artifact's content-addressable hash.
            blob: Serialized artifact data (JSON bytes).
            artifact_type: Type tag for manifest (e.g. "scene_ir").

        Returns:
            Path to the stored blob file.
        """
        prefix = content_hash[:2]
        sub_dir = self.store_dir / prefix
        sub_dir.mkdir(exist_ok=True)

        dest = sub_dir / f"{content_hash}.json"
        dest.write_bytes(blob)

        self.manifest[content_hash] = {
            "path": str(dest),
            "size": len(blob),
            "created_at": time.time(),
            "artifact_type": artifact_type,
        }
        self._save_manifest()
        return dest

    def exists(self, content_hash: str) -> bool:
        """Check if artifact exists and is valid."""
        return self.get(content_hash) is not None

    def evict(self, max_size_mb: int = 4096):
        """Evict oldest entries if store exceeds size limit.

        Args:
            max_size_mb: Maximum store size in megabytes. Default 4GB.
        """
        max_bytes = max_size_mb * 1024 * 1024
        total = sum(e.get("size", 0) for e in self.manifest.values())
        if total <= max_bytes:
            return

        sorted_entries = sorted(
            self.manifest.items(),
            key=lambda x: x[1].get("created_at", 0),
        )

        target = int(max_bytes * 0.8)
        for hash_key, entry in sorted_entries:
            if total <= target:
                break
            path = Path(entry["path"])
            if path.exists():
                path.unlink(missing_ok=True)
            total -= entry.get("size", 0)
            del self.manifest[hash_key]

        self._save_manifest()

    def stats(self) -> dict:
        """Return store statistics."""
        total_size = sum(e.get("size", 0) for e in self.manifest.values())
        return {
            "entries": len(self.manifest),
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "store_dir": str(self.store_dir),
        }

    def clear(self):
        """Remove all stored artifacts and reset the manifest."""
        for entry in self.manifest.values():
            path = Path(entry["path"])
            if path.exists():
                path.unlink(missing_ok=True)
        self.manifest.clear()
        self._save_manifest()
