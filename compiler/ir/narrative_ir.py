"""Narrative IR — Content structure and rhetorical flow.

Captures the STORY STRUCTURE independent of visual/audio implementation.

    IntentIR(topic="Redis", tone="dramatic")
    ↓ LLM planning
    NarrativeIR(beats=[
        HookBeat(text="Redis为什么这么快？", duration=5),
        ProblemBeat(text="传统数据库的瓶颈...", duration=8),
        RevealBeat(text="秘密在于内存+单线程", duration=12),
        CTABeat(text="关注学习更多", duration=3),
    ])
    ↓ scene decomposition
    SceneIR(...)

Design principles:
  - Beats are semantic units (not scenes, not frames)
  - Pacing is relative (ratios, not absolute frames)
  - Text is primary content (visual is downstream)
  - Transitions are rhetorical (not visual effects)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from compiler.thinking.canonicalize import canonicalize, content_hash


class BeatType(str, Enum):
    HOOK = "hook"              # Attention grabber
    PROBLEM = "problem"        # Pain point / tension
    EXPLANATION = "explanation" # Educational content
    REVEAL = "reveal"          # Key insight / twist
    EXAMPLE = "example"        # Concrete illustration
    COMPARISON = "comparison"  # Before/after, A vs B
    CTA = "cta"               # Call to action
    SUMMARY = "summary"       # Recap
    TRANSITION = "transition"  # Rhetorical bridge


class TransitionType(str, Enum):
    """Rhetorical transition types (not visual effects)."""
    CUT = "cut"               # Abrupt topic change
    BUILD = "build"           # Progressive buildup
    CONTRAST = "contrast"     # Juxtaposition
    CALLBACK = "callback"     # Reference to earlier beat
    QUESTION = "question"     # Rhetorical question bridge
    SILENCE = "silence"       # Dramatic pause


@dataclass(frozen=True)
class Beat:
    """A single narrative beat — the atomic unit of story structure.

    A beat is a semantic unit: it has content (text), a role (type),
    and pacing hints (relative_duration). It does NOT specify how
    to render the content — that's Scene IR's job.
    """
    beat_type: BeatType
    text: str                               # Primary content text
    relative_duration: float = 1.0          # Relative weight (normalized to sum=1)
    key_point: str = ""                     # One-line summary for LLM
    emotional_intensity: float = 0.5        # 0=calm, 1=intense
    transition_after: TransitionType = TransitionType.CUT
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.text or not self.text.strip():
            raise ValueError("beat text must be non-empty")
        if not 0.0 <= self.emotional_intensity <= 1.0:
            raise ValueError(
                f"emotional_intensity must be in [0, 1], got {self.emotional_intensity}"
            )
        if self.relative_duration <= 0:
            raise ValueError(
                f"relative_duration must be > 0, got {self.relative_duration}"
            )

    def to_dict(self) -> dict[str, Any]:
        d = {
            "beat_type": self.beat_type.value,
            "text": self.text.strip(),
            "relative_duration": self.relative_duration,
            "emotional_intensity": self.emotional_intensity,
            "transition_after": self.transition_after.value,
        }
        if self.key_point:
            d["key_point"] = self.key_point
        if self.metadata:
            d["metadata"] = self.metadata
        return d


# ── Convenience constructors ──

def HookBeat(
    text: str,
    duration: float = 1.0,
    intensity: float = 0.8,
    **kw: Any,
) -> Beat:
    return Beat(
        beat_type=BeatType.HOOK,
        text=text,
        relative_duration=duration,
        emotional_intensity=intensity,
        **kw,
    )

def ProblemBeat(
    text: str,
    duration: float = 1.0,
    intensity: float = 0.6,
    **kw: Any,
) -> Beat:
    return Beat(
        beat_type=BeatType.PROBLEM,
        text=text,
        relative_duration=duration,
        emotional_intensity=intensity,
        **kw,
    )

def RevealBeat(
    text: str,
    duration: float = 1.0,
    intensity: float = 0.9,
    **kw: Any,
) -> Beat:
    return Beat(
        beat_type=BeatType.REVEAL,
        text=text,
        relative_duration=duration,
        emotional_intensity=intensity,
        transition_after=TransitionType.BUILD,
        **kw,
    )

def CTABeat(
    text: str,
    duration: float = 0.5,
    intensity: float = 0.4,
    **kw: Any,
) -> Beat:
    return Beat(
        beat_type=BeatType.CTA,
        text=text,
        relative_duration=duration,
        emotional_intensity=intensity,
        **kw,
    )


@dataclass(frozen=True)
class NarrativeIR:
    """Content structure — the story skeleton.

    A NarrativeIR is an ordered sequence of beats with pacing metadata.
    It captures WHAT to say and in WHAT ORDER, but not HOW to render it.

    The beats list is the primary content. Other fields are metadata
    for downstream IR passes.
    """
    beats: tuple[Beat, ...]
    title: str = ""                         # Video title
    subtitle: str = ""                      # Subtitle / tagline
    pacing: str = "normal"                  # slow | normal | fast | dynamic
    emotional_arc: str = "buildup"          # flat | buildup | wave | surprise
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.beats:
            raise ValueError("beats must be non-empty")
        self._validate_beat_sequence()

    def _validate_beat_sequence(self):
        """Validate beat sequence has reasonable structure."""
        types = [b.beat_type for b in self.beats]
        # Must start with hook (unless single-beat narrative)
        if len(types) > 1 and types[0] != BeatType.HOOK:
            raise ValueError(
                f"First beat must be HOOK, got {types[0].value}"
            )
        # Must not end with transition
        if types[-1] == BeatType.TRANSITION:
            raise ValueError("Last beat cannot be TRANSITION")
        # No consecutive transitions
        for i in range(len(types) - 1):
            if types[i] == BeatType.TRANSITION and types[i + 1] == BeatType.TRANSITION:
                raise ValueError(f"Consecutive TRANSITION beats at index {i}")

    # ── Derived Properties ──

    @property
    def total_relative_duration(self) -> float:
        return sum(b.relative_duration for b in self.beats)

    @property
    def normalized_durations(self) -> list[float]:
        """Beat durations normalized to sum=1."""
        total = self.total_relative_duration
        if total == 0:
            return [1.0 / len(self.beats)] * len(self.beats)
        return [b.relative_duration / total for b in self.beats]

    @property
    def beat_count(self) -> int:
        return len(self.beats)

    @property
    def beat_types(self) -> list[BeatType]:
        return [b.beat_type for b in self.beats]

    def beats_of_type(self, beat_type: BeatType) -> list[Beat]:
        return [b for b in self.beats if b.beat_type == beat_type]

    def absolute_durations(self, total_seconds: float) -> list[float]:
        """Compute absolute durations (seconds) for each beat."""
        norms = self.normalized_durations
        return [n * total_seconds for n in norms]

    # ── Canonical Form ──

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "beats": [b.to_dict() for b in self.beats],
            "pacing": self.pacing,
            "emotional_arc": self.emotional_arc,
        }
        if self.title:
            d["title"] = self.title
        if self.subtitle:
            d["subtitle"] = self.subtitle
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    def canonical(self) -> dict[str, Any]:
        """Canonical form for hashing."""
        return canonicalize(self.to_dict())

    def content_hash(self) -> str:
        """Content hash for downstream cache key."""
        return content_hash(self.canonical())

    # ── Display ──

    def summary(self) -> str:
        types = " → ".join(b.value for b in self.beat_types)
        return f"NarrativeIR({self.beat_count} beats: {types})"

    def outline(self) -> str:
        """Human-readable outline."""
        lines = []
        for i, b in enumerate(self.beats):
            intensity_bar = "█" * int(b.emotional_intensity * 5)
            lines.append(
                f"  {i+1}. [{b.beat_type.value:12s}] "
                f"{intensity_bar:5s} "
                f"{b.text[:60]}"
            )
        return "\n".join(lines)
