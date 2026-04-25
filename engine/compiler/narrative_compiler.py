"""
Narrative Compiler — transforms narrative kernel state into an executable render instruction IR.
This is the OUTPUT CONTRACT — the bridge from Narrative OS to Video Renderer.
Each instruction is a self-contained "导演脚本" describing ONE shot.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class RenderIR:
    scene: str
    shot: str
    motion: str
    duration: float
    transition: str
    intensity: float
    camera: dict
    flash: dict
    emphasis: str
    audio: str
    renderHints: dict


SHOT_MAP = {
    'chaos':  'jitter-cut',
    'burst':  'wide-push',
    'focus':  'tighten',
    'linger': 'static-hold',
    'normal': 'cut',
}


def compile_narrative_instruction(seg: dict) -> RenderIR:
    """
    Transform a segment's narrative kernel state into a renderable IR.
    seg keys: scene, mode, transition, camZoom, camMove, camCutSnap, camDur,
              flashColor, flashDur, rhythm, emphasis, tags, glow, shake,
              edgeColor, edgeWidth, duration
    """
    scene      = seg.get('scene', 'normal')
    mode       = seg.get('mode', 'normal')
    transition = seg.get('transition') or 'cut'
    cam_zoom   = seg.get('camZoom', 1.0)
    cam_move   = seg.get('camMove', False)
    cam_snap   = seg.get('camCutSnap', False)
    cam_dur    = seg.get('camDur', 0)
    flash_col  = seg.get('flashColor')
    flash_dur = seg.get('flashDur', 0)
    emphasis   = seg.get('emphasis', 'none')
    tags       = seg.get('tags', [])
    glow       = seg.get('glow', False)
    shake      = seg.get('shake', False)
    edge_color = seg.get('edgeColor')
    edge_width = seg.get('edgeWidth')
    duration   = seg.get('duration', 0)
    rhythm     = seg.get('rhythm', {})

    # Shot type from mode
    shot = SHOT_MAP.get(mode, 'cut')

    # Motion profile
    if mode == 'chaos':
        motion = 'jitter'
    elif mode == 'burst':
        motion = 'accelerate'
    elif mode == 'focus':
        motion = 'push-in'
    elif mode == 'linger':
        motion = 'decelerate'
    elif 'unblocked' in tags or 'retry' in tags:
        motion = 'snap'
    else:
        motion = 'steady'

    # Intensity 0-1
    if mode == 'chaos':
        intensity = 1.0
    elif mode == 'burst':
        intensity = 0.8
    elif mode == 'focus':
        intensity = 0.7
    elif mode == 'linger':
        intensity = 0.3
    elif emphasis == 'strong':
        intensity = 0.9
    elif emphasis == 'medium':
        intensity = 0.6
    elif emphasis == 'weak':
        intensity = 0.3
    else:
        intensity = 0.5

    # Canonicalize transition
    render_trans = transition
    if render_trans == 'release-cut':
        render_trans = 'ease-out'
    elif render_trans == 'snap-in':
        render_trans = 'snap'

    camera_instruction = {
        'zoom': cam_zoom,
        'move': cam_move,
        'snap': cam_snap,
        'duration': cam_dur,
    }

    flash_instruction = {
        'color': flash_col,
        'duration': flash_dur,
    }

    # Audio hint
    if scene == 'climax' and rhythm.get('accent'):
        audio_hint = 'build-tension'
    elif scene == 'release':
        audio_hint = 'wind-down'
    elif scene == 'buildup':
        audio_hint = 'pulse'
    elif mode == 'chaos':
        audio_hint = 'disrupt'
    elif mode == 'linger':
        audio_hint = 'sustain'
    else:
        audio_hint = 'neutral'

    render_hints = {
        'glow': glow,
        'shake': shake,
        'edgeColor': edge_color,
        'edgeWidth': edge_width,
    }

    return RenderIR(
        scene=scene,
        shot=shot,
        motion=motion,
        duration=duration,
        transition=render_trans,
        intensity=intensity,
        camera=camera_instruction,
        flash=flash_instruction,
        emphasis=emphasis,
        audio=audio_hint,
        renderHints=render_hints,
    )
