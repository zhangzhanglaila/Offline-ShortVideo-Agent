"""Task Queue — Priority queue for distributed render tasks.

Provides a thread-safe task queue with priority, retry tracking,
and completion callbacks.

Usage:
    queue = RenderTaskQueue()
    queue.enqueue("scene_1", priority=1, payload={"content_hash": "abc"})
    queue.enqueue("scene_2", priority=0, payload={"content_hash": "def"})

    task = queue.next_task()  # Highest priority first
    queue.mark_running(task.task_id)
    queue.mark_completed(task.task_id)
    # or
    queue.mark_failed(task.task_id, "ffmpeg error")
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


@dataclass
class RenderTask:
    """A single render task."""
    task_id: str
    scene_id: str
    content_hash: str
    priority: int = 0  # Higher = more urgent
    status: TaskStatus = TaskStatus.PENDING
    payload: dict[str, Any] = field(default_factory=dict)
    assigned_worker: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    error: str | None = None
    created_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    completed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "scene_id": self.scene_id,
            "content_hash": self.content_hash,
            "priority": self.priority,
            "status": self.status.value,
            "assigned_worker": self.assigned_worker,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


class RenderTaskQueue:
    """Thread-safe priority queue for render tasks."""

    def __init__(self, max_retries: int = 3):
        self.tasks: dict[str, RenderTask] = {}
        self.max_retries = max_retries
        self._lock = threading.Lock()

    def enqueue(
        self,
        scene_id: str,
        content_hash: str,
        priority: int = 0,
        payload: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> RenderTask:
        """Add a task to the queue."""
        tid = task_id or f"task_{scene_id}_{int(time.monotonic() * 1000)}"
        task = RenderTask(
            task_id=tid,
            scene_id=scene_id,
            content_hash=content_hash,
            priority=priority,
            payload=payload or {},
            max_retries=self.max_retries,
        )
        with self._lock:
            self.tasks[tid] = task
        return task

    def next_task(self) -> Optional[RenderTask]:
        """Get the highest-priority pending task."""
        with self._lock:
            pending = [
                t for t in self.tasks.values()
                if t.status in (TaskStatus.PENDING, TaskStatus.RETRYING)
            ]
            if not pending:
                return None
            # Sort by priority (desc), then created_at (asc)
            pending.sort(key=lambda t: (-t.priority, t.created_at))
            return pending[0]

    def mark_running(self, task_id: str, worker_id: str) -> bool:
        """Mark a task as running on a worker."""
        with self._lock:
            task = self.tasks.get(task_id)
            if not task or task.status not in (TaskStatus.PENDING, TaskStatus.RETRYING):
                return False
            task.status = TaskStatus.RUNNING
            task.assigned_worker = worker_id
            task.started_at = time.monotonic()
            return True

    def mark_completed(self, task_id: str) -> bool:
        """Mark a task as completed."""
        with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                return False
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.monotonic()
            return True

    def mark_failed(self, task_id: str, error: str) -> bool:
        """Mark a task as failed. Auto-retries if under limit."""
        with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                return False
            task.error = error
            task.retry_count += 1
            task.assigned_worker = None

            if task.retry_count < task.max_retries:
                task.status = TaskStatus.RETRYING
            else:
                task.status = TaskStatus.FAILED
            return True

    def get_task(self, task_id: str) -> Optional[RenderTask]:
        with self._lock:
            return self.tasks.get(task_id)

    def list_tasks(self, status: TaskStatus | None = None) -> list[RenderTask]:
        with self._lock:
            if status:
                return [t for t in self.tasks.values() if t.status == status]
            return list(self.tasks.values())

    def pending_count(self) -> int:
        with self._lock:
            return sum(
                1 for t in self.tasks.values()
                if t.status in (TaskStatus.PENDING, TaskStatus.RETRYING)
            )

    def stats(self) -> dict[str, int]:
        with self._lock:
            counts: dict[str, int] = {}
            for t in self.tasks.values():
                s = t.status.value
                counts[s] = counts.get(s, 0) + 1
            counts["total"] = len(self.tasks)
            return counts

    def completed_hashes(self) -> set[str]:
        """Get content hashes of all completed tasks (for dedup)."""
        with self._lock:
            return {t.content_hash for t in self.tasks.values() if t.status == TaskStatus.COMPLETED}

    def remove_completed(self):
        """Remove completed tasks to free memory."""
        with self._lock:
            to_remove = [tid for tid, t in self.tasks.items() if t.status == TaskStatus.COMPLETED]
            for tid in to_remove:
                del self.tasks[tid]

    def clear(self):
        with self._lock:
            self.tasks.clear()
