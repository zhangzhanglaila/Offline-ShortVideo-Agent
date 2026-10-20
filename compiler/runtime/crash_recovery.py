"""Crash Recovery — Resume interrupted rendering tasks.

Tracks in-progress renders on disk. On restart, detects incomplete tasks
and marks them for re-render (content hash → no duplicate work since
RenderCache handles dedup).

Storage: output/.render_state/
    manifest.json  — {scene_id: {status, content_hash, started_at, ...}}

Usage:
    recovery = CrashRecovery()
    recovery.mark_started("scene_1", "abc123")
    # ... render ...
    recovery.mark_completed("scene_1")
    # On restart:
    pending = recovery.get_pending()  # scenes that were started but not completed
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional


def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


class CrashRecovery:
    """Tracks render state on disk for crash recovery.

    Thread-safe. Uses atomic writes (temp → rename).
    """

    def __init__(self, state_dir: Path | str | None = None):
        self.state_dir = Path(state_dir) if state_dir else _get_project_root() / "output" / ".render_state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.state_dir / "manifest.json"
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

    def mark_started(self, scene_id: str, content_hash: str, metadata: dict[str, Any] | None = None):
        """Record that a render has started."""
        with self._lock:
            self.manifest[scene_id] = {
                "status": "running",
                "content_hash": content_hash,
                "started_at": time.time(),
                "completed_at": None,
                "error": None,
                **(metadata or {}),
            }
            self._save_manifest()

    def mark_completed(self, scene_id: str):
        """Record that a render completed successfully."""
        with self._lock:
            if scene_id in self.manifest:
                self.manifest[scene_id]["status"] = "completed"
                self.manifest[scene_id]["completed_at"] = time.time()
                self._save_manifest()

    def mark_failed(self, scene_id: str, error: str):
        """Record that a render failed."""
        with self._lock:
            if scene_id in self.manifest:
                self.manifest[scene_id]["status"] = "failed"
                self.manifest[scene_id]["completed_at"] = time.time()
                self.manifest[scene_id]["error"] = error
                self._save_manifest()

    def get_pending(self) -> list[dict[str, Any]]:
        """Get scenes that were started but never completed (crash recovery).

        Returns list of dicts with scene_id, content_hash, started_at, etc.
        """
        pending = []
        for scene_id, entry in self.manifest.items():
            if entry.get("status") == "running":
                pending.append({"scene_id": scene_id, **entry})
        return pending

    def get_failed(self) -> list[dict[str, Any]]:
        """Get scenes that failed."""
        failed = []
        for scene_id, entry in self.manifest.items():
            if entry.get("status") == "failed":
                failed.append({"scene_id": scene_id, **entry})
        return failed

    def get_completed(self) -> list[dict[str, Any]]:
        """Get scenes that completed."""
        completed = []
        for scene_id, entry in self.manifest.items():
            if entry.get("status") == "completed":
                completed.append({"scene_id": scene_id, **entry})
        return completed

    def clear_completed(self):
        """Remove completed entries to keep manifest small."""
        with self._lock:
            to_remove = [
                sid for sid, entry in self.manifest.items()
                if entry.get("status") == "completed"
            ]
            for sid in to_remove:
                del self.manifest[sid]
            if to_remove:
                self._save_manifest()

    def reset(self, scene_id: str):
        """Reset a scene's status (e.g., for re-render)."""
        with self._lock:
            if scene_id in self.manifest:
                del self.manifest[scene_id]
                self._save_manifest()

    def stats(self) -> dict[str, int]:
        """Get counts by status."""
        counts: dict[str, int] = {}
        for entry in self.manifest.values():
            status = entry.get("status", "unknown")
            counts[status] = counts.get(status, 0) + 1
        return counts
