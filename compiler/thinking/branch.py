"""Branch & Merge — Git for Media.

Enables parallel exploration of creative directions:
  branch A: rewrite narration
  branch B: rewrite visuals
  merge(): combine changes with conflict detection

This turns the ThinkingSession into a version-controlled
creative workspace, where every edit is a patch and every
branch is an alternative timeline.

Conflict types (media-specific):
  TEXT_EDIT:      Same sentence edited differently in both branches
  TIMING_EDIT:    Same clip timing changed in both branches
  STRUCTURAL:     Sentence added/removed in one, edited in other
  GRAPH_EDIT:     Same graph node changed in both branches
  MODULE_STATUS:  Module approval status differs

Resolution strategies:
  OURS:           Keep the current branch's version
  THEIRS:         Accept the incoming branch's version
  MANUAL:         User resolves via edit instruction
  AUTO_MERGE:     Non-conflicting changes merged automatically
"""

from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from compiler.thinking.patch import (
    PatchOperation, PatchHistory, EditSentencePatch,
    AddSentencePatch, RemoveSentencePatch, EditGraphNodePatch,
    ApproveModulePatch, BatchPatch, _patch_from_dict,
)
from compiler.thinking.persistence import PatchStore


class ConflictType(str, Enum):
    """Types of merge conflicts specific to media editing."""
    TEXT_EDIT = "text_edit"           # Same sentence, different text
    TIMING_EDIT = "timing_edit"       # Same clip, different timing
    STRUCTURAL = "structural"         # Add/remove vs edit
    GRAPH_EDIT = "graph_edit"         # Same graph node, different data
    MODULE_STATUS = "module_status"   # Approval status differs


class ResolutionStrategy(str, Enum):
    """How to resolve a merge conflict."""
    OURS = "ours"           # Keep current branch
    THEIRS = "theirs"       # Accept incoming branch
    MANUAL = "manual"       # User resolves
    AUTO_MERGE = "auto"     # Non-conflicting, merge automatically


@dataclass
class MergeConflict:
    """A detected conflict between two branches."""
    conflict_type: ConflictType = ConflictType.TEXT_EDIT
    target: str = ""                    # e.g., "mod_00.s3.text"
    ours_patch: Optional[PatchOperation] = None
    theirs_patch: Optional[PatchOperation] = None
    ours_value: Any = None
    theirs_value: Any = None
    resolution: ResolutionStrategy = ResolutionStrategy.MANUAL
    resolved_value: Any = None
    resolved: bool = False


@dataclass
class MergeResult:
    """Result of a merge operation."""
    success: bool = False
    conflicts: list[MergeConflict] = field(default_factory=list)
    auto_merged: list[PatchOperation] = field(default_factory=list)
    total_ours: int = 0
    total_theirs: int = 0
    total_merged: int = 0
    duration_ms: float = 0.0

    @property
    def has_conflicts(self) -> bool:
        return any(not c.resolved for c in self.conflicts)

    @property
    def unresolved_count(self) -> int:
        return sum(1 for c in self.conflicts if not c.resolved)

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "has_conflicts": self.has_conflicts,
            "unresolved": self.unresolved_count,
            "total_ours": self.total_ours,
            "total_theirs": self.total_theirs,
            "total_merged": self.total_merged,
            "conflicts": [
                {
                    "type": c.conflict_type.value,
                    "target": c.target,
                    "ours": str(c.ours_value),
                    "theirs": str(c.theirs_value),
                    "resolution": c.resolution.value,
                    "resolved": c.resolved,
                }
                for c in self.conflicts
            ],
            "auto_merged_count": len(self.auto_merged),
            "duration_ms": self.duration_ms,
        }


