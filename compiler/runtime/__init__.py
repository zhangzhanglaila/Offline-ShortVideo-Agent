"""Reactive Live Editing Runtime.

The interactive layer on top of the incremental compiler.

    session = LiveSession(artifact_graph)
    session.edit("hook.text", "Redis为什么快？")
    # → semantic diff → invalidate → recompute → rerender → recompose

Components:
  - LiveSession: manages the editing lifecycle
  - EditOperation: typed edit commands (patch, replace, reorder)
  - SemanticDiff: structural change detection
"""

from compiler.runtime.edit_operations import (
    EditOperation, EditResult, EditType,
)
from compiler.runtime.invalidation import SemanticDiff, DiffResult, InvalidationDepth
from compiler.runtime.live_session import LiveSession, SessionStats
