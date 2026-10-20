"""TTS Service — edge-tts with word-level timestamps.

Generates per-sentence audio with precise word timing data.
Uses edge-tts WordBoundary API for native word-level timestamps
(no Whisper post-processing needed).

    service = TTSService(output_dir=Path("output/tts"))
    result = await service.synthesize("Redis为什么这么快？")
    # result.audio_path, result.word_timings, result.duration

    # Batch with concurrency control
    results = await service.synthesize_segments(["sentence1", "sentence2"])
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import edge_tts

from thinking.canonicalize import content_hash


# ── Data Structures ──

@dataclass(frozen=True)
class WordTiming:
    """A single word's timing within an audio segment."""
    word: str
    start: float   # seconds
    end: float     # seconds

    def to_dict(self) -> dict[str, Any]:
        return {"word": self.word, "start": round(self.start, 3), "end": round(self.end, 3)}


@dataclass(frozen=True)
class SegmentResult:
    """Result of synthesizing a single text segment."""
    text: str
    audio_path: str
    duration: float                            # seconds (measured from audio)
    word_timings: tuple[WordTiming, ...]       # per-word timestamps
    voice: str
    content_hash: str                          # deterministic hash for caching

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "audio_path": self.audio_path,
            "duration": round(self.duration, 3),
            "word_timings": [w.to_dict() for w in self.word_timings],
            "voice": self.voice,
            "content_hash": self.content_hash,
        }


# ── TTS Service ──

_TICKS_PER_SECOND = 10_000_000  # edge-tts uses 100ns ticks

_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
_MAX_CONCURRENT = 3  # match TypeScript p-limit(3)


class TTSService:
    """edge-tts based TTS with word-level timestamps.

    Args:
        output_dir: Directory for generated audio files.
        voice: edge-tts voice name.
        rate: Speech rate adjustment (-10 to +10).
        max_concurrent: Max parallel synthesis tasks.
    """

    def __init__(
        self,
        output_dir: Path,
        voice: str = _DEFAULT_VOICE,
        rate: int = 0,
        max_concurrent: int = _MAX_CONCURRENT,
    ):
        self.output_dir = Path(output_dir)
        self.voice = voice
        self.rate = rate
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._cache: dict[str, SegmentResult] = {}

    def segment_hash(self, text: str) -> str:
        """Deterministic content hash for a text segment."""
        return content_hash({"text": text, "voice": self.voice, "rate": self.rate})

    async def synthesize(self, text: str) -> SegmentResult:
        """Generate audio + word-level timestamps for a single text segment.

        Uses edge-tts WordBoundary API to get per-word timestamps natively.
        Results are cached by content hash.
        """
        seg_hash = self.segment_hash(text)
        if seg_hash in self._cache:
            return self._cache[seg_hash]

        async with self._semaphore:
            result = await self._synthesize_impl(text, seg_hash)

        self._cache[seg_hash] = result
        return result

    async def synthesize_segments(self, texts: list[str]) -> list[SegmentResult]:
        """Generate multiple segments concurrently (semaphore-limited)."""
        tasks = [self.synthesize(t) for t in texts]
        return await asyncio.gather(*tasks)

    async def _synthesize_impl(self, text: str, seg_hash: str) -> SegmentResult:
        """Internal: generate audio and extract word timings."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        audio_path = self.output_dir / f"{seg_hash}.mp3"

        rate_str = f"{self.rate:+d}%" if self.rate != 0 else "+0%"
        communicate = edge_tts.Communicate(
            text, voice=self.voice, rate=rate_str, boundary="WordBoundary",
        )

        # Stream audio + collect word boundary events
        word_events: list[dict[str, Any]] = []
        with open(audio_path, "wb") as f:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    word_events.append({
                        "text": chunk["text"],
                        "offset": chunk["offset"],    # ticks (100ns)
                        "duration": chunk["duration"], # ticks
                    })

        # Convert ticks to seconds
        word_timings = tuple(
            WordTiming(
                word=evt["text"],
                start=evt["offset"] / _TICKS_PER_SECOND,
                end=(evt["offset"] + evt["duration"]) / _TICKS_PER_SECOND,
            )
            for evt in word_events
        )

        # Measure actual audio duration
        duration = _measure_audio_duration(audio_path)

        return SegmentResult(
            text=text,
            audio_path=str(audio_path),
            duration=duration,
            word_timings=word_timings,
            voice=self.voice,
            content_hash=seg_hash,
        )

    def clear_cache(self):
        self._cache.clear()


def _measure_audio_duration(audio_path: Path) -> float:
    """Measure audio file duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(audio_path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError, FileNotFoundError):
        # Fallback: estimate from file size (very rough)
        return 0.0
