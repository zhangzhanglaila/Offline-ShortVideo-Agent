"""
Timeline Renderer — IR → Time-indexed Shot Curves.
Transforms discrete shot descriptions into continuous frame-level keyframe curves.
"""

from dataclasses import dataclass
from typing import List, Tuple
import math


@dataclass
class Keyframe:
    t: float        # normalized time [0,1]
    zoom: float
    glow: float
    shake: bool
    flash: bool


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def get_shot_easing(scene: str, motion: str) -> str:
    """Scene-based easing curve selection."""
    if motion == 'jitter':
        return 'linear'
    if motion == 'snap':
        return 'ease-out'
    if motion == 'push-in':
        if scene == 'climax':
            return 'ease-in'
        return 'ease-in-out'
    if motion == 'decelerate':
        return 'ease-out'
    if scene == 'release':
        return 'ease-out'
    if scene == 'buildup':
        return 'ease-in'
    if scene == 'climax':
        return 'ease-in'
    return 'ease-in-out'


def apply_easing(t: float, easing: str) -> float:
    """Apply named easing function to normalized t."""
    t = max(0.0, min(1.0, t))
    if easing == 'linear':
        return t
    if easing == 'ease-in':
        return t * t
    if easing == 'ease-out':
        return 1 - (1 - t) * (1 - t)
    # default: ease-in-out-cubic
    return t < 0.5 and 2 * t * t or 1 - math.pow(-2 * t + 2, 3) / 2


@dataclass
class ShotCurve:
    keyframes: List[Keyframe]
    easing: str


def compile_shot_curve(ir: dict, total_duration_ms: float) -> ShotCurve:
    """
    Compile a RenderIR into a pre-computed shot curve (keyframes + easing).
    ir: RenderIR dict
    total_duration_ms: segment duration in ms
    """
    scene  = ir.get('scene', 'normal')
    motion = ir.get('motion', 'steady')
    shot   = ir.get('shot', 'cut')
    camera = ir.get('camera', {})
    flash  = ir.get('flash', {})
    hints  = ir.get('renderHints', {})
    target_zoom = camera.get('zoom', 1.0)
    has_flash   = flash.get('color') and flash.get('duration', 0) > 0

    easing = get_shot_easing(scene, motion)
    keyframes: List[Keyframe] = []

    # Always start at neutral
    keyframes.append(Keyframe(t=0.0, zoom=1.0, glow=0.0, shake=False, flash=False))

    if shot == 'jitter-cut':
        # 4 jitter keyframes
        for i in range(1, 5):
            kt = i / 4.0
            keyframes.append(Keyframe(
                t=kt,
                zoom=1.0 + (0.15 if i % 2 == 0 else -0.15),
                glow=0.6 if hints.get('glow') else 0.0,
                shake=True,
                flash=False,
            ))
    elif motion == 'accelerate':
        # Push-in curve: zoom ramps up
        keyframes.append(Keyframe(t=0.5, zoom=target_zoom * 0.7,  glow=0.3, shake=False, flash=False))
        keyframes.append(Keyframe(t=1.0, zoom=target_zoom,         glow=0.6 if hints.get('glow') else 0.0, shake=False, flash=False))
    elif motion == 'push-in':
        # Sustained tighten
        keyframes.append(Keyframe(t=0.4, zoom=target_zoom * 0.8,  glow=0.4, shake=False, flash=False))
        keyframes.append(Keyframe(t=1.0, zoom=target_zoom,         glow=0.8 if hints.get('glow') else 0.0, shake=False, flash=False))
    elif motion == 'decelerate':
        # Slow settle back to neutral
        keyframes.append(Keyframe(t=0.3, zoom=target_zoom,           glow=0.3, shake=False, flash=False))
        keyframes.append(Keyframe(t=1.0, zoom=1.0,                 glow=0.0, shake=False, flash=False))
    else:
        # Default: hold with optional flash at start
        if has_flash:
            keyframes.append(Keyframe(t=0.02, zoom=1.0, glow=0.0, shake=False, flash=True))
        keyframes.append(Keyframe(t=1.0, zoom=target_zoom, glow=0.5 if hints.get('glow') else 0.0, shake=False, flash=False))

    return ShotCurve(keyframes=keyframes, easing=easing)


def evaluate_shot_curve(curve: ShotCurve, local_t: float) -> dict:
    """
    Evaluate the compiled shot curve at normalized time local_t.
    Returns {zoom, glow, shake} for frame rendering.
    """
    if not curve.keyframes:
        return {'zoom': 1.0, 'glow': 0.0, 'shake': False}
    if len(curve.keyframes) == 1:
        k = curve.keyframes[0]
        return {'zoom': k.zoom, 'glow': k.glow, 'shake': k.shake}

    t = apply_easing(max(0.0, min(1.0, local_t)), curve.easing)

    # Find surrounding keyframe pair
    lo = curve.keyframes[0]
    hi = curve.keyframes[-1]
    for i in range(len(curve.keyframes) - 1):
        if local_t >= curve.keyframes[i].t and local_t <= curve.keyframes[i + 1].t:
            lo = curve.keyframes[i]
            hi = curve.keyframes[i + 1]
            break

    span_t = 0.0 if hi.t == lo.t else (t - lo.t) / (hi.t - lo.t)
    return {
        'zoom':  lerp(lo.zoom, hi.zoom, span_t),
        'glow':  lerp(lo.glow, hi.glow, span_t),
        'shake': hi.shake or (span_t < 0.5 and lo.shake),
    }
