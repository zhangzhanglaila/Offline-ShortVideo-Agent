"""Scheduler — Distributed render task scheduler.

Coordinates task assignment across worker nodes. Provides:
  - Scene-level task decomposition
  - Load-balanced worker selection
  - Parallel execution with configurable concurrency
  - Integration with RenderCache for dedup

Usage:
    from runtime.distributed_scheduler import DistributedScheduler
    from runtime.worker import WorkerRegistry
    from runtime.render_task_queue import RenderTaskQueue

    scheduler = DistributedScheduler()

    # Register workers
    scheduler.add_worker("w1", "http://localhost:9000", capacity=4)
    scheduler.add_worker("w2", "http://localhost:9001", capacity=2)

    # Submit scenes for rendering
    scheduler.submit_scenes([
        {"scene_id": "s1", "content_hash": "abc", "scene_ir": {...}},
        {"scene_id": "s2", "content_hash": "def", "scene_ir": {...}},
    ])

    # Process (blocks until all done or failed)
    results = scheduler.process_all(render_fn)
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from typing import Any, Callable, Optional

from runtime.worker import WorkerRegistry, WorkerInfo
from runtime.render_task_queue import RenderTaskQueue, RenderTask, TaskStatus

logger = logging.getLogger(__name__)


class RenderResult:
    """Result of a single scene render."""

    def __init__(self, task: RenderTask, output_path: str = "", success: bool = True, error: str = ""):
        self.task = task
        self.output_path = output_path
        self.success = success
        self.error = error
        self.duration = 0.0
        if task.started_at and task.completed_at:
            self.duration = task.completed_at - task.started_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task.task_id,
            "scene_id": self.task.scene_id,
            "content_hash": self.task.content_hash,
            "output_path": self.output_path,
            "success": self.success,
            "error": self.error,
            "duration": self.duration,
            "retry_count": self.task.retry_count,
        }


class DistributedScheduler:
    """Distributed render scheduler.

    Assigns render tasks to worker nodes using load-balanced selection.
    Supports parallel execution with configurable concurrency.
    """

    def __init__(self, max_workers: int = 8):
        self.registry = WorkerRegistry()
        self.queue = RenderTaskQueue()
        self.max_workers = max_workers
        self.results: dict[str, RenderResult] = {}

    def add_worker(self, worker_id: str, url: str, capacity: int = 4):
        """Register a render worker."""
        self.registry.register(worker_id, url, capacity)

    def remove_worker(self, worker_id: str):
        """Unregister a worker."""
        self.registry.unregister(worker_id)

    def submit_scenes(self, scenes: list[dict[str, Any]]):
        """Submit scenes for distributed rendering.

        Each scene dict should have: scene_id, content_hash, scene_ir.
        """
        for scene in scenes:
            self.queue.enqueue(
                scene_id=scene["scene_id"],
                content_hash=scene["content_hash"],
                priority=scene.get("priority", 0),
                payload=scene,
            )

    def process_all(
        self,
        render_fn: Callable[[dict[str, Any]], str],
        timeout: float = 300.0,
    ) -> list[RenderResult]:
        """Process all pending tasks using available workers.

        Args:
            render_fn: Function that takes a scene payload and returns output path.
            timeout: Max time to wait for all tasks.

        Returns:
            List of RenderResult for all tasks.
        """
        results: list[RenderResult] = []
        start = time.monotonic()

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures: dict[Future, RenderTask] = {}

            while time.monotonic() - start < timeout:
                # Submit tasks to available workers
                while True:
                    task = self.queue.next_task()
                    if not task:
                        break

                    worker = self.registry.best_worker()
                    if not worker:
                        break

                    self.queue.mark_running(task.task_id, worker.worker_id)
                    self.registry.assign_task(worker.worker_id, task.task_id)

                    future = executor.submit(
                        self._execute_task, worker, task, render_fn
                    )
                    futures[future] = task

                # Collect completed futures
                done_futures = [f for f in futures if f.done()]
                for future in done_futures:
                    result = future.result()
                    results.append(result)
                    self.results[result.task.task_id] = result
                    del futures[future]

                # Check if all tasks are done
                if not futures and self.queue.pending_count() == 0:
                    break

                # Brief sleep to avoid busy-waiting
                if futures:
                    time.sleep(0.05)

            # Wait for remaining futures
            for future in as_completed(futures, timeout=max(0, timeout - (time.monotonic() - start))):
                result = future.result()
                results.append(result)
                self.results[result.task.task_id] = result

        return results

    def _execute_task(
        self,
        worker: WorkerInfo,
        task: RenderTask,
        render_fn: Callable[[dict[str, Any]], str],
    ) -> RenderResult:
        """Execute a single render task on a worker."""
        try:
            output_path = render_fn(task.payload)
            self.queue.mark_completed(task.task_id)
            self.registry.complete_task(worker.worker_id, task.task_id)
            return RenderResult(task=task, output_path=output_path, success=True)
        except Exception as e:
            self.queue.mark_failed(task.task_id, str(e))
            self.registry.fail_task(worker.worker_id, task.task_id)
            return RenderResult(task=task, success=False, error=str(e))

    def get_status(self) -> dict[str, Any]:
        """Get current scheduler status."""
        return {
            "workers": self.registry.stats(),
            "queue": self.queue.stats(),
            "results_count": len(self.results),
        }
