"""Text -> Scene DSL -> graph layout bridge for Remotion.

This module is intentionally separate from the image-shot pipeline. It produces
structured graph scenes with nodes and edges, so concept videos can explain
relationships instead of rotating through searched images.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

from engine.shared.path_utils import get_project_root, ensure_public_audio_copy

FPS = 30
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920
DEFAULT_TTS_VOICE = "zh-CN-XiaoxiaoNeural"


def _extract_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    match = re.search(r"\{[\s\S]*\}", text)
    candidate = match.group(0) if match else text
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clean_id(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value or "").strip("_").lower()
    return cleaned or fallback


def _topic_subject(text: str) -> str:
    text = (text or "").strip()
    cleaned = re.sub(
        r"(是什么|什么是|底层原理|原理|为什么|怎么实现|如何实现|\?|？)",
        "",
        text,
    ).strip()
    return cleaned or text or "Concept"


def _call_llm_for_scene_dsl(text: str) -> dict[str, Any] | None:
    prompt = f"""
Convert the user topic into a single graph Scene DSL for a short explainer video.

Return JSON only. The graph must explain how components interact.
Schema:
{{
  "scene_type": "graph",
  "title": "short title",
  "summary": "one sentence narration goal",
  "nodes": [
    {{"id": "client", "label": "Client", "role": "source|processor|storage|result", "group": "optional"}}
  ],
  "edges": [
    {{"id": "e1", "from": "client", "to": "server", "label": "request", "kind": "request|store|lookup|return|control"}}
  ],
  "steps": [
    {{"caption": "what happens in this beat", "nodeIds": ["client"], "edgeIds": ["e1"]}}
  ],
  "timeline": [
    {{"time": 0, "duration": 2000, "action": "highlight_path", "text": "Client sends a request", "nodeIds": ["client", "server"], "edgeIds": ["e1"]}}
  ]
}}

Rules:
- Use 4 to 7 nodes.
- Use 4 to 8 edges.
- Timeline must have 4 to 8 ordered beats.
- Every timeline beat must highlight specific nodeIds and edgeIds.
- Use timeline to explain sequence, not just list components.
- Labels should be concise and visual.
- Do not describe stock photos or image search.
- Make the graph specific to the topic, not a generic template.

