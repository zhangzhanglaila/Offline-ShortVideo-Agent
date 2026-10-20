"""Scene → Timeline Compiler Pass.

Transforms a list of SceneIRs into a frame-accurate TimelineIR.

    [SceneIR, SceneIR, SceneIR]
    ↓
    TimelineIR(tracks=[
        Track(layer=0, start=0, end=150, type=VIDEO),    # scene 0
        Track(layer=0, start=142, end=350, type=VIDEO),  # scene 1 (8f overlap)
        Track(layer=1, start=0, end=350, type=SUBTITLE),
        Track(layer=2, start=0, end=347, type=AUDIO),
    ])

Responsibilities:
  - Absolute frame allocation (scene durations → frame ranges)
  - Track packing (video on layer 0, subtitle on layer 1, audio on layer 2)
  - Overlap computation (transition frames between scenes)
  - Subtitle timing (word-level alignment)
  - Audio sync (track timing relative to video)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from compiler.ir.timeline_ir import (
    TimelineIR, Track, TrackType, Transition, TransitionEffect,
)
from compiler.thinking.canonicalize import canonicalize, content_hash
from compiler.base import CompilerPass

# Import SceneIR from narrative_to_scene pass
from compiler.pass_narrative_to_scene import SceneIR


# Transition type mapping
_TRANSITION_MAP: dict[str, tuple[TransitionEffect, int]] = {
    "cut": (TransitionEffect.NONE, 0),
    "fade": (TransitionEffect.FADE, 8),
    "build": (TransitionEffect.FADE, 12),
    "contrast": (TransitionEffect.FADEBLACK, 8),
    "callback": (TransitionEffect.FADE, 8),
    "question": (TransitionEffect.FADE, 10),
    "silence": (TransitionEffect.NONE, 0),
}


@dataclass(frozen=True)
class SubtitleSegment:
    """A subtitle segment with frame-accurate timing."""
    text: str
    start_frame: int
    end_frame: int
    word_timings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "text": self.text,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
        }
        if self.word_timings:
            d["word_timings"] = self.word_timings
        return d


class SceneToTimelinePass(CompilerPass[list[SceneIR], TimelineIR]):
    """Transform list of SceneIRs → TimelineIR.

    Allocates frames, computes overlaps, packs tracks.
    """

    name = "scene_to_timeline"

    def __init__(
        self,
        fps: int = 30,
        width: int = 1080,
        height: int = 1920,
        default_overlap: int = 8,
        tts_data: Optional[dict[str, Any]] = None,
    ):
        self.fps = fps
        self.width = width
        self.height = height
        self.default_overlap = default_overlap
        self.tts_data = tts_data
        # Build sentence_id → segment lookup for fast access
        self._tts_segments: dict[str, dict] = {}
        if tts_data:
            for seg in tts_data.get("segments", []):
                sid = seg.get("sentence_id", "")
                if sid:
                    self._tts_segments[sid] = seg

    def transform(self, scenes: list[SceneIR]) -> TimelineIR:
        if not scenes:
            return TimelineIR(
                tracks=(
                    Track(
                        track_id="empty", track_type=TrackType.VIDEO,
                        layer=0, start_frame=0, end_frame=1,
                    ),
                ),
                fps=self.fps,
                width=self.width,
                height=self.height,
            )

        video_tracks = []
        subtitle_tracks = []
        audio_tracks = []
        transitions = []

        current_frame = 0

        for i, scene in enumerate(scenes):
            is_last = i == len(scenes) - 1
            scene_start = current_frame
            scene_end = scene_start + scene.duration_in_frames

            # Video track
            video_tracks.append(Track(
                track_id=f"video_{scene.scene_id}",
                track_type=TrackType.VIDEO,
                layer=0,
                start_frame=scene_start,
                end_frame=scene_end,
                content={
                    "scene_id": scene.scene_id,
                    "scene_type": scene.scene_type,
                    "text": scene.text,
                    "camera_motion": scene.camera_motion,
                    "background": scene.background,
                    "text_style": scene.text_style,
                },
                metadata=scene.metadata,
            ))

            # Subtitle track (same range as video, with word timings from TTS)
            sub_content: dict[str, Any] = {"text": scene.text}
            tts_seg = self._tts_segments.get(scene.scene_id)
            if tts_seg and tts_seg.get("word_timings"):
                # Convert word timings from seconds to frames (relative to scene start)
                word_timings_frames = []
                for wt in tts_seg["word_timings"]:
                    word_timings_frames.append({
                        "word": wt["word"],
                        "start_frame": scene_start + round(wt["start"] * self.fps),
                        "end_frame": scene_start + round(wt["end"] * self.fps),
                        "start": wt["start"],
                        "end": wt["end"],
                    })
                sub_content["word_timings"] = word_timings_frames
                sub_content["audio_path"] = tts_seg.get("audio_path", "")

            subtitle_tracks.append(Track(
                track_id=f"sub_{scene.scene_id}",
                track_type=TrackType.SUBTITLE,
                layer=1,
                start_frame=scene_start,
                end_frame=scene_end,
                content=sub_content,
            ))

            # Audio track (same range as video)
            audio_tracks.append(Track(
                track_id=f"audio_{scene.scene_id}",
                track_type=TrackType.AUDIO,
                layer=2,
                start_frame=scene_start,
                end_frame=scene_end,
                content={"scene_id": scene.scene_id},
            ))

            # Transition to next scene
            if not is_last:
                trans_type = scene.transition_after
                effect, overlap = _TRANSITION_MAP.get(
                    trans_type, (TransitionEffect.FADE, self.default_overlap),
                )
                overlap = min(overlap, scene.duration_in_frames // 2)

                if overlap > 0:
                    next_scene = scenes[i + 1]
                    transitions.append(Transition(
                        from_track=f"video_{scene.scene_id}",
                        to_track=f"video_{next_scene.scene_id}",
                        effect=effect,
                        duration_frames=overlap,
                        offset_frame=scene_end - overlap,
                    ))
                    # Advance frame by overlap
                    current_frame = scene_end - overlap
                else:
                    current_frame = scene_end
            else:
                current_frame = scene_end

        all_tracks = tuple(video_tracks + subtitle_tracks + audio_tracks)

        return TimelineIR(
            tracks=all_tracks,
            transitions=tuple(transitions),
            fps=self.fps,
            width=self.width,
            height=self.height,
            metadata={"scene_count": len(scenes)},
        )
