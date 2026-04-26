"""
bridge.py - Python -> Remotion VideoLayout bridge.

This module converts Narrative OS segments into a Remotion-safe VideoLayout.
The key responsibility is not just shape conversion, but enforcing timeline
invariants so every frame has exactly one valid shot.
"""

from __future__ import annotations

import json
from typing import Any

FPS = 30
MIN_SHOT_FRAMES = 1

_MODE_CAMERA_MAP = {
    'chaos': 'shake',
    'burst': 'push-in',
    'climax': 'push-in',
    'buildup': 'pan-right',
    'focus': 'push-in',
    'linger': 'static',
    'release': 'pan-left',
    'idle': 'static',
    'normal': 'static',
}

_MODE_EMOTION_LABEL_MAP = {
    'chaos': 'intense',
    'burst': 'dramatic',
    'climax': 'dramatic',
    'buildup': 'warm',
    'focus': 'warm',
    'linger': 'calm',
    'release': 'calm',
    'idle': 'neutral',
    'normal': 'neutral',
}

_MODE_EMOTION_VALUE_MAP = {
    'chaos': 0.95,
    'burst': 0.82,
    'climax': 0.9,
    'buildup': 0.65,
    'focus': 0.55,
    'linger': 0.3,
    'release': 0.4,
    'idle': 0.2,
    'normal': 0.5,
}

_MODE_PACING_VALUE_MAP = {
    'chaos': 0.95,
    'burst': 0.85,
    'climax': 0.78,
    'buildup': 0.62,
    'focus': 0.48,
    'linger': 0.28,
    'release': 0.35,
    'idle': 0.2,
    'normal': 0.45,
}

_SCENE_COLOR_BG = {
    'intro': '#1a0a2e',
    'buildup': '#0a1a2e',
    'climax': '#2e0a1a',
    'release': '#0a2e1a',
    'idle': '#0a0a0f',
    'focus-arc': '#1a1a2e',
    'normal': '#0a0a0f',
}

_SCENE_TYPE_MAP = {
    'intro': 'hook',
    'buildup': 'explain',
    'focus-arc': 'explain',
    'climax': 'cta',
    'release': 'cta',
    'idle': 'explain',
    'normal': 'explain',
}

_SCENE_STYLE_MAP = {
    'intro': 'bold',
    'buildup': 'tech',
    'focus-arc': 'cinematic',
    'climax': 'bold',
    'release': 'warm',
    'idle': 'minimalist',
    'normal': 'cinematic',
}