Topic:
{text}
""".strip()

    try:
        from agent.llm.ollama_client import get_llm_client

        client = get_llm_client()
        response = client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.25,
            timeout=60,
            max_tokens=900,
        )
        return _extract_json_object(response)
    except Exception:
        return None


def _fallback_scene_dsl(text: str) -> dict[str, Any]:
    subject = _topic_subject(text)

    nodes = [
        {"id": "input", "label": "Input", "role": "source"},
        {"id": "concept", "label": subject, "role": "processor"},
        {"id": "structure", "label": "Structure", "role": "storage"},
        {"id": "process", "label": "Process", "role": "processor"},
        {"id": "output", "label": "Output", "role": "result"},
    ]
    edges = [
        {"id": "e1", "from": "input", "to": "concept", "label": "ask", "kind": "request"},
        {"id": "e2", "from": "concept", "to": "structure", "label": "organize", "kind": "control"},
        {"id": "e3", "from": "structure", "to": "process", "label": "drive", "kind": "control"},
        {"id": "e4", "from": "process", "to": "output", "label": "produce", "kind": "return"},
    ]
    steps = [
        {"caption": f"Start from the question: {subject}.", "nodeIds": ["input", "concept"], "edgeIds": ["e1"]},
        {"caption": "Break it into visible components.", "nodeIds": ["concept", "structure"], "edgeIds": ["e2"]},
        {"caption": "Show how the components drive the process.", "nodeIds": ["structure", "process"], "edgeIds": ["e3"]},
        {"caption": "End with the result the user sees.", "nodeIds": ["process", "output"], "edgeIds": ["e4"]},
    ]
    return {
        "scene_type": "graph",
        "title": subject,
        "summary": f"Explain {subject} through component interactions.",
        "nodes": nodes,
        "edges": edges,
        "steps": steps,
        "timeline": [
            {
                "time": index * 2000,
                "duration": 2000,
                "action": "highlight_path",
                "text": step["caption"],
                "nodeIds": step["nodeIds"],
                "edgeIds": step["edgeIds"],
            }
            for index, step in enumerate(steps)
        ],
    }


def generate_scene_dsl(text: str) -> dict[str, Any]:
    dsl = _call_llm_for_scene_dsl(text) or _fallback_scene_dsl(text)
    dsl["scene_type"] = "graph"
    return _normalize_scene_dsl(dsl, text)


def _normalize_scene_dsl(dsl: dict[str, Any], text: str) -> dict[str, Any]:
    raw_nodes = dsl.get("nodes") if isinstance(dsl.get("nodes"), list) else []
    raw_edges = dsl.get("edges") if isinstance(dsl.get("edges"), list) else []

    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, node in enumerate(raw_nodes[:8]):
        if not isinstance(node, dict):
            continue
        node_id = _clean_id(str(node.get("id") or node.get("label") or ""), f"node_{index}")
        if node_id in seen:
            node_id = f"{node_id}_{index}"
        seen.add(node_id)
        nodes.append(
            {
                "id": node_id,
                "label": str(node.get("label") or node_id).strip()[:28],
                "role": str(node.get("role") or "processor").strip(),
                "group": str(node.get("group") or "").strip(),
            }
        )

    if len(nodes) < 2:
        return _fallback_scene_dsl(text)

    node_ids = {node["id"] for node in nodes}
    edges: list[dict[str, Any]] = []
    for index, edge in enumerate(raw_edges[:10]):
        if not isinstance(edge, dict):
            continue
        source = _clean_id(str(edge.get("from") or edge.get("source") or ""), "")
        target = _clean_id(str(edge.get("to") or edge.get("target") or ""), "")
        if source not in node_ids or target not in node_ids or source == target:
            continue
        edges.append(
            {
                "id": _clean_id(str(edge.get("id") or ""), f"edge_{index}"),
                "from": source,
                "to": target,
                "label": str(edge.get("label") or "").strip()[:24],
                "kind": str(edge.get("kind") or "flow").strip(),
            }
        )

    if not edges:
        for index in range(len(nodes) - 1):
            edges.append(
                {
                    "id": f"edge_{index}",
                    "from": nodes[index]["id"],
                    "to": nodes[index + 1]["id"],
                    "label": "flow",
                    "kind": "flow",
                }
            )

    raw_steps = dsl.get("steps") if isinstance(dsl.get("steps"), list) else []
    steps: list[dict[str, Any]] = []
    edge_ids = {edge["id"] for edge in edges}
    for index, step in enumerate(raw_steps[:8]):
        if not isinstance(step, dict):
            continue
        step_nodes = [node_id for node_id in step.get("nodeIds", []) if node_id in node_ids]
        step_edges = [edge_id for edge_id in step.get("edgeIds", []) if edge_id in edge_ids]
        if not step_nodes and not step_edges:
            continue
        steps.append(
            {
                "id": _clean_id(str(step.get("id") or ""), f"step_{index}"),
                "caption": str(step.get("caption") or "").strip()[:80],
                "nodeIds": step_nodes,
                "edgeIds": step_edges,
            }
        )

    if not steps:
        for edge in edges:
            steps.append(
                {
                    "id": f"step_{edge['id']}",
                    "caption": edge["label"] or f"{edge['from']} to {edge['to']}",
                    "nodeIds": [edge["from"], edge["to"]],
                    "edgeIds": [edge["id"]],
                }
            )

    raw_timeline = dsl.get("timeline") if isinstance(dsl.get("timeline"), list) else []
    timeline: list[dict[str, Any]] = []
    allowed_actions = {"highlight_node", "highlight_edge", "highlight_path", "pulse"}
    for index, event in enumerate(raw_timeline[:10]):
        if not isinstance(event, dict):
            continue
        event_nodes = [node_id for node_id in event.get("nodeIds", []) if node_id in node_ids]
        event_edges = [edge_id for edge_id in event.get("edgeIds", []) if edge_id in edge_ids]
        if not event_nodes and not event_edges:
            highlight = str(event.get("highlight") or "")
            for edge in edges:
                edge_key = f"{edge['from']}->{edge['to']}"
                if edge_key in highlight or edge["id"] in highlight:
                    event_edges.append(edge["id"])
                    event_nodes.extend([edge["from"], edge["to"]])
                    break
        if not event_nodes and not event_edges:
            continue
        try:
            time_ms = max(0, int(float(event.get("time", index * 2000))))
        except (TypeError, ValueError):
            time_ms = index * 2000
        try:
            duration_ms = max(400, int(float(event.get("duration", 2000))))
        except (TypeError, ValueError):
            duration_ms = 2000
        timeline.append(
            {
                "id": _clean_id(str(event.get("id") or ""), f"tl_{index}"),
                "time": time_ms,
                "duration": duration_ms,
                "action": (
                    str(event.get("action"))
                    if str(event.get("action")) in allowed_actions
                    else "highlight_path"
                ),
                "text": str(event.get("text") or event.get("caption") or "").strip()[:90],
                "nodeIds": list(dict.fromkeys(event_nodes)),
                "edgeIds": list(dict.fromkeys(event_edges)),
            }
        )

    if not timeline:
        timeline = [
            {
                "id": f"tl_{index}",
                "time": index * 2000,
                "duration": 2000,
                "action": "highlight_path",
                "text": step.get("caption") or "",
                "nodeIds": step["nodeIds"],
                "edgeIds": step["edgeIds"],
            }
            for index, step in enumerate(steps)
        ]

    return {
        "scene_type": "graph",
        "title": str(dsl.get("title") or _topic_subject(text)).strip()[:40],
        "summary": str(dsl.get("summary") or "").strip()[:120],
        "nodes": nodes,
        "edges": edges,
        "steps": steps,
        "timeline": timeline,
    }


def _role_color(role: str) -> dict[str, str]:
    palette = {
        "source": {"stroke": "#62d9ff", "fill": "rgba(98,217,255,0.14)"},
        "processor": {"stroke": "#7cf29a", "fill": "rgba(124,242,154,0.13)"},
        "storage": {"stroke": "#ffd166", "fill": "rgba(255,209,102,0.14)"},
        "result": {"stroke": "#ff8f70", "fill": "rgba(255,143,112,0.14)"},
    }
    return palette.get(role, {"stroke": "#9bb7ff", "fill": "rgba(155,183,255,0.12)"})


def apply_graph_layout(
    dsl: dict[str, Any],
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> dict[str, Any]:
    nodes = [dict(node) for node in dsl["nodes"]]
    edges = [dict(edge) for edge in dsl["edges"]]

    count = len(nodes)
    center_x = width / 2
    top = 360
    usable_h = 760

    if count <= 4:
        positions = [(center_x, top + i * (usable_h / max(1, count - 1))) for i in range(count)]
    else:
        positions = []
        for index in range(count):
            layer = index
            y = top + layer * (usable_h / max(1, count - 1))
            spread = 250 if count >= 5 else 190
            x = center_x + (math.sin(index * 1.7) * spread)
            if index == 0:
                x = center_x - 240
            elif index == count - 1:
                x = center_x + 240
            positions.append((x, y))

    for index, node in enumerate(nodes):
        x, y = positions[index]
        colors = _role_color(str(node.get("role", "")))
        node.update(
            {
                "x": round(x - 125),
                "y": round(y - 50),
                "width": 250,
                "height": 100,
                "color": colors["stroke"],
                "fill": colors["fill"],
            }
        )

    node_map = {node["id"]: node for node in nodes}
    for edge in edges:
        source = node_map[edge["from"]]
        target = node_map[edge["to"]]
        edge["points"] = [
            round(source["x"] + source["width"] / 2),
            round(source["y"] + source["height"] / 2),
            round(target["x"] + target["width"] / 2),
            round(target["y"] + target["height"] / 2),
        ]
        edge["color"] = {
            "request": "#62d9ff",
            "lookup": "#7cf29a",
            "store": "#ffd166",
            "return": "#ff8f70",
            "control": "#c7d2fe",
        }.get(str(edge.get("kind")), "#9bb7ff")

    return {
        **dsl,
        "nodes": nodes,
        "edges": edges,
    }


def _generate_explainer_script(topic: str, num_sentences: int = 5) -> list[str]:
    """LLM generates natural explainer narration (separate from visual captions)."""
    subject = _topic_subject(topic)
    prompt = f"""用讲解视频风格解释以下内容，要求口语化自然，像一个人在讲解。

