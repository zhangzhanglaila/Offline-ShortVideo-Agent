"""P7.2 — Narrative IR Tests.

Verifies:
  - Beat construction and validation
  - NarrativeIR construction and structural validation
  - Canonical form and content hashing
  - Duration normalization and absolute duration computation
  - Convenience constructors (HookBeat, ProblemBeat, etc.)
  - Edge cases
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.ir.narrative_ir import (
    NarrativeIR, Beat, BeatType, TransitionType,
    HookBeat, ProblemBeat, RevealBeat, CTABeat,
)


# ═══════════════════════════════════════════════════════════════════════
# Beat Construction
# ═══════════════════════════════════════════════════════════════════════


class TestBeatConstruction:
    """Beat dataclass behavior."""

    def test_basic_beat(self):
        b = Beat(beat_type=BeatType.HOOK, text="What is Redis?")
        assert b.beat_type == BeatType.HOOK
        assert b.text == "What is Redis?"
        assert b.relative_duration == 1.0
        assert b.emotional_intensity == 0.5

    def test_frozen(self):
        b = Beat(beat_type=BeatType.HOOK, text="hello")
        with pytest.raises(AttributeError):
            b.text = "changed"  # type: ignore

    def test_empty_text_rejected(self):
        with pytest.raises(ValueError, match="text"):
            Beat(beat_type=BeatType.HOOK, text="")

    def test_whitespace_text_rejected(self):
        with pytest.raises(ValueError, match="text"):
            Beat(beat_type=BeatType.HOOK, text="   ")

    def test_invalid_intensity_rejected(self):
        with pytest.raises(ValueError, match="emotional_intensity"):
            Beat(beat_type=BeatType.HOOK, text="x", emotional_intensity=1.5)

    def test_negative_intensity_rejected(self):
        with pytest.raises(ValueError, match="emotional_intensity"):
            Beat(beat_type=BeatType.HOOK, text="x", emotional_intensity=-0.1)

    def test_zero_duration_rejected(self):
        with pytest.raises(ValueError, match="relative_duration"):
            Beat(beat_type=BeatType.HOOK, text="x", relative_duration=0)

    def test_negative_duration_rejected(self):
        with pytest.raises(ValueError, match="relative_duration"):
            Beat(beat_type=BeatType.HOOK, text="x", relative_duration=-1)

    def test_to_dict(self):
        b = HookBeat("What is Redis?", duration=2.0, intensity=0.9)
        d = b.to_dict()
        assert d["beat_type"] == "hook"
        assert d["text"] == "What is Redis?"
        assert d["relative_duration"] == 2.0
        assert d["emotional_intensity"] == 0.9


# ═══════════════════════════════════════════════════════════════════════
# Convenience Constructors
# ═══════════════════════════════════════════════════════════════════════


class TestConvenienceConstructors:
    """HookBeat, ProblemBeat, RevealBeat, CTABeat."""

    def test_hook_beat(self):
        b = HookBeat("What is Redis?")
        assert b.beat_type == BeatType.HOOK
        assert b.emotional_intensity == 0.8

    def test_problem_beat(self):
        b = ProblemBeat("Traditional DBs are slow")
        assert b.beat_type == BeatType.PROBLEM
        assert b.emotional_intensity == 0.6

    def test_reveal_beat(self):
        b = RevealBeat("The secret is in-memory")
        assert b.beat_type == BeatType.REVEAL
        assert b.transition_after == TransitionType.BUILD

    def test_cta_beat(self):
        b = CTABeat("Follow for more")
        assert b.beat_type == BeatType.CTA
        assert b.relative_duration == 0.5

    def test_all_beat_types_creatable(self):
        for bt in BeatType:
            b = Beat(beat_type=bt, text=f"test {bt.value}")
            assert b.beat_type == bt


# ═══════════════════════════════════════════════════════════════════════
# NarrativeIR Construction & Validation
# ═══════════════════════════════════════════════════════════════════════


class TestNarrativeConstruction:
    """NarrativeIR structural validation."""

    def _make_narrative(self) -> NarrativeIR:
        return NarrativeIR(
            beats=(
                HookBeat("What is Redis?", duration=2.0),
                ProblemBeat("DB bottleneck", duration=3.0),
                RevealBeat("In-memory + single thread", duration=4.0),
                CTABeat("Follow for more", duration=1.0),
            ),
            title="Redis Explained",
        )

    def test_basic_construction(self):
        n = self._make_narrative()
        assert n.beat_count == 4
        assert n.title == "Redis Explained"

    def test_frozen(self):
        n = self._make_narrative()
        with pytest.raises(AttributeError):
            n.beats = ()  # type: ignore

    def test_empty_beats_rejected(self):
        with pytest.raises(ValueError, match="beats"):
            NarrativeIR(beats=())

    def test_first_beat_must_be_hook(self):
        with pytest.raises(ValueError, match="HOOK"):
            NarrativeIR(beats=(
                ProblemBeat("X"),
                CTABeat("Y"),
            ))

    def test_last_beat_cannot_be_transition(self):
        with pytest.raises(ValueError, match="TRANSITION"):
            NarrativeIR(beats=(
                HookBeat("X"),
                Beat(beat_type=BeatType.TRANSITION, text="bridge"),
            ))

    def test_consecutive_transitions_rejected(self):
        with pytest.raises(ValueError, match="Consecutive"):
            NarrativeIR(beats=(
                HookBeat("X"),
                Beat(beat_type=BeatType.TRANSITION, text="a"),
                Beat(beat_type=BeatType.TRANSITION, text="b"),
                CTABeat("Y"),
            ))

    def test_single_beat_narrative(self):
        """Single beat (just hook) is valid."""
        n = NarrativeIR(beats=(HookBeat("Quick tip"),))
        assert n.beat_count == 1

    def test_frozen(self):
        n = self._make_narrative()
        with pytest.raises(AttributeError):
            n.title = "changed"  # type: ignore


# ═══════════════════════════════════════════════════════════════════════
# Duration Properties
# ═══════════════════════════════════════════════════════════════════════


class TestDurations:
    """Duration normalization and absolute computation."""

    def test_total_relative_duration(self):
        n = NarrativeIR(beats=(
            HookBeat("A", duration=2.0),
            CTABeat("B", duration=3.0),
        ))
        assert n.total_relative_duration == 5.0

    def test_normalized_durations_sum_to_one(self):
        n = NarrativeIR(beats=(
            HookBeat("A", duration=2.0),
            ProblemBeat("B", duration=3.0),
            CTABeat("C", duration=5.0),
        ))
        norms = n.normalized_durations
        assert abs(sum(norms) - 1.0) < 1e-10
        assert abs(norms[0] - 0.2) < 1e-10
        assert abs(norms[1] - 0.3) < 1e-10
        assert abs(norms[2] - 0.5) < 1e-10

    def test_absolute_durations(self):
        n = NarrativeIR(beats=(
            HookBeat("A", duration=1.0),
            CTABeat("B", duration=1.0),
        ))
        absolutes = n.absolute_durations(30.0)
        assert abs(absolutes[0] - 15.0) < 1e-10
        assert abs(absolutes[1] - 15.0) < 1e-10

    def test_absolute_durations_respect_weights(self):
        n = NarrativeIR(beats=(
            HookBeat("A", duration=1.0),
            ProblemBeat("B", duration=2.0),
            CTABeat("C", duration=1.0),
        ))
        absolutes = n.absolute_durations(40.0)
        assert abs(absolutes[0] - 10.0) < 1e-10
        assert abs(absolutes[1] - 20.0) < 1e-10
        assert abs(absolutes[2] - 10.0) < 1e-10


# ═══════════════════════════════════════════════════════════════════════
# Canonical Form & Hashing
# ═══════════════════════════════════════════════════════════════════════


class TestCanonicalization:
    """Canonical form and content hashing."""

    def test_canonical_deterministic(self):
        n = NarrativeIR(beats=(
            HookBeat("A"),
            CTABeat("B"),
        ))
        hashes = [n.content_hash() for _ in range(100)]
        assert len(set(hashes)) == 1

    def test_different_text_different_hash(self):
        n1 = NarrativeIR(beats=(HookBeat("A"), CTABeat("B")))
        n2 = NarrativeIR(beats=(HookBeat("X"), CTABeat("B")))
        assert n1.content_hash() != n2.content_hash()

    def test_different_order_different_hash(self):
        n1 = NarrativeIR(beats=(
            HookBeat("A"),
            ProblemBeat("B"),
            CTABeat("C"),
        ))
        n2 = NarrativeIR(beats=(
            HookBeat("A"),
            RevealBeat("B"),  # different beat type
            CTABeat("C"),
        ))
        assert n1.content_hash() != n2.content_hash()

    def test_same_content_same_hash(self):
        n1 = NarrativeIR(beats=(
            HookBeat("What is Redis?"),
            ProblemBeat("Slow queries"),
            CTABeat("Follow"),
        ))
        n2 = NarrativeIR(beats=(
            HookBeat("What is Redis?"),
            ProblemBeat("Slow queries"),
            CTABeat("Follow"),
        ))
        assert n1.content_hash() == n2.content_hash()

    def test_to_dict_structure(self):
        n = NarrativeIR(
            beats=(HookBeat("A"), CTABeat("B")),
            title="Test",
            pacing="fast",
        )
        d = n.to_dict()
        assert d["title"] == "Test"
        assert d["pacing"] == "fast"
        assert len(d["beats"]) == 2
        assert d["beats"][0]["beat_type"] == "hook"


# ═══════════════════════════════════════════════════════════════════════
# Query Methods
# ═══════════════════════════════════════════════════════════════════════


class TestQueryMethods:
    """beats_of_type, beat_types, etc."""

    def test_beat_types(self):
        n = NarrativeIR(beats=(
            HookBeat("A"),
            ProblemBeat("B"),
            CTABeat("C"),
        ))
        assert n.beat_types == [BeatType.HOOK, BeatType.PROBLEM, BeatType.CTA]

    def test_beats_of_type(self):
        n = NarrativeIR(beats=(
            HookBeat("A"),
            ProblemBeat("B"),
            ProblemBeat("C"),
            CTABeat("D"),
        ))
        problems = n.beats_of_type(BeatType.PROBLEM)
        assert len(problems) == 2
        assert problems[0].text == "B"
        assert problems[1].text == "C"

    def test_beats_of_type_none(self):
        n = NarrativeIR(beats=(
            HookBeat("A"),
            CTABeat("B"),
        ))
        assert n.beats_of_type(BeatType.REVEAL) == []


# ═══════════════════════════════════════════════════════════════════════
# Display
# ═══════════════════════════════════════════════════════════════════════


class TestDisplay:
    """summary() and outline()"""

    def test_summary(self):
        n = NarrativeIR(beats=(
            HookBeat("A"),
            ProblemBeat("B"),
            CTABeat("C"),
        ))
        s = n.summary()
        assert "3" in s
        assert "hook" in s
        assert "cta" in s

    def test_outline(self):
        n = NarrativeIR(beats=(
            HookBeat("What is Redis?"),
            ProblemBeat("Slow queries"),
            CTABeat("Follow"),
        ))
        o = n.outline()
        assert "What is Redis?" in o
        assert "hook" in o
        assert "1." in o


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
