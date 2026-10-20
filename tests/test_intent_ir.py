"""P7.1 — Intent IR Tests.

Verifies:
  - Construction and validation
  - Canonical form (key ordering, None stripping, float normalization)
  - Content hashing (deterministic, semantic equivalence)
  - Platform defaults
  - Edge cases
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.ir.intent_ir import IntentIR, Tone, Platform, AspectRatio


# ═══════════════════════════════════════════════════════════════════════
# Construction & Validation
# ═══════════════════════════════════════════════════════════════════════


class TestConstruction:
    """Basic construction and field access."""

    def test_basic_construction(self):
        ir = IntentIR(
            topic="Redis",
            tone=Tone.DRAMATIC,
            target_duration=45.0,
            audience="beginners",
        )
        assert ir.topic == "Redis"
        assert ir.tone == Tone.DRAMATIC
        assert ir.target_duration == 45.0
        assert ir.audience == "beginners"

    def test_defaults(self):
        ir = IntentIR(
            topic="Redis",
            tone=Tone.EDUCATIONAL,
            target_duration=30.0,
            audience="general",
        )
        assert ir.platform == Platform.DOUYIN
        assert ir.aspect_ratio == AspectRatio.PORTRAIT_9_16
        assert ir.language == "zh-CN"
        assert ir.style == "modern"
        assert ir.max_scenes == 8

    def test_frozen(self):
        ir = IntentIR(
            topic="Redis",
            tone=Tone.DRAMATIC,
            target_duration=45.0,
            audience="beginners",
        )
        with pytest.raises(AttributeError):
            ir.topic = "changed"  # type: ignore


class TestValidation:
    """Validation rules."""

    def test_empty_topic_rejected(self):
        with pytest.raises(ValueError, match="topic"):
            IntentIR(topic="", tone=Tone.DRAMATIC, target_duration=45.0, audience="a")

    def test_whitespace_topic_rejected(self):
        with pytest.raises(ValueError, match="topic"):
            IntentIR(topic="   ", tone=Tone.DRAMATIC, target_duration=45.0, audience="a")

    def test_zero_duration_rejected(self):
        with pytest.raises(ValueError, match="target_duration"):
            IntentIR(topic="X", tone=Tone.DRAMATIC, target_duration=0, audience="a")

    def test_negative_duration_rejected(self):
        with pytest.raises(ValueError, match="target_duration"):
            IntentIR(topic="X", tone=Tone.DRAMATIC, target_duration=-5, audience="a")

    def test_excessive_duration_rejected(self):
        with pytest.raises(ValueError, match="target_duration"):
            IntentIR(topic="X", tone=Tone.DRAMATIC, target_duration=9999, audience="a")

    def test_empty_audience_rejected(self):
        with pytest.raises(ValueError, match="audience"):
            IntentIR(topic="X", tone=Tone.DRAMATIC, target_duration=45, audience="")

    def test_zero_max_scenes_rejected(self):
        with pytest.raises(ValueError, match="max_scenes"):
            IntentIR(
                topic="X", tone=Tone.DRAMATIC, target_duration=45,
                audience="a", max_scenes=0,
            )

    def test_excessive_max_scenes_rejected(self):
        with pytest.raises(ValueError, match="max_scenes"):
            IntentIR(
                topic="X", tone=Tone.DRAMATIC, target_duration=45,
                audience="a", max_scenes=999,
            )


# ═══════════════════════════════════════════════════════════════════════
# Canonical Form & Hashing
# ═══════════════════════════════════════════════════════════════════════


class TestCanonicalization:
    """Canonical form must be deterministic and semantically stable."""

    def test_canonical_key_order_invariant(self):
        """Same intent → same canonical regardless of construction order."""
        ir1 = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45.0, audience="beginners",
        )
        ir2 = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45.0, audience="beginners",
        )
        assert ir1.canonical() == ir2.canonical()

    def test_content_hash_deterministic(self):
        ir = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45.0, audience="beginners",
        )
        hashes = [ir.content_hash() for _ in range(100)]
        assert len(set(hashes)) == 1

    def test_different_content_different_hash(self):
        ir1 = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45.0, audience="beginners",
        )
        ir2 = IntentIR(
            topic="MongoDB", tone=Tone.DRAMATIC,
            target_duration=45.0, audience="beginners",
        )
        assert ir1.content_hash() != ir2.content_hash()

    def test_different_tone_different_hash(self):
        base = dict(topic="Redis", target_duration=45.0, audience="beginners")
        ir1 = IntentIR(tone=Tone.DRAMATIC, **base)
        ir2 = IntentIR(tone=Tone.EDUCATIONAL, **base)
        assert ir1.content_hash() != ir2.content_hash()

    def test_different_duration_different_hash(self):
        base = dict(topic="Redis", tone=Tone.DRAMATIC, audience="beginners")
        ir1 = IntentIR(target_duration=30.0, **base)
        ir2 = IntentIR(target_duration=60.0, **base)
        assert ir1.content_hash() != ir2.content_hash()

    def test_float_precision_normalized(self):
        """Duration noise beyond precision should not affect hash."""
        base = dict(topic="Redis", tone=Tone.DRAMATIC, audience="beginners")
        ir1 = IntentIR(target_duration=45.0, **base)
        ir2 = IntentIR(target_duration=45.00000000001, **base)
        assert ir1.content_hash() == ir2.content_hash()

    def test_whitespace_topic_normalized(self):
        """Leading/trailing whitespace stripped in canonical form."""
        ir1 = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45.0, audience="beginners",
        )
        ir2 = IntentIR(
            topic="  Redis  ", tone=Tone.DRAMATIC,
            target_duration=45.0, audience="  beginners  ",
        )
        assert ir1.canonical() == ir2.canonical()

    def test_keywords_tuple_order_matters(self):
        """Keywords are ordered — different order → different hash."""
        base = dict(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45.0, audience="beginners",
        )
        ir1 = IntentIR(keywords=("fast", "cache"), **base)
        ir2 = IntentIR(keywords=("cache", "fast"), **base)
        assert ir1.content_hash() != ir2.content_hash()

    def test_empty_keywords_same_as_no_keywords(self):
        base = dict(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45.0, audience="beginners",
        )
        ir1 = IntentIR(**base)
        ir2 = IntentIR(keywords=(), **base)
        assert ir1.content_hash() == ir2.content_hash()


# ═══════════════════════════════════════════════════════════════════════
# Platform Defaults
# ═══════════════════════════════════════════════════════════════════════


class TestPlatformDefaults:
    """with_platform_defaults should produce sensible defaults."""

    def test_douyin_defaults(self):
        ir = IntentIR.with_platform_defaults(
            topic="Redis",
            tone=Tone.EDUCATIONAL,
            audience="beginners",
        )
        assert ir.platform == Platform.DOUYIN
        assert ir.aspect_ratio == AspectRatio.PORTRAIT_9_16
        assert ir.target_duration <= 60

    def test_youtube_defaults(self):
        ir = IntentIR.with_platform_defaults(
            topic="Redis",
            tone=Tone.EDUCATIONAL,
            audience="beginners",
            platform=Platform.YOUTUBE,
        )
        assert ir.platform == Platform.YOUTUBE
        assert ir.aspect_ratio == AspectRatio.LANDSCAPE_16_9

    def test_overrides_apply(self):
        ir = IntentIR.with_platform_defaults(
            topic="Redis",
            tone=Tone.EDUCATIONAL,
            audience="beginners",
            target_duration=120.0,
        )
        assert ir.target_duration == 120.0

    def test_all_platforms_constructable(self):
        for p in Platform:
            ir = IntentIR.with_platform_defaults(
                topic="X", tone=Tone.CASUAL, audience="a", platform=p,
            )
            assert ir.platform == p


# ═══════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Boundary conditions."""

    def test_minimal_duration(self):
        ir = IntentIR(
            topic="X", tone=Tone.CASUAL,
            target_duration=0.1, audience="a",
        )
        assert ir.target_duration == 0.1

    def test_max_duration(self):
        ir = IntentIR(
            topic="X", tone=Tone.CASUAL,
            target_duration=3600.0, audience="a",
        )
        assert ir.target_duration == 3600.0

    def test_single_max_scene(self):
        ir = IntentIR(
            topic="X", tone=Tone.CASUAL,
            target_duration=10.0, audience="a", max_scenes=1,
        )
        assert ir.max_scenes == 1

    def test_metadata_preserved(self):
        ir = IntentIR(
            topic="X", tone=Tone.CASUAL,
            target_duration=10.0, audience="a",
            metadata={"custom": True},
        )
        d = ir.to_dict()
        assert d["metadata"] == {"custom": True}

    def test_summary(self):
        ir = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45.0, audience="beginners",
        )
        s = ir.summary()
        assert "Redis" in s
        assert "dramatic" in s
        assert "45" in s


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