{topic}

要求：
1. 每句 10-20 字
2. 逻辑清晰：先讲是什么 → 再讲为什么重要 → 最后讲怎么工作
3. 一共 {num_sentences} 句
4. 中文，不用序号或标记
5. 不要讲"今天我们来"这类开场

只输出纯文本，每行一句。""".strip()

    try:
        from agent.llm.ollama_client import get_llm_client

        client = get_llm_client()
        response = client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.4,
            timeout=60,
            max_tokens=300,
        )
        lines = [
            line.strip()
            for line in (response or "").strip().split("\n")
            if line.strip()
        ]
        if lines:
            return lines[:num_sentences]
    except Exception:
        pass

    # Fallback: rule-based explainer sentences
    if "redis" in topic.lower():
        return [
            "Redis其实就是一个把数据放在内存里的数据库。",
            "它之所以特别快，是因为所有读写都在内存中完成。",
            "不同的数据结构，底层用了哈希表、跳表和压缩列表来存储。",
            "理解Redis的关键，不是背命令，而是看它怎么组织数据。",
            "这样设计，让它既能做缓存，又能做消息队列和排行榜。",
        ]
    return [
        f"{subject}，本质上是一个高效的数据处理系统。",
        f"它的核心设计思路，是用空间换时间和用简单换可靠。",
        f"在底层，它会根据不同的场景选择最合适的数据结构。",
        f"真正理解{subject}，关键是看它如何组织数据和调度任务。",
    ]


def _call_llm_for_animation_plan(dsl: dict[str, Any]) -> dict[str, Any] | None:
    """Generate animation_plan from existing Scene DSL (second LLM call)."""
    nodes_summary = [
        {"id": n["id"], "label": n["label"], "role": n.get("role", "")}
        for n in dsl["nodes"]
    ]
    edges_summary = [
        {"id": e["id"], "from": e["from"], "to": e["to"], "label": e.get("label", ""), "kind": e.get("kind", "")}
        for e in dsl["edges"]
    ]
    steps_summary = [
        {"id": s["id"], "caption": s.get("caption", ""), "nodeIds": s["nodeIds"], "edgeIds": s["edgeIds"]}
        for s in dsl["steps"]
    ]

    prompt = f"""
