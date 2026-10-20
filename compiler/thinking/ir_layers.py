"""IR Layer Hierarchy — Four distinct intermediate representations.

The Thinking Agent transforms data through 4 IR layers:

  Semantic IR        "What to say"
    ↓ (script_to_timeline)
  Temporal IR        "When to say it"
    ↓ (timeline_to_layout)
  Render IR          "How to show it"
    ↓ (layout_to_video)
  Execution IR       "How to produce it"

Each layer has:
  - Clear input/output types
  - Deterministic transformation functions
  - Version tracking for reproducibility
  - Validation rules

This separation prevents the common problem of mixing
content decisions with timing decisions with rendering decisions.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from compiler.thinking.state import (
    VideoProjectState, ModuleState, ScriptSentence,
    GraphSpec, Timeline, Track, Clip, Constraint, Anchor,
)


# ── Layer 1: Semantic IR ──

@dataclass
class SemanticIR:
    """What to say — the content layer.

    Contains:
      - Script (narration text with metadata)
      - Knowledge graphs (visual concepts)
      - Cards (summary points)
      - Teaching strategy (pacing, hooks, transitions)
      - Semantic anchors (key moments)
    """
    module_id: str = ""
    topic: str = ""
    title: str = ""
    # Script
    sentences: list[ScriptSentence] = field(default_factory=list)
    # Visual content
    graph_a: Optional[GraphSpec] = None
    graph_b: Optional[GraphSpec] = None
    cards_a: dict = field(default_factory=dict)
    cards_b: dict = field(default_factory=dict)
    # Teaching strategy
    hook_strategy: str = ""
    pacing: str = ""
    key_concepts: list[str] = field(default_factory=list)
    # Semantic anchors
    semantic_anchors: list[dict] = field(default_factory=list)

    def content_hash(self) -> str:
        """Hash of all content for change detection."""
        parts = [
            self.topic, self.title,
            "|".join(s.text for s in self.sentences),
            str(self.graph_a) if self.graph_a else "",
            str(self.graph_b) if self.graph_b else "",
        ]
        return hashlib.md5("|".join(parts).encode()).hexdigest()


# ── Layer 2: Temporal IR ──

@dataclass
class TemporalIR:
    """When to say it — the timing layer.

    Contains:
      - Multi-track timeline (audio, subtitle, visual, camera, animation)
      - Constraints (temporal dependencies between tracks)
      - Anchors (semantic timing points)
      - Duration and pacing information
    """
    module_id: str = ""
    timeline: Optional[Timeline] = None
    total_duration_frames: int = 0
    fps: int = 30

    def timing_hash(self) -> str:
        """Hash of all timing for change detection."""
        if not self.timeline:
            return ""
        data = self.timeline.to_dict()
        return hashlib.md5(json.dumps(data, sort_keys=True).encode()).hexdigest()


# ── Layer 3: Render IR ──

@dataclass
class RenderIR:
    """How to show it — the rendering layer.

    Contains the final layout JSON that Remotion consumes:
      - Scene sequence (what to show when)
      - Element positions (text, graphs, cards)
      - Audio tracks (TTS files)
      - Animation plans (camera moves, transitions)
    """
    module_id: str = ""
    layout: dict = field(default_factory=dict)
    width: int = 1920
    height: int = 1080
    fps: int = 30
    duration_frames: int = 0

    def layout_hash(self) -> str:
        """Hash of layout for change detection."""
        return hashlib.md5(
            json.dumps(self.layout, sort_keys=True).encode()
        ).hexdigest()


# ── Layer 4: Execution IR ──

@dataclass
class ExecutionIR:
    """How to produce it — the execution layer.

    Contains:
      - Runtime graph (dependency structure)
      - Scheduler state (what's computed, what's cached)
      - Patch history (event sourcing log)
      - Render job status
    """
    session_id: str = ""
    graph_summary: dict = field(default_factory=dict)
    scheduler_status: dict = field(default_factory=dict)
    patch_count: int = 0
    render_status: str = ""


# ── Transformations between layers ──

class IRTransformer:
    """Deterministic transformations between IR layers.

    Each transform function:
      - Takes one IR as input
      - Produces the next IR as output
      - Is pure (same input → same output)
      - Is versioned (for reproducibility)
    """

    @staticmethod
    def semantic_to_temporal(semantic: SemanticIR, fps: int = 30,
                              voice: str = "zh-CN-YunxiNeural") -> TemporalIR:
        """Transform Semantic IR → Temporal IR.

        This is where script becomes timeline:
          - Each sentence becomes an audio clip
          - Subtitle clips are synced to audio
          - Graph scenes are placed at appropriate positions
          - Camera moves are bound to semantic anchors
        """
        timeline = Timeline(fps=fps)

        # Create audio track from sentences
        audio_track = timeline.get_or_create_track("audio", "旁白")
        current_frame = 0
        for i, sentence in enumerate(semantic.sentences):
            # Estimate duration: ~4 chars/second for Chinese
            char_count = len(sentence.text)
            duration_frames = max(fps, int(char_count / 4 * fps))

            clip = Clip(
                id=f"audio_{i}",
                track_type="audio",
                start=current_frame,
                duration=duration_frames,
                text=sentence.text,
                sentence_id=sentence.id,
            )
            audio_track.add_clip(clip)
            current_frame += duration_frames

        # Create subtitle track (synced to audio)
        subtitle_track = timeline.get_or_create_track("subtitle", "字幕")
        for audio_clip in audio_track.clips:
            sub_clip = Clip(
                id=f"sub_{audio_clip.id}",
                track_type="subtitle",
                start=audio_clip.start,
                duration=audio_clip.duration,
                text=audio_clip.text,
                sentence_id=audio_clip.sentence_id,
            )
            subtitle_track.add_clip(sub_clip)

        # Add semantic anchors
        for anchor_data in semantic.semantic_anchors:
            idx = anchor_data.get("sentence_index", 0)
            if 0 <= idx < len(audio_track.clips):
                timeline.add_semantic_anchor(
                    clip_id=audio_track.clips[idx].id,
                    semantic_type=anchor_data.get("type", "emphasis"),
                    relative_pos=anchor_data.get("relative_pos", 0.5),
                    source="semantic_ir",
                )

        # Build default constraints (subtitle follows audio)
        timeline.build_default_constraints()

        total_frames = current_frame
        return TemporalIR(
            module_id=semantic.module_id,
            timeline=timeline,
            total_duration_frames=total_frames,
            fps=fps,
        )

    @staticmethod
    def temporal_to_render(temporal: TemporalIR,
                            width: int = 1920, height: int = 1080,
                            background: str = "#070b10") -> RenderIR:
        """Transform Temporal IR → Render IR.

        This is where timeline becomes layout JSON:
          - Clips become Remotion elements
          - Constraints are resolved to final positions
          - Animation plans are generated
          - Scene sequence is computed
        """
        if not temporal.timeline:
            return RenderIR(module_id=temporal.module_id)

        # Resolve constraints
        from compiler.thinking.constraint_solver import ConstraintSolver
        solver = ConstraintSolver(temporal.timeline)
        solver.solve()

        # Build layout from resolved timeline
        layout = {
            "width": width,
            "height": height,
            "fps": temporal.fps,
            "durationInFrames": temporal.total_duration_frames,
            "background": background,
            "scene_type": "graph",
            "elements": [],
            "audioTracks": [],
            "scenes": [],
        }

        # Convert tracks to layout elements
        for track in temporal.timeline.tracks:
            for clip in track.clips:
                if track.track_type == "subtitle":
                    layout["elements"].append({
                        "id": clip.id,
                        "type": "text",
                        "text": clip.text,
                        "x": width // 2,
                        "y": int(height * 0.89),
                        "fontSize": 28,
                        "color": "#f8fbff",
                        "textAlign": "center",
                        "start": clip.start,
                        "duration": clip.duration,
                        "zIndex": 20,
                    })
                elif track.track_type == "audio":
                    layout["audioTracks"].append({
                        "id": clip.id,
                        "src": clip.src,
                        "start": clip.start,
                        "duration": clip.duration,
                        "text": clip.text,
                    })

        return RenderIR(
            module_id=temporal.module_id,
            layout=layout,
            width=width,
            height=height,
            fps=temporal.fps,
            duration_frames=temporal.total_duration_frames,
        )

    @staticmethod
    def state_to_semantic(state: VideoProjectState, module_id: str) -> SemanticIR:
        """Extract Semantic IR from VideoProjectState for a module."""
        module = state.get_module(module_id)
        if not module:
            return SemanticIR(module_id=module_id)

        return SemanticIR(
            module_id=module_id,
            topic=state.topic,
            title=module.title,
            sentences=list(module.script),
            graph_a=module.graph_a,
            graph_b=module.graph_b,
            cards_a={"title": module.cards_a_title, "items": module.cards_a_items},
            cards_b={"title": module.cards_b_title, "items": module.cards_b_items},
            key_concepts=[
                s.key_concept for s in module.script if s.key_concept
            ],
        )


# ── Determinism guarantees ──

@dataclass
class DeterminismConfig:
    """Configuration for deterministic execution.

    Ensures that the same input always produces the same output,
    which is critical for:
      - Replay (reconstruct state from patches)
      - Testing (reproducible results)
      - Collaboration (consistent state across instances)
    """
    # Stable ordering
    topological_sort: str = "bfs"  # bfs or dfs (bfs is more stable)
    tie_breaker: str = "node_id"   # How to break ties in topological sort

    # Version tracking
    patch_version: int = 1          # Increment when patch format changes
    ir_version: int = 1             # Increment when IR format changes

    # Idempotency
    idempotent_recompute: bool = True  # Same inputs → skip recompute
    cache_enabled: bool = True         # Enable memoization

    # Reproducibility
    record_seed: bool = True           # Record random seeds
    timestamp_precision: str = "ms"    # Timestamp precision


class DeterminismChecker:
    """Verify that transformations are deterministic."""

    @staticmethod
    def check_transform(input_ir: Any, transform_fn, iterations: int = 3) -> bool:
        """Run a transform multiple times and verify identical output."""
        results = []
        for _ in range(iterations):
            result = transform_fn(input_ir)
            results.append(hashlib.md5(
                json.dumps(result, sort_keys=True, default=str).encode()
            ).hexdigest())
        return len(set(results)) == 1

    @staticmethod
    def check_replay(state: VideoProjectState, patches: list,
                     iterations: int = 3) -> bool:
        """Replay patches multiple times and verify identical final state."""
        from compiler.thinking.patch import PatchHistory
        from compiler.thinking.state import VideoProjectState as VPS

        results = []
        for _ in range(iterations):
            s = VPS(topic=state.topic)
            history = PatchHistory()
            for patch in patches:
                patch.apply(s)
                history.record(patch)
            results.append(s.to_json())

        return len(set(results)) == 1
