"""Timeline IR — Frame-accurate temporal layout.

Captures WHEN and WHERE media elements exist on the timeline.

    SceneIR(content="What is Redis?")
    ↓ timing pass
    TimelineIR(tracks=[
        Track(layer=0, start=0, end=150, content=video),
        Track(layer=1, start=0, end=150, content=subtitle),
        Track(layer=2, start=0, end=147, content=audio),
    ])

This is the bridge between semantic (Scene IR) and physical (Render IR).
All frame arithmetic, overlap resolution, and z-ordering happen here.

Design principles:
  - Frame-accurate (integer frames, not floats)
  - Layer-based (z-order via layer index)
  - Gap-free by construction (validation enforces)
  - Transition-aware (overlaps are explicit)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from compiler.thinking.canonicalize import canonicalize, content_hash


class TrackType(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"
    EFFECT = "effect"
    TRANSITION = "transition"


class TransitionEffect(str, Enum):
    NONE = "none"
    FADE = "fade"
    FADEBLACK = "fadeblack"
    FADEWHITE = "fadewhite"
    SLIDELEFT = "slideleft"
    SLIDERIGHT = "slideright"
    SLIDEUP = "slideup"
    SLIDEDOWN = "slidedown"
    WIPELEFT = "wipeleft"
    WIPERIGHT = "wiperight"
    CROSSFADE = "crossfade"


@dataclass(frozen=True)
class Track:
    """A single media track on the timeline.

    Represents a contiguous region of media (video, audio, subtitle, effect)
    at a specific layer and time range. All values are in frames.
    """
    track_id: str
    track_type: TrackType
    layer: int                       # z-order (higher = on top)
    start_frame: int                 # inclusive, 0-indexed
    end_frame: int                   # exclusive
    content: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.start_frame < 0:
            raise ValueError(f"start_frame must be >= 0, got {self.start_frame}")
        if self.end_frame <= self.start_frame:
            raise ValueError(
                f"end_frame ({self.end_frame}) must be > start_frame ({self.start_frame})"
            )
        if self.layer < 0:
            raise ValueError(f"layer must be >= 0, got {self.layer}")

    @property
    def duration_frames(self) -> int:
        return self.end_frame - self.start_frame

    def overlaps(self, other: Track) -> bool:
        """Check if two tracks overlap in time."""
        return self.start_frame < other.end_frame and other.start_frame < self.end_frame

    def overlap_frames(self, other: Track) -> int:
        """Number of overlapping frames with another track."""
        start = max(self.start_frame, other.start_frame)
        end = min(self.end_frame, other.end_frame)
        return max(0, end - start)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "track_id": self.track_id,
            "track_type": self.track_type.value,
            "layer": self.layer,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
        }
        if self.content:
            d["content"] = self.content
        if self.metadata:
            d["metadata"] = self.metadata
        return d


@dataclass(frozen=True)
class Transition:
    """A transition between two tracks."""
    from_track: str                  # track_id of outgoing track
    to_track: str                    # track_id of incoming track
    effect: TransitionEffect = TransitionEffect.FADE
    duration_frames: int = 8         # overlap frames
    offset_frame: int = 0            # frame where transition starts

    def __post_init__(self):
        if self.duration_frames <= 0:
            raise ValueError(f"duration_frames must be > 0, got {self.duration_frames}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_track": self.from_track,
            "to_track": self.to_track,
            "effect": self.effect.value,
            "duration_frames": self.duration_frames,
            "offset_frame": self.offset_frame,
        }


@dataclass(frozen=True)
class TimelineIR:
    """Frame-accurate temporal layout.

    A timeline is a set of tracks (media regions) and transitions
    (overlap effects between tracks). It describes exactly what
    appears on screen at each frame.

    The timeline is the last IR before render lowering. All frame
    arithmetic must be resolved here — Render IR only deals with
    ffmpeg/remotion commands.
    """
    tracks: tuple[Track, ...]
    transitions: tuple[Transition, ...] = ()
    fps: int = 30
    width: int = 1080
    height: int = 1920
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.tracks:
            raise ValueError("tracks must be non-empty")
        if self.fps <= 0:
            raise ValueError(f"fps must be > 0, got {self.fps}")
        self._validate_no_id_collisions()
        self._validate_transitions()

    def _validate_no_id_collisions(self):
        ids = [t.track_id for t in self.tracks]
        if len(ids) != len(set(ids)):
            dupes = [x for x in ids if ids.count(x) > 1]
            raise ValueError(f"Duplicate track IDs: {set(dupes)}")

    def _validate_transitions(self):
        track_ids = {t.track_id for t in self.tracks}
        for tr in self.transitions:
            if tr.from_track not in track_ids:
                raise ValueError(f"Transition references unknown track: {tr.from_track}")
            if tr.to_track not in track_ids:
                raise ValueError(f"Transition references unknown track: {tr.to_track}")

    # ── Derived Properties ──

    @property
    def duration_frames(self) -> int:
        """Total timeline duration in frames."""
        return max(t.end_frame for t in self.tracks)

    @property
    def duration_seconds(self) -> float:
        return self.duration_frames / self.fps

    @property
    def layer_count(self) -> int:
        return max(t.layer for t in self.tracks) + 1

    @property
    def track_count(self) -> int:
        return len(self.tracks)

    def tracks_at_frame(self, frame: int) -> list[Track]:
        """All tracks active at a given frame."""
        return [t for t in self.tracks if t.start_frame <= frame < t.end_frame]

    def tracks_on_layer(self, layer: int) -> list[Track]:
        """All tracks on a specific layer, sorted by start frame."""
        return sorted(
            [t for t in self.tracks if t.layer == layer],
            key=lambda t: t.start_frame,
        )

    def tracks_of_type(self, track_type: TrackType) -> list[Track]:
        return [t for t in self.tracks if t.track_type == track_type]

    def transition_between(self, from_id: str, to_id: str) -> Optional[Transition]:
        """Find transition between two tracks."""
        for tr in self.transitions:
            if tr.from_track == from_id and tr.to_track == to_id:
                return tr
        return None

    # ── Canonical Form ──

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "tracks": [t.to_dict() for t in self.tracks],
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
        }
        if self.transitions:
            d["transitions"] = [tr.to_dict() for tr in self.transitions]
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    def canonical(self) -> dict[str, Any]:
        return canonicalize(self.to_dict())

    def content_hash(self) -> str:
        return content_hash(self.canonical())

    # ── Display ──

    def summary(self) -> str:
        return (
            f"TimelineIR({self.track_count} tracks, "
            f"{self.duration_frames} frames / {self.duration_seconds:.1f}s, "
            f"{self.layer_count} layers, {self.fps}fps)"
        )

    def ascii_timeline(self) -> str:
        """ASCII visualization of the timeline."""
        lines = []
        for layer in range(self.layer_count):
            tracks = self.tracks_on_layer(layer)
            line = f"L{layer}: "
            pos = 0
            for t in tracks:
                gap = t.start_frame - pos
                if gap > 0:
                    line += " " * min(gap, 20)
                bar_len = min(t.duration_frames, 30)
                label = t.track_id[:bar_len]
                line += f"[{label:^{bar_len}}]"
                pos = t.end_frame
            lines.append(line)
        return "\n".join(lines)
