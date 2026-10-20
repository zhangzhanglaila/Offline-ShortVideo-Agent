"""Worker — Node management for distributed rendering.

Tracks worker nodes with their status, capabilities, and current load.
Provides health checking and load balancing.

Usage:
    registry = WorkerRegistry()
    registry.register("worker-1", url="http://192.168.1.10:9000", capacity=4)
    registry.register("worker-2", url="http://192.168.1.11:9000", capacity=2)

    worker = registry.best_worker()  # Least loaded
    registry.assign_task("worker-1", "scene_1")
    registry.complete_task("worker-1", "scene_1")
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class WorkerStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    BUSY = "busy"
    FAILED = "failed"
    OFFLINE = "offline"


@dataclass
class WorkerInfo:
    """Information about a render worker node."""
    worker_id: str
    url: str
    capacity: int = 4  # Max concurrent tasks
    status: WorkerStatus = WorkerStatus.IDLE
    active_tasks: dict[str, float] = field(default_factory=dict)  # task_id → started_at
    completed_count: int = 0
    failed_count: int = 0
    last_heartbeat: float = field(default_factory=time.monotonic)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def load(self) -> int:
        return len(self.active_tasks)

    @property
    def available_capacity(self) -> int:
        return max(0, self.capacity - self.load)

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "url": self.url,
            "capacity": self.capacity,
            "status": self.status.value,
            "load": self.load,
            "available_capacity": self.available_capacity,
            "active_tasks": list(self.active_tasks.keys()),
            "completed_count": self.completed_count,
            "failed_count": self.failed_count,
            "last_heartbeat": self.last_heartbeat,
        }


class WorkerRegistry:
    """Registry of render worker nodes.

    Thread-safe. Provides load-balanced worker selection.
    """

    def __init__(self, heartbeat_timeout: float = 30.0):
        self.workers: dict[str, WorkerInfo] = {}
        self._lock = threading.Lock()
        self.heartbeat_timeout = heartbeat_timeout

    def register(self, worker_id: str, url: str, capacity: int = 4, metadata: dict[str, Any] | None = None):
        """Register a new worker or update existing."""
        with self._lock:
            if worker_id in self.workers:
                worker = self.workers[worker_id]
                worker.url = url
                worker.capacity = capacity
                worker.last_heartbeat = time.monotonic()
                if metadata:
                    worker.metadata.update(metadata)
            else:
                self.workers[worker_id] = WorkerInfo(
                    worker_id=worker_id,
                    url=url,
                    capacity=capacity,
                    metadata=metadata or {},
                )

    def unregister(self, worker_id: str) -> bool:
        """Remove a worker from the registry."""
        with self._lock:
            if worker_id in self.workers:
                del self.workers[worker_id]
                return True
            return False

    def heartbeat(self, worker_id: str):
        """Update worker heartbeat timestamp."""
        with self._lock:
            if worker_id in self.workers:
                self.workers[worker_id].last_heartbeat = time.monotonic()

    def best_worker(self) -> Optional[WorkerInfo]:
        """Select the best available worker (least loaded, healthy).

        Returns None if no workers are available.
        """
        with self._lock:
            self._check_health()
            available = [
                w for w in self.workers.values()
                if w.status in (WorkerStatus.IDLE, WorkerStatus.RUNNING)
                and w.available_capacity > 0
            ]
            if not available:
                return None
            # Sort by load (ascending), then by completed count (ascending for fairness)
            available.sort(key=lambda w: (w.load, w.completed_count))
            return available[0]

    def assign_task(self, worker_id: str, task_id: str) -> bool:
        """Assign a task to a worker."""
        with self._lock:
            worker = self.workers.get(worker_id)
            if not worker or worker.available_capacity <= 0:
                return False
            worker.active_tasks[task_id] = time.monotonic()
            if worker.load >= worker.capacity:
                worker.status = WorkerStatus.BUSY
            else:
                worker.status = WorkerStatus.RUNNING
            return True

    def complete_task(self, worker_id: str, task_id: str) -> bool:
        """Mark a task as completed on a worker."""
        with self._lock:
            worker = self.workers.get(worker_id)
            if not worker or task_id not in worker.active_tasks:
                return False
            del worker.active_tasks[task_id]
            worker.completed_count += 1
            if worker.load == 0:
                worker.status = WorkerStatus.IDLE
            elif worker.load < worker.capacity:
                worker.status = WorkerStatus.RUNNING
            return True

    def fail_task(self, worker_id: str, task_id: str) -> bool:
        """Mark a task as failed on a worker."""
        with self._lock:
            worker = self.workers.get(worker_id)
            if not worker or task_id not in worker.active_tasks:
                return False
            del worker.active_tasks[task_id]
            worker.failed_count += 1
            if worker.load == 0:
                worker.status = WorkerStatus.IDLE
            elif worker.load < worker.capacity:
                worker.status = WorkerStatus.RUNNING
            return True

    def get_worker(self, worker_id: str) -> Optional[WorkerInfo]:
        with self._lock:
            return self.workers.get(worker_id)

    def list_workers(self) -> list[dict[str, Any]]:
        with self._lock:
            self._check_health()
            return [w.to_dict() for w in self.workers.values()]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            self._check_health()
            total_capacity = sum(w.capacity for w in self.workers.values())
            total_load = sum(w.load for w in self.workers.values())
            total_completed = sum(w.completed_count for w in self.workers.values())
            total_failed = sum(w.failed_count for w in self.workers.values())
            return {
                "total_workers": len(self.workers),
                "total_capacity": total_capacity,
                "total_load": total_load,
                "available_capacity": total_capacity - total_load,
                "total_completed": total_completed,
                "total_failed": total_failed,
                "by_status": {
                    s.value: sum(1 for w in self.workers.values() if w.status == s)
                    for s in WorkerStatus
                },
            }

    def _check_health(self):
        """Mark workers as offline if heartbeat expired."""
        now = time.monotonic()
        for worker in self.workers.values():
            if worker.status != WorkerStatus.OFFLINE:
                if now - worker.last_heartbeat > self.heartbeat_timeout:
                    worker.status = WorkerStatus.OFFLINE
