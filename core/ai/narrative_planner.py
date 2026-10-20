"""LLM Narrative Planner — Generate NarrativeIR from IntentIR.

The core AI component: takes a user intent and produces a structured
narrative with beats, pacing, and emotional arc.

    planner = NarrativePlanner(backend=OpenAIBackend(api_key="..."))
    narrative = planner.plan(IntentIR(topic="Redis为什么快？", ...))
    # → NarrativeIR(beats=[Hook("Redis为什么这么快？"), ...])

The planner is pluggable:
  - TemplateBackend: deterministic, no LLM (for testing)
  - OpenAIBackend: GPT-4, GPT-3.5
  - QwenBackend: Qwen via DashScope
  - CustomBackend: any function matching the signature

All backends must return structured JSON that maps to NarrativeIR.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ir.intent_ir import IntentIR, Tone, Platform
from ir.narrative_ir import (
    NarrativeIR, Beat, BeatType, TransitionType,
    HookBeat, ProblemBeat, RevealBeat, CTABeat,
)
from thinking.canonicalize import content_hash


@dataclass
class LLMConfig:
    """Configuration for LLM backend."""
    model: str = "gpt-4o-mini"
    temperature: float = 0.7
    max_tokens: int = 2000
    timeout: int = 30


class LLMBackend(ABC):
    """Abstract LLM backend interface.

    Subclasses implement `generate()` to call their specific LLM API.
    The return value must be a JSON string matching the NarrativeIR schema.
    """

    @abstractmethod
    def generate(self, prompt: str, config: LLMConfig) -> str:
        """Generate text from prompt. Returns JSON string."""
        ...


class TemplateBackend(LLMBackend):
    """Deterministic template backend (no LLM).

    Used for testing and as fallback when no LLM is available.
    Produces the same output for the same input — guaranteed deterministic.
    """

    def generate(self, prompt: str, config: LLMConfig) -> str:
        # Parse intent from prompt (prompt contains serialized intent)
        # This is a fallback — in practice, the planner calls this directly
        return ""  # Not used — planner has _template_fallback


# ── Prompt Templates ──

_SYSTEM_PROMPT = """你是一个专业的短视频脚本策划师。你需要根据用户提供的主题，生成一个结构化的视频脚本。

输出格式要求（严格 JSON）：
{
  "title": "视频标题",
  "subtitle": "副标题（可选）",
  "beats": [
    {
      "beat_type": "hook",
      "text": "开场文案",
      "relative_duration": 2.0,
      "emotional_intensity": 0.9,
      "key_point": "一句话概括这个beat的核心",
      "transition_after": "build"
    },
    {
      "beat_type": "problem",
      "text": "痛点描述",
      "relative_duration": 3.0,
      "emotional_intensity": 0.6,
      "transition_after": "cut"
    },
    {
      "beat_type": "reveal",
      "text": "核心揭示",
      "relative_duration": 4.0,
      "emotional_intensity": 0.9,
      "transition_after": "build"
    },
    {
      "beat_type": "cta",
      "text": "行动号召",
      "relative_duration": 1.0,
      "emotional_intensity": 0.4,
      "transition_after": "cut"
    }
  ],
  "pacing": "dynamic",
  "emotional_arc": "buildup"
}

规则：
1. 第一个 beat 必须是 hook（吸引注意力）
2. 最后一个 beat 推荐是 cta（行动号召）
3. 不要有连续的 transition beat
4. 文案要口语化、有节奏感
5. emotional_intensity 范围 0-1
6. relative_duration 是相对权重，不是绝对秒数
"""

_USER_PROMPT_TEMPLATE = """请为以下主题生成短视频脚本：

主题：{topic}
语气风格：{tone}
目标受众：{audience}
目标平台：{platform}
目标时长：{target_duration}秒
关键词：{keywords}