You are a video animation director for a graph explainer video.

Given this graph structure, create an animation_plan (a "director script") that describes
exactly how to animate the graph reveal.

GRAPH STRUCTURE:
Nodes: {json.dumps(nodes_summary, ensure_ascii=False)}
Edges: {json.dumps(edges_summary, ensure_ascii=False)}
Steps (narration beats): {json.dumps(steps_summary, ensure_ascii=False)}
Title: {dsl.get("title", "")}

Return JSON only. Schema:
{{
  "version": 1,
  "steps": [
    {{
      "id": "unique_step_id",
      "action": "reveal|flow|highlight|pulse|camera_pan|miss_effect",
      "start": 0,
      "duration": 90,
      "nodeIds": ["node_id"],
      "edgeIds": ["edge_id"],
      "text": "optional caption",
      "intensity": 0.8
    }}
  ]
}}

DIRECTOR RULES:
1. First step MUST be "reveal" — bring all nodes on screen with staggered spring entrances.
   Use nodeIds containing ALL node IDs.
2. After reveal, alternate between "flow" (animate data along new edges) and "highlight"
   (glow the relevant nodes) to match the narration steps.
3. Use "pulse" on key nodes when the narrator emphasizes them (2-3 times max).
4. Use "camera_pan" when transitioning between distant parts of the graph (1-2 times max).
5. "miss_effect" is for decorative accents — use sparingly (0-2 times).
6. Every step in the narration steps should map to at least one animation_plan step.
7. Durations: reveal=60-90 frames, flow=30-60, highlight=30-60, pulse=20-30,
   camera_pan=45-75, miss_effect=15-25.
8. Stagger start times so animations flow smoothly (no gaps).
9. intensity ranges from 0.3 (subtle) to 1.0 (dramatic).
10. Total steps: 6-12.

