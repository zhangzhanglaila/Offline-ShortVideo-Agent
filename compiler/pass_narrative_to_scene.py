"""Narrative → Scene Compiler Pass.

Transforms a NarrativeIR (beats) into a list of SceneIRs (visual plans).

    NarrativeIR(beats=[Hook, Problem, Reveal, CTA])
    ↓
    [
        SceneIR(scene_type="hook", text="...", motion="zoom_in"),
        SceneIR(scene_type="graph", text="...", motion="push_in"),
        SceneIR(scene_type="reveal", text="...", motion="fade_in"),
        SceneIR(scene_type="cta", text="...", motion="static"),
    ]

Each beat maps to one or more scenes. The mapping depends on:
  - Beat type → visual grammar (hook = big text + zoom, etc.)
  - Text length → auto-split into multiple scenes if too long
  - Emotional intensity → camera motion and effects
  - Pacing → duration allocation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from compiler.ir.intent_ir import IntentIR
from compiler.ir.narrative_ir import NarrativeIR, Beat, BeatType, TransitionType
from compiler.thinking.canonicalize import canonicalize, content_hash
from compiler.base import CompilerPass


# ── Visual Grammar Mapping ──

@dataclass(frozen=True)
class SceneStyle:
    """Visual style hints for a scene type."""
    scene_type: str                     # hook | graph | reveal | cta | ...
    camera_motion: str = "static"       # static | zoom_in | push_in | pan_left | ...
    background: str = "default"         # dark | light | gradient | image
    text_style: str = "normal"          # normal | big | highlight | subtitle
    emphasis: float = 0.5               # 0=subtle, 1=maximum
    max_chars_per_scene: int = 60       # Auto-split threshold

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_type": self.scene_type,
            "camera_motion": self.camera_motion,
            "background": self.background,
            "text_style": self.text_style,
            "emphasis": self.emphasis,
        }


# Beat type → default visual grammar
_BEAT_STYLES: dict[BeatType, SceneStyle] = {
    BeatType.HOOK: SceneStyle(
        scene_type="hook",
        camera_motion="zoom_in",
        background="dark",
        text_style="big",
        emphasis=0.9,
    ),
    BeatType.PROBLEM: SceneStyle(
        scene_type="graph",
        camera_motion="push_in",
        background="dark",
        text_style="normal",
        emphasis=0.6,
    ),
    BeatType.EXPLANATION: SceneStyle(
        scene_type="graph",
        camera_motion="static",
        background="light",
        text_style="normal",
        emphasis=0.5,
    ),
    BeatType.REVEAL: SceneStyle(
        scene_type="reveal",
        camera_motion="fade_in",
        background="gradient",
        text_style="highlight",
        emphasis=0.9,
    ),
    BeatType.EXAMPLE: SceneStyle(
        scene_type="graph",
        camera_motion="pan_left",
        background="light",
        text_style="normal",
        emphasis=0.5,
    ),
    BeatType.COMPARISON: SceneStyle(
        scene_type="graph",
        camera_motion="static",
        background="light",
        text_style="normal",
        emphasis=0.6,
    ),
    BeatType.CTA: SceneStyle(
        scene_type="cta",
        camera_motion="static",
        background="dark",
        text_style="big",
        emphasis=0.4,
    ),
    BeatType.SUMMARY: SceneStyle(
        scene_type="graph",
        camera_motion="static",
        background="light",
        text_style="subtitle",
        emphasis=0.3,
    ),
    BeatType.TRANSITION: SceneStyle(
        scene_type="transition",
        camera_motion="fade_in",
        background="dark",
        text_style="subtitle",
        emphasis=0.2,
    ),
}


@dataclass(frozen=True)
class SceneIR:
    """Single scene intermediate representation.

    Derived from one beat (or part of a beat if text was split).
    Contains visual plan, audio plan, and timing constraints.
    All coordinates are local to this scene.
    """
    scene_id: str
    scene_type: str                     # hook | graph | reveal | cta | transition
    text: str                           # Primary display text
    duration_in_frames: int             # Local duration
    camera_motion: str = "static"
    background: str = "default"
    text_style: str = "normal"
    emphasis: float = 0.5
    emotional_intensity: float = 0.5
    transition_after: str = "cut"
    elements: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "scene_id": self.scene_id,
            "scene_type": self.scene_type,
            "text": self.text,
            "duration_in_frames": self.duration_in_frames,
            "camera_motion": self.camera_motion,
            "background": self.background,
            "text_style": self.text_style,
            "emphasis": self.emphasis,
            "emotional_intensity": self.emotional_intensity,
            "transition_after": self.transition_after,
        }
        if self.elements:
            d["elements"] = self.elements
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    def canonical(self) -> dict[str, Any]:
        return canonicalize(self.to_dict())

    def content_hash(self) -> str:
        return content_hash(self.canonical())


def _split_text(text: str, max_chars: int) -> list[str]:
    """Split long text into chunks at sentence boundaries."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break

        # Find best split point
        split_at = max_chars
        for sep in ["。", "！", "？", ".", "!", "?", "，", ",", "；"]:
            idx = remaining[:max_chars].rfind(sep)
            if idx > max_chars // 3:
                split_at = idx + 1
                break

        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    return chunks if chunks else [text]