_INTENT_CAMERA_MAP = {
    'impact': 'push-in',
    'approach': 'pan-right',
    'reveal': 'push-in',
    'release': 'pan-left',
    'linger': 'static',
    'steady': 'static',
}


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _derive_visual_semantics(
    seg: dict[str, Any],
    mode: str,
    scene: str,
) -> dict[str, Any]:
    semantic_block = seg.get('semantics', {}) if isinstance(seg.get('semantics'), dict) else {}
    render_ir = seg.get('renderIR', {}) if isinstance(seg.get('renderIR'), dict) else {}
    explicit_intent = semantic_block.get('intent', seg.get('intent'))
    explicit_emotion = semantic_block.get('emotion', seg.get('emotion'))
    explicit_rhythm = semantic_block.get('rhythm', seg.get('rhythm'))
    explicit_focus = semantic_block.get('focus', seg.get('focus'))
    explicit_motion_profile = semantic_block.get('motionProfile', seg.get('motionProfile'))
    explicit_energy = semantic_block.get('energy', seg.get('energy'))
    explicit_intent = explicit_intent if isinstance(explicit_intent, str) else None
    explicit_emotion = explicit_emotion if isinstance(explicit_emotion, str) else None
    explicit_rhythm = explicit_rhythm if isinstance(explicit_rhythm, str) else None
    explicit_focus = explicit_focus if isinstance(explicit_focus, str) else None
    explicit_motion_profile = explicit_motion_profile if isinstance(explicit_motion_profile, str) else None
    emphasis = seg.get('emphasis', 'none')
    intensity = float(render_ir.get('intensity', 0.5) or 0.5)
    accent = bool(seg.get('accent', False))
    snap = bool(seg.get('camCutSnap', False)) or render_ir.get('motion') == 'snap'

    if explicit_intent:
        intent = explicit_intent
    elif scene == 'release' or mode == 'release':
        intent = 'release'
    elif scene == 'climax' or mode in {'chaos', 'burst'}:
        intent = 'impact'
    elif mode == 'focus' or scene == 'focus-arc':
        intent = 'reveal'
    elif scene == 'buildup' or mode == 'buildup':
        intent = 'approach'
    elif mode == 'linger':
        intent = 'linger'
    else:
        intent = 'steady'

    if explicit_emotion:
        emotion = explicit_emotion
    elif mode == 'chaos':
        emotion = 'tension'
    elif scene == 'climax' or mode == 'burst':
        emotion = 'excited'
    elif mode in {'focus', 'buildup'} or emphasis == 'strong':
        emotion = 'anticipation'
    elif mode in {'release', 'linger'}:
        emotion = 'calm'
    else:
        emotion = 'neutral'

    if explicit_rhythm:
        rhythm = explicit_rhythm
    else:
        rhythm = 'accent' if accent or intensity >= 0.75 else 'flow'

    if explicit_motion_profile:
        motion_profile = explicit_motion_profile
    else:
        motion_profile = 'snap' if snap else 'glide'

    if explicit_focus:
        focus = explicit_focus
    else:
        focus = 'subject' if mode != 'idle' else 'wide'

    if explicit_energy is None:
        energy = max(0.2, min(1.0, intensity))
    else:
        try:
            energy = max(0.2, min(1.0, float(explicit_energy)))
        except (TypeError, ValueError):
            energy = max(0.2, min(1.0, intensity))

    return {
        'intent': intent,
        'emotion': emotion,
        'rhythm': rhythm,
        'motionProfile': motion_profile,
        'focus': focus,
        'energy': energy,
    }


def _semantic_emotion_label(semantics: dict[str, Any]) -> str:
    emotion = semantics.get('emotion', 'neutral')
    return {
        'tension': 'intense',
        'excited': 'dramatic',
        'anticipation': 'warm',
        'calm': 'calm',
        'neutral': 'neutral',
    }.get(emotion, 'neutral')


def _semantic_emotion_value(semantics: dict[str, Any], mode: str) -> float:
    emotion = semantics.get('emotion', 'neutral')
    return {
        'tension': 0.95,
        'excited': 0.84,
        'anticipation': 0.68,
        'calm': 0.30,
        'neutral': _MODE_EMOTION_VALUE_MAP.get(mode, 0.5),
    }.get(emotion, _MODE_EMOTION_VALUE_MAP.get(mode, 0.5))


def _semantic_pacing_value(semantics: dict[str, Any], mode: str) -> float:
    rhythm = semantics.get('rhythm', 'flow')
    return {
        'accent': 0.84,
        'pulse': 0.72,
        'flow': _MODE_PACING_VALUE_MAP.get(mode, 0.45),
        'linger': 0.28,
    }.get(rhythm, _MODE_PACING_VALUE_MAP.get(mode, 0.45))


