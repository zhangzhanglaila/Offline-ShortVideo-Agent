"""Patch System — Event Sourcing for VideoProjectState.

Instead of directly mutating state, all changes go through PatchOperations:
  - Each patch is an atomic, reversible transformation
  - Patches are recorded in order (Event Sourcing)
  - Supports: undo, redo, replay, branch, diff

This replaces direct state.update_sentence() calls with a composable,
auditable transformation system.

Usage:
    patch = EditSentencePatch(module_id="mod_00", sentence_id="s_01", new_text="...")
    patch.apply(state)
    # ... later ...
    patch.revert(state)
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from compiler.thinking.state import VideoProjectState


@dataclass
class PatchOperation(ABC):
    """Base class for all patch operations.

    Every patch must:
      - Be serializable (to_dict/from_dict)
      - Be reversible (revert)
      - Be applicable (apply)
      - Carry enough context to be replayed standalone
    """
    id: str = ""
    timestamp: float = 0.0
    target: str = ""        # e.g. "mod_00.script.s_01"
    description: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = f"patch_{uuid.uuid4().hex[:10]}"
        if not self.timestamp:
            self.timestamp = time.time()

    @abstractmethod
    def apply(self, state: VideoProjectState) -> bool:
        """Apply this patch to the state. Returns True if successful."""
        ...

    @abstractmethod
    def revert(self, state: VideoProjectState) -> bool:
        """Revert this patch from the state. Returns True if successful."""
        ...

    @abstractmethod
    def to_dict(self) -> dict:
        """Serialize to dict for persistence."""
        ...

    @classmethod
    @abstractmethod
    def from_dict(cls, data: dict) -> PatchOperation:
        """Deserialize from dict."""
        ...


# ── Concrete Patch Operations ──

@dataclass
class EditSentencePatch(PatchOperation):
    """Edit a single sentence's text."""
    module_id: str = ""
    sentence_id: str = ""
    old_text: str = ""
    new_text: str = ""

    def apply(self, state: VideoProjectState) -> bool:
        return state.update_sentence(self.module_id, self.sentence_id, self.new_text)

    def revert(self, state: VideoProjectState) -> bool:
        return state.update_sentence(self.module_id, self.sentence_id, self.old_text)

    def to_dict(self) -> dict:
        return {
            "type": "edit_sentence", "id": self.id, "timestamp": self.timestamp,
            "target": self.target, "description": self.description,
            "module_id": self.module_id, "sentence_id": self.sentence_id,
            "old_text": self.old_text, "new_text": self.new_text,
        }

    @classmethod
    def from_dict(cls, data: dict) -> EditSentencePatch:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class AddSentencePatch(PatchOperation):
    """Add a new sentence to a module."""
    module_id: str = ""
    text: str = ""
    index: int = -1
    created_sentence_id: str = ""  # filled after apply

    def apply(self, state: VideoProjectState) -> bool:
        result = state.add_sentence(self.module_id, self.text, self.index)
        if result:
            self.created_sentence_id = result.id
            return True
        return False

    def revert(self, state: VideoProjectState) -> bool:
        if self.created_sentence_id:
            return state.remove_sentence(self.module_id, self.created_sentence_id)
        return False

    def to_dict(self) -> dict:
        return {
            "type": "add_sentence", "id": self.id, "timestamp": self.timestamp,
            "target": self.target, "description": self.description,
            "module_id": self.module_id, "text": self.text, "index": self.index,
            "created_sentence_id": self.created_sentence_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AddSentencePatch:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class RemoveSentencePatch(PatchOperation):
    """Remove a sentence from a module."""
    module_id: str = ""
    sentence_id: str = ""
    removed_text: str = ""         # snapshot for revert
    removed_index: int = 0

    def apply(self, state: VideoProjectState) -> bool:
        module = state.get_module(self.module_id)
        if module:
            for s in module.script:
                if s.id == self.sentence_id:
                    self.removed_text = s.text
                    self.removed_index = s.index
                    break
        return state.remove_sentence(self.module_id, self.sentence_id)

    def revert(self, state: VideoProjectState) -> bool:
        result = state.add_sentence(self.module_id, self.removed_text, self.removed_index)
        return result is not None

    def to_dict(self) -> dict:
        return {
            "type": "remove_sentence", "id": self.id, "timestamp": self.timestamp,
            "target": self.target, "description": self.description,
            "module_id": self.module_id, "sentence_id": self.sentence_id,
            "removed_text": self.removed_text, "removed_index": self.removed_index,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RemoveSentencePatch:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ApproveModulePatch(PatchOperation):
    """Approve a module component."""
    module_id: str = ""
    component: str = "all"  # script/graphs/audio/cards/all
    was_approved: bool = False  # snapshot for revert

    def apply(self, state: VideoProjectState) -> bool:
        module = state.get_module(self.module_id)
        if module:
            if self.component in ("script", "all"):
                self.was_approved = self.was_approved or module.script_approved
            return state.approve_module(self.module_id, self.component)
        return False

    def revert(self, state: VideoProjectState) -> bool:
        module = state.get_module(self.module_id)
        if module:
            if self.component in ("script", "all"):
                module.script_approved = False
            if self.component in ("graphs", "all"):
                module.graphs_approved = False
            if self.component in ("audio", "all"):
                module.audio_approved = False
            return True
        return False

    def to_dict(self) -> dict:
        return {
            "type": "approve_module", "id": self.id, "timestamp": self.timestamp,
            "target": self.target, "description": self.description,
            "module_id": self.module_id, "component": self.component,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ApproveModulePatch:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class EditGraphNodePatch(PatchOperation):
    """Edit a graph node's label or role."""
    module_id: str = ""
    graph_key: str = ""      # "a" or "b"
    node_id: str = ""
    old_label: str = ""
    new_label: str = ""
    old_role: str = ""
    new_role: str = ""

    def apply(self, state: VideoProjectState) -> bool:
        module = state.get_module(self.module_id)
        if module:
            graph = module.graph_a if self.graph_key == "a" else module.graph_b
            if graph:
                for n in graph.nodes:
                    if n.id == self.node_id:
                        self.old_label = n.label
                        self.old_role = n.role
                        break
        return state.update_graph_node(
            self.module_id, self.graph_key, self.node_id,
            label=self.new_label, role=self.new_role,
        )

    def revert(self, state: VideoProjectState) -> bool:
        return state.update_graph_node(
            self.module_id, self.graph_key, self.node_id,
            label=self.old_label, role=self.old_role,
        )

    def to_dict(self) -> dict:
        return {
            "type": "edit_graph_node", "id": self.id, "timestamp": self.timestamp,
            "module_id": self.module_id, "graph_key": self.graph_key,
            "node_id": self.node_id, "old_label": self.old_label,
            "new_label": self.new_label, "old_role": self.old_role,
            "new_role": self.new_role,
        }

    @classmethod
    def from_dict(cls, data: dict) -> EditGraphNodePatch:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ConditionalPatch(PatchOperation):
    """Patch that only applies if a condition is met.

    The condition is evaluated at apply-time against the current state.
    If the condition returns False, the patch is a no-op (apply returns True
    but nothing changes). Revert is also a no-op if the inner patch was
    never applied.
    """
    condition_fn: Any = None    # Callable[[VideoProjectState], bool]
    inner_patch: PatchOperation = None
    _applied: bool = field(default=False, repr=False)

    def apply(self, state: VideoProjectState) -> bool:
        if self.condition_fn and not self.condition_fn(state):
            return True  # Condition not met — no-op
        if self.inner_patch:
            self._applied = True
            return self.inner_patch.apply(state)
        return True

    def revert(self, state: VideoProjectState) -> bool:
        if not self._applied:
            return True  # Nothing to revert
        if self.inner_patch:
            return self.inner_patch.revert(state)
        return True

    def to_dict(self) -> dict:
        return {
            "type": "conditional", "id": self.id, "timestamp": self.timestamp,
            "target": self.target, "description": self.description,
            "inner_patch": self.inner_patch.to_dict() if self.inner_patch else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ConditionalPatch:
        inner = _patch_from_dict(data["inner_patch"]) if data.get("inner_patch") else None
        return cls(
            id=data.get("id", ""),
            timestamp=data.get("timestamp", 0),
            target=data.get("target", ""),
            description=data.get("description", ""),
            inner_patch=inner,
        )


@dataclass
class BatchPatch(PatchOperation):
    """A composite patch that groups multiple operations atomically."""
    patches: list[PatchOperation] = field(default_factory=list)

    def apply(self, state: VideoProjectState) -> bool:
        for p in self.patches:
            if not p.apply(state):
                return False
        return True

    def revert(self, state: VideoProjectState) -> bool:
        # Revert in reverse order
        for p in reversed(self.patches):
            if not p.revert(state):
                return False
        return True

    def to_dict(self) -> dict:
        return {
            "type": "batch", "id": self.id, "timestamp": self.timestamp,
            "target": self.target, "description": self.description,
            "patches": [p.to_dict() for p in self.patches],
        }

    @classmethod
    def from_dict(cls, data: dict) -> BatchPatch:
        patches = [_patch_from_dict(p) for p in data.get("patches", [])]
        return cls(
            id=data.get("id", ""),
            timestamp=data.get("timestamp", 0),
            target=data.get("target", ""),
            description=data.get("description", ""),
            patches=patches,
        )


# ── Patch History (Event Sourcing) ──

class PatchHistory:
    """Ordered history of applied patches with undo/redo support.

    This is the Event Sourcing log — every state mutation is recorded
    as a patch, enabling full replay, undo, and branching.
    """

    def __init__(self):
        self._applied: list[PatchOperation] = []
        self._undone: list[PatchOperation] = []

    def record(self, patch: PatchOperation):
        """Record a newly applied patch. Clears redo stack."""
        self._applied.append(patch)
        self._undone.clear()

    def undo(self, state: VideoProjectState) -> bool:
        """Undo the last applied patch."""
        if not self._applied:
            return False
        patch = self._applied.pop()
        success = patch.revert(state)
        if success:
            self._undone.append(patch)
        else:
            # Re-apply if revert failed
            self._applied.append(patch)
        return success

    def redo(self, state: VideoProjectState) -> bool:
        """Redo the last undone patch."""
        if not self._undone:
            return False
        patch = self._undone.pop()
        success = patch.apply(state)
        if success:
            self._applied.append(patch)
        else:
            self._undone.append(patch)
        return success

    def replay(self, state: VideoProjectState) -> int:
        """Replay all patches from scratch. Returns count of successful patches."""
        count = 0
        for patch in self._applied:
            if patch.apply(state):
                count += 1
        return count

    @property
    def applied(self) -> list[PatchOperation]:
        return list(self._applied)

    @property
    def can_undo(self) -> bool:
        return len(self._applied) > 0

    @property
    def can_redo(self) -> bool:
        return len(self._undone) > 0

    def to_dicts(self) -> list[dict]:
        """Serialize all applied patches."""
        return [p.to_dict() for p in self._applied]

    def load_dicts(self, data: list[dict]):
        """Load patches from serialized data."""
        self._applied = [_patch_from_dict(d) for d in data]
        self._undone.clear()


# ── Deserialization ──

_PATCH_TYPES = {
    "edit_sentence": EditSentencePatch,
    "add_sentence": AddSentencePatch,
    "remove_sentence": RemoveSentencePatch,
    "approve_module": ApproveModulePatch,
    "edit_graph_node": EditGraphNodePatch,
    "conditional": ConditionalPatch,
    "batch": BatchPatch,
}


def _patch_from_dict(data: dict) -> PatchOperation:
    """Deserialize a patch from its dict representation."""
    patch_type = data.get("type", "")
    cls = _PATCH_TYPES.get(patch_type)
    if cls:
        return cls.from_dict(data)
    raise ValueError(f"Unknown patch type: {patch_type}")
