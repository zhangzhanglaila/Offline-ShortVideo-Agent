"""Asset Store — General-purpose Content-Addressable Storage.

Stores any file (images, audio, video, etc.) by content hash.
Supports metadata, deduplication, atomic writes, and thread-safe access.

    store = AssetStore(store_dir=Path("output/.asset_store"))
    hash_id = store.put(Path("photo.jpg"), metadata={"tags": ["nature"]})
    path = store.get(hash_id)  # → Path to stored file
    store.exists(hash_id)      # → True

Storage layout:
    <store_dir>/
        manifest.json          # hash → {path, size, ext, metadata, created_at}
        ab/
            abcdef12345678.jpg  # Content-addressed files
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


class AssetStore:
    """Content-addressable file store with metadata.

    Thread-safe. Writes are atomic (temp file → rename).
    Duplicate files are deduplicated by content hash.
    """

    def __init__(self, store_dir: Path | str):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.store_dir / "manifest.json"
        self._manifest: dict[str, dict] = self._load_manifest()
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
            json.dump(self._manifest, f, indent=2, ensure_ascii=False)
        tmp.replace(self.manifest_path)

    def _compute_hash(self, file_path: Path) -> str:
        """SHA-256 hash of file contents."""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def put(
        self,
        file_path: Path | str,
        metadata: Optional[dict[str, Any]] = None,
        asset_type: str = "",
    ) -> str:
        """Store a file and return its content hash.

        If the file already exists (same hash), skips copy and
        merges metadata if provided.

        Args:
            file_path: Path to the file to store.
            metadata: Optional metadata dict (tags, license, duration, etc.).
            asset_type: Optional type hint (image, video, audio, etc.).

        Returns:
            Content hash (SHA-256 hex prefix, 16 chars).
        """
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        content_hash = self._compute_hash(file_path)[:16]

        with self._lock:
            if content_hash in self._manifest:
                # Already stored — merge metadata if new metadata provided
                if metadata:
                    existing = self._manifest[content_hash]
                    existing_meta = existing.get("metadata", {})
                    existing_meta.update(metadata)
                    existing["metadata"] = existing_meta
                    self._save_manifest()
                return content_hash

            # Atomic copy: write to temp, then move
            ext = file_path.suffix or ".bin"
            prefix = content_hash[:2]
            sub_dir = self.store_dir / prefix
            sub_dir.mkdir(exist_ok=True)
            dest = sub_dir / f"{content_hash}{ext}"

            # Write via temp file for atomicity
            with open(file_path, "rb") as src:
                with tempfile.NamedTemporaryFile(
                    dir=sub_dir, suffix=ext, delete=False,
                ) as tmp:
                    shutil.copyfileobj(src, tmp)
                    tmp_path = Path(tmp.name)
            tmp_path.replace(dest)

            self._manifest[content_hash] = {
                "path": str(dest),
                "size": dest.stat().st_size,
                "ext": ext,
                "asset_type": asset_type,
                "metadata": metadata or {},
                "created_at": time.time(),
                "content_hash": content_hash,
            }
            self._save_manifest()
            return content_hash

    def get(self, content_hash: str) -> Optional[Path]:
        """Get file path by content hash. Returns None if not found."""
        entry = self._manifest.get(content_hash)
        if not entry:
            return None
        path = Path(entry["path"])
        if path.exists() and path.stat().st_size > 0:
            return path
        # Stale entry
        with self._lock:
            del self._manifest[content_hash]
            self._save_manifest()
        return None

    def exists(self, content_hash: str) -> bool:
        return self.get(content_hash) is not None

    def get_metadata(self, content_hash: str) -> Optional[dict[str, Any]]:
        """Get metadata for a stored asset."""
        entry = self._manifest.get(content_hash)
        if not entry:
            return None
        return entry.get("metadata", {})

    def delete(self, content_hash: str) -> bool:
        """Delete a stored asset. Returns True if deleted."""
        with self._lock:
            entry = self._manifest.get(content_hash)
            if not entry:
                return False
            path = Path(entry["path"])
            if path.exists():
                path.unlink(missing_ok=True)
            del self._manifest[content_hash]
            self._save_manifest()
            return True

    def list_assets(self, asset_type: str = "") -> list[dict[str, Any]]:
        """List stored assets, optionally filtered by type."""
        results = []
        for h, entry in self._manifest.items():
            if asset_type and entry.get("asset_type") != asset_type:
                continue
            results.append({
                "content_hash": h,
                "path": entry["path"],
                "size": entry.get("size", 0),
                "asset_type": entry.get("asset_type", ""),
                "metadata": entry.get("metadata", {}),
                "created_at": entry.get("created_at", 0),
            })
        return results

    def stats(self) -> dict[str, Any]:
        total_size = sum(e.get("size", 0) for e in self._manifest.values())
        return {
            "entries": len(self._manifest),
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "store_dir": str(self.store_dir),
        }

    def evict(self, max_size_mb: int = 2048):
        """Evict oldest entries if store exceeds size limit."""
        max_bytes = max_size_mb * 1024 * 1024
        total = sum(e.get("size", 0) for e in self._manifest.values())
        if total <= max_bytes:
            return

        sorted_entries = sorted(
            self._manifest.items(),
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
                del self._manifest[hash_key]
            self._save_manifest()

    def clear(self):
        """Remove all stored files and reset manifest."""
        with self._lock:
            for entry in self._manifest.values():
                path = Path(entry["path"])
                if path.exists():
                    path.unlink(missing_ok=True)
            self._manifest.clear()
            self._save_manifest()
