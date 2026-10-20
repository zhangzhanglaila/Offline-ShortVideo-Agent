"""Intent → Narrative Compiler Pass.

Transforms a user intent (topic, tone, audience) into a narrative
structure (ordered beats with pacing).

In production, this pass calls an LLM to generate beats.
For testing, a deterministic template-based fallback is provided.

    IntentIR(topic="Redis", tone="dramatic", audience="beginners")
    ↓
    NarrativeIR(beats=[
        HookBeat("Redis为什么这么快？"),
        ProblemBeat("传统数据库的瓶颈..."),
        RevealBeat("秘密在于内存+单线程"),
        CTABeat("关注学习更多"),
    ])
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from compiler.ir.intent_ir import IntentIR, Tone
from compiler.ir.narrative_ir import (
    NarrativeIR, Beat, BeatType, TransitionType,
    HookBeat, ProblemBeat, RevealBeat, CTABeat,
)
from compiler.base import CompilerPass


# ── Tone-to-template mapping ──

_TEMPLATES: dict[str, dict[str, Any]] = {
    "dramatic": {
        "pacing": "dynamic",
        "emotional_arc": "buildup",
        "hook_intensity": 0.9,
        "reveal_intensity": 1.0,
        "hook_prefix": "你绝对想不到",
        "reveal_prefix": "真相竟然是",
        "cta": "关注，解锁更多硬核知识",
    },
    "educational": {
        "pacing": "normal",
        "emotional_arc": "buildup",
        "hook_intensity": 0.6,
        "reveal_intensity": 0.7,
        "hook_prefix": "你知道吗",
        "reveal_prefix": "答案是",
        "cta": "点赞收藏，下次不迷路",
    },
    "casual": {
        "pacing": "normal",
        "emotional_arc": "flat",
        "hook_intensity": 0.5,
        "reveal_intensity": 0.5,
        "hook_prefix": "聊聊",
        "reveal_prefix": "其实就是",
        "cta": "关注一下呗",
    },
    "inspirational": {
        "pacing": "slow",
        "emotional_arc": "wave",
        "hook_intensity": 0.8,
        "reveal_intensity": 0.9,
        "hook_prefix": "每个人都应该知道",
        "reveal_prefix": "真正的秘密是",
        "cta": "分享给需要的人",
    },
    "humorous": {
        "pacing": "fast",
        "emotional_arc": "surprise",
        "hook_intensity": 0.7,
        "reveal_intensity": 0.8,
        "hook_prefix": "笑死",
        "reveal_prefix": "万万没想到",
        "cta": "关注不迷路，迷路你找我",
    },
    "professional": {
        "pacing": "normal",
        "emotional_arc": "flat",
        "hook_intensity": 0.5,
        "reveal_intensity": 0.6,
        "hook_prefix": "深度解析",
        "reveal_prefix": "核心要点",
        "cta": "了解更多，欢迎关注",
    },
}


def _template_narrative(intent: IntentIR) -> NarrativeIR:
    """Deterministic template-based narrative generation.

    Produces a standard 4-beat structure: Hook → Problem → Reveal → CTA.
    The text is derived from the intent's topic and tone, making it
    deterministic for the same input.
    """
    tmpl = _TEMPLATES.get(intent.tone.value, _TEMPLATES["educational"])

    beats = (
        HookBeat(
            text=f"{tmpl['hook_prefix']}，{intent.topic}为什么这么重要？",
            duration=2.0,
            intensity=tmpl["hook_intensity"],
        ),
        ProblemBeat(
            text=f"很多人不了解{intent.topic}，走了很多弯路",
            duration=3.0,
            intensity=0.6,
        ),
        RevealBeat(
            text=f"{tmpl['reveal_prefix']}，{intent.topic}的核心原理其实很简单",
            duration=4.0,
            intensity=tmpl["reveal_intensity"],
        ),
        CTABeat(
            text=tmpl["cta"],
            duration=1.0,
            intensity=0.4,
        ),
    )

    return NarrativeIR(
        beats=beats,
        title=f"{intent.topic}深度解析",
        pacing=tmpl["pacing"],
        emotional_arc=tmpl["emotional_arc"],
    )


class IntentToNarrativePass(CompilerPass[IntentIR, NarrativeIR]):
    """Transform IntentIR → NarrativeIR.

    Uses a pluggable generation function. The default is a deterministic
    template. Override with an LLM-based function for production.

    Usage:
        # Template-based (deterministic, for testing)
        pass_ = IntentToNarrativePass()
        result = pass_.run(intent_ir)

        # LLM-based (for production)
        pass_ = IntentToNarrativePass(generate_fn=my_llm_fn)
        result = pass_.run(intent_ir)
    """

    name = "intent_to_narrative"

    def __init__(
        self,
        generate_fn: Callable[[IntentIR], NarrativeIR] | None = None,
    ):
        self.generate_fn = generate_fn or _template_narrative

    def transform(self, intent: IntentIR) -> NarrativeIR:
        return self.generate_fn(intent)
