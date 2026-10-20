"""P11.1 — Narrative Planner Tests.

Verifies:
  - Template fallback produces valid NarrativeIR
  - LLM backend integration (mocked)
  - JSON parsing from LLM responses
  - Caching behavior
  - Error handling (bad JSON, empty beats)
  - Different tones produce different narratives
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.ir.intent_ir import IntentIR, Tone, Platform
from compiler.ir.narrative_ir import NarrativeIR, BeatType
from core.ai.narrative_planner import (
    NarrativePlanner, LLMBackend, LLMConfig,
    TemplateBackend, _extract_json, _parse_narrative_json,
)


# ═══════════════════════════════════════════════════════════════════════
# JSON Extraction
# ═══════════════════════════════════════════════════════════════════════


class TestJSONExtraction:
    """Extract JSON from various LLM response formats."""

    def test_direct_json(self):
        data = _extract_json('{"beats": []}')
        assert data == {"beats": []}

    def test_markdown_code_block(self):
        text = '```json\n{"beats": []}\n```'
        data = _extract_json(text)
        assert data == {"beats": []}

    def test_code_block_no_lang(self):
        text = '```\n{"beats": []}\n```'
        data = _extract_json(text)
        assert data == {"beats": []}

    def test_json_embedded_in_text(self):
        text = 'Here is the result:\n{"beats": []}\nDone.'
        data = _extract_json(text)
        assert data == {"beats": []}

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="Could not extract"):
            _extract_json("no json here at all")


# ═══════════════════════════════════════════════════════════════════════
# JSON Parsing
# ═══════════════════════════════════════════════════════════════════════


class TestNarrativeParsing:
    """Parse JSON dict into NarrativeIR."""

    def test_basic_parse(self):
        data = {
            "title": "Test",
            "beats": [
                {"beat_type": "hook", "text": "Hello", "relative_duration": 2.0},
                {"beat_type": "cta", "text": "Follow", "relative_duration": 1.0},
            ],
        }
        n = _parse_narrative_json(data)
        assert n.title == "Test"
        assert n.beat_count == 2
        assert n.beat_types[0] == BeatType.HOOK

    def test_all_beat_types_parsed(self):
        data = {
            "beats": [
                {"beat_type": bt, "text": f"text {bt}"}
                for bt in ["hook", "problem", "explanation", "reveal",
                           "example", "comparison", "cta", "summary"]
            ],
        }
        n = _parse_narrative_json(data)
        assert n.beat_count == 8

    def test_emotional_intensity_parsed(self):
        data = {
            "beats": [
                {"beat_type": "hook", "text": "A", "emotional_intensity": 0.9},
                {"beat_type": "cta", "text": "B", "emotional_intensity": 0.3},
            ],
        }
        n = _parse_narrative_json(data)
        assert n.beats[0].emotional_intensity == 0.9
        assert n.beats[1].emotional_intensity == 0.3

    def test_empty_beats_raises(self):
        with pytest.raises(ValueError, match="beats"):
            _parse_narrative_json({"beats": []})


# ═══════════════════════════════════════════════════════════════════════
# Template Fallback
# ═══════════════════════════════════════════════════════════════════════


class TestTemplateFallback:
    """Template-based narrative generation (no LLM)."""

    def test_produces_valid_narrative(self):
        planner = NarrativePlanner()  # No backend → template
        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45, audience="beginners",
        )
        n = planner.plan(intent)
        assert isinstance(n, NarrativeIR)
        assert n.beat_count >= 3

    def test_starts_with_hook(self):
        planner = NarrativePlanner()
        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45, audience="beginners",
        )
        n = planner.plan(intent)
        assert n.beat_types[0] == BeatType.HOOK

    def test_topic_appears_in_text(self):
        planner = NarrativePlanner()
        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45, audience="beginners",
        )
        n = planner.plan(intent)
        # Topic should appear somewhere in the beats
        all_text = " ".join(b.text for b in n.beats)
        assert "Redis" in all_text

    def test_deterministic(self):
        planner = NarrativePlanner()
        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45, audience="beginners",
        )
        n1 = planner.plan(intent)
        n2 = planner.plan(intent)
        assert n1.content_hash() == n2.content_hash()

    def test_different_tone_different_narrative(self):
        planner = NarrativePlanner()
        i1 = IntentIR(topic="Redis", tone=Tone.DRAMATIC, target_duration=45, audience="a")
        i2 = IntentIR(topic="Redis", tone=Tone.EDUCATIONAL, target_duration=45, audience="a")
        n1 = planner.plan(i1)
        n2 = planner.plan(i2)
        assert n1.content_hash() != n2.content_hash()

    def test_different_topic_different_narrative(self):
        planner = NarrativePlanner()
        i1 = IntentIR(topic="Redis", tone=Tone.DRAMATIC, target_duration=45, audience="a")
        i2 = IntentIR(topic="MongoDB", tone=Tone.DRAMATIC, target_duration=45, audience="a")
        n1 = planner.plan(i1)
        n2 = planner.plan(i2)
        assert n1.content_hash() != n2.content_hash()

    def test_all_tones_work(self):
        planner = NarrativePlanner()
        for tone in Tone:
            intent = IntentIR(
                topic="Test", tone=tone,
                target_duration=30, audience="general",
            )
            n = planner.plan(intent)
            assert n.beat_count >= 3


# ═══════════════════════════════════════════════════════════════════════
# LLM Backend Integration
# ═══════════════════════════════════════════════════════════════════════


class MockLLMBackend(LLMBackend):
    """Mock LLM backend that returns pre-defined JSON."""

    def __init__(self, response: str):
        self.response = response
        self.call_count = 0
        self.last_prompt = ""

    def generate(self, prompt: str, config: LLMConfig) -> str:
        self.call_count += 1
        self.last_prompt = prompt
        return self.response


class TestLLMBackend:
    """LLM backend integration."""

    def test_mock_backend_called(self):
        response = json.dumps({
            "title": "Test",
            "beats": [
                {"beat_type": "hook", "text": "Hello from LLM"},
                {"beat_type": "cta", "text": "Follow"},
            ],
        })
        backend = MockLLMBackend(response)
        planner = NarrativePlanner(backend=backend)

        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45, audience="beginners",
        )
        n = planner.plan(intent)

        assert backend.call_count == 1
        assert "Hello from LLM" in n.beats[0].text

    def test_llm_failure_falls_back_to_template(self):
        class FailingBackend(LLMBackend):
            def generate(self, prompt, config):
                raise RuntimeError("API error")

        planner = NarrativePlanner(backend=FailingBackend())
        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45, audience="beginners",
        )
        n = planner.plan(intent)
        # Should still produce a valid narrative (template fallback)
        assert isinstance(n, NarrativeIR)
        assert n.beat_count >= 3

    def test_bad_json_falls_back_to_template(self):
        backend = MockLLMBackend("this is not json at all")
        planner = NarrativePlanner(backend=backend)
        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45, audience="beginners",
        )
        n = planner.plan(intent)
        assert isinstance(n, NarrativeIR)

    def test_markdown_json_parsed(self):
        response = '''```json
{
    "title": "Redis解析",
    "beats": [
        {"beat_type": "hook", "text": "Redis为什么这么快？"},
        {"beat_type": "reveal", "text": "答案是内存+单线程"},
        {"beat_type": "cta", "text": "关注学习更多"}
    ]
}
```'''
        backend = MockLLMBackend(response)
        planner = NarrativePlanner(backend=backend)
        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45, audience="beginners",
        )
        n = planner.plan(intent)
        assert n.title == "Redis解析"
        assert n.beat_count == 3


# ═══════════════════════════════════════════════════════════════════════
# Caching
# ═══════════════════════════════════════════════════════════════════════


class TestCaching:
    """Planner caching behavior."""

    def test_same_intent_returns_cached(self):
        response = json.dumps({
            "beats": [
                {"beat_type": "hook", "text": "Cached"},
                {"beat_type": "cta", "text": "Done"},
            ],
        })
        backend = MockLLMBackend(response)
        planner = NarrativePlanner(backend=backend, use_cache=True)

        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45, audience="beginners",
        )
        planner.plan(intent)
        planner.plan(intent)  # Second call

        # Backend should only be called once (cache hit on second)
        assert backend.call_count == 1

    def test_cache_disabled_calls_every_time(self):
        response = json.dumps({
            "beats": [
                {"beat_type": "hook", "text": "No cache"},
                {"beat_type": "cta", "text": "Done"},
            ],
        })
        backend = MockLLMBackend(response)
        planner = NarrativePlanner(backend=backend, use_cache=False)

        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45, audience="beginners",
        )
        planner.plan(intent)
        planner.plan(intent)

        assert backend.call_count == 2

    def test_clear_cache(self):
        response = json.dumps({
            "beats": [
                {"beat_type": "hook", "text": "A"},
                {"beat_type": "cta", "text": "B"},
            ],
        })
        backend = MockLLMBackend(response)
        planner = NarrativePlanner(backend=backend, use_cache=True)

        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45, audience="beginners",
        )
        planner.plan(intent)
        planner.clear_cache()
        planner.plan(intent)

        assert backend.call_count == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
