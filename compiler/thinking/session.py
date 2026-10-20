"""ThinkingSession — stateful runtime for interactive video creation.

Manages:
  - VideoProjectState (the editable IR)
  - Action history (for undo/review)
  - Interruption queue (user can pause and redirect)
  - Persistence (save/resume sessions)
  - Event callbacks (notify frontend of state changes)
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from compiler.thinking.state import (
    VideoProjectState,
    ModuleState,
    ScriptSentence,
    GraphSpec,
    GraphNode,
    GraphEdge,
    AgentAction,
    ThinkingPhase,
)
from compiler.thinking.event_bus import Event, get_event_bus
from compiler.thinking.patch import (
    PatchOperation, PatchHistory,
    EditSentencePatch, AddSentencePatch, RemoveSentencePatch, ApproveModulePatch,
)


# Session storage directory
SESSIONS_DIR = Path(__file__).parent.parent / "output" / "thinking_sessions"


class ThinkingSession:
    """A stateful, resumable video creation session.

    This is the core runtime that:
      - Holds the VideoProjectState (editable IR)
      - Records every mutation as an AgentAction
      - Supports user interruptions at any phase
      - Persists to disk for resume
      - Notifies listeners of state changes (for SSE streaming)
    """

    def __init__(self, topic: str = "", session_id: str = "",
                 state: Optional[VideoProjectState] = None):
        if state:
            self.state = state
        else:
            self.state = VideoProjectState(topic=topic)

        self.history: list[AgentAction] = []
        self._interruption: Optional[str] = None  # User instruction text
        self._listeners: list[Callable] = []       # SSE callback functions
        self._cancelled: bool = False

        # Set up persistent patch store
        session_dir = SESSIONS_DIR / self.id
        from compiler.thinking.persistence import PatchStore, PersistentPatchHistory
        self._patch_store = PatchStore(session_dir / "patch_log")
        self.patch_history = PersistentPatchHistory(self._patch_store)

        # Set up incremental scheduler
        from compiler.thinking.scheduler import create_thinking_scheduler
        self.scheduler = create_thinking_scheduler()

    @property
    def id(self) -> str:
        return self.state.session_id

    # ── Event system ──

    def on_event(self, callback: Callable):
        """Register a listener for state change events."""
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable):
        """Unregister a listener."""
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _emit(self, event_type: str, data: Any = None, source: str = "session"):
        """Emit an event through the EventBus and to legacy listeners."""
        event = Event(
            type=event_type,
            source=source,
            session_id=self.id,
            phase=self.state.phase.value,
            data=data,
        )
        # Publish to EventBus (new pattern)
        get_event_bus().publish(event)
        # Also notify legacy listeners (backward compat)
        event_dict = event.to_dict()
        for listener in self._listeners:
            try:
                listener(event_dict)
            except Exception:
                pass

    # ── Action recording ──

    def record_action(self, action_type: str, target: str,
                      description: str, data_before: Any = None,
                      data_after: Any = None, is_user: bool = False):
        """Record an action in history."""
        action = AgentAction(
            action_type=action_type,
            target=target,
            description=description,
            data_before=data_before,
            data_after=data_after,
            is_user_action=is_user,
        )
        self.history.append(action)
        self._emit("action", {
            "action_id": action.id,
            "type": action_type,
            "target": target,
            "description": description,
        })

    # ── Phase management ──

    def set_phase(self, phase: ThinkingPhase, message: str = ""):
        """Transition to a new phase."""
        old_phase = self.state.phase
        self.state.phase = phase
        self.state.touch()
        self._emit("phase_change", {
            "old": old_phase.value,
            "new": phase.value,
            "message": message,
        })
        self.save()

    # ── Interruption system ──

    def interrupt(self, instruction: str):
        """User interrupts the current operation with an instruction.

        The agent loop checks for interruptions between steps.
        """
        self._interruption = instruction
        self.state.user_feedback.append(instruction)
        self._emit("interrupt", {"instruction": instruction})

    def check_interruption(self) -> Optional[str]:
        """Check if user has queued an interruption. Returns instruction or None."""
        instruction = self._interruption
        self._interruption = None
        return instruction

    def cancel(self):
        """Cancel the entire session."""
        self._cancelled = True
        self._emit("cancel", {})

    def is_cancelled(self) -> bool:
        return self._cancelled

    # ── State mutation (with Patch System) ──

    def apply_patch(self, patch: PatchOperation) -> bool:
        """Apply a patch, record it, and notify the scheduler."""
        success = patch.apply(self.state)
        if success:
            self.patch_history.record(patch)
            self.record_action(
                "patch", patch.target or patch.id,
                patch.description or f"Applied {patch.__class__.__name__}",
                data_after=patch.to_dict(), is_user=True,
            )
            self._emit("patch_applied", {
                "patch_id": patch.id,
                "type": patch.__class__.__name__,
                "target": patch.target,
            })
            # Notify scheduler for incremental recomputation
            self.scheduler.on_patch(patch, self.state)
        return success

    def undo(self) -> bool:
        """Undo the last patch."""
        success = self.patch_history.undo(self.state)
        if success:
            self._emit("undo", {"patch_id": self.patch_history._undone[-1].id})
        return success

    def redo(self) -> bool:
        """Redo the last undone patch."""
        success = self.patch_history.redo(self.state)
        if success:
            self._emit("redo", {"patch_id": self.patch_history._applied[-1].id})
        return success

    def update_sentence(self, module_id: str, sentence_id: str, new_text: str) -> bool:
        """Update a single sentence via patch."""
        module = self.state.get_module(module_id)
        if not module:
            return False
        old_text = ""
        for s in module.script:
            if s.id == sentence_id:
                old_text = s.text
                break
        patch = EditSentencePatch(
            module_id=module_id, sentence_id=sentence_id,
            old_text=old_text, new_text=new_text,
            target=f"{module_id}.script.{sentence_id}",
            description=f"修改文案: '{old_text[:20]}...' → '{new_text[:20]}...'",
        )
        return self.apply_patch(patch)

    def add_sentence(self, module_id: str, text: str, index: int = -1) -> Optional[ScriptSentence]:
        """Add a sentence via patch."""
        patch = AddSentencePatch(
            module_id=module_id, text=text, index=index,
            target=f"{module_id}.script",
            description=f"添加文案: '{text[:30]}...'",
        )
        success = self.apply_patch(patch)
        if success and patch.created_sentence_id:
            module = self.state.get_module(module_id)
            if module:
                for s in module.script:
                    if s.id == patch.created_sentence_id:
                        return s
        return None

    def remove_sentence(self, module_id: str, sentence_id: str) -> bool:
        """Remove a sentence via patch."""
        module = self.state.get_module(module_id)
        old_text = ""
        if module:
            for s in module.script:
                if s.id == sentence_id:
                    old_text = s.text
                    break
        patch = RemoveSentencePatch(
            module_id=module_id, sentence_id=sentence_id,
            removed_text=old_text,
            target=f"{module_id}.script.{sentence_id}",
            description=f"删除文案: '{old_text[:30]}...'",
        )
        return self.apply_patch(patch)

    def approve_module(self, module_id: str, component: str = "all") -> bool:
        """Approve a module component via patch."""
        patch = ApproveModulePatch(
            module_id=module_id, component=component,
            target=f"{module_id}.{component}",
            description=f"确认 {module_id} 的 {component}",
        )
        success = self.apply_patch(patch)
        if success:
            self._emit("module_approved", {
                "module_id": module_id,
                "component": component,
            })
        return success

    def set_module_thinking(self, module_id: str, thinking: str):
        """Append a thinking log entry for a module."""
        module = self.state.get_module(module_id)
        if module:
            module.thinking_log.append(thinking)
            self._emit("thinking", {
                "module_id": module_id,
                "text": thinking,
            })

    # ── Persistence ──

    def save(self):
        """Save session state to disk with checkpoint."""
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = SESSIONS_DIR / f"{self.id}.json"
        self.state.to_json(path)
        # Also save a patch checkpoint
        try:
            self.patch_history.take_checkpoint(self.state)
        except Exception:
            pass  # Don't let checkpoint failure break the save

    @classmethod
    def load(cls, session_id: str) -> Optional[ThinkingSession]:
        """Load a session from disk."""
        path = SESSIONS_DIR / f"{session_id}.json"
        if not path.exists():
            return None
        state = VideoProjectState.from_json(path)
        return cls(state=state)

    @classmethod
    def list_sessions(cls) -> list[dict]:
        """List all saved sessions with summary info."""
        if not SESSIONS_DIR.exists():
            return []
        sessions = []
        for path in sorted(SESSIONS_DIR.glob("*.json"), reverse=True):
            try:
                state = VideoProjectState.from_json(path)
                sessions.append({
                    "session_id": state.session_id,
                    "topic": state.topic,
                    "phase": state.phase.value,
                    "modules": len(state.modules),
                    "created_at": state.created_at,
                    "updated_at": state.updated_at,
                })
            except Exception:
                continue
        return sessions

    @classmethod
    def delete_session(cls, session_id: str) -> bool:
        """Delete a saved session."""
        path = SESSIONS_DIR / f"{session_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    # ── Summary ──

    def summary(self) -> dict:
        """Get a summary of the current session state."""
        return {
            "session_id": self.id,
            "topic": self.state.topic,
            "phase": self.state.phase.value,
            "modules": [
                {
                    "id": m.id,
                    "title": m.title,
                    "status": m.status,
                    "sentences": len(m.script),
                    "has_graph_a": m.graph_a is not None,
                    "has_graph_b": m.graph_b is not None,
                    "script_approved": m.script_approved,
                    "graphs_approved": m.graphs_approved,
                    "audio_approved": m.audio_approved,
                }
                for m in self.state.modules
            ],
            "history_count": len(self.history),
            "user_feedback_count": len(self.state.user_feedback),
        }