请直接输出 JSON，不要有其他文字。"""


def _build_prompt(intent: IntentIR) -> str:
    """Build LLM prompt from IntentIR."""
    return _USER_PROMPT_TEMPLATE.format(
        topic=intent.topic,
        tone=intent.tone.value,
        audience=intent.audience,
        platform=intent.platform.value,
        target_duration=intent.target_duration,
        keywords=", ".join(intent.keywords) if intent.keywords else "无",
    )


# ── JSON Parsing ──

_BEAT_TYPE_MAP = {
    "hook": BeatType.HOOK,
    "problem": BeatType.PROBLEM,
    "explanation": BeatType.EXPLANATION,
    "reveal": BeatType.REVEAL,
    "example": BeatType.EXAMPLE,
    "comparison": BeatType.COMPARISON,
    "cta": BeatType.CTA,
    "summary": BeatType.SUMMARY,
    "transition": BeatType.TRANSITION,
}

_TRANSITION_MAP = {
    "cut": TransitionType.CUT,
    "build": TransitionType.BUILD,
    "contrast": TransitionType.CONTRAST,
    "callback": TransitionType.CALLBACK,
    "question": TransitionType.QUESTION,
    "silence": TransitionType.SILENCE,
}


def _parse_narrative_json(data: dict[str, Any]) -> NarrativeIR:
    """Parse JSON dict into NarrativeIR."""
    beats_data = data.get("beats", [])
    if not beats_data:
        raise ValueError("beats must be non-empty")

    beats = []
    for bd in beats_data:
        beat_type_str = bd.get("beat_type", "hook")
        beat_type = _BEAT_TYPE_MAP.get(beat_type_str, BeatType.HOOK)

        transition_str = bd.get("transition_after", "cut")
        transition = _TRANSITION_MAP.get(transition_str, TransitionType.CUT)

        beats.append(Beat(
            beat_type=beat_type,
            text=bd.get("text", ""),
            relative_duration=float(bd.get("relative_duration", 1.0)),
            emotional_intensity=float(bd.get("emotional_intensity", 0.5)),
            key_point=bd.get("key_point", ""),
            transition_after=transition,
        ))

    return NarrativeIR(
        beats=tuple(beats),
        title=data.get("title", ""),
        subtitle=data.get("subtitle", ""),
        pacing=data.get("pacing", "normal"),
        emotional_arc=data.get("emotional_arc", "buildup"),
    )


def _extract_json(text: str) -> dict[str, Any]:
    """Extract JSON from LLM response (handles markdown code blocks)."""
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from code block
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding JSON object
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not extract JSON from LLM response: {text[:200]}")


# ── Planner ──

class NarrativePlanner:
    """Generate NarrativeIR from IntentIR using LLM.

    Usage:
        # With LLM
        planner = NarrativePlanner(backend=my_llm_backend)
        narrative = planner.plan(intent)

        # Template fallback (deterministic, no LLM)
        planner = NarrativePlanner()
        narrative = planner.plan(intent)

    The planner caches results by intent content hash — same intent
    always produces the same narrative (deterministic when using cache).
    """

    def __init__(
        self,
        backend: Optional[LLMBackend] = None,
        config: Optional[LLMConfig] = None,
        use_cache: bool = True,
    ):
        self.backend = backend
        self.config = config or LLMConfig()
        self.use_cache = use_cache
        self._cache: dict[str, NarrativeIR] = {}

    def plan(self, intent: IntentIR) -> NarrativeIR:
        """Generate NarrativeIR from IntentIR.

        If backend is set, uses LLM. Otherwise falls back to template.
        Results are cached by intent content hash.
        """
        cache_key = intent.content_hash()

        if self.use_cache and cache_key in self._cache:
            return self._cache[cache_key]

        if self.backend:
            narrative = self._plan_with_llm(intent)
        else:
            narrative = self._template_fallback(intent)

        if self.use_cache:
            self._cache[cache_key] = narrative

        return narrative

    def _plan_with_llm(self, intent: IntentIR) -> NarrativeIR:
        """Generate narrative using LLM backend."""
        prompt = _build_prompt(intent)

        try:
            response = self.backend.generate(prompt, self.config)
            data = _extract_json(response)
            return _parse_narrative_json(data)
        except Exception:
            # Fallback to template on any LLM failure
            return self._template_fallback(intent)

    def _template_fallback(self, intent: IntentIR) -> NarrativeIR:
        """Deterministic template fallback (no LLM)."""
        topic = intent.topic

        tone_templates = {
            Tone.DRAMATIC: {
                "hook_prefix": "你绝对想不到",
                "reveal_prefix": "真相竟然是",
                "cta": "关注，解锁更多硬核知识",
                "pacing": "dynamic",
                "hook_intensity": 0.9,
                "reveal_intensity": 1.0,
            },
            Tone.EDUCATIONAL: {
                "hook_prefix": "你知道吗",
                "reveal_prefix": "答案是",
                "cta": "点赞收藏，下次不迷路",
                "pacing": "normal",
                "hook_intensity": 0.6,
                "reveal_intensity": 0.7,
            },
            Tone.CASUAL: {
                "hook_prefix": "聊聊",
                "reveal_prefix": "其实就是",
                "cta": "关注一下呗",
                "pacing": "normal",
                "hook_intensity": 0.5,
                "reveal_intensity": 0.5,
            },
            Tone.INSPIRATIONAL: {
                "hook_prefix": "每个人都应该知道",
                "reveal_prefix": "真正的秘密是",
                "cta": "分享给需要的人",
                "pacing": "slow",
                "hook_intensity": 0.8,
                "reveal_intensity": 0.9,
            },
            Tone.HUMOROUS: {
                "hook_prefix": "笑死",
                "reveal_prefix": "万万没想到",
                "cta": "关注不迷路，迷路你找我",
                "pacing": "fast",
                "hook_intensity": 0.7,
                "reveal_intensity": 0.8,
            },
            Tone.PROFESSIONAL: {
                "hook_prefix": "深度解析",
                "reveal_prefix": "核心要点",
                "cta": "了解更多，欢迎关注",
                "pacing": "normal",
                "hook_intensity": 0.5,
                "reveal_intensity": 0.6,
            },
        }

        tmpl = tone_templates.get(intent.tone, tone_templates[Tone.EDUCATIONAL])

        beats = (
            HookBeat(
                text=f"{tmpl['hook_prefix']}，{topic}为什么这么重要？",
                duration=2.0,
                intensity=tmpl["hook_intensity"],
            ),
            ProblemBeat(
                text=f"很多人不了解{topic}，走了很多弯路",
                duration=3.0,
                intensity=0.6,
            ),
            RevealBeat(
                text=f"{tmpl['reveal_prefix']}，{topic}的核心原理其实很简单",
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
            title=f"{topic}深度解析",
            pacing=tmpl["pacing"],
            emotional_arc="buildup",
        )

    def clear_cache(self):
        self._cache.clear()
