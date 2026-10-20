"""Session Store — Persistence for timeline editing sessions.

Saves and restores session state (timeline tracks, undo/redo stacks, metadata)
as JSON files on disk. Each session gets its own directory.

Storage layout:
    <project_root>/output/sessions/<session_id>/
        timeline.json      # Current timeline tracks
        undo_stack.json    # Undo history (up to 50 snapshots)
        redo_stack.json    # Redo history
        meta.json          # Session metadata (topic, created_at, last_saved)

Usage:
    store = SessionStore()
    store.save("session_123", tracks, undo_stack, redo_stack, meta)
    state = store.load("session_123")
"""

from __future__ import annotations

import json
import time
import threading
from pathlib import Path
from typing import Any, Optional


def _get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


class SaveConflictError(Exception):
    """Raised when a save conflicts with a newer version."""
    def __init__(self, expected_version: int, actual_version: int):
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"Conflict: expected version {expected_version}, "
            f"but current version is {actual_version}"
        )


class SessionStore:
    """Thread-safe persistence for timeline editing sessions.

    Each save is atomic (write to temp, then rename).
    Conflict resolution: optimistic locking with version numbers.
    Use save() for last-write-wins, save_versioned() for conflict detection.
    """

    def __init__(self, sessions_dir: Path | str | None = None):
        self.sessions_dir = Path(sessions_dir) if sessions_dir else _get_project_root() / "output" / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    def save(
        self,
        session_id: str,
        tracks: list[dict],
        undo_stack: list[dict] | None = None,
        redo_stack: list[dict] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Save session state to disk (atomic writes, last-write-wins).

        Returns metadata dict with save timestamp.
        """
        session_dir = self._session_dir(session_id)
        with self._lock:
            session_dir.mkdir(parents=True, exist_ok=True)

            # Load existing meta to preserve version
            existing_meta = self._read_json(session_dir / "meta.json", {})
            version = existing_meta.get("version", 0) + 1

            # Save tracks
            self._atomic_write(session_dir / "timeline.json", tracks)

            # Save undo/redo stacks
            self._atomic_write(session_dir / "undo_stack.json", undo_stack or [])
            self._atomic_write(session_dir / "redo_stack.json", redo_stack or [])

            # Save metadata
            now = time.time()
            meta_data = {
                "session_id": session_id,
                "last_saved": now,
                "created_at": (meta or {}).get("created_at", now),
                "topic": (meta or {}).get("topic", ""),
                "tracks_count": len(tracks),
                "undo_depth": len(undo_stack or []),
                "redo_depth": len(redo_stack or []),
                "version": version,
            }
            if meta:
                meta_data.update({k: v for k, v in meta.items() if k not in meta_data})
            self._atomic_write(session_dir / "meta.json", meta_data)

            return meta_data

    def save_versioned(
        self,
        session_id: str,
        expected_version: int,
        tracks: list[dict],
        undo_stack: list[dict] | None = None,
        redo_stack: list[dict] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Save with optimistic locking.

        Raises SaveConflictError if expected_version doesn't match current version.
        This prevents stale writes from overwriting newer data.
        """
        session_dir = self._session_dir(session_id)
        with self._lock:
            session_dir.mkdir(parents=True, exist_ok=True)

            existing_meta = self._read_json(session_dir / "meta.json", {})
            current_version = existing_meta.get("version", 0)

            if expected_version != current_version:
                raise SaveConflictError(expected_version, current_version)

            # Proceed with save
            self._atomic_write(session_dir / "timeline.json", tracks)
            self._atomic_write(session_dir / "undo_stack.json", undo_stack or [])
            self._atomic_write(session_dir / "redo_stack.json", redo_stack or [])

            now = time.time()
            new_version = current_version + 1
            meta_data = {
                "session_id": session_id,
                "last_saved": now,
                "created_at": (meta or {}).get("created_at", now),
                "topic": (meta or {}).get("topic", ""),
                "tracks_count": len(tracks),
                "undo_depth": len(undo_stack or []),
                "redo_depth": len(redo_stack or []),
                "version": new_version,
            }
            if meta:
                meta_data.update({k: v for k, v in meta.items() if k not in meta_data})
            self._atomic_write(session_dir / "meta.json", meta_data)

            return meta_data

    def load(self, session_id: str) -> Optional[dict[str, Any]]:
        """Load session state from disk.

        Returns dict with keys: tracks, undo_stack, redo_stack, meta.
        Returns None if session doesn't exist.
        """
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            return None

        try:
            tracks = self._read_json(session_dir / "timeline.json", [])
            undo_stack = self._read_json(session_dir / "undo_stack.json", [])
            redo_stack = self._read_json(session_dir / "redo_stack.json", [])
            meta = self._read_json(session_dir / "meta.json", {})

            return {
                "tracks": tracks,
                "undo_stack": undo_stack,
                "redo_stack": redo_stack,
                "meta": meta,
            }
        except (json.JSONDecodeError, OSError):
            return None

    def exists(self, session_id: str) -> bool:
        return self._session_dir(session_id).exists()

    def delete(self, session_id: str) -> bool:
        """Delete a saved session."""
        session_dir = self._session_dir(session_id)
        if not session_dir.exists():
            return False
        with self._lock:
            import shutil
            shutil.rmtree(session_dir)
        return True

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all saved sessions with metadata."""
        sessions = []
        if not self.sessions_dir.exists():
            return sessions
        for d in self.sessions_dir.iterdir():
            if d.is_dir():
                meta = self._read_json(d / "meta.json", {})
                meta["session_id"] = d.name
                sessions.append(meta)
        sessions.sort(key=lambda s: s.get("last_saved", 0), reverse=True)
        return sessions

    def _atomic_write(self, path: Path, data: Any):
        """Write JSON atomically (temp file → rename)."""
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        tmp.replace(path)

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
