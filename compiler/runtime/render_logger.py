"""Render Logger — Structured logging for the rendering pipeline.

Provides:
  - RenderLogger with structured fields (scene_id, step, duration, cache_hit)
  - Context manager for timing operations
  - Log aggregation for debugging

Usage:
    from compiler.runtime.render_logger import RenderLogger

    logger = RenderLogger("render_pipeline")
    with logger.step("tts_synthesis", scene_id="scene_1") as ctx:
        result = tts.synthesize(text)
        ctx.cache_hit = True  # or False

    logger.summary()
    # {'steps': [...], 'total_duration': 2.3, 'cache_hits': 1, 'errors': 0}
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class StepRecord:
    """Record of a single pipeline step."""
    name: str
    scene_id: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    duration: float = 0.0
    cache_hit: bool = False
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepContext:
    """Mutable context passed into a step block."""
    cache_hit: bool = False
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class RenderLogger:
    """Structured logger for rendering pipeline operations.

    Collects step records and provides summary statistics.
    """

    def __init__(self, pipeline_name: str = "render"):
        self.pipeline_name = pipeline_name
        self.steps: list[StepRecord] = []
        self._start_time = time.monotonic()

    @contextmanager
    def step(self, name: str, scene_id: str = ""):
        """Context manager that times a pipeline step.

        Yields a StepContext for setting cache_hit, error, metadata.
        """
        ctx = StepContext()
        record = StepRecord(name=name, scene_id=scene_id, start_time=time.monotonic())
        try:
            yield ctx
        except Exception as e:
            ctx.error = str(e)
            raise
        finally:
            record.end_time = time.monotonic()
            record.duration = record.end_time - record.start_time
            record.cache_hit = ctx.cache_hit
            record.error = ctx.error
            record.metadata = ctx.metadata
            self.steps.append(record)

            log_fn = logger.warning if ctx.error else logger.info
            log_fn(
                "[%s] %s (scene=%s) %.3fs cache=%s%s",
                self.pipeline_name, name, scene_id or "-",
                record.duration, "HIT" if ctx.cache_hit else "MISS",
                f" error={ctx.error}" if ctx.error else "",
            )

    def summary(self) -> dict[str, Any]:
        """Get pipeline execution summary."""
        total = time.monotonic() - self._start_time
        cache_hits = sum(1 for s in self.steps if s.cache_hit)
        errors = sum(1 for s in self.steps if s.error)
        step_summaries = [
            {
                "name": s.name,
                "scene_id": s.scene_id,
                "duration": round(s.duration, 3),
                "cache_hit": s.cache_hit,
                "error": s.error,
            }
            for s in self.steps
        ]
        return {
            "pipeline": self.pipeline_name,
            "total_duration": round(total, 3),
            "steps": len(self.steps),
            "cache_hits": cache_hits,
            "cache_misses": len(self.steps) - cache_hits,
            "errors": errors,
            "details": step_summaries,
        }

    def log_summary(self):
        """Log the summary at INFO level."""
        s = self.summary()
        logger.info(
            "[%s] Pipeline complete: %d steps, %.3fs total, %d cache hits, %d errors",
            s["pipeline"], s["steps"], s["total_duration"], s["cache_hits"], s["errors"],
        )

    def reset(self):
        """Clear all recorded steps."""
        self.steps.clear()
        self._start_time = time.monotonic()
