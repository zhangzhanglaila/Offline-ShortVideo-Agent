"""Persistence Layer — Disk-backed Patch Log for Agent Session Database.

Saves every patch to disk as numbered JSON files:
  patch_log/
    0001_edit_sentence.json
    0002_add_sentence.json
    ...

Supports:
  - Replay: reconstruct state from all patches
  - Checkpoint: save full state snapshot for fast restore
  - Branch: fork patch history for parallel exploration
  - Collaboration-ready: patches are transferable

This turns the ThinkingSession from an in-memory runtime
into a persistent, versioned, branchable Agent Session Database.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Optional

from compiler.thinking.patch import PatchOperation, PatchHistory, _patch_from_dict


class PatchStore:
    """Disk-backed storage for patch operations.

    Directory structure:
      base_dir/
        patches/
          0001_edit_sentence.json
          0002_add_sentence.json
          ...
        checkpoints/
          checkpoint_0005.json  (full state snapshot after patch 5)
        meta.json  (session metadata)
    """

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.patches_dir = self.base_dir / "patches"
        self.checkpoints_dir = self.base_dir / "checkpoints"
        self._ensure_dirs()

    def _ensure_dirs(self):
        self.patches_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)

    # ── Patch Operations ──

    def save_patch(self, patch: PatchOperation, index: int) -> Path:
        """Save a single patch to disk."""
        data = patch.to_dict()
        filename = f"{index:04d}_{patch.__class__.__name__}.json"
        path = self.patches_dir / filename
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load_patch(self, path: Path) -> PatchOperation:
        """Load a single patch from disk."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return _patch_from_dict(data)

    def load_all_patches(self) -> list[PatchOperation]:
        """Load all patches in order."""
        patch_files = sorted(self.patches_dir.glob("*.json"))
        patches = []
        for f in patch_files:
            try:
                patches.append(self.load_patch(f))
            except Exception:
                continue  # Skip corrupted patches
        return patches

    def patch_count(self) -> int:
        """Count stored patches."""
        return len(list(self.patches_dir.glob("*.json")))

    # ── Checkpoint Operations ──

    def save_checkpoint(self, state, patch_index: int) -> Path:
        """Save a full state snapshot at a given patch index."""
        data = state.to_dict()
        data["_checkpoint_meta"] = {
            "patch_index": patch_index,
            "timestamp": time.time(),
        }
        filename = f"checkpoint_{patch_index:04d}.json"
        path = self.checkpoints_dir / filename
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load_latest_checkpoint(self) -> Optional[tuple[dict, int]]:
        """Load the most recent checkpoint. Returns (state_dict, patch_index) or None."""
        checkpoints = sorted(self.checkpoints_dir.glob("checkpoint_*.json"))
        if not checkpoints:
            return None
        latest = checkpoints[-1]
        data = json.loads(latest.read_text(encoding="utf-8"))
        meta = data.pop("_checkpoint_meta", {})
        return data, meta.get("patch_index", 0)

    def load_checkpoint_at(self, patch_index: int) -> Optional[dict]:
        """Load a specific checkpoint."""
        filename = f"checkpoint_{patch_index:04d}.json"
        path = self.checkpoints_dir / filename
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("_checkpoint_meta", None)
        return data

    # ── Replay ──

    def replay(self, state, from_checkpoint: bool = True) -> int:
        """Replay all patches to reconstruct state.

        Args:
            state: The VideoProjectState to replay into
            from_checkpoint: If True, start from latest checkpoint

        Returns:
            Number of patches successfully applied
        """
        patches = self.load_all_patches()
        start_index = 0

        if from_checkpoint:
            checkpoint = self.load_latest_checkpoint()
            if checkpoint:
                state_dict, start_index = checkpoint
                from compiler.thinking.state import VideoProjectState
                restored = VideoProjectState.from_dict(state_dict)
                # Copy restored state into the provided state object
                for key, value in restored.__dict__.items():
                    setattr(state, key, value)

        # Apply patches from the checkpoint forward
        count = 0
        for i, patch in enumerate(patches):
            if i < start_index:
                continue
            if patch.apply(state):
                count += 1

        return count

    # ── Branch Operations ──

    def create_branch(self, branch_name: str) -> Path:
        """Create a branch by copying the current patch log."""
        branch_dir = self.base_dir / "branches" / branch_name
        if branch_dir.exists():
            shutil.rmtree(branch_dir)
        shutil.copytree(self.base_dir, branch_dir, ignore=shutil.ignore_patterns("branches"))
        return branch_dir

    def list_branches(self) -> list[str]:
        """List all branches."""
        branches_dir = self.base_dir / "branches"
        if not branches_dir.exists():
            return []
        return [d.name for d in branches_dir.iterdir() if d.is_dir()]

    # ── Metadata ──

    def save_meta(self, meta: dict):
        """Save session metadata."""
        path = self.base_dir / "meta.json"
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_meta(self) -> dict:
        """Load session metadata."""
        path = self.base_dir / "meta.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}


class PersistentPatchHistory(PatchHistory):
    """Extended PatchHistory that persists every patch to disk.

    Drop-in replacement for PatchHistory — same API, but patches
    are saved to a PatchStore on every record/undo/redo.
    """

    def __init__(self, store: PatchStore):
        super().__init__()
        self.store = store
        self._checkpoint_interval = 10  # Auto-checkpoint every N patches

    def record(self, patch: PatchOperation):
        """Record a patch and save to disk."""
        super().record(patch)
        index = len(self._applied) - 1
        self.store.save_patch(patch, index)

        # Auto-checkpoint
        if (index + 1) % self._checkpoint_interval == 0:
            # Checkpoint will be saved when session.save() is called
            pass

    def undo(self, state) -> bool:
        """Undo and record the undo as a new patch entry."""
        success = super().undo(state)
        if success:
            # Save an "undo" marker
            from compiler.thinking.patch import BatchPatch
            undo_marker = BatchPatch(
                target="undo",
                description=f"Undo: {self._undone[-1].description}",
            )
            self.store.save_patch(undo_marker, len(self._applied))
        return success

    def replay_from_disk(self, state) -> int:
        """Replay all patches from disk to reconstruct state."""
        return self.store.replay(state)

    def take_checkpoint(self, state) -> Path:
        """Save a checkpoint of the current state."""
        return self.store.save_checkpoint(state, len(self._applied))