class BranchManager:
    """Manages branches — parallel creative timelines.

    Each branch is a copy of the patch log at a point in time.
    Branches can diverge as patches are applied independently,
    then merge back with conflict detection.
    """

    def __init__(self, store: PatchStore):
        self.store = store
        self._current_branch: str = "main"
        self._branch_metadata: dict[str, dict] = {}

    @property
    def current_branch(self) -> str:
        return self._current_branch

    def create_branch(self, name: str, description: str = "") -> Path:
        """Create a new branch from the current state.

        Copies the entire patch log to a new branch directory.
        """
        branch_dir = self.store.create_branch(name)
        self._branch_metadata[name] = {
            "created_at": time.time(),
            "description": description,
            "parent": self._current_branch,
        }
        return branch_dir

    def switch_branch(self, name: str):
        """Switch to a different branch."""
        branches = self.store.list_branches()
        if name != "main" and name not in branches:
            raise ValueError(f"Branch '{name}' does not exist")
        self._current_branch = name

    def list_branches(self) -> list[dict]:
        """List all branches with metadata."""
        branches = [{"name": "main", "current": self._current_branch == "main"}]
        for name in self.store.list_branches():
            meta = self._branch_metadata.get(name, {})
            branches.append({
                "name": name,
                "current": self._current_branch == name,
                "created_at": meta.get("created_at"),
                "description": meta.get("description", ""),
                "parent": meta.get("parent", ""),
            })
        return branches

    def get_branch_patches(self, branch_name: str) -> list[PatchOperation]:
        """Load all patches from a specific branch."""
        if branch_name == "main":
            return self.store.load_all_patches()

        branch_dir = self.store.base_dir / "branches" / branch_name
        if not branch_dir.exists():
            return []

        branch_store = PatchStore(branch_dir)
        return branch_store.load_all_patches()

    def get_divergence(self, branch_a: str, branch_b: str) -> dict:
        """Analyze how two branches have diverged.

        Returns counts of unique patches in each branch
        and the common ancestor point.
        """
        patches_a = self.get_branch_patches(branch_a)
        patches_b = self.get_branch_patches(branch_b)

        # Find common prefix (patches that are identical in both)
        common_len = 0
        for i in range(min(len(patches_a), len(patches_b))):
            if patches_a[i].to_dict() == patches_b[i].to_dict():
                common_len = i + 1
            else:
                break

        return {
            "common_ancestor_index": common_len,
            "branch_a_unique": len(patches_a) - common_len,
            "branch_b_unique": len(patches_b) - common_len,
            "branch_a_total": len(patches_a),
            "branch_b_total": len(patches_b),
        }


