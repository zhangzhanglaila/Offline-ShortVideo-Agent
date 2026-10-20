"""Intent IR — User intent abstraction.

Captures WHAT the user wants, not HOW to render it.

    IntentIR(topic="Redis", tone="dramatic", audience="beginners")
    ↓ compiler pass
    NarrativeIR(beats=[Hook, Problem, Reveal, CTA])

Design principles:
  - Only semantic information (no render details)
  - Deterministic canonical form
  - Content-hashable (cache key for downstream)
  - Validated (structural constraints)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from compiler.thinking.canonicalize import canonicalize, content_hash


class Tone(str, Enum):
    DRAMATIC = "dramatic"
    EDUCATIONAL = "educational"
    CASUAL = "casual"
    INSPIRATIONAL = "inspirational"
    HUMOROUS = "humorous"
    PROFESSIONAL = "professional"


class Platform(str, Enum):
    DOUYIN = "douyin"
    BILIBILI = "bilibili"
    YOUTUBE = "youtube"
    XIAOHONGSHU = "xiaohongshu"
    GENERIC = "generic"


class AspectRatio(str, Enum):
    PORTRAIT_9_16 = "9:16"    # Douyin, Reels, Shorts
    LANDSCAPE_16_9 = "16:9"   # YouTube, Bilibili
    SQUARE_1_1 = "1:1"        # Instagram
    PORTRAIT_3_4 = "3:4"      # Xiaohongshu


# Platform defaults
_PLATFORM_DEFAULTS: dict[str, dict[str, Any]] = {
    "douyin": {"aspect_ratio": "9:16", "max_duration": 60, "fps": 30},
    "bilibili": {"aspect_ratio": "16:9", "max_duration": 300, "fps": 30},
    "youtube": {"aspect_ratio": "16:9", "max_duration": 600, "fps": 30},
    "xiaohongshu": {"aspect_ratio": "3:4", "max_duration": 120, "fps": 30},
    "generic": {"aspect_ratio": "9:16", "max_duration": 120, "fps": 30},
}


@dataclass(frozen=True)
class IntentIR:
    """User intent — what to create, not how to render it.

    This is the root of the IR pipeline. All downstream IRs
    derive from an IntentIR. Changing the intent invalidates
    the entire pipeline.

    Fields are semantic — no render hints, no pixel values,
    no ffmpeg parameters.
    """
    topic: str                              # e.g., "Redis", "量子计算"
    tone: Tone                              # emotional register
    target_duration: float                  # seconds (semantic target, not exact)
    audience: str                           # e.g., "beginners", "experts", "general"
    platform: Platform = Platform.DOUYIN    # target platform
    aspect_ratio: AspectRatio = AspectRatio.PORTRAIT_9_16
    language: str = "zh-CN"                 # BCP 47
    style: str = "modern"                   # visual style descriptor
    max_scenes: int = 8                     # upper bound on scene count
    keywords: tuple[str, ...] = ()          # topic keywords for retrieval
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self._validate()

    def _validate(self):
        if not self.topic or not self.topic.strip():
            raise ValueError("topic must be non-empty")
        if self.target_duration <= 0:
            raise ValueError(f"target_duration must be > 0, got {self.target_duration}")
        if self.target_duration > 3600:
            raise ValueError(f"target_duration must be <= 3600s, got {self.target_duration}")
        if not self.audience or not self.audience.strip():
            raise ValueError("audience must be non-empty")
        if self.max_scenes < 1:
            raise ValueError(f"max_scenes must be >= 1, got {self.max_scenes}")
        if self.max_scenes > 50:
            raise ValueError(f"max_scenes must be <= 50, got {self.max_scenes}")

    # ── Canonical Form ──

    def to_dict(self) -> dict[str, Any]:
        """Deterministic dict representation (canonicalization-ready)."""
        d = {
            "topic": self.topic.strip(),
            "tone": self.tone.value,
            "target_duration": self.target_duration,
            "audience": self.audience.strip(),
            "platform": self.platform.value,
            "aspect_ratio": self.aspect_ratio.value,
            "language": self.language,
            "style": self.style,
            "max_scenes": self.max_scenes,
            "keywords": list(self.keywords),
        }
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    def canonical(self) -> dict[str, Any]:
        """Canonical form — sorted keys, None-stripped, float-normalized."""
        return canonicalize(self.to_dict())

    def content_hash(self) -> str:
        """Content hash for downstream cache key."""
        return content_hash(self.canonical())

    # ── Platform-Aware Defaults ──

    @classmethod
    def with_platform_defaults(
        cls,
        topic: str,
        tone: Tone,
        audience: str,
        platform: Platform = Platform.DOUYIN,
        **overrides: Any,
    ) -> IntentIR:
        """Create IntentIR with platform-appropriate defaults.

        Example:
            intent = IntentIR.with_platform_defaults(
                topic="Redis",
                tone=Tone.EDUCATIONAL,
                audience="beginners",
            )
        """
        defaults = _PLATFORM_DEFAULTS.get(platform.value, _PLATFORM_DEFAULTS["generic"])
        kwargs = {
            "topic": topic,
            "tone": tone,
            "target_duration": min(45.0, defaults["max_duration"]),
            "audience": audience,
            "platform": platform,
            "aspect_ratio": AspectRatio(defaults["aspect_ratio"]),
        }
        kwargs.update(overrides)
        return cls(**kwargs)

    # ── Display ──

    def summary(self) -> str:
        return (
            f"IntentIR(topic={self.topic!r}, tone={self.tone.value}, "
            f"duration={self.target_duration}s, audience={self.audience!r}, "
            f"platform={self.platform.value})"
        )
