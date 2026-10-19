# -*- coding: utf-8 -*-
"""从 ThinkingSession 的运行态构建时间轴产物。

`api/timeline_api.py` 通过 `session.artifact_graph` 读取 TIMELINE 产物，
但 `ThinkingSession` 本身并不持有 artifact graph（它属于 `VideoRuntimeAdapter`，
而后者从未接入应用主流程）。结果是时间轴接口永远返回空轨道。

这里做一件事：按需把会话状态投影成一组轨道，写入 TIMELINE 产物并挂到
session 上，让时间轴编辑器（以及 DAG 视图）拿到真实数据。

轨道时间单位与 `thinking.state.AudioTrack` 一致：**帧**（基准 `state.fps`）。
"""

from __future__ import annotations

from typing import Any

from thinking.artifacts import ArtifactGraph, ArtifactType

# 无音频轨道时，按中文口播速度估算时长（约 4.5 字/秒）
CHARS_PER_SECOND = 4.5


def estimate_frames(text: str, fps: int) -> int:
    """按字数估算口播时长（帧），至少 1 秒。"""
    chars = len((text or "").strip())
    seconds = max(chars / CHARS_PER_SECOND, 1.0)
    return int(round(seconds * fps))


def module_span_frames(module: Any, fps: int) -> int:
    """单个模块的时长（帧）。

    优先用 TTS 音频轨道的 (start + duration)；没有音频时按文案字数估算。
    """
    audio = list(getattr(module, "audio_tracks", []) or [])
    spans = [int(a.start) + int(a.duration) for a in audio if getattr(a, "duration", 0)]
    if spans:
        return max(max(spans), fps)

    script = list(getattr(module, "script", []) or [])
    estimated = sum(estimate_frames(getattr(s, "text", ""), fps) for s in script)
    return max(estimated, fps * 3)


def _track(
    track_id: str,
    track_type: str,
    layer: int,
    start: int,
    end: int,
    scene_id: str,
    content: dict,
) -> dict:
    return {
        "track_id": track_id,
        "track_type": track_type,
        "layer": layer,
        "start_frame": int(start),
        "end_frame": int(end),
        "scene_id": scene_id,
        "content": content,
    }


def build_timeline_tracks(state: Any) -> list[dict]:
    """把 `VideoProjectState` 投影为轨道列表。

    每个模块产出 3 条同起止的轨道（visual / subtitle / audio），
    共享同一个 `scene_id`，便于时间轴按"场景"整体移动或伸缩。
    """
    fps = int(getattr(state, "fps", 30) or 30)
    modules = sorted(
        list(getattr(state, "modules", []) or []),
        key=lambda m: getattr(m, "index", 0),
    )

    tracks: list[dict] = []
    cursor = 0
    for index, module in enumerate(modules):
        span = module_span_frames(module, fps)
        start, end = cursor, cursor + span
        module_id = getattr(module, "id", "") or f"module_{index}"
        title = getattr(module, "title", "") or f"模块 {index + 1}"
        script_text = "".join(
            getattr(s, "text", "") for s in getattr(module, "script", []) or []
        ).strip()
        audio = list(getattr(module, "audio_tracks", []) or [])

        tracks.append(_track(
            f"{module_id}_visual", "visual", 0, start, end, module_id,
            {"title": title, "module_index": index, "kind": "visual"},
        ))
        tracks.append(_track(
            f"{module_id}_subtitle", "subtitle", 1, start, end, module_id,
            {"title": title, "text": script_text, "sentences": len(getattr(module, "script", []) or [])},
        ))
        tracks.append(_track(
            f"{module_id}_audio", "audio", 2, start, end, module_id,
            {"title": title, "voice": getattr(state, "voice", ""), "segments": len(audio)},
        ))
        cursor = end

    return tracks


def _timeline_payload(state: Any, tracks: list[dict]) -> dict:
    return {
        "tracks": tracks,
        "fps": int(getattr(state, "fps", 30) or 30),
        "width": int(getattr(state, "width", 1080) or 1080),
        "height": int(getattr(state, "height", 1920) or 1920),
    }


def ensure_timeline_graph(session: Any) -> ArtifactGraph:
    """确保 session 上有 artifact graph，且包含 TIMELINE 产物。

    幂等：已有 TIMELINE 时直接返回，不覆盖用户正在编辑的轨道。
    """
    graph = getattr(session, "artifact_graph", None)
    if graph is None:
        graph = ArtifactGraph()
        setattr(session, "artifact_graph", graph)

    timeline = graph.find_by_type(ArtifactType.TIMELINE)
    if timeline is not None:
        return graph

    state = session.state
    deps = []

    script_text = "\n".join(
        getattr(s, "text", "")
        for module in getattr(state, "modules", []) or []
        for s in getattr(module, "script", []) or []
        if getattr(s, "text", "")
    ).strip()
    if script_text:
        deps.append(graph.create(
            ArtifactType.SCRIPT,
            content={"text": script_text, "modules": len(getattr(state, "modules", []) or [])},
        ))

    for module in getattr(state, "modules", []) or []:
        audio = list(getattr(module, "audio_tracks", []) or [])
        if audio:
            deps.append(graph.create(
                ArtifactType.TTS_SENTENCE,
                content={
                    "module_id": getattr(module, "id", ""),
                    "segments": len(audio),
                    "duration": sum(int(getattr(a, "duration", 0)) for a in audio),
                },
            ))

    tracks = build_timeline_tracks(state)
    timeline = graph.create(
        ArtifactType.TIMELINE,
        content=_timeline_payload(state, tracks),
        depends_on=deps,
    )
    graph.create(
        ArtifactType.RENDER_PLAN,
        content={
            "fps": timeline.content["fps"],
            "width": timeline.content["width"],
            "height": timeline.content["height"],
            "tracks": len(tracks),
        },
        depends_on=[timeline],
    )
    return graph


def apply_tracks(session: Any, tracks: list[dict]) -> None:
    """把磁盘上保存的轨道写回 TIMELINE 产物（/load 之后生效）。"""
    if not tracks:
        return
    graph = ensure_timeline_graph(session)
    timeline = graph.find_by_type(ArtifactType.TIMELINE)
    if timeline is None:
        return
    content = dict(timeline.content or {})
    content["tracks"] = tracks
    timeline.content = content
