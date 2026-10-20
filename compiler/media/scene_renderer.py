"""Scene Renderer — Incremental per-scene rendering with CAS integration.

Orchestrates the render loop for individual scenes:
  1. Hash scene IR content
  2. Check media cache → hit: reuse, miss: render
  3. Store rendered mp4 in cache
  4. Return path to rendered scene video

This is the core of incremental rendering: only scenes whose content
hash changes are actually re-rendered.

Usage:
    renderer = SceneRenderer()
    result = renderer.render_scene(scene_ir)
    # result.cached = True/False
    # result.video_path = Path to mp4
    # result.content_hash = hash for downstream use
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from compiler.media.media_cache import MediaCache
from compiler.thinking.canonicalize import content_hash, derived_hash


@dataclass
class SceneRenderResult:
    """Result of rendering a single scene."""
    scene_id: str
    content_hash: str
    video_path: Path
    cached: bool
    render_duration: float = 0.0
    error: Optional[str] = None


@dataclass
class IncrementalRenderStats:
    """Stats from a batch incremental render."""
    total_scenes: int = 0
    cache_hits: int = 0
    renders: int = 0
    errors: int = 0
    total_duration: float = 0.0
    render_durations: dict[str, float] = field(default_factory=dict)

    @property
    def cache_hit_rate(self) -> float:
        return self.cache_hits / self.total_scenes if self.total_scenes > 0 else 0.0


class SceneRenderer:
    """Incremental scene renderer with content-addressable caching.

    The render function is pluggable — set `render_fn` to your actual
    renderer (Remotion, ffmpeg, etc.). The cache layer is domain-agnostic.

    render_config: dict of external factors that affect rendered output
    (ffmpeg_version, font, fps, width, height, transition, etc.).
    These are included in the cache key via derived_hash.
    """

    def __init__(
        self,
        cache: MediaCache | None = None,
        render_fn: Callable[[dict, Path], Path] | None = None,
        render_config: dict | None = None,
    ):
        self.cache = cache or MediaCache()
        self.render_fn = render_fn or self._default_render_fn
        self.render_config = render_config or {}

    def render_scene(self, scene_ir: dict, output_dir: Path | None = None) -> SceneRenderResult:
        """Render a single scene with cache-aware incremental behavior.

        Args:
            scene_ir: Scene IR dict (must include 'content' key for hashing).
            output_dir: Directory for temporary render output.

        Returns:
            SceneRenderResult with video_path and cache status.
        """
        content = scene_ir.get("content", scene_ir)
        scene_id = content.get("scene_id", "unknown")
        # Use derived_hash when render config is present (includes external factors)
        h = derived_hash(content, **self.render_config) if self.render_config else content_hash(content)

        # Cache probe
        cached_path = self.cache.lookup(content)
        if cached_path:
            return SceneRenderResult(
                scene_id=scene_id,
                content_hash=h,
                video_path=cached_path,
                cached=True,
            )

        # Cache miss — render
        if output_dir is None:
            output_dir = Path("output") / ".render_tmp"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{h}.mp4"

        start = time.time()
        try:
            video_path = self.render_fn(content, output_path)
            render_duration = time.time() - start

            # Store in cache
            cached_path = self.cache.store(content, video_path, scene_id=scene_id)

            return SceneRenderResult(
                scene_id=scene_id,
                content_hash=h,
                video_path=cached_path,
                cached=False,
                render_duration=render_duration,
            )
        except Exception as e:
            return SceneRenderResult(
                scene_id=scene_id,
                content_hash=h,
                video_path=output_path,
                cached=False,
                render_duration=time.time() - start,
                error=str(e),
            )

    def render_scenes(
        self,
        scene_irs: list[dict],
        output_dir: Path | None = None,
    ) -> tuple[list[SceneRenderResult], IncrementalRenderStats]:
        """Render multiple scenes incrementally.

        Only scenes whose content hash changed are actually rendered.
        Returns ordered results (same order as input) and stats.
        """
        stats = IncrementalRenderStats(total_scenes=len(scene_irs))
        results = []
        overall_start = time.time()

        for ir in scene_irs:
            result = self.render_scene(ir, output_dir)
            results.append(result)

            if result.cached:
                stats.cache_hits += 1
            elif result.error:
                stats.errors += 1
            else:
                stats.renders += 1

            stats.render_durations[result.scene_id] = result.render_duration

        stats.total_duration = time.time() - overall_start
        return results, stats

    @staticmethod
    def _default_render_fn(content: dict, output_path: Path) -> Path:
        """Default render function — writes IR as JSON (placeholder).

        In production, this would call Remotion or ffmpeg.
        Override via constructor: SceneRenderer(render_fn=my_fn).
        """
        output_path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
        return output_path