ANIMATION PLAN:
""".strip()

    try:
        from agent.llm.ollama_client import get_llm_client

        client = get_llm_client()
        response = client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.35,
            timeout=60,
            max_tokens=1200,
        )
        return _extract_json_object(response)
    except Exception:
        return None


def _fallback_animation_plan(dsl: dict[str, Any]) -> dict[str, Any]:
    """Derive animation_plan from existing timeline when LLM fails."""
    steps = []
    timeline = dsl.get("timeline") or dsl.get("steps") or []

    # Step 1: Reveal all nodes
    all_node_ids = [n["id"] for n in dsl["nodes"]]
    steps.append({
        "id": "anim_reveal_all",
        "action": "reveal",
        "start": 0,
        "duration": 75,
        "nodeIds": all_node_ids,
        "edgeIds": [],
        "intensity": 0.85,
    })

    # Map timeline entries to flow + highlight steps
    for i, event in enumerate(timeline):
        start_frame = int(event.get("start", i * 2000 // 1000 * 30))
        duration = int(event.get("duration", 2000 // 1000 * 30))

        node_ids = event.get("nodeIds", [])
        edge_ids = event.get("edgeIds", [])
        text = event.get("text") or event.get("caption") or ""

        # Flow step for edges
        if edge_ids:
            steps.append({
                "id": f"anim_flow_{i}",
                "action": "flow",
                "start": start_frame,
                "duration": max(30, duration // 2),
                "nodeIds": node_ids,
                "edgeIds": edge_ids,
                "text": text,
                "intensity": 0.8,
            })

        # Highlight step for nodes
        if node_ids:
            steps.append({
                "id": f"anim_highlight_{i}",
                "action": "highlight",
                "start": start_frame + max(30, duration // 2),
                "duration": max(30, duration - max(30, duration // 2)),
                "nodeIds": node_ids,
                "edgeIds": edge_ids,
                "text": text,
                "intensity": 0.75,
            })

    return {"version": 1, "steps": steps}


def _generate_explainer_audio_tracks(
    script_sentences: list[str],
    total_ms: int,
    voice: str = DEFAULT_TTS_VOICE,
    rate: int = 0,
) -> list[dict[str, Any]]:
    """Generate TTS for natural script sentences, serial (no overlap)."""
    try:
        from core.tts_module import get_tts_module
    except Exception:
        return []

    tts = get_tts_module(voice)
    tts.set_rate(rate)
    backend = tts.get_backend_name() if hasattr(tts, "get_backend_name") else "none"

    output_root = get_project_root() / "output" / "generated-audio"
    output_root.mkdir(parents=True, exist_ok=True)
    ext = ".mp3" if backend in {"edge", "gtts", "baidu", "xunfei"} else ".wav"

    GAP_MS = 300  # small pause between sentences for natural pacing

    audio_tracks: list[dict[str, Any]] = []
    current_ms = 0.0

    for index, sentence in enumerate(script_sentences):
        text = sentence.strip()
        if not text:
            continue

        start_frame = round(current_ms / 1000 * FPS)

        file_name = f"explain_{index:03d}_{uuid.uuid4().hex[:8]}{ext}"
        source_path = output_root / file_name
        try:
            if tts.generate_audio(text, str(source_path)) and source_path.exists():
                measured_s = tts.get_audio_duration(str(source_path))
                measured_ms = measured_s * 1000
                measured_frames = max(1, round(measured_s * FPS))
                src = ensure_public_audio_copy(source_path, file_name)
                audio_tracks.append({
                    "id": f"explain_audio_{index}",
                    "src": src,
                    "start": start_frame,
                    "duration": measured_frames,
                    "text": text,
                })
                current_ms += measured_ms + GAP_MS
        except Exception:
            continue

    return audio_tracks


def build_graph_video_layout(
    text: str,
    total_ms: int = 12000,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    enable_audio: bool = False,
    voice: str = DEFAULT_TTS_VOICE,
    rate: int = 0,
) -> dict[str, Any]:
    total_frames = max(1, round(total_ms / 1000 * FPS))
    dsl = generate_scene_dsl(text)
    graph = apply_graph_layout(dsl, width=width, height=height)

    timeline = []
    raw_timeline = graph.get("timeline") or []
    if raw_timeline:
        max_end_ms = max(
            int(event.get("time", 0)) + int(event.get("duration", 0))
            for event in raw_timeline
        ) or total_ms
        scale = total_ms / max_end_ms
        for index, event in enumerate(raw_timeline):
            start = round(int(event.get("time", 0)) * scale / 1000 * FPS)
            duration = max(1, round(int(event.get("duration", 2000)) * scale / 1000 * FPS))
            if index == len(raw_timeline) - 1:
                duration = max(1, total_frames - start)
            timeline.append({**event, "start": start, "duration": duration})
    graph["timeline"] = timeline

    steps = []
    source_steps = timeline if timeline else graph["steps"]
    for index, step in enumerate(source_steps):
        start = int(step["start"])
        duration = int(step["duration"])
        steps.append({**step, "start": start, "duration": duration})
    graph["steps"] = steps

    # Generate audio first so total_frames reflects true duration
    audio_tracks: list[dict[str, Any]] = []
    explainer_script: list[str] = []
    if enable_audio:
        explainer_script = _generate_explainer_script(text, num_sentences=5)
        audio_tracks = _generate_explainer_audio_tracks(
            explainer_script, total_ms=total_ms, voice=voice, rate=rate
        )
        audio_end = max(
            (t["start"] + t["duration"] for t in audio_tracks),
            default=0,
        )
        if audio_end > total_frames:
            total_frames = audio_end

    # Generate animation plan (director script)
    animation_plan = _call_llm_for_animation_plan(graph) or _fallback_animation_plan(graph)
    # Scale plan step times to total_frames
    raw_plan_steps = animation_plan.get("steps", [])
    if raw_plan_steps:
        max_plan_end = max(
            s.get("start", 0) + s.get("duration", 30)
            for s in raw_plan_steps
        ) or total_frames
        scale = total_frames / max_plan_end
        scaled_steps = []
        for s in raw_plan_steps:
            scaled_steps.append({
                **s,
                "start": round(s.get("start", 0) * scale),
                "duration": max(1, round(s.get("duration", 30) * scale)),
            })
        animation_plan["steps"] = scaled_steps
    graph["animation_plan"] = animation_plan

    # === VIRAL DIRECTOR PLAN (硬覆盖，验证视觉表现) ===
    _nids = [n["id"] for n in graph["nodes"]]
    _eids = [e["id"] for e in graph["edges"]]
    _core = _nids[1] if len(_nids) > 1 else _nids[0]
    graph["animation_plan"] = {
        "version": 1,
        "steps": [
            {"id": "intro_boom",    "action": "reveal",     "start": 0,   "duration": 25,  "nodeIds": [_core],   "edgeIds": [],     "intensity": 1.2},
            {"id": "others_fade",   "action": "reveal",     "start": 20,  "duration": 40,  "nodeIds": _nids,     "edgeIds": [],     "intensity": 0.6},
            {"id": "flow_in",       "action": "flow",       "start": 60,  "duration": 80,  "nodeIds": _nids,     "edgeIds": _eids,  "intensity": 1.0},
            {"id": "redis_pulse",   "action": "pulse",      "start": 100, "duration": 120, "nodeIds": [_core],   "edgeIds": [],     "intensity": 1.3},
            {"id": "camera_focus",  "action": "camera_pan", "start": 140, "duration": 80,  "nodeIds": [],        "edgeIds": [],     "intensity": 1.0, "cameraFrom": _nids[0], "cameraTo": _core},
            {"id": "final_glow",    "action": "highlight",  "start": 220, "duration": total_frames - 220, "nodeIds": _nids, "edgeIds": _eids, "intensity": 0.9},
        ]
    }
    # === END VIRAL PLAN ===

    # Subtitles: prefer audio-track-synced captions so text matches voiceover
    elements: list[dict[str, Any]] = []
    if audio_tracks:
        for track in audio_tracks:
            elements.append({
                "id": f"subtitle_{track['id']}",
                "type": "text",
                "text": track["text"],
                "x": 540,
                "y": 1450,
                "fontSize": 38,
                "color": "#f8fbff",
                "fontWeight": 680,
                "textAlign": "center",
                "lineHeight": 1.35,
                "maxWidth": 860,
                "start": track["start"],
                "duration": track["duration"],
                "zIndex": 20,
                "animation": {"enter": "blur-in", "exit": "fade", "duration": 8},
            })
    else:
        for index, step in enumerate(steps):
            elements.append({
                "id": f"graph_caption_{index}",
                "type": "text",
                "text": step.get("text") or step.get("caption") or graph.get("summary") or graph.get("title"),
                "x": 540,
                "y": 1450,
                "fontSize": 38,
                "color": "#dbeafe",
                "fontWeight": 650,
                "textAlign": "center",
                "lineHeight": 1.35,
                "maxWidth": 860,
                "start": step["start"],
                "duration": step["duration"],
                "zIndex": 20,
                "animation": {"enter": "blur-in", "exit": "fade", "duration": 12},
            })

    # Add text overlay elements from animation_plan steps (only when no audio subtitles)
    if not audio_tracks:
        for step in graph["animation_plan"].get("steps", []):
            if step.get("text"):
                elements.append({
                    "id": f"anim_caption_{step['id']}",
                    "type": "text",
                    "text": step["text"],
                    "x": 540,
                    "y": 1450,
                    "fontSize": 38,
                    "color": "#dbeafe",
                    "fontWeight": 650,
                    "textAlign": "center",
                    "lineHeight": 1.35,
                    "maxWidth": 860,
                    "start": step["start"],
                    "duration": step["duration"],
                    "zIndex": 20,
                    "animation": {"enter": "blur-in", "exit": "fade", "duration": 12},
                })

    return {
        "width": width,
        "height": height,
        "fps": FPS,
        "durationInFrames": total_frames,
        "background": "#070b10",
        "scene_type": "graph",
        "graph": graph,
        "nodes": graph["nodes"],
        "edges": graph["edges"],
        "elements": elements,
        "shots": [],
        "audioTracks": audio_tracks,
        "explainerScript": explainer_script,
    }


def render_graph_video(
    text: str,
    layout_out: str = "output/graph_layout.json",
    video_out: str = "output/graph_scene.mp4",
    total_ms: int = 12000,
    enable_audio: bool = False,
    voice: str = DEFAULT_TTS_VOICE,
    rate: int = 0,
) -> tuple[str, str]:
    layout = build_graph_video_layout(
        text,
        total_ms=total_ms,
        enable_audio=enable_audio,
        voice=voice,
        rate=rate,
    )
    with open(layout_out, "w", encoding="utf-8") as file:
        json.dump(layout, file, ensure_ascii=False, indent=2)

    result = subprocess.run(
        [
            "node",
            "render-agent-semantic.mjs",
            f"..\\{layout_out}",
            f"..\\{video_out}",
        ],
        cwd="remotion-renderer",
        check=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("Remotion graph render failed")
    return layout_out, video_out


def main() -> None:
    parser = argparse.ArgumentParser(description="Build graph Remotion layout from text.")
    parser.add_argument("text", help="Topic or question")
    parser.add_argument("--out", default="output/graph_layout.json", help="Output layout JSON")
    parser.add_argument("--render", action="store_true", help="Render the generated graph layout to mp4")
    parser.add_argument("--video-out", default="output/graph_scene.mp4", help="Output mp4 path when --render is set")
    parser.add_argument("--duration-ms", type=int, default=12000)
    parser.add_argument("--enable-audio", action="store_true", default=True,
                        help="Generate TTS narration audio (default: on, use --no-enable-audio to skip)")
    parser.add_argument("--no-enable-audio", action="store_false", dest="enable_audio",
                        help="Skip TTS narration audio generation")
    parser.add_argument("--voice", default=DEFAULT_TTS_VOICE, help=f"TTS voice (default: {DEFAULT_TTS_VOICE})")
    parser.add_argument("--rate", type=int, default=0, help="TTS speed (-10 to +10)")
    args = parser.parse_args()

    if args.render:
        layout_out, video_out = render_graph_video(
            args.text,
            layout_out=args.out,
            video_out=args.video_out,
            total_ms=args.duration_ms,
            enable_audio=args.enable_audio,
            voice=args.voice,
            rate=args.rate,
        )
        print(json.dumps({"layout": layout_out, "video": video_out}, ensure_ascii=False))
        return

    layout = build_graph_video_layout(
        args.text,
        total_ms=args.duration_ms,
        enable_audio=args.enable_audio,
        voice=args.voice,
        rate=args.rate,
    )
    with open(args.out, "w", encoding="utf-8") as file:
        json.dump(layout, file, ensure_ascii=False, indent=2)
    print(args.out)


if __name__ == "__main__":
    main()
