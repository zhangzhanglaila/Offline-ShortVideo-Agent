"""
bridge.py - Python -> Remotion VideoLayout bridge.

This module converts Narrative OS segments into a Remotion-safe VideoLayout.
The key responsibility is not just shape conversion, but enforcing timeline
invariants so every frame has exactly one valid shot.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from engine.shared.path_utils import get_project_root, ensure_public_audio_copy

FPS = 30
MIN_SHOT_FRAMES = 1
DEFAULT_TTS_VOICE = "zh-CN-XiaoxiaoNeural"
VISUAL_STOPWORDS = {
    "是什么", "什么是", "为什么", "怎么实现", "底层", "原理", "我们", "你", "我",
    "的", "了", "是", "在", "中", "和", "以及", "一个", "一种", "这个", "那个",
}
VISUAL_QUERY_MAP = {
    "redis": "redis database",
    "redis底层": "redis database architecture",
    "原理": "architecture",
    "底层": "system architecture",
    "数据库": "database server",
    "内存": "memory computing",
    "缓存": "cache server",
    "数据结构": "data structure",
    "跳表": "skip list",
    "哈希表": "hash table",
    "键值": "key value database",
    "网络": "network server",
}
BAD_VISUAL_TERMS = {
    "nature", "landscape", "mountain", "sunset", "beach", "forest", "river", "travel",
}

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


def _segment_text(seg: dict[str, Any]) -> str:
    content_binding = seg.get('contentBinding', {}) if isinstance(seg.get('contentBinding'), dict) else {}
    return (
        str(seg.get('text') or '').strip()
        or str(content_binding.get('caption') or '').strip()
        or str(seg.get('caption') or '').strip()
        or str(seg.get('type') or 'segment').strip()
    )


def _extract_visual_keywords(text: str) -> list[str]:
    compact = re.sub(r"[，。！？、,.!?\s]+", " ", text or "").strip()
    tokens = [token.strip() for token in compact.split(" ") if token.strip()]
    keywords: list[str] = []
    for token in tokens:
        if token in VISUAL_STOPWORDS:
            continue
        if len(token) >= 2:
            keywords.append(token)
    if not keywords:
        return ["technology"]
    unique: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        if keyword not in seen:
            unique.append(keyword)
            seen.add(keyword)
    return unique[:4]


def _normalize_visual_query(keywords: list[str], topic: str = "") -> str:
    results: list[str] = []
    lowered_topic = (topic or "").lower()
    if "redis" in lowered_topic:
        results.append("redis database architecture")
    for keyword in keywords:
        lowered = keyword.lower()
        if lowered == "redis":
            results.append("redis database")
        else:
            results.append(VISUAL_QUERY_MAP.get(keyword, keyword))
    query = " ".join(results).strip()
    return query or "technology interface"


def _is_bad_visual_meta(meta_text: str) -> bool:
    lowered = (meta_text or "").lower()
    return any(term in lowered for term in BAD_VISUAL_TERMS)


def _ensure_public_image_copy(source_path: Path, file_name: str) -> str:
    project_root = get_project_root()
    public_dir = project_root / "remotion-renderer" / "public" / "generated-images"
    build_dir = project_root / "remotion-renderer" / "build" / "generated-images"
    public_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, public_dir / file_name)
    if build_dir.parent.exists():
        build_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, build_dir / file_name)
    return f"/generated-images/{file_name}"


def _resolve_semantic_image(seg: dict[str, Any], idx: int, fallback: str) -> str:
    text = _segment_text(seg)
    topic = str(seg.get("topic") or seg.get("contentBinding", {}).get("genPrompt") or text)
    keywords = _extract_visual_keywords(f"{topic} {text}")
    query = _normalize_visual_query(keywords, topic=topic)
    seg["visualQuery"] = query
    try:
        from core.image_fetch_module import get_image_fetch_module

        image_module = get_image_fetch_module()
        candidates: list[dict[str, Any]] = []
        if image_module.PEXELS_API_KEY:
            candidates.extend(image_module.fetch_from_pexels(query, per_page=6))
        if len(candidates) < 3 and image_module.UNSPLASH_ACCESS_KEY:
            candidates.extend(image_module.fetch_from_unsplash(query, per_page=4))

        filtered = []
        for item in candidates:
            meta = " ".join(
                str(item.get(key, ""))
                for key in ("alt", "photographer", "avg_color", "description")
            )
            if not _is_bad_visual_meta(meta):
                filtered.append(item)

        picked = filtered[0] if filtered else (candidates[0] if candidates else None)
        if picked:
            raw_url = (
                (picked.get("src", {}) or {}).get("large2x")
                or (picked.get("src", {}) or {}).get("large")
                or (picked.get("urls", {}) or {}).get("regular")
                or picked.get("url")
            )
            if raw_url:
                downloaded = image_module.download_image(raw_url, filename=f"semantic_{idx:03d}_{uuid.uuid4().hex[:8]}.jpg")
                if downloaded:
                    return _ensure_public_image_copy(Path(downloaded), Path(downloaded).name)
    except Exception:
        pass

    return fallback


def _estimate_text_duration_ms(text: str, fallback_ms: int = 1800) -> int:
    clean = (text or "").strip()
    if not clean:
        return fallback_ms
    chars_per_second = 4.2
    return max(1000, int((len(clean) / chars_per_second) * 1000))


def _build_audio_payload(
    segments: list[dict[str, Any]],
    voice: str = DEFAULT_TTS_VOICE,
    rate: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    try:
        from core.tts_module import get_tts_module
    except Exception:
        return segments, [], 0

    tts = get_tts_module(voice)
    tts.voice = voice
    tts.set_rate(rate)
    backend = tts.get_backend_name() if hasattr(tts, "get_backend_name") else "none"

    output_root = get_project_root() / "output" / "generated-audio"
    output_root.mkdir(parents=True, exist_ok=True)

    prepared_segments: list[dict[str, Any]] = []
    audio_tracks: list[dict[str, Any]] = []
    cursor_ms = 0

    for index, seg in enumerate(segments):
        text = _segment_text(seg)
        seg_copy = dict(seg)
        seg_copy['text'] = text

        duration_ms = _estimate_text_duration_ms(text)
        audio_src = None

        if text:
            extension = ".mp3" if backend in {"edge", "gtts", "baidu", "xunfei"} else ".wav"
            file_name = f"seg_{index:03d}_{uuid.uuid4().hex[:8]}{extension}"
            source_path = output_root / file_name
            try:
                if tts.generate_audio(text, str(source_path)) and source_path.exists():
                    measured = int(round(tts.get_audio_duration(str(source_path)) * 1000))
                    duration_ms = max(1000, measured)
                    audio_src = ensure_public_audio_copy(source_path, file_name)
            except Exception:
                audio_src = None

        seg_copy['_audio_duration_ms'] = duration_ms
        if audio_src:
            seg_copy['audioSrc'] = audio_src
        prepared_segments.append(seg_copy)

        if audio_src:
            audio_tracks.append({
                'id': f'audio_{index}',
                'src': audio_src,
                'start': round(cursor_ms / 1000 * FPS),
                'duration': round(duration_ms / 1000 * FPS),
                'text': text,
            })

        cursor_ms += duration_ms

    total_ms = cursor_ms
    if total_ms <= 0:
        return prepared_segments, audio_tracks, 0

    normalized_segments: list[dict[str, Any]] = []
    cursor_ms = 0
    for seg in prepared_segments:
        duration_ms = int(seg.get('_audio_duration_ms', _estimate_text_duration_ms(seg.get('text', ''))))
        seg_copy = dict(seg)
        seg_copy['start'] = round(cursor_ms / total_ms, 6)
        cursor_ms += duration_ms
        seg_copy['end'] = round(cursor_ms / total_ms, 6)
        normalized_segments.append(seg_copy)

    return normalized_segments, audio_tracks, total_ms


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
    text = _segment_text(seg)
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
    fallback_src = f"https://picsum.photos/seed/{picsum_seed}/1080"
    image_src = _resolve_semantic_image(seg, idx, fallback_src)

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
        'caption': text,
        'text': text,
        'audioSrc': seg.get('audioSrc'),
        'visualQuery': seg.get('visualQuery'),
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
    caption = _segment_text(seg)

    return {
        'id': f'cap_{idx}',
        'type': 'text',
        'text': caption,
        'x': 540,
        'y': 1460,
        'fontSize': 40,
        'color': style.get('text', '#c8dcff'),
        'fontWeight': 600,
        'textAlign': 'center',
        'lineHeight': 1.35,
        'maxWidth': 860,
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
    enable_audio: bool = False,
    voice: str = DEFAULT_TTS_VOICE,
    rate: int = 0,
) -> dict[str, Any]:
    audio_tracks: list[dict[str, Any]] = []
    if enable_audio and segments:
        segments, audio_tracks, audio_total_ms = _build_audio_payload(
            segments,
            voice=voice,
            rate=rate,
        )
        if audio_total_ms > 0:
            total_ms = audio_total_ms

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
            'audioTracks': [],
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
        'audioTracks': audio_tracks,
        'director': {
            'arc': 'viral',
            'scenes': scenes,
            'emotionalCurve': emotional_curve or [0.5],
            'pacingCurve': pacing_curve or [0.45],
            'ttsVoice': voice if enable_audio else 'neutral',
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


def build_spoken_video_layout(
    question: str,
    total_ms: int = 12000,
    voice: str = DEFAULT_TTS_VOICE,
    rate: int = 0,
) -> dict[str, Any]:
    from engine.render import generate_spoken_semantic_segments

    segments = generate_spoken_semantic_segments(question, video_duration=max(8, round(total_ms / 1000)))
    return build_video_layout(
        segments,
        total_ms=total_ms,
        enable_audio=True,
        voice=voice,
        rate=rate,
    )


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
