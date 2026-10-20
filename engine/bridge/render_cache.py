"""Render Cache — Content-addressable storage for rendered scene videos.

Maps scene content hashes to rendered mp4 files. Persisted across sessions
via a manifest JSON file.

Storage layout:
    <project_root>/output/.render_cache/
        manifest.json          # hash -> {path, size, created_at, scene_id}
        ab/cd/abcdef1234.mp4   # Rendered scene files (content-addressed)

Usage:
    cache = RenderCache()
    cached_path = cache.lookup("abc123def456")
    if cached_path:
        # Reuse cached render
        pass
    else:
        # Render and store
        cache.store("abc123def456", Path("scene.mp4"), "scene_hook")
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

from compiler.thinking.canonicalize import content_hash as semantic_hash


def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def scene_content_hash(scene_ir: dict[str, Any], settings: dict[str, Any] | None = None) -> str:
    """Compute deterministic hash for a scene IR + render settings.

    Hash includes:
      - scene_id, scene_type, duration, text, camera_motion
      - audio_paths, word_timings (if present)
      - render settings (fps, width, height, theme)

    Excludes: absolute start_frame, overlap values, other scenes' data.
    """
    key = {
        "scene_id": scene_ir.get("scene_id", ""),
        "scene_type": scene_ir.get("scene_type", ""),
        "duration_in_frames": scene_ir.get("duration_in_frames", 0),
        "text": scene_ir.get("text", ""),
        "camera_motion": scene_ir.get("camera_motion", ""),
        "background": scene_ir.get("background", ""),
        "text_style": scene_ir.get("text_style", ""),
        "audio_tracks": scene_ir.get("audio_tracks", []),
        "elements": scene_ir.get("elements", []),
    }
    if settings:
        key["settings"] = {
            "fps": settings.get("fps", 30),
            "width": settings.get("width", 1080),
            "height": settings.get("height", 1920),
            "theme": settings.get("theme", "light"),
        }
    return semantic_hash(key)


class RenderCache:
    """Content-addressable cache for rendered scene videos.

    Thread-safe. Writes are atomic (temp file → rename).
    Each scene's content hash maps to a pre-rendered mp4 file.
    """

    def __init__(self, cache_dir: Path | str | None = None):
        self.cache_dir = Path(cache_dir) if cache_dir else _get_project_root() / "output" / ".render_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.cache_dir / "manifest.json"
        self.manifest: dict[str, dict] = self._load_manifest()
        self._lock = threading.Lock()

    def _load_manifest(self) -> dict[str, dict]:
        if self.manifest_path.exists():
            try:
                with open(self.manifest_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_manifest(self):
        tmp = self.manifest_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f, indent=2, ensure_ascii=False)
        tmp.replace(self.manifest_path)

    def lookup(self, content_hash: str) -> Optional[Path]:
        """Return path to cached mp4 if it exists and is valid, else None."""
        entry = self.manifest.get(content_hash)
        if not entry:
            return None
        path = Path(entry["path"])
        if path.exists() and path.stat().st_size > 0:
            return path
        # Stale entry
        with self._lock:
            if content_hash in self.manifest:
                del self.manifest[content_hash]
                self._save_manifest()
        return None

    def store(self, content_hash: str, mp4_path: Path, scene_id: str = "") -> Path:
        """Move an mp4 into the cache (atomic write).

        Args:
            content_hash: The scene's content-addressable hash.
            mp4_path: Path to the rendered mp4 file.
            scene_id: The scene identifier (for debugging).

        Returns:
            Path to the cached mp4 file.
        """
        with self._lock:
            prefix = content_hash[:2]
            sub_dir = self.cache_dir / prefix
            sub_dir.mkdir(exist_ok=True)

            dest = sub_dir / f"{content_hash}.mp4"

            if mp4_path.resolve() != dest.resolve():
                # Atomic: copy to temp in same dir, then rename
                with open(mp4_path, "rb") as src:
                    with tempfile.NamedTemporaryFile(
                        dir=sub_dir, suffix=".mp4", delete=False,
                    ) as tmp:
                        shutil.copyfileobj(src, tmp)
                        tmp_path = Path(tmp.name)
                tmp_path.replace(dest)

            self.manifest[content_hash] = {
                "path": str(dest),
                "size": dest.stat().st_size,
                "created_at": time.time(),
                "scene_id": scene_id,
                "content_hash": content_hash,
            }
            self._save_manifest()
            return dest

    def exists(self, content_hash: str) -> bool:
        return self.lookup(content_hash) is not None

    def evict(self, max_size_mb: int = 2048):
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
        with self._lock:
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
        total_size = sum(e.get("size", 0) for e in self.manifest.values())
        return {
            "entries": len(self.manifest),
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "cache_dir": str(self.cache_dir),
        }

    def clear(self):
        with self._lock:
            for entry in self.manifest.values():
                path = Path(entry["path"])
                if path.exists():
                    path.unlink(missing_ok=True)
            self.manifest.clear()
            self._save_manifest()