class MergeEngine:
    """Three-way merge engine for media patch histories.

    Algorithm:
      1. Find common ancestor (shared patch prefix)
      2. Extract unique patches from each branch
      3. Classify patches by target (which sentence/graph/clip they modify)
      4. Detect conflicts (same target modified differently)
      5. Auto-merge non-conflicting changes
      6. Return conflicts for user resolution

    Inspired by Git's three-way merge, but adapted for
    structured media edits (not text lines).
    """

    def __init__(self, branch_manager: BranchManager):
        self.bm = branch_manager

    def merge(self, source_branch: str, target_branch: str = None,
              state: Any = None) -> MergeResult:
        """Merge source_branch into target_branch (default: current).

        Returns MergeResult with conflicts that need resolution.
        """
        start_time = time.time()
        target = target_branch or self.bm.current_branch

        result = MergeResult()

        # Get patches from both branches
        patches_source = self.bm.get_branch_patches(source_branch)
        patches_target = self.bm.get_branch_patches(target)

        result.total_theirs = len(patches_source)
        result.total_ours = len(patches_target)

        # Find divergence point
        divergence = self.bm.get_divergence(target, source_branch)
        common_idx = divergence["common_ancestor_index"]

        # Extract unique patches from each branch
        ours_unique = patches_target[common_idx:]
        theirs_unique = patches_source[common_idx:]

        # Build target → patches index for conflict detection
        ours_by_target = self._index_patches_by_target(ours_unique)
        theirs_by_target = self._index_patches_by_target(theirs_unique)

        # Find conflicting targets (modified in both branches)
        conflicting_targets = set(ours_by_target.keys()) & set(theirs_by_target.keys())

        # Process non-conflicting patches (auto-merge)
        auto_merged = []
        for target_key, patches in theirs_by_target.items():
            if target_key not in conflicting_targets:
                auto_merged.extend(patches)

        # Process conflicting targets
        for target_key in conflicting_targets:
            ours_patches = ours_by_target[target_key]
            theirs_patches = theirs_by_target[target_key]

            conflict = self._classify_conflict(target_key, ours_patches, theirs_patches)
            if conflict:
                result.conflicts.append(conflict)
            else:
                # No real conflict — different aspects of same target
                auto_merged.extend(theirs_patches)

        result.auto_merged = auto_merged
        result.total_merged = len(auto_merged)
        result.success = True
        result.duration_ms = (time.time() - start_time) * 1000

        # If state provided and no unresolved conflicts, apply auto-merged patches
        if state and not result.has_conflicts:
            for patch in auto_merged:
                patch.apply(state)
                result.total_merged += 1

        return result

    def _index_patches_by_target(self, patches: list[PatchOperation]) -> dict[str, list[PatchOperation]]:
        """Index patches by their target for conflict detection.

        Target key format: "sentence:mod_00.s3" or "graph:mod_00.node_a"
        """
        index: dict[str, list[PatchOperation]] = {}

        for patch in patches:
            targets = self._extract_targets(patch)
            for t in targets:
                if t not in index:
                    index[t] = []
                index[t].append(patch)

        return index

    def _extract_targets(self, patch: PatchOperation) -> list[str]:
        """Extract target keys from a patch."""
        targets = []

        if isinstance(patch, EditSentencePatch):
            targets.append(f"sentence:{patch.module_id}.{patch.sentence_id}")

        elif isinstance(patch, AddSentencePatch):
            targets.append(f"sentence:{patch.module_id}.add_{patch.index}")

        elif isinstance(patch, RemoveSentencePatch):
            targets.append(f"sentence:{patch.module_id}.{patch.sentence_id}")

        elif isinstance(patch, EditGraphNodePatch):
            targets.append(f"graph:{patch.module_id}.{patch.node_id}")

        elif isinstance(patch, ApproveModulePatch):
            targets.append(f"module_status:{patch.module_id}")

        elif isinstance(patch, BatchPatch):
            for sub in patch.patches:
                targets.extend(self._extract_targets(sub))

        return targets

    def _classify_conflict(self, target_key: str,
                           ours: list[PatchOperation],
                           theirs: list[PatchOperation]) -> Optional[MergeConflict]:
        """Classify a conflict between two sets of patches on the same target."""

        # Determine conflict type from target key
        if target_key.startswith("sentence:"):
            conflict_type = ConflictType.TEXT_EDIT
        elif target_key.startswith("sentence_field:"):
            conflict_type = ConflictType.TIMING_EDIT
        elif target_key.startswith("graph:"):
            conflict_type = ConflictType.GRAPH_EDIT
        elif target_key.startswith("module_status:"):
            conflict_type = ConflictType.MODULE_STATUS
        else:
            conflict_type = ConflictType.TEXT_EDIT

        # Check if it's a structural conflict (add/remove vs edit)
        has_structural = any(
            isinstance(p, (AddSentencePatch, RemoveSentencePatch))
            for p in ours + theirs
        )
        if has_structural:
            conflict_type = ConflictType.STRUCTURAL

        # Extract final values
        ours_value = self._get_final_value(ours)
        theirs_value = self._get_final_value(theirs)

        # Check if values actually differ
        if ours_value == theirs_value:
            return None  # No real conflict

        # Try auto-resolution
        resolution = self._auto_resolve(conflict_type, ours, theirs)

        return MergeConflict(
            conflict_type=conflict_type,
            target=target_key,
            ours_patch=ours[-1] if ours else None,
            theirs_patch=theirs[-1] if theirs else None,
            ours_value=ours_value,
            theirs_value=theirs_value,
            resolution=resolution,
            resolved=(resolution != ResolutionStrategy.MANUAL),
        )

    def _get_final_value(self, patches: list[PatchOperation]) -> Any:
        """Get the final value after applying a sequence of patches."""
        if not patches:
            return None

        last = patches[-1]
        if isinstance(last, EditSentencePatch):
            return last.new_text
        elif isinstance(last, EditGraphNodePatch):
            return (last.new_label, last.new_role)
        elif isinstance(last, ApproveModulePatch):
            return last.component
        elif isinstance(last, AddSentencePatch):
            return f"add:{last.text}"
        elif isinstance(last, RemoveSentencePatch):
            return f"remove:{last.sentence_id}"
        return str(last.to_dict())

    def _auto_resolve(self, conflict_type: ConflictType,
                      ours: list[PatchOperation],
                      theirs: list[PatchOperation]) -> ResolutionStrategy:
        """Attempt automatic resolution based on conflict type.

        Rules:
          - MODULE_STATUS: keep ours (local decision wins)
          - TEXT_EDIT where one is a revert: auto-merge
          - Otherwise: manual resolution needed
        """
        if conflict_type == ConflictType.MODULE_STATUS:
            return ResolutionStrategy.OURS

        # If one side only has adds and the other only has edits:
        # the adds are structural, edits are content — manual
        if conflict_type == ConflictType.STRUCTURAL:
            return ResolutionStrategy.MANUAL

        # If both sides made the same edit (same final value after normalization):
        # already caught by _classify_conflict returning None

        return ResolutionStrategy.MANUAL

    def resolve_conflict(self, result: MergeConflict,
                         strategy: ResolutionStrategy,
                         resolved_value: Any = None,
                         state: Any = None) -> bool:
        """Resolve a single conflict.

        Args:
            result: The conflict to resolve
            strategy: How to resolve it
            resolved_value: For MANUAL, the user's chosen value
            state: If provided, apply the resolution immediately

        Returns:
            True if resolved successfully
        """
        result.resolution = strategy

        if strategy == ResolutionStrategy.OURS:
            result.resolved_value = result.ours_value
        elif strategy == ResolutionStrategy.THEIRS:
            result.resolved_value = result.theirs_value
        elif strategy == ResolutionStrategy.MANUAL:
            if resolved_value is None:
                return False
            result.resolved_value = resolved_value
        else:
            return False

        result.resolved = True

        # Apply resolution if state provided
        if state and result.theirs_patch:
            if strategy == ResolutionStrategy.THEIRS:
                result.theirs_patch.apply(state)
            elif strategy == ResolutionStrategy.MANUAL and result.theirs_patch:
                # Apply with overridden value
                if isinstance(result.theirs_patch, EditSentencePatch):
                    patched = EditSentencePatch(
                        module_id=result.theirs_patch.module_id,
                        sentence_id=result.theirs_patch.sentence_id,
                        old_text=result.theirs_patch.old_text,
                        new_text=str(resolved_value),
                    )
                    patched.apply(state)

        return True

    def apply_merge(self, result: MergeResult,
                    state: Any,
                    default_strategy: ResolutionStrategy = ResolutionStrategy.OURS) -> int:
        """Apply a complete merge result to state.

        Auto-merged patches are applied first.
        Conflicts are resolved using default_strategy (or already-resolved values).

        Returns:
            Number of patches applied
        """
        count = 0

        # Apply auto-merged patches
        for patch in result.auto_merged:
            if patch.apply(state):
                count += 1

        # Apply resolved conflicts
        for conflict in result.conflicts:
            if not conflict.resolved:
                # Use default strategy
                self.resolve_conflict(conflict, default_strategy, state=state)
            elif conflict.theirs_patch:
                if conflict.resolution == ResolutionStrategy.THEIRS:
                    conflict.theirs_patch.apply(state)
                    count += 1

        return count

    def three_way_merge_text(self, base: str, ours: str, theirs: str) -> tuple[str, bool]:
        """Simple three-way text merge for sentence content.

        Returns (merged_text, had_conflict).
        If both sides changed from base differently → conflict.
        If only one side changed → take that change.
        If both changed to same thing → no conflict.
        """
        ours_changed = (base != ours)
        theirs_changed = (base != theirs)

        if not ours_changed and not theirs_changed:
            return base, False

        if ours_changed and not theirs_changed:
            return ours, False

        if theirs_changed and not ours_changed:
            return theirs, False

        # Both changed
        if ours == theirs:
            return ours, False

        # Both changed differently — conflict
        return f"<<<<<<< OURS\n{ours}\n=======\n{theirs}\n>>>>>>> THEIRS", True
