"""EventBus — decoupled publish/subscribe event system.

All components (ThinkingAgent, TTS, Renderer, Timeline, etc.)
publish events through the bus. All consumers (UI, Logger,
Recorder, Metrics, History) subscribe to what they care about.

This replaces the direct session._emit() pattern with a proper
multi-consumer, filterable, replayable event system.

Design:
  - Thread-safe (publishes can come from any thread)
  - Filterable (subscribers get only what they want)
  - Replayable (EventLog stores all events for debug/replay)
  - Async-friendly (callbacks can be sync or async)
"""

from __future__ import annotations

import time
import uuid
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Event:
    """A single event on the bus."""
    id: str = ""
    type: str = ""              # e.g. "phase_change", "thinking", "edit", "render_progress"
    source: str = ""            # who produced it: "agent", "tts", "renderer", "user"
    session_id: str = ""
    timestamp: float = 0.0
    phase: str = ""
    data: Any = None
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = f"evt_{uuid.uuid4().hex[:10]}"
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "phase": self.phase,
            "data": self.data,
        }

    def to_sse(self) -> str:
        """Format as SSE data line."""
        import json
        return f"data: {json.dumps(self.to_dict(), ensure_ascii=False)}\n\n"


# Subscriber callback type
Subscriber = Callable[[Event], None]


class EventBus:
    """Central event bus with subscription filtering and event logging.

    Usage:
        bus = EventBus()

        # Subscribe to specific event types
        bus.subscribe("thinking", my_handler)
        bus.subscribe(["edit", "approve"], my_handler)

        # Subscribe to everything
        bus.subscribe("*", catch_all_handler)

        # Publish
        bus.publish(Event(type="thinking", data="reasoning..."))
    """

    def __init__(self, max_log_size: int = 5000):
        self._subscribers: dict[str, list[Subscriber]] = defaultdict(list)
        self._lock = threading.Lock()
        self._event_log: list[Event] = []
        self._max_log_size = max_log_size

    def subscribe(self, event_types: str | list[str], callback: Subscriber):
        """Subscribe to one or more event types. Use '*' for all events."""
        with self._lock:
            if isinstance(event_types, str):
                event_types = [event_types]
            for et in event_types:
                self._subscribers[et].append(callback)

    def unsubscribe(self, event_types: str | list[str], callback: Subscriber):
        """Remove a subscription."""
        with self._lock:
            if isinstance(event_types, str):
                event_types = [event_types]
            for et in event_types:
                subs = self._subscribers.get(et, [])
                if callback in subs:
                    subs.remove(callback)

    def publish(self, event: Event):
        """Publish an event to all matching subscribers.

        Thread-safe. Subscribers are called synchronously in publish thread.
        If you need async, wrap your subscriber accordingly.

        Supports wildcard patterns:
          - "*" matches all events
          - "artifact:*" matches "artifact:created", "artifact:stale", etc.
        """
        # Log the event
        with self._lock:
            self._event_log.append(event)
            if len(self._event_log) > self._max_log_size:
                self._event_log = self._event_log[-self._max_log_size:]

            # Collect matching subscribers (exact + wildcard)
            targets = list(self._subscribers.get(event.type, []))
            targets += list(self._subscribers.get("*", []))
            for pattern, subs in self._subscribers.items():
                if pattern != "*" and pattern.endswith(":*"):
                    prefix = pattern[:-1]  # "artifact:"
                    if event.type.startswith(prefix):
                        targets.extend(subs)

        # Call outside lock to avoid deadlocks
        for callback in targets:
            try:
                callback(event)
            except Exception:
                pass  # Don't let a broken subscriber crash the bus

    def get_log(self, event_type: str = "", since: float = 0,
                limit: int = 100) -> list[Event]:
        """Query the event log. Useful for replay and debugging."""
        with self._lock:
            events = self._event_log
        if event_type:
            events = [e for e in events if e.type == event_type]
        if since:
            events = [e for e in events if e.timestamp >= since]
        return events[-limit:]

    def clear_log(self):
        """Clear the event log."""
        with self._lock:
            self._event_log.clear()

    def subscriber_count(self, event_type: str = "") -> int:
        """Count subscribers for a type, or total."""
        with self._lock:
            if event_type:
                return len(self._subscribers.get(event_type, []))
            return sum(len(v) for v in self._subscribers.values())


# ── Global singleton ──

_global_bus: Optional[EventBus] = None
_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """Get the global EventBus singleton."""
    global _global_bus
    if _global_bus is None:
        with _bus_lock:
            if _global_bus is None:
                _global_bus = EventBus()
    return _global_bus
