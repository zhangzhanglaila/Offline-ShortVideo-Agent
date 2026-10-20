"""P11.2 — TTS Service Tests.

Verifies:
  - WordTiming dataclass construction
  - SegmentResult dataclass and serialization
  - TTSService.synthesize with mocked edge-tts
  - Word boundary extraction (ticks → seconds)
  - Caching behavior (content-addressable)
  - TTSPass produces valid TTS_SENTENCE content
  - SceneToTimelinePass populates word_timings from TTS data
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ai.tts_service import WordTiming, SegmentResult, TTSService, _TICKS_PER_SECOND
from compiler.pass_tts import TTSPass
from compiler.pass_scene_to_timeline import SceneToTimelinePass
from compiler.pass_narrative_to_scene import SceneIR


# ═══════════════════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════════════════


class TestWordTiming:
    """WordTiming dataclass."""

    def test_construction(self):
        wt = WordTiming(word="Redis", start=0.0, end=0.5)
        assert wt.word == "Redis"
        assert wt.start == 0.0
        assert wt.end == 0.5

    def test_frozen(self):
        wt = WordTiming(word="test", start=1.0, end=2.0)
        with pytest.raises(AttributeError):
            wt.word = "changed"

    def test_to_dict(self):
        wt = WordTiming(word="Redis", start=0.123, end=0.4567)
        d = wt.to_dict()
        assert d == {"word": "Redis", "start": 0.123, "end": 0.457}

    def test_precision(self):
        wt = WordTiming(word="a", start=0.0001, end=0.9999)
        d = wt.to_dict()
        assert d["start"] == 0.0
        assert d["end"] == 1.0


class TestSegmentResult:
    """SegmentResult dataclass."""

    def test_construction(self):
        sr = SegmentResult(
            text="hello",
            audio_path="/tmp/test.mp3",
            duration=2.5,
            word_timings=(WordTiming("hello", 0.0, 0.5),),
            voice="zh-CN-XiaoxiaoNeural",
            content_hash="abc123",
        )
        assert sr.text == "hello"
        assert len(sr.word_timings) == 1

    def test_to_dict(self):
        sr = SegmentResult(
            text="test",
            audio_path="/tmp/test.mp3",
            duration=1.0,
            word_timings=(WordTiming("test", 0.0, 1.0),),
            voice="en-US-AriaNeural",
            content_hash="hash1",
        )
        d = sr.to_dict()
        assert d["text"] == "test"
        assert d["word_timings"] == [{"word": "test", "start": 0.0, "end": 1.0}]
        assert d["voice"] == "en-US-AriaNeural"


# ═══════════════════════════════════════════════════════════════════════
# TTS Service (Mocked edge-tts)
# ═══════════════════════════════════════════════════════════════════════


def _make_word_boundary(text: str, offset_sec: float, duration_sec: float) -> dict:
    """Create a WordBoundary chunk (ticks format)."""
    return {
        "type": "WordBoundary",
        "text": text,
        "offset": int(offset_sec * _TICKS_PER_SECOND),
        "duration": int(duration_sec * _TICKS_PER_SECOND),
    }


def _make_audio_chunk(data: bytes = b"\x00" * 100) -> dict:
    """Create an audio data chunk."""
    return {"type": "audio", "data": data}


class MockCommunicate:
    """Mock edge_tts.Communicate."""

    def __init__(self, text, voice="en-US-AriaNeural", rate="+0%", boundary="WordBoundary"):
        self.text = text
        self.voice = voice
        self._chunks = [
            _make_audio_chunk(),
            _make_word_boundary("Hello", 0.0, 0.3),
            _make_audio_chunk(),
            _make_word_boundary("world", 0.3, 0.4),
            _make_audio_chunk(),
        ]

    async def stream(self):
        for chunk in self._chunks:
            yield chunk


class TestTTSService:
    """TTSService with mocked edge-tts."""

    @pytest.fixture
    def service(self, tmp_path):
        return TTSService(output_dir=tmp_path, voice="test-voice", rate=0)

    @patch("core.ai.tts_service.edge_tts.Communicate", MockCommunicate)
    @patch("core.ai.tts_service._measure_audio_duration", return_value=0.7)
    def test_synthesize_basic(self, mock_duration, service):
        result = asyncio.run(service.synthesize("Hello world"))
        assert isinstance(result, SegmentResult)
        assert result.text == "Hello world"
        assert result.voice == "test-voice"
        assert result.duration == 0.7
        assert Path(result.audio_path).exists()

    @patch("core.ai.tts_service.edge_tts.Communicate", MockCommunicate)
    @patch("core.ai.tts_service._measure_audio_duration", return_value=0.7)
    def test_word_timings_extracted(self, mock_duration, service):
        result = asyncio.run(service.synthesize("Hello world"))
        assert len(result.word_timings) == 2
        assert result.word_timings[0].word == "Hello"
        assert result.word_timings[0].start == 0.0
        assert result.word_timings[0].end == 0.3
        assert result.word_timings[1].word == "world"
        assert result.word_timings[1].start == 0.3
        assert result.word_timings[1].end == 0.7

    @patch("core.ai.tts_service.edge_tts.Communicate", MockCommunicate)
    @patch("core.ai.tts_service._measure_audio_duration", return_value=0.7)
    def test_ticks_to_seconds_conversion(self, mock_duration, service):
        result = asyncio.run(service.synthesize("test"))
        # 0.3 seconds = 3_000_000 ticks, should convert back to 0.3
        wt = result.word_timings[0]
        assert abs(wt.start - 0.0) < 0.001
        assert abs(wt.end - 0.3) < 0.001

    @patch("core.ai.tts_service.edge_tts.Communicate", MockCommunicate)
    @patch("core.ai.tts_service._measure_audio_duration", return_value=0.7)
    def test_content_hash_deterministic(self, mock_duration, service):
        r1 = asyncio.run(service.synthesize("test"))
        # Clear cache to force re-synthesis
        service.clear_cache()
        r2 = asyncio.run(service.synthesize("test"))
        assert r1.content_hash == r2.content_hash

    @patch("core.ai.tts_service.edge_tts.Communicate", MockCommunicate)
    @patch("core.ai.tts_service._measure_audio_duration", return_value=0.7)
    def test_different_text_different_hash(self, mock_duration, service):
        r1 = asyncio.run(service.synthesize("hello"))
        r2 = asyncio.run(service.synthesize("world"))
        assert r1.content_hash != r2.content_hash

    @patch("core.ai.tts_service.edge_tts.Communicate", MockCommunicate)
    @patch("core.ai.tts_service._measure_audio_duration", return_value=0.7)
    def test_cache_hit(self, mock_duration, service):
        """Second call with same text should return cached result."""
        r1 = asyncio.run(service.synthesize("cached"))
        r2 = asyncio.run(service.synthesize("cached"))
        assert r1 is r2  # Same object (cache hit)

    @patch("core.ai.tts_service.edge_tts.Communicate", MockCommunicate)
    @patch("core.ai.tts_service._measure_audio_duration", return_value=0.7)
    def test_clear_cache(self, mock_duration, service):
        r1 = asyncio.run(service.synthesize("test"))
        service.clear_cache()
        r2 = asyncio.run(service.synthesize("test"))
        assert r1.content_hash == r2.content_hash
        assert r1 is not r2  # Different objects (cache cleared)

    @patch("core.ai.tts_service.edge_tts.Communicate", MockCommunicate)
    @patch("core.ai.tts_service._measure_audio_duration", return_value=0.7)
    def test_synthesize_segments(self, mock_duration, service):
        results = asyncio.run(service.synthesize_segments(["a", "b", "c"]))
        assert len(results) == 3
        for r in results:
            assert isinstance(r, SegmentResult)

    def test_segment_hash_format(self, service):
        h = service.segment_hash("test")
        assert isinstance(h, str)
        assert len(h) >= 8  # At least 8 hex chars


# ═══════════════════════════════════════════════════════════════════════
# TTS Compiler Pass
# ═══════════════════════════════════════════════════════════════════════


class TestTTSPass:
    """TTSPass with mocked TTSService."""

    def _make_script_content(self):
        return {
            "topic": "Redis",
            "sentences": [
                {"id": "s_0", "index": 0, "text": "Redis为什么这么快？"},
                {"id": "s_1", "index": 1, "text": "答案是内存加单线程"},
            ],
        }

    def _make_mock_service(self):
        service = MagicMock()
        service.voice = "zh-CN-XiaoxiaoNeural"
        service.rate = 0

        async def mock_synthesize_segments(texts):
            results = []
            for i, text in enumerate(texts):
                results.append(SegmentResult(
                    text=text,
                    audio_path=f"/tmp/seg_{i}.mp3",
                    duration=2.5,
                    word_timings=(
                        WordTiming(word=text[:3], start=0.0, end=0.5),
                        WordTiming(word=text[3:], start=0.5, end=2.5),
                    ),
                    voice="zh-CN-XiaoxiaoNeural",
                    content_hash=f"hash_{i}",
                ))
            return results

        service.synthesize_segments = mock_synthesize_segments
        return service

    def test_basic_output(self):
        service = self._make_mock_service()
        pass_ = TTSPass(service)
        result = pass_.run(self._make_script_content())

        assert "segments" in result
        assert "total_duration" in result
        assert "voice" in result
        assert len(result["segments"]) == 2

    def test_segment_structure(self):
        service = self._make_mock_service()
        pass_ = TTSPass(service)
        result = pass_.run(self._make_script_content())

        seg = result["segments"][0]
        assert seg["sentence_id"] == "s_0"
        assert seg["text"] == "Redis为什么这么快？"
        assert seg["audio_path"] == "/tmp/seg_0.mp3"
        assert seg["duration"] == 2.5
        assert len(seg["word_timings"]) == 2
        assert seg["content_hash"] == "hash_0"

    def test_total_duration(self):
        service = self._make_mock_service()
        pass_ = TTSPass(service)
        result = pass_.run(self._make_script_content())
        assert result["total_duration"] == 5.0

    def test_empty_sentences(self):
        service = self._make_mock_service()
        pass_ = TTSPass(service)
        result = pass_.run({"topic": "test", "sentences": []})
        assert result["segments"] == []
        assert result["total_duration"] == 0.0

    def test_voice_preserved(self):
        service = self._make_mock_service()
        pass_ = TTSPass(service)
        result = pass_.run(self._make_script_content())
        assert result["voice"] == "zh-CN-XiaoxiaoNeural"


# ═══════════════════════════════════════════════════════════════════════
# Scene-to-Timeline with TTS Data
# ═══════════════════════════════════════════════════════════════════════


class TestTimelineWithTTS:
    """SceneToTimelinePass populates word_timings from TTS data."""

    def _make_scenes(self):
        return [
            SceneIR(
                scene_id="scene_0", scene_type="hook", text="Hello",
                duration_in_frames=90, camera_motion="zoom_in",
                background="#000", text_style="bold",
            ),
            SceneIR(
                scene_id="scene_1", scene_type="reveal", text="World",
                duration_in_frames=120, camera_motion="fade_in",
                background="#111", text_style="normal",
            ),
        ]

    def _make_tts_data(self):
        return {
            "segments": [
                {
                    "sentence_id": "scene_0",
                    "text": "Hello",
                    "audio_path": "/tmp/scene_0.mp3",
                    "duration": 3.0,
                    "word_timings": [
                        {"word": "Hello", "start": 0.0, "end": 0.5},
                    ],
                },
                {
                    "sentence_id": "scene_1",
                    "text": "World",
                    "audio_path": "/tmp/scene_1.mp3",
                    "duration": 4.0,
                    "word_timings": [
                        {"word": "World", "start": 0.0, "end": 0.4},
                    ],
                },
            ],
            "total_duration": 7.0,
            "voice": "zh-CN-XiaoxiaoNeural",
        }

    def test_word_timings_populated(self):
        pass_ = SceneToTimelinePass(fps=30, tts_data=self._make_tts_data())
        result = pass_.run(self._make_scenes())
        timeline = result.output

        sub_tracks = [t for t in timeline.tracks if t.track_type.value == "subtitle"]
        assert len(sub_tracks) == 2

        sub0 = sub_tracks[0]
        assert "word_timings" in sub0.content
        wt = sub0.content["word_timings"]
        assert len(wt) == 1
        assert wt[0]["word"] == "Hello"
        assert wt[0]["start_frame"] == 0   # frame 0 (scene starts at 0)
        assert wt[0]["end_frame"] == 15    # 0.5s * 30fps = 15 frames

    def test_word_timings_have_both_formats(self):
        pass_ = SceneToTimelinePass(fps=30, tts_data=self._make_tts_data())
        result = pass_.run(self._make_scenes())
        timeline = result.output

        sub0 = [t for t in timeline.tracks if t.track_type.value == "subtitle"][0]
        wt = sub0.content["word_timings"][0]
        # Should have both frame and second formats
        assert "start_frame" in wt
        assert "end_frame" in wt
        assert "start" in wt
        assert "end" in wt

    def test_no_tts_data_no_word_timings(self):
        pass_ = SceneToTimelinePass(fps=30)
        result = pass_.run(self._make_scenes())
        timeline = result.output

        sub_tracks = [t for t in timeline.tracks if t.track_type.value == "subtitle"]
        for t in sub_tracks:
            assert "word_timings" not in t.content

    def test_audio_path_in_subtitle_content(self):
        pass_ = SceneToTimelinePass(fps=30, tts_data=self._make_tts_data())
        result = pass_.run(self._make_scenes())
        timeline = result.output

        sub0 = [t for t in timeline.tracks if t.track_type.value == "subtitle"][0]
        assert sub0.content["audio_path"] == "/tmp/scene_0.mp3"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
