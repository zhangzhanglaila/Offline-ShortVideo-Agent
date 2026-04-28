"""
render.py — Narrative OS Pipeline
Events → Narrative Kernel → Compiled IR → Content Binding → Shot Curves

WARNING: build_director() output is only valid as input to bridge.py (Python→Remotion).
Direct PIL/ffmpeg rendering via engine.renderer is DEPRECATED.
Legacy video_renderer.py is retained only for rollback safety.

Usage:
    python -c "from engine.bridge import build_director_json; print(build_director_json(trace))"
"""

import json
import sys
import math
import re
import argparse
from pathlib import Path

# Add project root to path so engine/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Engine imports
from engine.narrative import SceneResolver, ModeResolver, IntentQueue, Intent
from engine.compiler import compile_narrative_instruction, compile_content_binding, compile_audio_params
from engine.renderer import compile_shot_curve, evaluate_shot_curve


# ─────────────────────────────────────────────────────────────────────────────
# Constants (mirrored from original JS)
# ─────────────────────────────────────────────────────────────────────────────

RHYTHM = {
    'MIN_HOLD':    0.018,
    'MIN_GAP':     0.004,
    'TOTAL_DUR':   12000,
    'ACCENT_DURATION_MULT': 1.35,
    'MIN_SEG_DURATION': 0.008,
}

DIRECTOR_PLAN = {
    'CLAIMED':    lambda ctx: (['claimed'] if not ((ctx.get('nextSameType') or {}).get('type') == 'COMPLETED') else ['absorb']),
    'UNBLOCKED':  lambda ctx: ['unblocked', 'accent-beat'],
    'COMPLETED':  lambda ctx: (['absorb'] if (ctx.get('nextSameType') or {}).get('type') == 'COMPLETED' else ['completed']),
    'FAILED':     lambda ctx: ['failed'],
    'RETRY':      lambda ctx: (['retry', 'retry-storm', 'absorb'] if ctx.get('isRetryStorm') else ['retry']),
    'POISON_PILL':lambda ctx: ['poison-pill', 'accent-beat', 'cascade-start'],
    'HEARTBEAT':  lambda ctx: ['absorb'],
    'CREATED':    lambda ctx: ['absorb'],
    'PROGRESS':   lambda ctx: ['absorb'],
}


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1 — Semantic Context Builder
# ─────────────────────────────────────────────────────────────────────────────