def _build_shot_objects(
    image_src: str,
    semantics: dict[str, Any],
    zoom: float,
    meta: dict[str, Any],
    width: int = 1080,
    height: int = 1920,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    intent = semantics.get('intent', 'steady')
    emotion = semantics.get('emotion', 'neutral')
    rhythm = semantics.get('rhythm', 'flow')
    motion_profile = semantics.get('motionProfile', 'glide')
    energy = float(semantics.get('energy', 0.5) or 0.5)
    accent_color = meta.get('flashColor') or meta.get('edgeColor') or '#44aaff'
    glow_enabled = bool(meta.get('glow', False))
    shake_enabled = bool(meta.get('shake', False))

    subject_path_map = {
        'approach': ((90, 420), 250),
        'reveal': ((220, 360), 220),
        'impact': ((70, 470), 245),
        'release': ((430, 140), 290),
        'linger': ((280, 320), 300),
        'steady': ((150, 340), 275),
    }
    (subject_from_x, subject_to_x), subject_y = subject_path_map.get(
        intent,
        subject_path_map['steady'],
    )
    subject_width = 760 if zoom >= 1.2 else 700
    subject_height = 1240 if zoom >= 1.2 else 1160
    fg_from_x, fg_to_x = {
        'approach': (790, 610),
        'reveal': (700, 940),
        'impact': (760, 500),
        'release': (320, 500),
        'linger': (660, 640),
        'steady': (760, 620),
    }.get(intent, (760, 620))
    light_from_x, light_to_x = {
        'approach': (-260, 900),
        'reveal': (-180, 760),
        'impact': (-140, 700),
        'release': (120, 880),
        'linger': (-120, 420),
        'steady': (-220, 820),
    }.get(intent, (-220, 820))
    if shake_enabled:
        light_to_x -= 140

    bg_to_scale = {
        'approach': 1.10,
        'reveal': 1.12,
        'impact': 1.14,
        'release': 1.06,
        'linger': 1.04,
        'steady': 1.08,
    }.get(intent, 1.08)
    subject_to_scale = 1.02 + energy * (0.05 if emotion != 'calm' else 0.02)
    subject_end_y = subject_y + (22 if shake_enabled else -8 if intent == 'reveal' else 0)
    foreground_opacity = 0.20 + energy * 0.12 + (0.05 if glow_enabled else 0.0)
    light_opacity = 0.18 + energy * 0.18
    aura_opacity = 0.10 + energy * 0.18

    objects = [
        {
            'id': 'bg',
            'type': 'image',
            'src': image_src,
            'z': 0,
            'width': width,
            'height': height,
            'opacity': 0.96,
            'blur': 6,
            'animation': {
                'type': 'zoom',
                'fromScale': 1.02,
                'toScale': bg_to_scale + (0.01 if glow_enabled else 0.0),
            },
        },
        {
            'id': 'subject',
            'type': 'image',
            'src': image_src,
            'z': 2,
            'x': subject_from_x,
            'y': subject_y,
            'width': subject_width,
            'height': subject_height,
            'opacity': 0.98,
            'borderRadius': 28,
            'objectFit': 'cover',
            'animation': {
                'type': 'move',
                'from': [subject_from_x, subject_y],
                'to': [subject_to_x, subject_end_y],
                'fromScale': 1.01,
                'toScale': subject_to_scale,
            },
        },
        {
            'id': 'foreground',
            'type': 'fx',
            'effect': 'foreground-occlusion',
            'z': 3,
            'x': fg_from_x,
            'y': 0,
            'width': 420,
            'height': height,
            'opacity': foreground_opacity,
            'blur': 8 if motion_profile == 'snap' else 10,
            'color': accent_color,
            'animation': {
                'type': 'move',
                'from': [fg_from_x, 0],
                'to': [fg_to_x, 0],
            },
        },
        {
            'id': 'light',
            'type': 'fx',
            'effect': 'light-sweep',
            'z': 4,
            'x': light_from_x,
            'y': 0,
            'width': 560,
            'height': height,
            'opacity': light_opacity,
            'blur': 18,
            'color': accent_color,
            'blendMode': 'screen',
            'animation': {
                'type': 'move',
                'from': [light_from_x, 0],
                'to': [light_to_x, 0],
            },
        },
    ]

    if emotion in {'anticipation', 'excited', 'tension'}:
        objects.append({
            'id': 'aura',
            'type': 'fx',
            'effect': 'glow-orb',
            'z': 1,
            'x': 120 if intent != 'release' else 280,
            'y': 160 if intent != 'linger' else 260,
            'width': 760,
            'height': 760,
            'opacity': aura_opacity,
            'blur': 24 if emotion == 'tension' else 18,
            'color': accent_color,
            'blendMode': 'screen',
            'animation': {
                'type': 'float' if rhythm == 'flow' else 'zoom',
                'amplitude': 18,
                'speed': 0.05,
                'fromScale': 0.92,
                'toScale': 1.08,
            },
        })

    interactions = [
        {
            'sourceId': 'subject',
            'targetId': 'light',
            'type': 'link-opacity',
            'inputRange': [min(subject_from_x, subject_to_x), max(subject_from_x, subject_to_x)],
            'outputRange': [0.18 + energy * 0.08, 0.72 + energy * 0.22],
        },
        {
            'sourceId': 'subject',
            'targetId': 'foreground',
            'type': 'proximity-scale',
            'distance': 120 if motion_profile == 'snap' else 170,
            'outputRange': [1.0, 1.04 + energy * 0.08],
        },
    ]

    if emotion in {'anticipation', 'excited', 'tension'}:
        interactions.append({
            'sourceId': 'subject',
            'targetId': 'aura',
            'type': 'link-opacity',
            'inputRange': [min(subject_from_x, subject_to_x), max(subject_from_x, subject_to_x)],
            'outputRange': [0.12, 0.52 if emotion == 'tension' else 0.42],
        })

    return objects, interactions


def _normalize_shot_timeline(
    shot_entries: list[dict[str, Any]],
    total_frames: int,
) -> list[dict[str, Any]]:
    if not shot_entries:
        return []

    total_frames = max(1, total_frames)
    ordered_entries = sorted(shot_entries, key=lambda entry: entry['shot']['start'])
    normalized_entries: list[dict[str, Any]] = []
    total_entries = len(ordered_entries)

    for index, entry in enumerate(ordered_entries):
        shot = dict(entry['shot'])
        original_start = int(round(shot.get('start', 0)))
        remaining_entries = total_entries - index
        max_start = max(0, total_frames - remaining_entries)

        if index == 0:
            start = 0
        else:
            start = max(
                original_start,
                normalized_entries[-1]['shot']['start'] + MIN_SHOT_FRAMES,
            )

        shot['start'] = _clamp_int(start, 0, max_start)
        normalized_entries.append({**entry, 'shot': shot})

    for index in range(len(normalized_entries) - 1):
        current = normalized_entries[index]['shot']
        next_start = normalized_entries[index + 1]['shot']['start']
        current['duration'] = max(MIN_SHOT_FRAMES, next_start - current['start'])

    normalized_entries[-1]['shot']['duration'] = max(
        MIN_SHOT_FRAMES,
        total_frames - normalized_entries[-1]['shot']['start'],
    )

    return normalized_entries


def segment_to_shot(seg: dict[str, Any], idx: int, total_frames: int) -> tuple[dict[str, Any], dict[str, Any]]:
    mode = seg.get('mode', 'normal')
    scene = seg.get('scene', 'normal')
    zoom = seg.get('camZoom', 1.0)
    semantics = _derive_visual_semantics(seg, mode, scene)
    camera = seg.get('camera') or _INTENT_CAMERA_MAP.get(
        semantics.get('intent', 'steady'),
        _MODE_CAMERA_MAP.get(mode, 'static'),
    )

    norm_start = float(seg.get('start', 0.0))
    norm_end = float(seg.get('end', norm_start + 0.01))

    start_f = round(norm_start * total_frames)
    end_f = round(norm_end * total_frames)
    duration_f = max(15, end_f - start_f)

    job_idx = seg.get('jobIndex', 0) if isinstance(seg.get('jobIndex'), int) else idx
    picsum_seed = 100 + (job_idx % 50)
    image_src = f"https://picsum.photos/seed/{picsum_seed}/1080"

    crop_w = 0.6 + (1.0 - min(zoom, 2.0) / 2.0) * 0.4
    crop_h = 0.6 + (1.0 - min(zoom, 2.0) / 2.0) * 0.4
    crop_x = (1.0 - crop_w) / 2
    crop_y = (1.0 - crop_h) / 2

    breathe_intensity = {
        'chaos': 0.7,
        'climax': 0.6,
        'burst': 0.5,
        'buildup': 0.35,
        'focus': 0.25,
        'linger': 0.15,
        'release': 0.2,
        'idle': 0.1,
        'normal': 0.2,
    }.get(mode, 0.2)

    shot_meta = {
        'scene': scene,
        'mode': mode,
        'type': seg.get('type', 'NORMAL'),
        'zoom': zoom,
        'flashColor': seg.get('flashColor'),
        'flashDur': seg.get('flashDur'),
        'glow': seg.get('glow', False),
        'shake': seg.get('shake', False),
        'edgeColor': seg.get('edgeColor'),
        'edgeWidth': seg.get('edgeWidth'),
        'tags': seg.get('tags', []),
        'emphasis': seg.get('emphasis', 'none'),
        'intent': semantics['intent'],
        'emotion': semantics['emotion'],
        'semantics': semantics,
        'caption': seg.get('contentBinding', {}).get('caption', seg.get('type', 'segment')),
        'genPrompt': seg.get('contentBinding', {}).get('genPrompt', ''),
        'renderIR': seg.get('renderIR', {}),
        'sceneTransition': seg.get('sceneTransition'),
        'transition': seg.get('transition'),
        'camCutSnap': seg.get('camCutSnap', False),
    }
    objects, interactions = _build_shot_objects(
        image_src=image_src,
        semantics=semantics,
        zoom=zoom,
        meta=shot_meta,
    )

    return {
        'start': start_f,
        'duration': duration_f,
        'src': image_src,
        'camera': camera,
        'cropX': crop_x,
        'cropY': crop_y,
        'cropW': crop_w,
        'cropH': crop_h,
        'opacity': 1.0,
        'objects': objects,
        'interactions': interactions,
        '_meta': shot_meta,
    }, {
        'emotionLabel': _semantic_emotion_label(semantics),
        'emotionValue': _semantic_emotion_value(semantics, mode),
        'pacingValue': _semantic_pacing_value(semantics, mode),
        'cameraOverride': camera,
        'colorOverlay': _SCENE_COLOR_BG.get(scene, '#0a0a0f'),
        'breatheIntensity': breathe_intensity,
        'zoomBase': 1.0 + (zoom - 1.0) * 0.3,
        'sceneType': _SCENE_TYPE_MAP.get(scene, 'explain'),
        'visualStyle': _SCENE_STYLE_MAP.get(scene, 'cinematic'),
    }


def segment_to_element(seg: dict[str, Any], idx: int, total_frames: int) -> dict[str, Any]:
    norm_start = float(seg.get('start', 0.0))
    norm_end = float(seg.get('end', norm_start + 0.01))
    start_f = round(norm_start * total_frames)
    end_f = round(norm_end * total_frames)
    duration_f = max(15, end_f - start_f)

    cb = seg.get('contentBinding', {})
    style = cb.get('style', {})
    caption = cb.get('caption', seg.get('type', 'segment'))

    return {
        'id': f'cap_{idx}',
        'type': 'text',
        'text': caption,
        'x': 540,
        'y': 1600,
        'fontSize': 42,
        'color': style.get('text', '#c8dcff'),
        'fontWeight': 600,
        'textAlign': 'center',
        'start': start_f,
        'duration': duration_f,
        'zIndex': 10,
        'animation': {
            'enter': 'blur-in',
            'exit': 'fade',
            'duration': 15,
        },
    }


def build_video_layout(
    segments: list[dict[str, Any]],
    total_ms: int = 12000,
    width: int = 1080,
    height: int = 1920,
) -> dict[str, Any]:
    total_frames = max(1, int(total_ms / 1000 * FPS))

    if not segments:
        return {
            'width': width,
            'height': height,
            'fps': FPS,
            'durationInFrames': total_frames,
            'background': '#0a0a0f',
            'elements': [],
            'shots': [],
        }

    entries: list[dict[str, Any]] = []
    for idx, seg in enumerate(segments):
        shot, director_state = segment_to_shot(seg, idx, total_frames)
        element = segment_to_element(seg, idx, total_frames)
        entries.append({
            'shot': shot,
            'element': element,
            'director_state': director_state,
        })

    normalized_entries = _normalize_shot_timeline(entries, total_frames)

    shots: list[dict[str, Any]] = []
    elements: list[dict[str, Any]] = []
    scenes: list[dict[str, Any]] = []
    emotional_curve: list[float] = []
    pacing_curve: list[float] = []

    for entry in normalized_entries:
        shot = entry['shot']
        element = dict(entry['element'])
        director_state = entry['director_state']

        element['start'] = shot['start']
        element['duration'] = shot['duration']

        shots.append(shot)
        elements.append(element)
        emotional_curve.append(director_state['emotionValue'])
        pacing_curve.append(director_state['pacingValue'])
        scenes.append({
            'start': shot['start'] / FPS,
            'end': (shot['start'] + shot['duration']) / FPS,
            'type': director_state['sceneType'],
            'emotionalCurve': [director_state['emotionValue']],
            'pacingCurve': [director_state['pacingValue']],
            'visualStyle': director_state['visualStyle'],
        })

    cam_counts: dict[str, int] = {}
    for entry in normalized_entries:
        override = entry['director_state']['cameraOverride']
        cam_counts[override] = cam_counts.get(override, 0) + 1

    dominant_cam = max(cam_counts, key=cam_counts.get) if cam_counts else 'static'
    camera_strategy_map = {
        'shake': 'shake',
        'push-in': 'zoom-in-out',
        'pan-left': 'pan',
        'pan-right': 'pan',
        'static': 'static',
    }

    return {
        'width': width,
        'height': height,
        'fps': FPS,
        'durationInFrames': total_frames,
        'background': '#0a0a0f',
        'elements': elements,
        'shots': shots,
        'director': {
            'arc': 'viral',
            'scenes': scenes,
            'emotionalCurve': emotional_curve or [0.5],
            'pacingCurve': pacing_curve or [0.45],
            'ttsVoice': 'neutral',
            'ttsSpeed': 1.0,
            'emphasisPoints': [],
            'cameraStrategy': camera_strategy_map.get(dominant_cam, 'static'),
            'subtitleCues': [],
            'allWords': [],
            'emphasisPointsWord': [],
        },
    }


def build_director_timeline(trace: dict[str, Any], total_ms: int = 12000) -> dict[str, Any]:
    from engine.render import build_director

    segments = build_director(trace)
    return build_video_layout(segments, total_ms)


def build_director_json(trace: dict[str, Any], total_ms: int = 12000) -> str:
    layout = build_director_timeline(trace, total_ms)
    return json.dumps(layout, ensure_ascii=False, indent=2)


def dump_preview(trace_path: str, total_ms: int = 12000) -> None:
    with open(trace_path, encoding="utf-8") as file:
        trace = json.load(file)

    layout = build_director_timeline(trace, total_ms)
    print(
        f"\n=== VideoLayout ({layout['width']}x{layout['height']} @ {layout['fps']} fps) ==="
    )
    print(f"Duration: {layout['durationInFrames']} frames")
    print(f"Shots:    {len(layout['shots'])}")
    print(f"Elements: {len(layout['elements'])}")
    print(f"Camera:   {layout['director']['cameraStrategy']}")
    print()

    for shot in layout['shots']:
        meta = shot.get('_meta', {})
        print(
            f"[{shot['start']:4d}+{shot['duration']:3d}] "
            f"cam={shot['camera']:10s} scene={meta.get('scene', 'normal'):8s} "
            f"mode={meta.get('mode', 'normal'):8s} "
            f"zoom={meta.get('zoom', 1.0):.2f} "
            f"glow={meta.get('glow', False)} shake={meta.get('shake', False)}"
        )
    print()