class NarrativeToScenePass(CompilerPass[NarrativeIR, list[SceneIR]]):
    """Transform NarrativeIR → list of SceneIRs.

    Each beat produces one or more scenes:
      - Short beats → 1 scene
      - Long text → auto-split into multiple scenes
      - Beat type → visual grammar (camera, background, text style)

    The output is a flat list of scenes with local durations.
    Absolute timing is resolved in the Scene → Timeline pass.
    """

    name = "narrative_to_scene"

    def __init__(
        self,
        fps: int = 30,
        base_duration_per_char: float = 0.15,  # seconds per character
        min_scene_duration: float = 2.0,        # minimum scene duration in seconds
        max_chars_per_scene: int = 60,
    ):
        self.fps = fps
        self.base_duration_per_char = base_duration_per_char
        self.min_scene_duration = min_scene_duration
        self.max_chars_per_scene = max_chars_per_scene

    def transform(self, narrative: NarrativeIR) -> list[SceneIR]:
        scenes = []
        beat_durations = narrative.absolute_durations(
            total_seconds=narrative.total_relative_duration * 8,  # ~8s per beat unit
        )

        for i, (beat, beat_duration) in enumerate(zip(narrative.beats, beat_durations)):
            style = _BEAT_STYLES.get(beat.beat_type, _BEAT_STYLES[BeatType.EXPLANATION])

            # Split long text
            text_chunks = _split_text(beat.text, style.max_chars_per_scene)

            for j, chunk in enumerate(text_chunks):
                scene_id = f"scene_{i}_{j}" if len(text_chunks) > 1 else f"scene_{i}"

                # Duration: proportional to text length, with minimum
                char_duration = len(chunk) * self.base_duration_per_char
                chunk_duration = max(self.min_scene_duration, char_duration)

                # If beat was split, distribute duration proportionally
                if len(text_chunks) > 1:
                    total_chars = sum(len(c) for c in text_chunks)
                    chunk_duration = max(
                        self.min_scene_duration,
                        beat_duration * len(chunk) / total_chars,
                    )

                duration_frames = max(1, int(chunk_duration * self.fps))

                scene = SceneIR(
                    scene_id=scene_id,
                    scene_type=style.scene_type,
                    text=chunk,
                    duration_in_frames=duration_frames,
                    camera_motion=style.camera_motion,
                    background=style.background,
                    text_style=style.text_style,
                    emphasis=style.emphasis,
                    emotional_intensity=beat.emotional_intensity,
                    transition_after=beat.transition_after.value,
                    metadata={
                        "beat_index": i,
                        "beat_type": beat.beat_type.value,
                        "chunk_index": j,
                        "total_chunks": len(text_chunks),
                    },
                )
                scenes.append(scene)

        return scenes