def build_ctx(events, i, start_ts, end_ts, job_deps):
    ev    = events[i]
    prev  = events[i - 1] if i > 0 else None
    next_ev = events[i + 1] if i < len(events) - 1 else None

    # Parallel burst detection
    norm = lambda ts: (ts - start_ts) / (end_ts - start_ts or 1)
    THR  = 0.03

    pS = i
    while pS > 0 and norm(ev['ts']) - norm(events[pS - 1]['ts']) < THR and events[pS - 1]['type'] == ev['type']:
        pS -= 1
    pE = i
    while pE < len(events) - 1 and norm(events[pE + 1]['ts']) - norm(ev['ts']) < THR and events[pE + 1]['type'] == ev['type']:
        pE += 1
    parallel_size = pE - pS + 1
    is_montage    = parallel_size > 1

    # Same-type neighbours
    prev_same = next_same = None
    for k in range(i - 1, -1, -1):
        if events[k]['type'] == ev['type']:
            prev_same = events[k]
            break
    for k in range(i + 1, len(events)):
        if events[k]['type'] == ev['type']:
            next_same = events[k]
            break

    # Retry storm
    retry_run = sum(1 for k in range(i, -1, -1) if events[k]['type'] == 'RETRY')

    # Cascade
    fail_cascade = sum(1 for k in range(i, -1, -1) if events[k]['type'] in ('FAILED', 'POISON_PILL'))

    # Past claimed
    past_claimed = sum(1 for k in range(i) if events[k]['type'] == 'CLAIMED')

    norm_here = norm(ev['ts'])
    gap_prev  = norm_here - norm(prev['ts']) if prev else 1.0
    gap_next  = norm(next_ev['ts']) - norm_here if next_ev else 1.0

    deps = job_deps.get(ev.get('jobId') or ev.get('job_id'), [])

    return {
        'index': i,
        'totalEvents': len(events),
        'prevEvent': prev,
        'nextEvent': next_ev,
        'prevSameType': prev_same,
        'nextSameType': next_same,
        'isMontage': is_montage,
        'isFirstInMontage': is_montage and pS == i,
        'isLastInMontage':  is_montage and pE == i,
        'parallelBurstSize': parallel_size,
        'isRetryStorm': retry_run >= 3,
        'isCascade': fail_cascade >= 2,
        'pastClaimed': past_claimed,
        'gapPrev': gap_prev,
        'gapNext': gap_next,
        'jobDegree': len(deps),
        'isOnCriticalPath': len(deps) <= 1,
        'isFirstEvent': i == 0,
        'isLastEvent': i == len(events) - 1,
        'prevType': prev['type'] if prev else None,
        'nextType': next_ev['type'] if next_ev else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2 — Rhythm Resolver
# ─────────────────────────────────────────────────────────────────────────────

def resolve_rhythm(tags, ctx, mode, scene):
    """Returns {hold, merge, accent}."""
    if   mode == 'chaos':  return {'hold': 0.012, 'merge': False, 'accent': True}
    elif mode == 'burst':   return {'hold': 0.018, 'merge': True,  'accent': True}
    elif mode == 'focus':   return {'hold': 0.060, 'merge': False, 'accent': True}
    elif mode == 'linger':  return {'hold': 0.100, 'merge': False, 'accent': False}

    if   'claimed' in tags and 'montage-first' in tags: return {'hold': 0.050, 'accent': True}
    elif 'claimed' in tags and 'montage' in tags:        return {'hold': 0.025, 'accent': True}
    elif 'claimed' in tags and 'critical' in tags:       return {'hold': 0.055, 'accent': True}
    elif 'claimed' in tags:                                return {'hold': 0.040, 'accent': False}
    elif 'unblocked' in tags:                             return {'hold': 0.050, 'accent': True}
    elif 'completed' in tags:                              return {'hold': 0.030, 'accent': False}
    elif 'failed' in tags and 'cascade' in tags:         return {'hold': 0.060, 'accent': True}
    elif 'failed' in tags:                                return {'hold': 0.050, 'accent': False}
    elif 'retry' in tags:                                  return {'hold': 0.025, 'accent': False}
    elif 'poison-pill' in tags:                            return {'hold': 0.060, 'accent': True}
    elif 'absorb' in tags:                                 return {'hold': 0.000, 'merge': True}
    return {'hold': 0.030, 'merge': False, 'accent': False}


def resolve_emphasis(tags, ctx, mode):
    if 'claimed' in tags or 'poison-pill' in tags: return 'strong'
    if 'unblocked' in tags or 'completed' in tags or 'failed' in tags: return 'medium'
    if 'retry' in tags: return 'weak'
    return 'none'


def _read_semantic_directives(ev):
    semantic_block = ev.get('semantics', {}) if isinstance(ev.get('semantics'), dict) else {}

    def _pick(key, default=None):
        return semantic_block.get(key, ev.get(key, default))

    return {
        'intent': _pick('intent'),
        'emotion': _pick('emotion'),
        'rhythm': _pick('rhythm'),
        'focus': _pick('focus'),
        'motionProfile': _pick('motionProfile'),
        'energy': _pick('energy'),
    }


def resolve_segment_semantics(ev, tags, ctx, scene, mode, timing, emphasis, cam, vis):
    explicit = _read_semantic_directives(ev)

    if explicit['intent']:
        intent = explicit['intent']
    elif scene == 'release' or 'completed' in tags:
        intent = 'release'
    elif scene == 'climax' or mode in {'chaos', 'burst'} or 'failed' in tags or 'poison-pill' in tags:
        intent = 'impact'
    elif scene == 'focus-arc' or mode == 'focus' or 'unblocked' in tags:
        intent = 'reveal'
    elif scene == 'buildup' or mode == 'buildup' or 'claimed' in tags:
        intent = 'approach'
    elif mode == 'linger':
        intent = 'linger'
    else:
        intent = 'steady'

    if explicit['emotion']:
        emotion = explicit['emotion']
    elif mode == 'chaos':
        emotion = 'tension'
    elif scene == 'climax' or mode == 'burst':
        emotion = 'excited'
    elif scene in {'buildup', 'focus-arc'} or emphasis == 'strong':
        emotion = 'anticipation'
    elif scene == 'release' or mode == 'linger':
        emotion = 'calm'
    else:
        emotion = 'neutral'

    if explicit['rhythm']:
        semantic_rhythm = explicit['rhythm']
    elif timing.get('accent'):
        semantic_rhythm = 'accent'
    elif mode in {'chaos', 'burst'} or vis.get('pulse'):
        semantic_rhythm = 'pulse'
    elif mode == 'linger':
        semantic_rhythm = 'linger'
    else:
        semantic_rhythm = 'flow'

    if explicit['focus']:
        focus = explicit['focus']
    elif cam.get('zoom', 1.0) >= 1.35 or intent in {'impact', 'reveal'}:
        focus = 'subject'
    elif scene == 'release':
        focus = 'environment'
    else:
        focus = 'wide'

    if explicit['motionProfile']:
        motion_profile = explicit['motionProfile']
    elif cam.get('cutSnap') or mode in {'chaos', 'burst'}:
        motion_profile = 'snap'
    elif mode == 'linger':
        motion_profile = 'drift'
    else:
        motion_profile = 'glide'

    if explicit['energy'] is not None:
        try:
            energy = max(0.2, min(1.0, float(explicit['energy'])))
        except (TypeError, ValueError):
            energy = 0.5
    else:
        energy = 0.45
        if emotion == 'tension':
            energy = 0.95
        elif emotion == 'excited':
            energy = 0.84
        elif emotion == 'anticipation':
            energy = 0.68
        elif emotion == 'calm':
            energy = 0.30
        if timing.get('accent'):
            energy = min(1.0, energy + 0.08)

    return {
        'intent': intent,
        'emotion': emotion,
        'rhythm': semantic_rhythm,
        'focus': focus,
        'motionProfile': motion_profile,
        'energy': energy,
        'source': 'explicit' if explicit['intent'] or explicit['emotion'] or explicit['rhythm'] or explicit['focus'] else 'fallback',
    }


def generate_semantic_segments(text: str) -> list[dict]:
    """
    Minimal director-input helper.
    Converts free text into explicit semantic beats that upstream systems can inject.
    """
    beats = [
        chunk.strip()
        for chunk in re.split(r'[。！？!?;\n]+', text)
        if chunk and chunk.strip()
    ]
    if not beats and text.strip():
        beats = [text.strip()]

    semantic_segments: list[dict] = []
    total = max(len(beats), 1)

    for index, beat in enumerate(beats):
        lowered = beat.lower()

        if re.search(r'fail|error|crash|爆|炸|失败|警告|危险', lowered):
            intent, emotion = 'impact', 'tension'
        elif re.search(r'reveal|show|发现|看到|揭示|原来', lowered):
            intent, emotion = 'reveal', 'anticipation'
        elif re.search(r'release|done|finish|完成|解决|落地', lowered):
            intent, emotion = 'release', 'calm'
        elif index == 0:
            intent, emotion = 'approach', 'anticipation'
        elif index == total - 1:
            intent, emotion = 'release', 'calm'
        else:
            intent, emotion = 'steady', 'neutral'

        rhythm = 'accent' if len(beat) <= 12 or intent == 'impact' else 'flow'
        focus = 'subject' if intent in {'impact', 'reveal', 'approach'} else 'wide'
        duration = round(max(1.0, min(3.5, len(beat) * 0.18)), 2)

        semantic_segments.append({
            'text': beat,
            'intent': intent,
            'emotion': emotion,
            'rhythm': rhythm,
            'focus': focus,
            'duration': duration,
        })

    return semantic_segments


def _split_spoken_lines(text: str) -> list[str]:
    lines = [
        chunk.strip()
        for chunk in re.split(r'[。！？!?;\n]+', text)
        if chunk and chunk.strip()
    ]
    return lines


def _is_bad_script_line(text: str) -> bool:
    lowered = (text or "").lower()
    return (
        not lowered
        or "{'error'" in lowered
        or '"error"' in lowered
        or "ollama连接失败" in lowered
        or "云端api调用失败" in lowered
        or "httpsconnectionpool" in lowered
        or "max retries exceeded" in lowered
        or "'script':" in lowered
        or '"script":' in lowered
        or lowered.startswith("'))")
    )


def _is_explainer_question_v2(question: str) -> bool:
    lowered = (question or "").lower()
    return (
        "redis" in lowered
        or any(token in question for token in ["是什么", "什么是", "原理", "底层", "为什么", "怎么实现"])
    )


def _build_explainer_lines_v2(question: str) -> list[str]:
    subject = re.sub(r"(是什么|什么是|底层原理|原理|为什么|怎么实现|\?|？)", "", question).strip() or question.strip()
    if "redis" in question.lower():
        return [
            "Redis本质上是一个把数据放在内存里的高性能键值数据库。",
            "它之所以快，核心在于内存访问、事件驱动，以及尽量减少不必要的拷贝和阻塞。",
            "不同的数据类型，底层会组合使用哈希表、压缩结构、跳表和双端链表来存储。",
            "所以理解Redis底层原理，重点不是背命令，而是看它怎样组织数据和处理读写。",
        ]

    return [
        f"{subject}这类问题，本质上是在回答它到底是什么，以及为什么会这样工作。",
        f"如果往底层拆，通常要先看{subject}的数据结构，再看它的执行流程和资源模型。",
        f"真正理解{subject}，不是只记结论，而是知道它为什么这样设计。",
    ]


def _looks_like_marketing_line(text: str) -> bool:
    lowered = (text or "").lower()
    marketing_markers = [
        "学会这个",
        "你也可以做到",
        "点赞",
        "点个赞",
        "还不会",
        "必须知道",
        "别再",
        "看完你就懂",
        "赶紧收藏",
        "关注我",
    ]
    return any(marker in lowered for marker in marketing_markers)


def _is_explainer_question(question: str) -> bool:
    return any(token in question for token in ["是什么", "什么是", "原理", "底层", "为什么", "怎么实现"])


def _build_explainer_lines(question: str) -> list[str]:
    subject = re.sub(r"(是什么|什么是|底层原理|原理|为什么|怎么实现|\?|？)", "", question).strip() or question.strip()
    if "redis" in question.lower():
        return [
            "Redis本质上是一个把数据放在内存里的高性能键值数据库。",
            "它之所以快，核心是内存访问、事件驱动和尽量减少不必要的拷贝。",
            "不同的数据类型，在底层会组合使用哈希表、压缩结构和跳表来存储。",
            "所以理解Redis底层原理，重点不是背命令，而是看它怎样组织数据和处理读写。",
        ]

    return [
        f"{subject}本质上是在回答，它到底是什么，以及为什么会这样工作。",
        f"如果往底层拆，通常要先看{subject}的数据结构，再看它的执行流程。",
        f"真正理解{subject}，不是只记结论，而是知道它为什么这样设计。",
    ]


def generate_spoken_semantic_segments(
    question: str,
    platform: str = "抖音",
    video_duration: int = 12,
    style: str = "专业",
) -> list[dict]:
    """
    User question -> LLM script -> spoken semantic segments.
    Falls back to rule-based semantic splitting when LLM is unavailable.
    """
    question = (question or "").strip()
    if not question:
        return []

    lines: list[str] = []
    try:
        from core.script_module import ScriptModule

        script_module = ScriptModule()
        topic = {
            "title": question,
            "hook": question,
            "category": "知识科普",
            "tags": ["AI", "Agent", "解释"],
        }
        script_result = script_module.generate_script(
            topic,
            platform=platform,
            video_duration=video_duration,
            style=style,
        )

        hook = (script_result.get("hook") or "").strip()
        body = (script_result.get("body") or "").strip()
        cta = (script_result.get("cta") or "").strip()
        if hook:
            lines.append(hook)
        lines.extend(_split_spoken_lines(body))
        if cta:
            lines.append(cta)
    except Exception:
        lines = []

    if not lines:
        lines = _split_spoken_lines(question)

    lines = [line for line in lines if not _is_bad_script_line(line)]
    if _is_explainer_question_v2(question) and (
        not lines or any(_looks_like_marketing_line(line) for line in lines)
    ):
        lines = _build_explainer_lines_v2(question)

    if not lines:
        lines = [question]

    semantic_seed = "。".join(lines)
    semantic_segments = generate_semantic_segments(semantic_seed)
    if not semantic_segments:
        semantic_segments = [{"text": line, "intent": "steady", "emotion": "neutral", "rhythm": "flow", "focus": "subject", "duration": 2.0} for line in lines]

    result: list[dict] = []
    total = max(len(lines), 1)
    for index, line in enumerate(lines):
        semantic = semantic_segments[min(index, len(semantic_segments) - 1)]
        start = index / total
        end = (index + 1) / total
        result.append({
            "text": line,
            "intent": semantic.get("intent", "steady"),
            "emotion": semantic.get("emotion", "neutral"),
            "rhythm": semantic.get("rhythm", "flow"),
            "focus": semantic.get("focus", "subject"),
            "motionProfile": semantic.get("motionProfile", "glide"),
            "energy": semantic.get("energy", 0.5),
            "start": round(start, 4),
            "end": round(end, 4),
            "type": "NARRATION",
            "contentBinding": {
                "caption": line,
                "genPrompt": question,
            },
        })

    return result


# ─────────────────────────────────────────────────────────────────────────────
# Layer 3 — Change Arbiter
# ─────────────────────────────────────────────────────────────────────────────

def resolve_visual(tags, ctx, mode):
    """Returns {cam: {}, flash: {}, vis: {}} — mirrors original JS resolveVisual."""
    cam = {'zoom': 1.0, 'move': False, 'cutSnap': False, 'duration': 0}
    fl  = {'color': None, 'duration': 0}
    vis = {'glow': False, 'edgeColor': None, 'edgeWidth': None, 'labelDelay': 0, 'shake': False, 'pulse': False}

    if mode == 'chaos':
        cam.update({'zoom': 1.6, 'move': True, 'cutSnap': True, 'duration': 60})
        fl.update({'color': '#6b5000', 'duration': 50})
        vis.update({'shake': True, 'labelDelay': 0.90})
        return {'cam': cam, 'fl': fl, 'vis': vis}
    elif mode == 'burst':
        cam.update({'zoom': 0.75, 'move': True, 'cutSnap': True, 'duration': 100})
        fl.update({'color': '#0088cc', 'duration': 70})
        vis.update({'glow': True, 'labelDelay': 0.65})
        return {'cam': cam, 'fl': fl, 'vis': vis}
    elif mode == 'focus':
        cam.update({'zoom': 1.6, 'move': True, 'cutSnap': False, 'duration': 400})
        fl.update({'color': '#0088cc', 'duration': 100})
        vis.update({'glow': True, 'labelDelay': 0.60})
        return {'cam': cam, 'fl': fl, 'vis': vis}
    elif mode == 'linger':
        cam.update({'zoom': 1.0, 'move': False, 'cutSnap': False, 'duration': 0})
        fl.update({'color': '#004488', 'duration': 40})
        vis.update({'glow': False, 'labelDelay': 0.80})
        return {'cam': cam, 'fl': fl, 'vis': vis}

    # Tag-based fallthrough
    burst_zoom  = 0.85 if ctx['parallelBurstSize'] > 3 else (1.0 if ctx['parallelBurstSize'] > 1 else 1.15)
    degree_zoom = 0.80 if ctx['jobDegree'] > 4 else (1.0 if ctx['jobDegree'] > 2 else 1.10)
    gap_zoom    = 1.30 if ctx['gapPrev'] < 0.01 else (0.95 if ctx['gapPrev'] > 0.3 else 1.0)

    if 'claimed' in tags and 'montage-first' in tags:
        cam.update({'zoom': 0.75 * burst_zoom, 'move': True, 'cutSnap': True, 'duration': 400})
        fl.update({'color': '#0088cc', 'duration': 80})
        vis.update({'glow': True, 'labelDelay': 0.75})
    elif 'claimed' in tags and 'montage' in tags:
        cam.update({'zoom': 1.5 * degree_zoom * burst_zoom, 'move': True, 'cutSnap': True, 'duration': 80})
        fl.update({'color': '#0088cc', 'duration': 60})
        vis.update({'glow': True, 'labelDelay': 0.75})
    elif 'claimed' in tags and 'critical' in tags:
        cam.update({'zoom': 1.5 * gap_zoom, 'move': True, 'cutSnap': False, 'duration': 350})
        fl.update({'color': '#0088cc', 'duration': 90})
        vis.update({'glow': True, 'labelDelay': 0.70})
    elif 'claimed' in tags:
        cam.update({'zoom': 1.4 * degree_zoom, 'move': True, 'cutSnap': False, 'duration': 320})
        fl.update({'color': '#0088cc', 'duration': 80})
        vis.update({'glow': True, 'labelDelay': 0.75})
    elif 'unblocked' in tags:
        cam.update({'zoom': 1.1 * burst_zoom * gap_zoom, 'move': True, 'cutSnap': True, 'duration': 380})
        fl.update({'color': '#6b5000', 'duration': 80})
        vis.update({'glow': True, 'edgeColor': '#f0c040', 'edgeWidth': 2.5, 'labelDelay': 0.70})
    elif 'completed' in tags:
        fl.update({'color': '#00aa44', 'duration': 60})
        vis.update({'glow': True, 'edgeColor': '#00aa44', 'edgeWidth': 2.0, 'labelDelay': 0.70})
    elif 'failed' in tags and 'cascade' in tags:
        cam.update({'zoom': 1.3 * burst_zoom, 'move': True, 'cutSnap': False, 'duration': 400})
        fl.update({'color': '#aa0033', 'duration': 130})
        vis.update({'glow': True, 'edgeColor': '#aa0033', 'edgeWidth': 2.5, 'labelDelay': 0.60})
    elif 'failed' in tags:
        cam.update({'zoom': 1.2 * gap_zoom, 'move': True, 'cutSnap': False, 'duration': 350})
        fl.update({'color': '#aa0033', 'duration': 100})
        vis.update({'glow': True, 'edgeColor': '#aa0033', 'edgeWidth': 2.0, 'labelDelay': 0.65})
    elif 'retry' in tags and not 'retry-storm' in tags:
        fl.update({'color': '#6b5000', 'duration': 80})
        vis.update({'shake': True, 'labelDelay': 0.70})
    elif 'retry-storm' in tags:
        fl.update({'color': '#6b5000', 'duration': 60})
        vis.update({'shake': True, 'labelDelay': 0.80})
    elif 'poison-pill' in tags:
        cam.update({'zoom': 1.1 * burst_zoom, 'move': True, 'cutSnap': True, 'duration': 380})
        fl.update({'color': '#ff0033', 'duration': 120})
        vis.update({'glow': True, 'edgeColor': '#ff0033', 'edgeWidth': 2.5, 'labelDelay': 0.50})

    return {'cam': cam, 'fl': fl, 'vis': vis}


# ─────────────────────────────────────────────────────────────────────────────
# Scene transitions map
# ─────────────────────────────────────────────────────────────────────────────

SCENE_TRANSITIONS = {
    'intro→buildup':    'zoom-in',
    'buildup→climax':   'flash-cut',
    'climax→release':   'silence-hold',
    'focus-arc→climax': 'tighten',
    'release→idle':     'fade-out',
    'release→buildup':  'cut',
}


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def build_director(trace):
    """
    Full Narrative OS pipeline: events → segments → compiled IRs → shot curves.
    Returns list of fully-populated segment dicts ready for rendering.
    """
    events  = trace['events']
    start_ts = trace['startTs']
    end_ts   = trace['endTs']
    jobs    = trace.get('jobs', [])

    if not events:
        return []

    job_deps = {j['id']: j.get('dependsOn', []) for j in jobs}

    raw_segs = []
    scene_resolver = SceneResolver()
    mode_resolver = ModeResolver()
    intent_queue  = IntentQueue()

    prev_scene = None
    prev_mode  = 'normal'
    prev_hold = 0.03

    for i, ev in enumerate(events):
        fn = DIRECTOR_PLAN.get(ev['type'])
        if not fn:
            continue

        ctx = build_ctx(events, i, start_ts, end_ts, job_deps)
        tags = fn(ctx)
        if 'absorb' in tags:
            continue

        start   = (ev['ts'] - start_ts) / (end_ts - start_ts or 1)
        next_ts = events[i + 1]['ts'] if i < len(events) - 1 else ev['ts'] + 300
        end     = (next_ts - start_ts) / (end_ts - start_ts or 1)

        # Scene resolver
        scene_result  = scene_resolver.resolve(tags, ctx, ev['ts'])
        scene         = scene_result.scene
        from_scene    = scene_result.from_scene

        if scene_result.epoch != intent_queue.scene_epoch:
            intent_queue.advance_epoch()

        scene_transition = (SCENE_TRANSITIONS.get(f'{from_scene}→{scene}') if from_scene else None)

        # Mode resolver
        mode_result   = mode_resolver.resolve(tags, ctx, scene, ev['ts'])
        mode          = mode_result.mode
        transition    = mode_result.transition
        from_mode     = mode_result.from_mode

        scene_changed = from_scene is not None
        mode_changed  = from_mode  is not None

        # Change Arbiter
        committed_mode = mode
        committed_trans = transition
        if scene_changed:
            committed_mode = prev_mode
            committed_trans = None
            if mode_changed:
                intent_queue.push(Intent(
                    mode=mode,
                    transition=transition,
                    scene_at_capture=scene,
                    scene_epoch_id=intent_queue.scene_epoch,
                    created_at=ev['ts'],
                ))
        elif len(intent_queue) > 0:
            released = intent_queue.try_release(scene, intent_queue.scene_epoch, ev['ts'])
            if released:
                committed_mode   = released.mode
                committed_trans  = released.transition
            else:
                if len(intent_queue) == 0:
                    committed_mode   = mode
                    committed_trans  = (transition if mode != prev_mode else None)
        else:
            committed_trans = (transition if mode != prev_mode else None)

        # Rhythm
        rhythm = resolve_rhythm(tags, ctx, committed_mode, scene)
        if not scene_changed and not mode_changed and prev_hold:
            if abs(rhythm['hold'] - prev_hold) / prev_hold < 0.15:
                rhythm = {**rhythm, 'hold': prev_hold}

        # Visual
        vis_result = resolve_visual(tags, ctx, committed_mode)
        cam = vis_result['cam']
        fl  = vis_result['fl']
        vis = vis_result['vis']
        emphasis = resolve_emphasis(tags, ctx, committed_mode)
        semantics = resolve_segment_semantics(
            ev, tags, ctx, scene, committed_mode, rhythm, emphasis, cam, vis
        )

        # Narrative Compiler: compile segment → renderIR
        seg_for_compile = {
            'scene': scene,
            'mode':  committed_mode,
            'intent': semantics['intent'],
            'emotion': semantics['emotion'],
            'rhythm': semantics['rhythm'],
            'focus': semantics['focus'],
            'motionProfile': semantics['motionProfile'],
            'energy': semantics['energy'],
            'transition': committed_trans,
            'camZoom':   cam['zoom'],
            'camMove':   cam['move'],
            'camCutSnap': cam['cutSnap'],
            'camDur':    cam['duration'],
            'flashColor': fl['color'],
            'flashDur':  fl['duration'],
            'timing':    rhythm,
            'emphasis':   emphasis,
            'tags':       tags,
            'glow':       vis['glow'],
            'shake':      vis['shake'],
            'edgeColor':  vis['edgeColor'],
            'edgeWidth':  vis['edgeWidth'],
            'duration':   max(end - start, rhythm['hold'] or RHYTHM['MIN_SEG_DURATION']),
        }
        render_ir = compile_narrative_instruction(seg_for_compile)

        # Content Binding + Audio Compiler
        content_binding = compile_content_binding(render_ir.__dict__, seg_for_compile)
        audio_params    = compile_audio_params(render_ir.__dict__)

        # Timeline Renderer: compile shot curve
        total_ms = max(end - start, rhythm['hold'] or 0) * RHYTHM['TOTAL_DUR']
        shot_curve = compile_shot_curve(render_ir.__dict__, total_ms)

        duration = max(end - start, rhythm['hold'] or RHYTHM['MIN_SEG_DURATION'])
        if rhythm.get('accent'):
            duration *= RHYTHM['ACCENT_DURATION_MULT']

        raw_segs.append({
            **ev,
            'ctx':            ctx,
            'tags':           tags,
            'semantics':      semantics,
            'intent':         semantics['intent'],
            'emotion':        semantics['emotion'],
            'rhythm':         semantics['rhythm'],
            'focus':          semantics['focus'],
            'motionProfile':  semantics['motionProfile'],
            'energy':         semantics['energy'],
            'semanticSource': semantics['source'],
            'emphasis':       emphasis,
            'scene':          scene,
            'fromScene':      from_scene,
            'sceneTransition': scene_transition,
            'mode':           committed_mode,
            'transition':     committed_trans,
            'start':          start,
            'end':            end,
            'duration':       duration,
            'origDuration':   end - start,
            'camZoom':        cam['zoom'],
            'camMove':        cam['move'],
            'camCutSnap':     cam['cutSnap'],
            'camDur':         cam['duration'],
            'flashColor':     fl['color'],
            'flashDur':       fl['duration'],
            'glow':           vis['glow'],
            'edgeColor':      vis['edgeColor'],
            'edgeWidth':      vis['edgeWidth'],
            'labelDelay':     vis['labelDelay'],
            'shake':          vis['shake'],
            'pulse':          vis['pulse'],
            'merge':          rhythm.get('merge', False),
            'accent':         rhythm.get('accent', False),
            'merged':         False,
            'renderIR':       render_ir.__dict__,
            'contentBinding': content_binding.__dict__,
            'audioParams':    audio_params.__dict__,
            'shotCurve':      shot_curve,  # ShotCurve object
        })

        prev_scene = scene
        prev_mode  = committed_mode
        prev_hold  = rhythm['hold']

    if not raw_segs:
        return []

    # ── Pass 2: MERGE ─────────────────────────────────────────────────────────
    def clamp(v, lo, hi):
        return max(lo, min(hi, v))

    merged = []
    i = 0
    while i < len(raw_segs):
        seg = raw_segs[i]
        if seg['merge'] and i + 1 < len(raw_segs):
            next_seg = raw_segs[i + 1]
            next_seg['start'] = clamp(seg['start'], 0, 1)
            next_seg['duration'] = clamp(next_seg['end'] - next_seg['start'], RHYTHM['MIN_SEG_DURATION'], 1)
            if not next_seg.get('stateEffects'):
                next_seg['stateEffects'] = []
            next_seg['stateEffects'].append({'jobId': seg['jobId'], 'type': seg['type']})
            seg['merged'] = True
            i += 1
            continue

        j = i + 1
        while j < len(raw_segs) and raw_segs[j]['merged']:
            j += 1
        if j > i + 1:
            last = raw_segs[j - 1]
            seg['end']     = clamp(last['end'], seg['start'], 1)
            seg['duration'] = clamp(seg['end'] - seg['start'], RHYTHM['MIN_SEG_DURATION'], 1)

        merged.append(seg)
        i += 1

    # ── Pass 3: MIN_HOLD stretch ────────────────────────────────────────────────
    for seg in merged:
        if seg['duration'] < RHYTHM['MIN_HOLD']:
            pad = (RHYTHM['MIN_HOLD'] - seg['duration']) / 2
            seg['start'] = max(0, seg['start'] - pad)
            seg['end']   = min(1, seg['end']   + pad)
            seg['duration'] = seg['end'] - seg['start']

    # ── Pass 4: MIN_GAP ────────────────────────────────────────────────────────
    for i in range(1, len(merged)):
        prev = merged[i - 1]
        curr = merged[i]
        gap  = curr['start'] - prev['end']
        if gap < RHYTHM['MIN_GAP']:
            adj = (RHYTHM['MIN_GAP'] - gap) / 2
            curr['start'] = clamp(curr['start'] + adj, 0, 1)
            prev['end']   = clamp(prev['end']   - adj, 0, 1)
            curr['duration'] = clamp(curr['end'] - curr['start'], RHYTHM['MIN_SEG_DURATION'], 1)
            prev['duration'] = clamp(prev['end'] - prev['start'], RHYTHM['MIN_SEG_DURATION'], 1)

    for seg in merged:
        seg['start']    = clamp(seg['start'], 0, 1)
        seg['end']      = clamp(seg['end'], 0, 1)
        seg['duration'] = clamp(seg['end'] - seg['start'], RHYTHM['MIN_SEG_DURATION'], 1)

    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Frame Evaluation (Python port of applySegAtT)
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_segment_frame(seg, local_t):
    """
    Evaluate a segment at normalized local_t (0-1).
    Uses the pre-compiled shotCurve for zoom/glow/shake.
    """
    curve_result = evaluate_shot_curve(seg['shotCurve'], max(0.0, min(1.0, local_t)))
    return {
        'zoom':  curve_result['zoom'],
        'glow':  curve_result['glow'],
        'shake': curve_result['shake'],
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI / Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Narrative OS Renderer')
    parser.add_argument('input',  help='Input trace JSON file')
    parser.add_argument('--output', '-o', default='output.mp4', help='Output video file')
    parser.add_argument('--fps',   type=int, default=30, help='Frames per second')
    parser.add_argument('--width', type=int, default=1280, help='Output width')
    parser.add_argument('--height', type=int, default=720, help='Output height')
    parser.add_argument('--duration-ms', type=int, default=12000, help='Total duration in ms')
    parser.add_argument('--export-ir', action='store_true', help='Dump compiled IRs to JSON')
    parser.add_argument('--export-video', action='store_true', help='Render video via PIL+ffmpeg (requires ffmpeg in PATH)')
    args = parser.parse_args()

    with open(args.input) as f:
        trace = json.load(f)

    print(f"[trace] {len(trace['events'])} events, {len(trace.get('jobs', []))} jobs")
    print(f"[pipeline] Running Narrative OS...")

    segments = build_director(trace)
    print(f"[OK] Compiled {len(segments)} segments")

    if args.export_ir:
        out = {'segments': [], 'totalDuration': args.duration_ms}
        for seg in segments:
            out['segments'].append({
                'jobId':           seg.get('jobId') or seg.get('job_id'),
                'type':            seg.get('type'),
                'scene':           seg['scene'],
                'mode':            seg['mode'],
                'transition':      seg['transition'],
                'start':           seg['start'],
                'end':             seg['end'],
                'duration':        seg['duration'],
                'renderIR':        seg['renderIR'],
                'contentBinding':  seg['contentBinding'],
                'audioParams':     seg['audioParams'],
                'shotCurve':       {
                    'keyframes': [{'t': k.t, 'zoom': k.zoom, 'glow': k.glow, 'shake': k.shake}
                                  for k in seg['shotCurve'].keyframes],
                    'easing':    seg['shotCurve'].easing,
                },
            })
        with open('render_ir_export.json', 'w') as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print(f"📤 IR exported to render_ir_export.json")

    if args.export_video:
        from engine.renderer import render_video
        output_file = args.output or 'output.mp4'
        print(f"\n🎬 Rendering video: {output_file}")
        render_video(
            segments,
            output=output_file,
            total_ms=args.duration_ms,
            frames_dir='frames',
        )

    print(f"\n🎯 Pipeline complete. {len(segments)} segments ready for rendering.")


if __name__ == '__main__':
    main()
