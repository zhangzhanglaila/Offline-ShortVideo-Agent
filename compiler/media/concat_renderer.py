"""Concat Renderer — Compose final video from rendered scene mp4s.

Takes ordered scene video paths and composes them into a single output
video. Supports:
  - Simple concat (lossless, no transitions)
  - Xfade concat (crossfade transitions at scene boundaries)

The concat itself is cached: if the scene hash sequence hasn't changed,
the final output is reused.

Usage:
    from compiler.media.concat_renderer import ConcatRenderer
    concat = ConcatRenderer()
    final_path = concat.compose(
        scene_paths=[Path("hook.mp4"), Path("graph.mp4"), Path("cards.mp4")],
        scene_hashes=["abc", "def", "ghi"],
        overlaps=[8, 8],
        output_path=Path("final.mp4"),
    )
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.utils.ffmpeg_runner import get_video_duration, run_ffmpeg
from compiler.thinking.canonicalize import derived_hash


@dataclass
class ComposeResult:
    """Result of composing scenes into final video."""
    output_path: Path
    scene_hashes: list[str]
    composite_hash: str
    cached: bool
    duration: float = 0.0
    error: Optional[str] = None


def _composite_hash(
    scene_hashes: list[str],
    overlaps: list[int],
    fps: int = 30,
    transition: str = "fade",
) -> str:
    """Hash of the composition spec (scene sequence + transitions + config).

    This is the cache key for the final output.
    If any scene hash changes, or overlap/config values change, the
    composite hash changes → re-compose.
    """
    content = {"scenes": scene_hashes, "overlaps": overlaps}
    return derived_hash(content, fps=fps, transition=transition)


class ConcatRenderer:
    """Compose final video from scene mp4s with caching.

    The concat result is cached by composite hash. If the scene sequence
    and overlap values haven't changed, the final output is reused.
    """

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or Path("output") / ".concat_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.cache_dir / "manifest.json"
        self.manifest: dict[str, dict] = self._load_manifest()

    def _load_manifest(self) -> dict[str, dict]:
        if self.manifest_path.exists():
            try:
                return json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_manifest(self):
        self.manifest_path.write_text(
            json.dumps(self.manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def compose(
        self,
        scene_paths: list[Path],
        scene_hashes: list[str],
        overlaps: list[int],
        output_path: Path,
        fps: int = 30,
        transition: str = "fade",
    ) -> ComposeResult:
        """Compose scenes into final video with caching.

        Args:
            scene_paths: Ordered list of scene mp4 paths.
            scene_hashes: Content hash of each scene (for cache key).
            overlaps: overlap[i] = xfade frames between scene[i] and scene[i+1].
            output_path: Where to write the final mp4.
            fps: Frames per second.
            transition: xfade transition type.

        Returns:
            ComposeResult with output path and cache status.
        """
        c_hash = _composite_hash(scene_hashes, overlaps, fps=fps, transition=transition)

        # Cache probe
        cached = self._lookup(c_hash)
        if cached and cached.exists():
            shutil.copy2(cached, output_path)
            return ComposeResult(
                output_path=output_path,
                scene_hashes=scene_hashes,
                composite_hash=c_hash,
                cached=True,
            )

        # Single scene — just copy
        if len(scene_paths) == 1:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(scene_paths[0], output_path)
            self._store(c_hash, output_path)
            return ComposeResult(
                output_path=output_path,
                scene_hashes=scene_hashes,
                composite_hash=c_hash,
                cached=False,
            )

        # Multi-scene — ffmpeg xfade
        start = time.time()
        error = self._compose_xfade(
            scene_paths, overlaps, output_path, fps, transition,
        )
        duration = time.time() - start

        if error:
            return ComposeResult(
                output_path=output_path,
                scene_hashes=scene_hashes,
                composite_hash=c_hash,
                cached=False,
                duration=duration,
                error=error,
            )

        self._store(c_hash, output_path)
        return ComposeResult(
            output_path=output_path,
            scene_hashes=scene_hashes,
            composite_hash=c_hash,
            cached=False,
            duration=duration,
        )

    def _compose_xfade(
        self,
        scene_paths: list[Path],
        overlaps: list[int],
        output_path: Path,
        fps: int,
        transition: str,
    ) -> Optional[str]:
        """ffmpeg xfade composition. Returns error string or None."""
        scene_durations = [get_video_duration(p) for p in scene_paths]
        if any(d <= 0 for d in scene_durations):
            return "Could not determine duration of one or more scene videos"

        # Compute xfade offsets
        offsets = []
        accumulated = 0.0
        for i in range(len(overlaps)):
            overlap_sec = overlaps[i] / fps
            offset = accumulated + scene_durations[i] - overlap_sec
            offsets.append(max(0, offset))
            accumulated += scene_durations[i] - overlap_sec

        # Build filter graph
        inputs = []
        for v in scene_paths:
            inputs.extend(["-i", str(v)])

        filter_parts = []
        audio_parts = []
        prev_video = "[0:v]"
        prev_audio = "[0:a]"

        for i in range(1, len(scene_paths)):
            overlap_sec = overlaps[i - 1] / fps
            offset = offsets[i - 1]
            is_last = i == len(scene_paths) - 1
            v_label = "[vout]" if is_last else f"[v{i}]"
            a_label = "[aout]" if is_last else f"[a{i}]"

            filter_parts.append(
                f"{prev_video}[{i}:v]xfade=transition={transition}:"
                f"duration={overlap_sec:.3f}:offset={offset:.3f}{v_label}"
            )
            audio_parts.append(
                f"{prev_audio}[{i}:a]acrossfade=d={overlap_sec:.3f}{a_label}"
            )
            prev_video = v_label
            prev_audio = a_label

        filter_graph = ";".join(filter_parts + audio_parts)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            run_ffmpeg([
                "ffmpeg", "-y",
                *inputs,
                "-filter_complex", filter_graph,
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-c:a", "aac", "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                str(output_path),
            ])
        except RuntimeError as exc:
            return f"ffmpeg compose failed: {str(exc)[-500:]}"
        return None

    def _lookup(self, composite_hash: str) -> Optional[Path]:
        entry = self.manifest.get(composite_hash)
        if not entry:
            return None
        path = Path(entry["path"])
        if path.exists() and path.stat().st_size > 0:
            return path
        del self.manifest[composite_hash]
        self._save_manifest()
        return None

    def _store(self, composite_hash: str, video_path: Path):
        dest = self.cache_dir / f"{composite_hash}.mp4"
        if video_path.resolve() != dest.resolve():
            shutil.copy2(video_path, dest)
        self.manifest[composite_hash] = {
            "path": str(dest),
            "size": dest.stat().st_size,
            "created_at": time.time(),
        }
        self._save_manifest()

    def stats(self) -> dict:
        total_size = sum(e.get("size", 0) for e in self.manifest.values())
        return {
            "entries": len(self.manifest),
            "total_size_mb": round(total_size / (1024 * 1024), 2),
        }
