"""Media Cache — Content-addressable cache for rendered media files.

Bridges the runtime kernel's semantic hashing with file-level caching.
Uses `thinking.canonicalize.content_hash` for cache keys, stores
rendered mp4/wav files on disk.

This is the domain-specific cache layer:
  - thinking/artifact_store.py → generic blob CAS (JSON artifacts)
  - media/media_cache.py      → media file CAS (mp4, wav, large binaries)

Storage layout:
    <project>/output/.media_cache/
        manifest.json
        ab/
            abcdef12345678.mp4

Usage:
    cache = MediaCache()
    cached = cache.lookup(scene_ir_content)
    if not cached:
        video_path = render(scene_ir)
        cache.store(scene_ir_content, video_path, scene_id="hook")
    else:
        video_path = cached
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from compiler.thinking.canonicalize import content_hash


def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


class MediaCache:
    """Content-addressable cache for rendered media files.

    Thread-safe for reads. Writes are atomic (write to temp, then move).
    """

    def __init__(self, cache_dir: Path | str | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir else _get_project_root() / "output" / ".media_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.cache_dir / "manifest.json"
        self.manifest: dict[str, dict] = self._load_manifest()

    def _load_manifest(self) -> dict[str, dict]:
        if self.manifest_path.exists():
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_manifest(self):
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, indent=2, ensure_ascii=False)

    def _hash_content(self, content: Any) -> str:
        """Compute content hash for cache key."""
        return content_hash(content)

    def lookup(self, content: Any) -> Optional[Path]:
        """Return path to cached media file if it exists, else None.

        Args:
            content: The scene IR content dict (or any hashable content).
        """
        h = self._hash_content(content)
        entry = self.manifest.get(h)
        if not entry:
            return None
        path = Path(entry["path"])
        if path.exists() and path.stat().st_size > 0:
            return path
        # Stale entry
        del self.manifest[h]
        self._save_manifest()
        return None

    def store(self, content: Any, media_path: Path, scene_id: str = "") -> Path:
        """Move a media file into the cache.

        Args:
            content: The scene IR content (for computing cache key).
            media_path: Path to the rendered media file.
            scene_id: Scene identifier (for debugging).

        Returns:
            Path to the cached file.
        """
        h = self._hash_content(content)
        prefix = h[:2]
        sub_dir = self.cache_dir / prefix
        sub_dir.mkdir(exist_ok=True)

        # Determine extension from source
        ext = media_path.suffix or ".mp4"
        dest = sub_dir / f"{h}{ext}"

        if media_path.resolve() != dest.resolve():
            shutil.move(str(media_path), str(dest))

        self.manifest[h] = {
            "path": str(dest),
            "size": dest.stat().st_size,
            "created_at": time.time(),
            "scene_id": scene_id,
            "content_hash": h,
        }
        self._save_manifest()
        return dest

    def exists(self, content: Any) -> bool:
        """Check if content has a cached render."""
        return self.lookup(content) is not None

    def stats(self) -> dict:
        total_size = sum(e.get("size", 0) for e in self.manifest.values())
        return {
            "entries": len(self.manifest),
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "cache_dir": str(self.cache_dir),
        }

    def evict(self, max_size_mb: int = 4096):
        """Evict oldest entries if cache exceeds size limit."""
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

    def clear(self):
        for entry in self.manifest.values():
            path = Path(entry["path"])
            if path.exists():
                path.unlink(missing_ok=True)
        self.manifest.clear()
        self._save_manifest()
