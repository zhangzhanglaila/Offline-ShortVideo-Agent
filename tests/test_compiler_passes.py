"""P7.6 — Compiler Pass Tests.

Verifies:
  - Intent → Narrative pass produces valid NarrativeIR
  - Deterministic output (same input → same output)
  - Different inputs → different outputs
  - Pass pipeline composition
  - Custom generate function override
  - All tones produce valid narratives
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.ir.intent_ir import IntentIR, Tone, Platform
from compiler.ir.narrative_ir import NarrativeIR, BeatType, HookBeat, CTABeat
from compiler.pass_intent_to_narrative import IntentToNarrativePass
from compiler.base import PassPipeline


# ═══════════════════════════════════════════════════════════════════════
# Intent → Narrative Pass
# ═══════════════════════════════════════════════════════════════════════


class TestIntentToNarrative:
    """Basic pass behavior."""

    def _make_intent(self, **overrides) -> IntentIR:
        defaults = dict(
            topic="Redis",
            tone=Tone.DRAMATIC,
            target_duration=45.0,
            audience="beginners",
        )
        defaults.update(overrides)
        return IntentIR(**defaults)

    def test_produces_narrative_ir(self):
        pass_ = IntentToNarrativePass()
        result = pass_.run(self._make_intent())
        assert isinstance(result.output, NarrativeIR)

    def test_narrative_has_beats(self):
        pass_ = IntentToNarrativePass()
        result = pass_.run(self._make_intent())
        n = result.output
        assert n.beat_count >= 3

    def test_starts_with_hook(self):
        pass_ = IntentToNarrativePass()
        result = pass_.run(self._make_intent())
        assert result.output.beat_types[0] == BeatType.HOOK

    def test_pass_name(self):
        pass_ = IntentToNarrativePass()
        result = pass_.run(self._make_intent())
        assert result.pass_name == "intent_to_narrative"

    def test_timing_recorded(self):
        pass_ = IntentToNarrativePass()
        result = pass_.run(self._make_intent())
        assert result.duration >= 0

    def test_hashes_populated(self):
        pass_ = IntentToNarrativePass()
        result = pass_.run(self._make_intent())
        assert len(result.input_hash) > 0
        assert len(result.output_hash) > 0
        assert result.input_hash != result.output_hash


class TestDeterminism:
    """Same input → same output, always."""

    def test_deterministic_output(self):
        pass_ = IntentToNarrativePass()
        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45.0, audience="beginners",
        )
        results = [pass_.run(intent) for _ in range(50)]
        hashes = [r.output_hash for r in results]
        assert len(set(hashes)) == 1

    def test_deterministic_narrative_content(self):
        pass_ = IntentToNarrativePass()
        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45.0, audience="beginners",
        )
        r1 = pass_.run(intent)
        r2 = pass_.run(intent)
        assert r1.output.canonical() == r2.output.canonical()


class TestInputSensitivity:
    """Different inputs → different outputs."""

    def test_different_topic_different_output(self):
        pass_ = IntentToNarrativePass()
        i1 = IntentIR(topic="Redis", tone=Tone.DRAMATIC, target_duration=45, audience="a")
        i2 = IntentIR(topic="MongoDB", tone=Tone.DRAMATIC, target_duration=45, audience="a")
        r1 = pass_.run(i1)
        r2 = pass_.run(i2)
        assert r1.output_hash != r2.output_hash

    def test_different_tone_different_output(self):
        pass_ = IntentToNarrativePass()
        i1 = IntentIR(topic="Redis", tone=Tone.DRAMATIC, target_duration=45, audience="a")
        i2 = IntentIR(topic="Redis", tone=Tone.EDUCATIONAL, target_duration=45, audience="a")
        r1 = pass_.run(i1)
        r2 = pass_.run(i2)
        assert r1.output_hash != r2.output_hash


class TestAllTones:
    """Every tone produces a valid narrative."""

    def test_all_tones_valid(self):
        pass_ = IntentToNarrativePass()
        for tone in Tone:
            intent = IntentIR(
                topic="Test", tone=tone,
                target_duration=30.0, audience="general",
            )
            result = pass_.run(intent)
            n = result.output
            assert isinstance(n, NarrativeIR)
            assert n.beat_count >= 3
            assert n.beat_types[0] == BeatType.HOOK


class TestCustomGenerateFn:
    """Override generate_fn with custom logic."""

    def test_custom_fn_called(self):
        called_with = []

        def my_gen(intent: IntentIR) -> NarrativeIR:
            called_with.append(intent.topic)
            return NarrativeIR(
                beats=(
                    HookBeat(f"Custom: {intent.topic}"),
                    CTABeat("Done"),
                ),
            )

        pass_ = IntentToNarrativePass(generate_fn=my_gen)
        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45, audience="a",
        )
        result = pass_.run(intent)
        assert called_with == ["Redis"]
        assert "Custom: Redis" in result.output.beats[0].text


# ═══════════════════════════════════════════════════════════════════════
# Pass Pipeline
# ═══════════════════════════════════════════════════════════════════════


class TestPassPipeline:
    """Pipeline composition."""

    def test_single_pass_pipeline(self):
        pipeline = PassPipeline([IntentToNarrativePass()])
        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45, audience="beginners",
        )
        results = pipeline.run(intent)
        assert len(results) == 1
        assert isinstance(results[0].output, NarrativeIR)

    def test_pipeline_pass_names(self):
        pipeline = PassPipeline([IntentToNarrativePass()])
        assert pipeline.pass_names == ["intent_to_narrative"]

    def test_pipeline_add(self):
        pipeline = PassPipeline()
        pipeline.add(IntentToNarrativePass())
        assert len(pipeline.passes) == 1

    def test_pipeline_chaining(self):
        """Pipeline.run chains outputs: pass1.output → pass2.input."""
        call_log = []

        class LoggingPass(IntentToNarrativePass):
            def transform(self, input_ir):
                call_log.append(input_ir.topic if hasattr(input_ir, 'topic') else 'narrative')
                return super().transform(input_ir)

        pipeline = PassPipeline([LoggingPass()])
        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45, audience="a",
        )
        pipeline.run(intent)
        assert call_log == ["Redis"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
