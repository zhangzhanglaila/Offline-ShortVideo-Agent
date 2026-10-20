"""Semantic Diff & Invalidation Engine.

Determines WHAT changed and HOW FAR the change propagates.

    DiffResult(
        changed_fields=["text"],
        structural_change=False,
        invalidation_depth=InvalidationDepth.LOCAL,
    )
    → only recompute the scene, not the whole pipeline

Levels:
  - LOCAL: content change within a single artifact (cache hit for siblings)
  - SUBTREE: structural change that affects downstream
  - GLOBAL: pipeline-wide change (e.g., fps, aspect ratio)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional

from compiler.thinking.canonicalize import canonicalize, content_hash


class InvalidationDepth(IntEnum):
    """How far the change propagates."""
    NONE = 0        # No actual change (cache hit)
    LOCAL = 1       # Single artifact content change
    SUBTREE = 2     # Affects downstream dependents
    GLOBAL = 3      # Pipeline-wide (config, fps, dimensions)


# Fields that trigger GLOBAL invalidation
_GLOBAL_FIELDS = frozenset({
    "fps", "width", "height", "aspect_ratio",
    "backend", "renderer", "ffmpeg_version",
    "language", "platform",
})

# Fields that only need LOCAL invalidation (no downstream effect)
_LOCAL_ONLY_FIELDS = frozenset({
    "text", "title", "subtitle", "description",
    "style", "theme", "color", "font_size",
    "emotional_intensity", "key_point",
})


@dataclass(frozen=True)
class DiffResult:
    """Result of a semantic diff between two content states."""
    changed_fields: list[str]
    structural_change: bool
    depth: InvalidationDepth
    old_hash: str
    new_hash: str

    @property
    def has_change(self) -> bool:
        return self.depth > InvalidationDepth.NONE

    @property
    def summary(self) -> str:
        if not self.has_change:
            return "no change"
        fields = ", ".join(self.changed_fields)
        return f"{self.depth.name}: {fields}"


class SemanticDiff:
    """Compute semantic diff between two content dicts.

    Determines:
      1. Which fields changed
      2. Whether the change is structural or content-only
      3. How far the invalidation should propagate
    """

    def diff(
        self,
        old_content: dict[str, Any] | None,
        new_content: dict[str, Any] | None,
    ) -> DiffResult:
        """Compute semantic diff between old and new content."""
        old_hash = content_hash(old_content) if old_content is not None else ""
        new_hash = content_hash(new_content) if new_content is not None else ""

        if old_hash == new_hash:
            return DiffResult(
                changed_fields=[],
                structural_change=False,
                depth=InvalidationDepth.NONE,
                old_hash=old_hash,
                new_hash=new_hash,
            )

        if old_content is None or new_content is None:
            return DiffResult(
                changed_fields=["<existence>"],
                structural_change=True,
                depth=InvalidationDepth.SUBTREE,
                old_hash=old_hash,
                new_hash=new_hash,
            )

        changed = self._find_changed_fields(old_content, new_content, prefix="")
        structural = self._is_structural_change(old_content, new_content)
        depth = self._compute_depth(changed, structural)

        return DiffResult(
            changed_fields=changed,
            structural_change=structural,
            depth=depth,
            old_hash=old_hash,
            new_hash=new_hash,
        )

    def _find_changed_fields(
        self,
        old: dict[str, Any],
        new: dict[str, Any],
        prefix: str,
    ) -> list[str]:
        """Recursively find changed field paths."""
        changed = []
        all_keys = set(old.keys()) | set(new.keys())

        for key in sorted(all_keys):
            path = f"{prefix}.{key}" if prefix else key
            old_val = old.get(key)
            new_val = new.get(key)

            if old_val is None and new_val is None:
                continue
            if old_val is None or new_val is None:
                changed.append(path)
                continue

            if isinstance(old_val, dict) and isinstance(new_val, dict):
                changed.extend(self._find_changed_fields(old_val, new_val, path))
            elif isinstance(old_val, list) and isinstance(new_val, list):
                if len(old_val) != len(new_val):
                    changed.append(path)
                else:
                    for i, (a, b) in enumerate(zip(old_val, new_val)):
                        if content_hash(a) != content_hash(b):
                            changed.append(f"{path}[{i}]")
            else:
                if content_hash(old_val) != content_hash(new_val):
                    changed.append(path)

        return changed

    def _is_structural_change(
        self,
        old: dict[str, Any],
        new: dict[str, Any],
    ) -> bool:
        """Determine if the change is structural (not just content).

        Structural changes:
          - Different set of keys
          - Different list lengths
          - Type changes
        """
        old_keys = set(old.keys())
        new_keys = set(new.keys())
        if old_keys != new_keys:
            return True

        for key in old_keys:
            old_val = old[key]
            new_val = new[key]
            if type(old_val) != type(new_val):
                return True
            if isinstance(old_val, list) and isinstance(new_val, list):
                if len(old_val) != len(new_val):
                    return True

        return False

    def _compute_depth(
        self,
        changed_fields: list[str],
        structural: bool,
    ) -> InvalidationDepth:
        """Compute invalidation depth from changed fields."""
        if not changed_fields:
            return InvalidationDepth.NONE

        # Check if any changed field is a global config field
        for field_path in changed_fields:
            root_field = field_path.split(".")[0]
            if root_field in _GLOBAL_FIELDS:
                return InvalidationDepth.GLOBAL

        if structural:
            return InvalidationDepth.SUBTREE

        # All changed fields are local-only
        for field_path in changed_fields:
            root_field = field_path.split(".")[0]
            if root_field not in _LOCAL_ONLY_FIELDS:
                return InvalidationDepth.SUBTREE

        return InvalidationDepth.LOCAL
