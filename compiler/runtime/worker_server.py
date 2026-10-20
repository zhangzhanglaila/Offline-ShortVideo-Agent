"""Worker Server — HTTP server for render worker nodes.

Provides endpoints for the scheduler to dispatch tasks to workers:
  - POST /render — Accept a render task, execute it, return result
  - GET /health — Heartbeat / liveness check
  - GET /status — Current worker status (load, capacity, active tasks)
  - POST /cancel/{task_id} — Cancel a running task

Usage:
    from compiler.runtime.worker_server import create_worker_app
    app = create_worker_app(worker_id="worker-1", capacity=4, render_fn=my_render)
    # Run with: uvicorn runtime.worker_server:app --host 0.0.0.0 --port 9000
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Models ──


class RenderRequest(BaseModel):
    """Task to render on this worker."""
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    scene_id: str
    content_hash: str
    scene_ir: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    timeout: float = 120.0


class RenderResponse(BaseModel):
    task_id: str
    scene_id: str
    success: bool
    output_path: str = ""
    duration: float = 0.0
    error: str | None = None
    worker_id: str = ""


class HealthResponse(BaseModel):
    worker_id: str
    status: str
    uptime: float
    timestamp: float


class StatusResponse(BaseModel):
    worker_id: str
    capacity: int
    active_tasks: int
    completed_count: int
    failed_count: int
    active_task_ids: list[str]
    uptime: float


# ── Worker State ──


class WorkerState:
    """Thread-safe state for a single render worker."""

    def __init__(self, worker_id: str, capacity: int = 4):
        self.worker_id = worker_id
        self.capacity = capacity
        self.active_tasks: dict[str, float] = {}  # task_id → started_at
        self.completed_count = 0
        self.failed_count = 0
        self.start_time = time.monotonic()
        self._lock = threading.RLock()

    @property
    def load(self) -> int:
        with self._lock:
            return len(self.active_tasks)

    @property
    def available(self) -> int:
        with self._lock:
            return max(0, self.capacity - len(self.active_tasks))

    def accept_task(self, task_id: str) -> bool:
        with self._lock:
            if len(self.active_tasks) >= self.capacity:
                return False
            self.active_tasks[task_id] = time.monotonic()
            return True

    def complete_task(self, task_id: str):
        with self._lock:
            self.active_tasks.pop(task_id, None)
            self.completed_count += 1

    def fail_task(self, task_id: str):
        with self._lock:
            self.active_tasks.pop(task_id, None)
            self.failed_count += 1

    def cancel_task(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self.active_tasks:
                del self.active_tasks[task_id]
                return True
            return False

    def to_status(self) -> dict:
        with self._lock:
            return {
                "worker_id": self.worker_id,
                "capacity": self.capacity,
                "active_tasks": len(self.active_tasks),
                "available": self.available,
                "completed_count": self.completed_count,
                "failed_count": self.failed_count,
                "active_task_ids": list(self.active_tasks.keys()),
                "uptime": time.monotonic() - self.start_time,
            }


# ── Router Factory ──


def create_worker_router(
    worker_id: str,
    capacity: int = 4,
    render_fn: Callable[[dict[str, Any]], str] | None = None,
) -> tuple[APIRouter, WorkerState]:
    """Create a FastAPI router for a render worker.

    Args:
        worker_id: Unique identifier for this worker.
        capacity: Max concurrent tasks.
        render_fn: Function that renders a scene (payload dict → output path).

    Returns:
        (router, state) tuple. State can be used for monitoring.
    """
    state = WorkerState(worker_id, capacity)
    router = APIRouter()

    def _default_render(payload: dict[str, Any]) -> str:
        """Default render function (simulates work)."""
        import random
        time.sleep(random.uniform(0.05, 0.2))
        return f"/tmp/rendered_{payload.get('scene_id', 'unknown')}.mp4"

    actual_render = render_fn or _default_render

    @router.get("/health")
    async def health() -> HealthResponse:
        return HealthResponse(
            worker_id=worker_id,
            status="ok",
            uptime=time.monotonic() - state.start_time,
            timestamp=time.time(),
        )

    @router.get("/status")
    async def status() -> StatusResponse:
        s = state.to_status()
        return StatusResponse(
            worker_id=s["worker_id"],
            capacity=s["capacity"],
            active_tasks=s["active_tasks"],
            completed_count=s["completed_count"],
            failed_count=s["failed_count"],
            active_task_ids=s["active_task_ids"],
            uptime=s["uptime"],
        )

    @router.post("/render")
    async def render(req: RenderRequest) -> RenderResponse:
        if not state.accept_task(req.task_id):
            raise HTTPException(
                status_code=503,
                detail=f"Worker at capacity ({state.capacity})",
            )

        # perf_counter 分辨率远高于 monotonic（Windows 时钟粒度约 15ms），
        # 否则毫秒级渲染会记成 0.0s
        start = time.perf_counter()
        try:
            payload = {
                "task_id": req.task_id,
                "scene_id": req.scene_id,
                "content_hash": req.content_hash,
                **req.scene_ir,
                **req.settings,
            }

            # Run render in thread pool to avoid blocking
            output_path = await asyncio.wait_for(
                asyncio.to_thread(actual_render, payload),
                timeout=req.timeout,
            )

            duration = time.perf_counter() - start
            state.complete_task(req.task_id)

            logger.info(
                "[worker-%s] Rendered %s in %.2fs → %s",
                worker_id, req.scene_id, duration, output_path,
            )

            return RenderResponse(
                task_id=req.task_id,
                scene_id=req.scene_id,
                success=True,
                output_path=output_path,
                duration=duration,
                worker_id=worker_id,
            )

        except asyncio.TimeoutError:
            state.fail_task(req.task_id)
            logger.warning("[worker-%s] Timeout rendering %s", worker_id, req.scene_id)
            return RenderResponse(
                task_id=req.task_id,
                scene_id=req.scene_id,
                success=False,
                error=f"Timeout after {req.timeout}s",
                duration=time.perf_counter() - start,
                worker_id=worker_id,
            )

        except Exception as e:
            state.fail_task(req.task_id)
            logger.error("[worker-%s] Error rendering %s: %s", worker_id, req.scene_id, e)
            return RenderResponse(
                task_id=req.task_id,
                scene_id=req.scene_id,
                success=False,
                error=str(e),
                duration=time.perf_counter() - start,
                worker_id=worker_id,
            )

    @router.post("/cancel/{task_id}")
    async def cancel(task_id: str):
        if state.cancel_task(task_id):
            return {"cancelled": True, "task_id": task_id}
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

    return router, state
