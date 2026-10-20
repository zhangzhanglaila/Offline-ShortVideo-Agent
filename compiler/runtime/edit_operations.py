"""Edit Operations — Typed edit commands for the reactive runtime.

Each edit operation is a pure, deterministic command that modifies
an artifact's content. The runtime applies the edit, computes the
semantic diff, and triggers incremental recomputation.

Edit types:
  - PatchEdit: merge partial content into existing artifact
  - ReplaceEdit: replace entire artifact content
  - ReorderEdit: reorder elements within an artifact

All edits are immutable — applying an edit returns a new EditResult.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from compiler.thinking.canonicalize import content_hash


class EditType(str, Enum):
    PATCH = "patch"          # Merge partial fields
    REPLACE = "replace"      # Full content replacement
    REORDER = "reorder"      # Reorder elements
    DELETE = "delete"        # Remove artifact


@dataclass(frozen=True)
class EditOperation:
    """A single edit command.

    target_id: artifact ID to modify
    edit_type: kind of edit
    patch: the new content (or partial content for PATCH)
    path: dot-separated field path for targeted edits (e.g., "text", "audio.volume")
    """
    target_id: str
    edit_type: EditType
    patch: Any = None
    path: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.target_id:
            raise ValueError("target_id must be non-empty")
        if self.edit_type == EditType.PATCH and self.patch is None:
            raise ValueError("PATCH edit requires patch data")

    def apply(self, current_content: dict[str, Any] | None) -> dict[str, Any] | None:
        """Apply this edit to content, returning new content.

        Pure function — does not mutate input.
        """
        if self.edit_type == EditType.DELETE:
            return None

        if self.edit_type == EditType.REPLACE:
            return self.patch if isinstance(self.patch, dict) else {"value": self.patch}

        if self.edit_type == EditType.PATCH:
            if current_content is None:
                return self.patch if isinstance(self.patch, dict) else {"value": self.patch}
            return _apply_patch(current_content, self.path, self.patch)

        if self.edit_type == EditType.REORDER:
            if current_content is None:
                return current_content
            return _apply_reorder(current_content, self.path, self.patch)

        return current_content


def _apply_patch(content: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """Apply a patch at a dot-separated path.

    _apply_patch({"a": {"b": 1}}, "a.b", 2) → {"a": {"b": 2}}
    _apply_patch({"a": 1}, "b", 2) → {"a": 1, "b": 2}
    """
    if not path:
        # Merge at root level
        result = dict(content)
        if isinstance(value, dict):
            result.update(value)
        else:
            result["value"] = value
        return result

    keys = path.split(".")
    result = dict(content)
    current = result

    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        else:
            current[key] = dict(current[key])
        current = current[key]

    current[keys[-1]] = value
    return result


def _apply_reorder(content: dict[str, Any], path: str, new_order: Any) -> dict[str, Any]:
    """Reorder elements at path according to new_order indices.

    _apply_reorder({"items": [a, b, c]}, "items", [2, 0, 1])
    → {"items": [c, a, b]}
    """
    if not path or not isinstance(new_order, list):
        return content

    keys = path.split(".")
    result = dict(content)
    current = result

    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            return content
        current[key] = dict(current[key])
        current = current[key]

    target_key = keys[-1]
    if target_key not in current or not isinstance(current[target_key], list):
        return content

    original = current[target_key]
    if len(new_order) != len(original):
        return content

    try:
        current[target_key] = [original[i] for i in new_order]
    except (IndexError, TypeError):
        return content

    return result


@dataclass(frozen=True)
class EditResult:
    """Result of applying an edit operation."""
    operation: EditOperation
    old_hash: str                       # Content hash before edit
    new_hash: str                       # Content hash after edit
    changed: bool                       # Whether content actually changed
    old_content: dict[str, Any] | None  # Content before edit
    new_content: dict[str, Any] | None  # Content after edit

    @property
    def target_id(self) -> str:
        return self.operation.target_id
