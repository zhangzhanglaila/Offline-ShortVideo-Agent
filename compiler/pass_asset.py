"""Asset Compiler Pass — NarrativeIR/SceneIR → ASSET artifacts.

Searches for B-roll images, videos, and music that match each
narrative beat's theme and mood. Results are cached by content hash
so repeated searches with the same keywords return instantly.

    narrative = NarrativeIR(beats=[...])
    ↓ AssetPass
    asset_content = {
        "images": [AssetResult, ...],
        "videos": [AssetResult, ...],
        "music": [AssetResult, ...],
        "keywords": ["redis", "database", "memory"],
    }
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.ai.asset_retriever import AssetRetriever, AssetResult
from compiler.ir.narrative_ir import NarrativeIR, BeatType
from compiler.thinking.canonicalize import content_hash


# Keyword extraction from beat types
_BEAT_KEYWORDS: dict[BeatType, list[str]] = {
    BeatType.HOOK: ["attention", "dramatic", "eye-catching"],
    BeatType.PROBLEM: ["question", "confused", "challenge"],
    BeatType.EXPLANATION: ["diagram", "infographic", "process"],
    BeatType.REVEAL: ["reveal", "discovery", "eureka"],
    BeatType.EXAMPLE: ["example", "demo", "real-world"],
    BeatType.COMPARISON: ["comparison", "versus", "split-screen"],
    BeatType.CTA: ["subscribe", "follow", "call-to-action"],
    BeatType.SUMMARY: ["summary", "recap", "overview"],
    BeatType.TRANSITION: ["transition", "abstract", "motion"],
}


def _extract_keywords(narrative: NarrativeIR, topic: str) -> list[str]:
    """Extract search keywords from narrative beats."""
    keywords = {topic.lower()}
    for beat in narrative.beats:
        keywords.add(beat.beat_type.value)
        # Extract words from beat text (simple split)
        for word in beat.text.split():
            if len(word) > 2:
                keywords.add(word.lower())
    return list(keywords)


def _extract_mood(narrative: NarrativeIR) -> str:
    """Infer mood from emotional arc and intensity."""
    avg_intensity = sum(b.emotional_intensity for b in narrative.beats) / max(len(narrative.beats), 1)
    if avg_intensity > 0.7:
        return "dramatic"
    elif avg_intensity > 0.4:
        return "neutral"
    else:
        return "calm"


class AssetPass:
    """NarrativeIR → ASSET content (images, videos, music).

    Not a standard CompilerPass because the output is a plain dict,
    not a formal IR type, and the async search needs special handling.
    """

    name = "asset_pass"

    def __init__(
        self,
        retriever: AssetRetriever,
        images_per_beat: int = 3,
        videos_per_beat: int = 1,
        music_count: int = 2,
    ):
        self.retriever = retriever
        self.images_per_beat = images_per_beat
        self.videos_per_beat = videos_per_beat
        self.music_count = music_count

    def run(self, narrative: NarrativeIR, topic: str = "") -> dict[str, Any]:
        """Search for assets matching the narrative.

        Args:
            narrative: The NarrativeIR with beats to match.
            topic: The video topic (used as primary keyword).

        Returns:
            ASSET artifact content dict.
        """
        keywords = _extract_keywords(narrative, topic)
        mood = _extract_mood(narrative)

        # Run async search
        result = asyncio.run(self._search_all(keywords, mood))
        result["keywords"] = keywords
        result["mood"] = mood
        return result

    async def _search_all(self, keywords: list[str], mood: str) -> dict[str, Any]:
        """Search for all asset types concurrently."""
        images_task = self.retriever.search_images(keywords, count=self.images_per_beat)
        videos_task = self.retriever.search_videos(keywords, count=self.videos_per_beat)
        music_task = self.retriever.search_music(keywords + [mood], count=self.music_count)

        images, videos, music = await asyncio.gather(
            images_task, videos_task, music_task, return_exceptions=True,
        )

        return {
            "images": [r.to_dict() for r in images] if isinstance(images, list) else [],
            "videos": [r.to_dict() for r in videos] if isinstance(videos, list) else [],
            "music": [r.to_dict() for r in music] if isinstance(music, list) else [],
        }
