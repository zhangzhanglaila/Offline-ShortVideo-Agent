"""TTS Compiler Pass — Script content → TTS_SENTENCE content.

Takes script sentences and generates per-sentence audio with word-level
timestamps using the TTSService.

    script_content = {
        "topic": "Redis",
        "sentences": [
            {"id": "s_0", "index": 0, "text": "Redis为什么这么快？"},
            {"id": "s_1", "index": 1, "text": "答案是内存加单线程"},
        ]
    }
    ↓ TTSPass
    tts_content = {
        "segments": [
            {
                "sentence_id": "s_0",
                "text": "Redis为什么这么快？",
                "audio_path": "output/tts/abc123.mp3",
                "duration": 2.5,
                "word_timings": [{"word": "Redis", "start": 0.0, "end": 0.5}, ...],
            }, ...
        ],
        "total_duration": 8.3,
        "voice": "zh-CN-XiaoxiaoNeural",
    }
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.ai.tts_service import TTSService
from compiler.base import CompilerPass


class TTSPass:
    """Script content → TTS_SENTENCE content.

    Not a standard CompilerPass (no fixed InputT/OutputT) because
    script content is a plain dict, not a formal IR type.
    """

    name = "tts_pass"

    def __init__(self, tts_service: TTSService):
        self.tts = tts_service

    def run(self, script_content: dict[str, Any]) -> dict[str, Any]:
        """Generate TTS for all sentences in the script.

        Args:
            script_content: SCRIPT artifact content with "sentences" list.

        Returns:
            TTS_SENTENCE artifact content dict.
        """
        sentences = script_content.get("sentences", [])
        if not sentences:
            return {"segments": [], "total_duration": 0.0, "voice": self.tts.voice}

        texts = [s["text"] for s in sentences]

        # Run async synthesis
        results = asyncio.run(self.tts.synthesize_segments(texts))

        segments = []
        total_duration = 0.0
        for sentence, result in zip(sentences, results):
            segments.append({
                "sentence_id": sentence.get("id", ""),
                "text": result.text,
                "audio_path": result.audio_path,
                "duration": result.duration,
                "word_timings": [w.to_dict() for w in result.word_timings],
                "content_hash": result.content_hash,
            })
            total_duration += result.duration

        return {
            "segments": segments,
            "total_duration": round(total_duration, 3),
            "voice": self.tts.voice,
            "rate": self.tts.rate,
        }
