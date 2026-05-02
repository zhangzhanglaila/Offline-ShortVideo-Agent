"""Text -> Scene DSL -> graph layout bridge for Remotion.

This module is intentionally separate from the image-shot pipeline. It produces
structured graph scenes with nodes and edges, so concept videos can explain
relationships instead of rotating through searched images.
"""

from __future__ import annotations

import argparse
import hashlib
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

    audio_tracks: list[dict[str, Any]] = []

    for index, sentence in enumerate(script_sentences):
        text = sentence.strip()
        if not text:
            continue

        file_name = f"explain_{index:03d}_{uuid.uuid4().hex[:8]}{ext}"
        source_path = output_root / file_name
        try:
            if tts.generate_audio(text, str(source_path)) and source_path.exists():
                measured_s = tts.get_audio_duration(str(source_path))
                measured_frames = max(1, round(measured_s * FPS))
                src = ensure_public_audio_copy(source_path, file_name)
                audio_tracks.append({
                    "id": f"explain_audio_{index}",
                    "src": src,
                    "start": 0,  # P4.1: normalized to serial by _normalize_audio_tracks
                    "duration": measured_frames,
                    "text": text,
                })
        except Exception:
            continue

    return audio_tracks


def _build_hook_text(topic: str, first_sentence: str) -> str:
    """Derive a punchy hook from topic + first audio sentence.

    Uses curated templates for stronger curiosity gap and variety.
    Template selection is deterministic (hash-based) so the same topic
    always produces the same hook.
    """
    subject = _topic_subject(topic)
    clean = first_sentence.strip().rstrip("。，！？,.!?")

    # If the first sentence is already a question, use it directly
    if "？" in clean or "?" in clean:
        return clean

    templates = [
        f"你真的了解{subject}吗？",
        f"{subject}其实比你想的更复杂",
        f"{subject}是怎么工作的？",
        f"{subject}的底层原理，90%的人都不知道",
        f"一个视频讲清楚{subject}",
        f"为什么{subject}这么重要？",
        f"{subject}到底做了什么？",
    ]

    # Deterministic selection via hash of the topic
    idx = hash(topic) % len(templates)
    return templates[idx]


def _build_summary_items(graph: dict[str, Any]) -> list[str]:
    """Extract 3-4 verb-phrase summary items from graph semantics.

    Instead of abstract nouns (e.g. "高速缓存"), output actionable
    verb phrases (e.g. "缓存热点数据") so cards read like capabilities.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    roles = [n.get("role", "").lower() for n in nodes]
    labels = [n.get("label", "") for n in nodes]
    rl_pairs = list(zip(roles, labels))

    # Priority 1: verb-phrase mapping from role + label keywords
    verb_map: dict[str, str] = {
        "cache": "缓存热点数据",
        "缓存": "缓存高频访问数据",
        "queue": "处理异步消息队列",
        "消息": "实现消息队列系统",
        "rank": "实时计算排行榜",
        "排行": "实时排名与数据统计",
        "lock": "保证分布式锁一致性",
        "锁": "协调分布式资源锁",
        "store": "持久化关键业务数据",
        "存储": "高效存储与索引数据",
        "db": "管理数据库读写请求",
        "source": "接收外部输入数据",
        "result": "输出最终计算结果",
        "processor": "调度核心处理流程",
        "proxy": "代理并路由请求流量",
        "router": "智能分发请求到后端",
    }

    items: list[str] = []
    seen: set[str] = set()

    for r, l in rl_pairs:
        for keyword, verb_phrase in verb_map.items():
            if keyword in r or keyword in l:
                if verb_phrase not in seen:
                    items.append(verb_phrase)
                    seen.add(verb_phrase)
                break

    # Priority 2: derive from edge semantics (action + target)
    if len(items) < 3:
        for edge in edges:
            kind = (edge.get("kind") or "").lower()
            label_text = (edge.get("label") or "").lower()
            action_map = {
                "request": "发起请求",
                "store": "存储数据",
                "lookup": "查询索引",
                "return": "返回结果",
                "control": "调度控制",
                "flow": "流转消息",
            }
            action = action_map.get(kind, action_map.get(label_text, ""))
            if action and action not in seen:
                items.append(action)
                seen.add(action)
            if len(items) >= 4:
                break

    # Fallback: generic capability phrases
    if len(items) < 3:
        fallbacks = ["高性能处理", "弹性可扩展", "高可用保障", "低延迟响应"]
        for fb in fallbacks:
            if fb not in seen and len(items) < 4:
                items.append(fb)

    return items[:4]


def classify_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """Node hierarchy + edge importance for visual grammar.

    Returns:
        hero: str                 — central node ID
        secondary: list[str]      — directly connected to hero
        others: list[str]         — everything else
        primary_edges: list[str]  — edges touching hero
        secondary_edges: list[str] — edges among secondary nodes
        tertiary_edges: list[str] — everything else
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    node_map: dict[str, dict[str, Any]] = {n["id"]: n for n in nodes}

    if not nodes:
        return {"hero": "", "secondary": [], "others": [],
                "primary_edges": [], "secondary_edges": [], "tertiary_edges": []}

    nids = list(node_map.keys())
    if len(nids) == 1:
        return {"hero": nids[0], "secondary": [], "others": [],
                "primary_edges": [], "secondary_edges": [], "tertiary_edges": []}

    # Centrality scoring (same as original pick_main_node)
    in_deg: dict[str, int] = {nid: 0 for nid in nids}
    out_deg: dict[str, int] = {nid: 0 for nid in nids}
    for e in edges:
        frm = e.get("from", "")
        to = e.get("to", "")
        if frm in out_deg:
            out_deg[frm] += 1
        if to in in_deg:
            in_deg[to] += 1

    CENTRAL_ROLES = {"processor", "storage", "server", "core", "cache", "engine"}
    best_id = nids[0]
    best_score = -1
    for n in nodes:
        nid = n["id"]
        role = (n.get("role") or "").lower()
        role_boost = 3 if role in CENTRAL_ROLES else 0
        label = (n.get("label") or "").lower()
        label_boost = 2 if any(
            kw in label for kw in ("redis", "缓存", "cache", "核心", "引擎", "server", "db")
        ) else 0
        score = in_deg[nid] * 2 + out_deg[nid] + role_boost + label_boost
        if score > best_score:
            best_score = score
            best_id = nid

    hero = best_id

    # Build adjacency
    neighbors: dict[str, set[str]] = {nid: set() for nid in nids}
    for e in edges:
        frm = e.get("from", "")
        to = e.get("to", "")
        if frm in neighbors and to in neighbors:
            neighbors[frm].add(to)
            neighbors[to].add(frm)

    secondary_set = neighbors.get(hero, set())
    others_set = set(nids) - {hero} - secondary_set

    primary_edges: list[str] = []
    secondary_edges_list: list[str] = []
    tertiary_edges: list[str] = []
    for e in edges:
        eid = e["id"]
        frm = e.get("from", "")
        to = e.get("to", "")
        if hero in (frm, to):
            primary_edges.append(eid)
        elif frm in secondary_set or to in secondary_set:
            secondary_edges_list.append(eid)
        else:
            tertiary_edges.append(eid)

    return {
        "hero": hero,
        "secondary": list(secondary_set),
        "others": list(others_set),
        "primary_edges": primary_edges,
        "secondary_edges": secondary_edges_list,
        "tertiary_edges": tertiary_edges,
    }


def build_default_plan(
    graph: dict[str, Any],
    total_frames: int,
    audio_tracks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Deterministic 6-beat animation plan for any graph.

    Beat structure:
        Beat 1 — Hero solo reveal (intensity 1.2)
        Beat 2 — Ensemble reveal   (intensity 0.6)
        Beat 3 — Edge flow          (intensity 1.0)
        Beat 4 — Hero pulse         (intensity 1.3)
        Beat 5 — Camera pan to hero (intensity 1.0)
        Beat 6 — Final highlight    (intensity 0.9, fills remaining)

    When audio_tracks are provided, beats are anchored to audio boundaries
    so visuals and voiceover stay in sync.
    """
    cg = classify_graph(graph)
    main = cg["hero"]
    secondary = cg["secondary"]
    primary_edges = cg["primary_edges"]
    secondary_edges = cg["secondary_edges"]
    nids = [n["id"] for n in graph["nodes"]]
    all_eids = [e["id"] for e in graph["edges"]]

    # Pick cameraFrom: prefer a node that has an edge *into* the hero
    camera_from = secondary[0] if secondary else (nids[0] if nids else "")
    hero_incoming = [
        e.get("from", "") for e in graph.get("edges", [])
        if e.get("to", "") == main
    ]
    if hero_incoming:
        camera_from = hero_incoming[0]

    if audio_tracks and len(audio_tracks) >= 3:
        # Audio-driven: anchor each beat to a voiceover sentence boundary
        at = audio_tracks
        def at_end(i: int) -> int:
            return at[i]["start"] + at[i]["duration"]

        # Beat 1 — hero solo: first ~1.5s of sentence 0
        b1 = at[0]["start"]
        b1_end = min(b1 + 49, at_end(0) - 4)

        # Beat 2 — ensemble reveal: rest of sentence 0 → end of sentence 1
        b2 = b1_end
        b2_end = at_end(min(1, len(at) - 1))

        # Beat 3 — edge flow: sentence 1 → end of sentence 2
        b3 = at[1]["start"] if len(at) > 1 else b2_end
        b3_end = at_end(min(2, len(at) - 1))

        # Beat 4 — hero pulse: sentence 2 → end of sentence 3
        b4 = at[2]["start"] if len(at) > 2 else b3_end
        b4_end = at_end(min(3, len(at) - 1))

        # Beat 5 — camera pan: sentence 3 → cover sentence 4
        b5 = at[3]["start"] if len(at) > 3 else b4_end
        b5_end = at_end(min(4, len(at) - 1))

        # Beat 6 — finale: last sentence → video end
        b6 = at[-1]["start"]
        b6_end = total_frames
    else:
        # Fallback: proportional scaling (no audio)
        scale = max(1, total_frames / 360)
        def beat(s: float, d: float) -> tuple[int, int]:
            return (round(s * scale), max(1, round(d * scale)))
        b1, b1_end = 0, 0
        b1, b1_end = beat(0, 25)
        b2, b2_end = beat(20, 40)
        b3, b3_end = beat(60, 80)
        b4, b4_end = beat(100, 120)
        b5, b5_end = beat(140, 80)
        b6 = round(220 * scale)
        b6_end = total_frames
        b1_end += b1
        b2_end += b2
        b3_end += b3
        b4_end += b4
        b5_end += b5

    steps = [
        {"id": "intro_hero",     "action": "reveal",     "start": b1,    "duration": b1_end - b1,     "nodeIds": [main],           "edgeIds": [],              "intensity": 1.2},
        {"id": "intro_ensemble", "action": "reveal",     "start": b2,    "duration": b2_end - b2,     "nodeIds": nids,             "edgeIds": [],              "intensity": 0.6},
        {"id": "flow_primary",   "action": "flow",       "start": b3,    "duration": b3_end - b3,     "nodeIds": [main] + secondary,"edgeIds": primary_edges,    "intensity": 1.0},
        {"id": "hero_pulse",     "action": "pulse",      "start": b4,    "duration": b4_end - b4,     "nodeIds": [main],           "edgeIds": [],              "intensity": 1.3},
        {"id": "camera_hero",    "action": "camera_pan", "start": b5,    "duration": b5_end - b5,     "nodeIds": [],               "edgeIds": [],              "intensity": 1.0, "cameraFrom": camera_from, "cameraTo": main},
        {"id": "finale",         "action": "highlight",  "start": b6,    "duration": b6_end - b6,     "nodeIds": nids,             "edgeIds": all_eids,        "intensity": 0.9},
    ]

    # ── Shot system: each beat becomes a camera shot ──
    shots = [
        {"focus": "node",    "targetIds": [main],           "camera": "zoom-in",  "start": b1, "duration": b1_end - b1},
        {"focus": "overview","targetIds": nids,             "camera": "pull-out", "start": b2, "duration": b2_end - b2},
        {"focus": "edge",    "targetIds": primary_edges,    "camera": "pan",      "start": b3, "duration": b3_end - b3},
        {"focus": "node",    "targetIds": [main],           "camera": "push-in",  "start": b4, "duration": b4_end - b4},
        {"focus": "group",   "targetIds": [main] + secondary,"camera": "pan",     "start": b5, "duration": b5_end - b5},
        {"focus": "overview","targetIds": nids,             "camera": "static",   "start": b6, "duration": b6_end - b6},
    ]
    # Attach first-sentence text to first shot when audio available
    if audio_tracks:
        shots[0]["text"] = audio_tracks[0].get("text", "")

    return {
        "version": 1,
        "nodeTiers": {"hero": main, "secondary": secondary, "others": cg["others"]},
        "steps": steps,
        "shots": shots,
    }


def _normalize_audio_tracks(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """P4.1: Force serial timeline — no overlap, each track chains after the previous."""
    if not tracks:
        return tracks
    tracks = sorted(tracks, key=lambda x: x["start"])
    fixed: list[dict[str, Any]] = []
    cursor = 0
    GAP_MS = 100
    GAP_FRAMES = max(1, round(GAP_MS / 1000 * FPS))

    for t in tracks:
        fixed.append({**t, "start": cursor, "duration": t["duration"]})
        cursor += t["duration"] + GAP_FRAMES

    return fixed


def _director_cache_path() -> Path:
    return get_project_root() / "output" / ".director_cache.json"


def _load_director_cache() -> dict[str, Any]:
    path = _director_cache_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"plans": {}, "hits": 0, "misses": 0}


def _save_director_cache(cache: dict[str, Any]) -> None:
    path = _director_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Prune to max 200 entries
    plans = cache.get("plans", {})
    if isinstance(plans, dict) and len(plans) > 200:
        # Keep only the most recent 150
        keys = list(plans.keys())[-150:]
        cache["plans"] = {k: plans[k] for k in keys}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _compute_director_cache_key(topic: str, graph: dict[str, Any]) -> str:
    """Stable cache key from topic + graph topology (node/edge IDs)."""
    nids = sorted(n["id"] for n in graph.get("nodes", []))
    eids = sorted(e["id"] for e in graph.get("edges", []))
    payload = json.dumps({"t": topic.strip().lower(), "n": nids, "e": eids}, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _extract_audio_emphasis(
    audio_tracks: list[dict[str, Any]],
    graph: dict[str, Any],
) -> list[str]:
    """P6.2: Extract node emphasis from audio narration text.

    Scans audio track text for node labels — when the narrator mentions
    a node name, that node gets visual emphasis. Runs as a complement
    to LLM director emphasis (or standalone when LLM is disabled).
    """
    if not audio_tracks:
        return []
    all_text = " ".join(t.get("text", "") for t in audio_tracks).lower()
    if not all_text:
        return []
    emphasized: list[str] = []
    for node in graph.get("nodes", []):
        label = (node.get("label", "")).lower()
        nid = node["id"]
        # Check if node label appears in audio text (substring match)
        if label and len(label) >= 2 and label in all_text:
            emphasized.append(nid)
    return emphasized


def _log_director_diff(director_plan, translated_scenes: list[dict[str, Any]]) -> None:
    """P3.4: Print director-vs-executor comparison for every shot.

    Format:
        [Scene] hook — "grab attention"
          [LLM] introduce_node → Redis
          [Translator] camera=zoom-in focus=node targetIds=[redis]
          [LLM] show_flow → Client→Redis
          [Translator] camera=pan focus=edge targetIds=[e1]
          [FALLBACK] emphasize → UnknownLabel  → camera=static focus=overview
    """
    print("\n" + "=" * 72)
    print("  DIRECTOR vs EXECUTOR")
    print("=" * 72)

    for si, sp in enumerate(director_plan.scenes):
        ts = translated_scenes[si] if si < len(translated_scenes) else None
        dropped = ts.get("_dropped", 0) if ts else 0
        all_fb = ts.get("_allFallback", False) if ts else False
        tag = " [ALL FALLBACK]" if all_fb else (f" [{dropped} dropped]" if dropped else "")
        print(f"\n  [Scene {si+1}] {sp.type} — \"{sp.goal}\"{tag}")

        if not ts:
            print("    [MISS] Scene dropped by translator (no valid shots)")
            continue

        translated_shots = ts.get("shots", [])

        for shot_idx, si_obj in enumerate(sp.shots):
            intent = si_obj.intent
            target = si_obj.target

            match = translated_shots[shot_idx] if shot_idx < len(translated_shots) else None

            if match:
                is_fb = match.get("_fallback", False)
                prefix = "[FALLBACK]" if is_fb else "[LLM]"
                print(f"    {prefix} {intent} → {target}")
                print(f"    [Translator] camera={match['camera']} focus={match['focus']} targetIds={match['targetIds']}")
            else:
                print(f"    [DROP] {intent} → {target}  (guard 3: count limit)")

    emphasis = getattr(director_plan, "emphasis", []) or []
    if emphasis:
        print(f"\n  [Emphasis keywords] {emphasis}")
    print(f"  [Pace] {director_plan.pace}")
    print("=" * 72 + "\n")


def build_graph_video_layout(
    text: str,
    total_ms: int = 12000,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    enable_audio: bool = False,
    voice: str = DEFAULT_TTS_VOICE,
    rate: int = 0,
    use_llm_director: bool = False,
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
        audio_tracks = _normalize_audio_tracks(audio_tracks)
        # P4.1 guard + debug: print timeline + assert no overlap
        print("  [Audio] Normalized timeline:")
        for i, t in enumerate(audio_tracks):
            end = t["start"] + t["duration"]
            gap = audio_tracks[i + 1]["start"] - end if i + 1 < len(audio_tracks) else 0
            print(f"    track[{i}] start={t['start']:>5} end={end:>5} dur={t['duration']:>4} gap={gap:>3}")
            if gap < 0:
                raise RuntimeError(
                    f"Audio overlap: track[{i}] end={end} > next start={audio_tracks[i+1]['start']}"
                )
        audio_end = max(
            (t["start"] + t["duration"] for t in audio_tracks),
            default=0,
        )
        if audio_end > total_frames:
            total_frames = audio_end

    # Step 2: Director — rule-based baseline (always computed as fallback)
    graph["animation_plan"] = build_default_plan(graph, total_frames, audio_tracks if audio_tracks else None)
    graph["shots"] = graph["animation_plan"].get("shots", [])

    # Step 3 (P3): LLM Director Brain — optional semantic intent layer
    # LLM only outputs "what to show", translator converts to "how to show".
    # When disabled or LLM fails, rule-based plan from Step 2 is used.
    llm_scenes: list[dict[str, Any]] | None = None
    if use_llm_director:
        # ── P3.5: Failure cache — skip LLM for previously failed hashes ──
        cache_key = _compute_director_cache_key(text, graph)
        director_cache = _load_director_cache()

        if cache_key in director_cache.get("plans", {}):
            cached = director_cache["plans"][cache_key]
            director_cache["hits"] = director_cache.get("hits", 0) + 1
            _save_director_cache(director_cache)
            if cached is not None:
                # Previous LLM call succeeded — replay cached result
                llm_scenes = cached.get("scenes")
                if llm_scenes:
                    graph["_emphasis"] = cached.get("emphasis", [])
                    graph["_pace"] = cached.get("pace", "medium")
                    graph["_debug"] = True
                    all_llm_shots: list[dict[str, Any]] = []
                    for sc in llm_scenes:
                        if sc["type"] == "graph":
                            for s in sc.get("shots", []):
                                if "start" in s and "duration" in s:
                                    all_llm_shots.append(s)
                    if all_llm_shots:
                        graph["shots"] = all_llm_shots
                    print(f"  [Cache] hit → {len(llm_scenes)} scenes (skipped LLM)")
            else:
                # Previous LLM call failed — skip entirely, use fallback
                print(f"  [Cache] hit → previous failure (skipped LLM)")
        else:
            director_cache["misses"] = director_cache.get("misses", 0) + 1
            try:
                from engine.bridge.director_plan import (
                    call_llm_for_director_plan,
                    plan_to_scenes_and_shots,
                )

                director_plan = call_llm_for_director_plan(text, graph)
                if director_plan:
                    translated = plan_to_scenes_and_shots(
                        director_plan, graph, total_frames,
                        audio_tracks if audio_tracks else [],
                    )
                    llm_scenes = translated.get("scenes")
                    if llm_scenes:
                        # ── P3.4: Director vs Executor observability log ──
                        _log_director_diff(director_plan, llm_scenes)
                        # Debug overlay in rendered video
                        graph["_debug"] = True
                        # Inject emphasis into graph for visual boost
                        graph["_emphasis"] = translated.get("emphasis", [])
                        graph["_pace"] = translated.get("pace", "medium")
                        # Override graph shots with LLM-directed shots
                        all_llm_shots: list[dict[str, Any]] = []
                        for sc in llm_scenes:
                            if sc["type"] == "graph":
                                for s in sc.get("shots", []):
                                    if "start" in s and "duration" in s:
                                        all_llm_shots.append(s)
                        if all_llm_shots:
                            graph["shots"] = all_llm_shots
                        # Cache success
                        director_cache["plans"][cache_key] = {
                            "scenes": llm_scenes,
                            "emphasis": translated.get("emphasis", []),
                            "pace": translated.get("pace", "medium"),
                        }
                        _save_director_cache(director_cache)
                else:
                    # Cache failure (null = known failure, skip next time)
                    director_cache["plans"][cache_key] = None
                    _save_director_cache(director_cache)
            except Exception:
                director_cache["plans"][cache_key] = None
                _save_director_cache(director_cache)
                llm_scenes = None

    # P6.2: Audio-driven keyword → node emphasis (complements or substitutes LLM)
    if audio_tracks:
        audio_keywords = _extract_audio_emphasis(audio_tracks, graph)
        existing = set(graph.get("_emphasis", []) if isinstance(graph.get("_emphasis"), list) else [])
        existing.update(audio_keywords)
        if existing:
            graph["_emphasis"] = list(existing)

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

    # Build multi-scene sequence: hook → explain (graph) → summary (cards)
    # Scene timing is driven entirely by audio segments — no manual calculation.
    scenes: list[dict[str, Any]] = []
    if audio_tracks and len(audio_tracks) >= 3:
        # hook scene = first audio sentence
        hook_track = audio_tracks[0]
        # graph scene = middle explanation sentences (audio_tracks[1:-1])
        middle_tracks = audio_tracks[1:-1]
        graph_start = middle_tracks[0]["start"]
        graph_end = middle_tracks[-1]["start"] + middle_tracks[-1]["duration"]
        # cards scene = last audio sentence
        cards_track = audio_tracks[-1]

        # Use LLM intent for hook text + cards title when available
        hook_text = _build_hook_text(text, hook_track.get("text", ""))
        cards_title = "它能做什么？"
        cards_items = _build_summary_items(graph)

        if llm_scenes:
            for lsc in llm_scenes:
                if lsc["type"] == "hook" and lsc.get("goal"):
                    hook_text = lsc["goal"]
                if lsc["type"] == "cards" and lsc.get("goal"):
                    cards_title = lsc["goal"]

        scenes = [
            {
                "id": "scene_hook",
                "type": "hook",
                "start": hook_track["start"],
                "duration": hook_track["duration"],
                "text": hook_text,
                "audioTracks": [hook_track],
            },
            {
                "id": "scene_graph",
                "type": "graph",
                "start": graph_start,
                "duration": graph_end - graph_start,
                "graph": graph,
                "audioTracks": middle_tracks,
            },
            {
                "id": "scene_cards",
                "type": "cards",
                "start": cards_track["start"],
                "duration": cards_track["duration"],
                "title": cards_title,
                "items": cards_items,
                "audioTracks": [cards_track],
            },
        ]
    else:
        # Single scene fallback (no audio)
        scenes = [
            {
                "id": "scene_graph",
                "type": "graph",
                "start": 0,
                "duration": total_frames,
                "graph": graph,
            },
        ]

    # Audio-driven crossfade overlap: use actual audio end/start for pause calc
    for i in range(len(scenes) - 1):
        curr_audio = scenes[i].get("audioTracks", [])
        next_audio = scenes[i + 1].get("audioTracks", [])
        curr_audio_end = max(
            (t["start"] + t["duration"] for t in curr_audio),
            default=scenes[i]["start"] + scenes[i]["duration"],
        )
        next_audio_start = min(
            (t["start"] for t in next_audio),
            default=scenes[i + 1]["start"],
        )
        pause_frames = max(0, next_audio_start - curr_audio_end)
        # Map pause to overlap: 0→4, long pause→12
        # P6.3: minimum 6-frame crossfade for visual breathing
        overlap = max(6, min(12, round(pause_frames * 0.75)))
        scenes[i]["overlapOut"] = overlap
        scenes[i + 1]["overlapIn"] = overlap

    # Seal gaps: extend each scene's duration to meet the next scene start,
    # so crossfade transitions have no black frames between scenes.
    for i in range(len(scenes) - 1):
        next_start = scenes[i + 1]["start"]
        current_end = scenes[i]["start"] + scenes[i]["duration"]
        if next_start > current_end:
            scenes[i]["duration"] = next_start - scenes[i]["start"]

    # P4.1: Strip per-scene audioTracks — audio rendered at top level, no nesting risk
    for scene in scenes:
        scene.pop("audioTracks", None)

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
        "scenes": scenes,
        "audioTracks": audio_tracks,
        "explainerScript": explainer_script,
    }


def render_layout_json(layout_path: str, video_out: str) -> str:
    """Render a pre-built layout JSON to video. No rebuild, no TTS regeneration."""
    result = subprocess.run(
        [
            "node",
            "render-agent-semantic.mjs",
            f"..\\{layout_path}",
            f"..\\{video_out}",
        ],
        cwd="remotion-renderer",
        check=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("Remotion graph render failed")
    return video_out


def render_graph_video(
    text: str,
    layout_out: str = "output/graph_layout.json",
    video_out: str = "output/graph_scene.mp4",
    total_ms: int = 12000,
    enable_audio: bool = False,
    voice: str = DEFAULT_TTS_VOICE,
    rate: int = 0,
    use_llm_director: bool = False,
) -> tuple[str, str]:
    layout = build_graph_video_layout(
        text,
        total_ms=total_ms,
        enable_audio=enable_audio,
        voice=voice,
        rate=rate,
        use_llm_director=use_llm_director,
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
    parser.add_argument("--llm-director", action="store_true", default=False,
                        help="Use LLM for semantic director intent (off by default)")
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
            use_llm_director=args.llm_director,
        )
        print(json.dumps({"layout": layout_out, "video": video_out}, ensure_ascii=False))
        return

    layout = build_graph_video_layout(
        args.text,
        total_ms=args.duration_ms,
        enable_audio=args.enable_audio,
        voice=args.voice,
        rate=args.rate,
        use_llm_director=args.llm_director,
    )
    with open(args.out, "w", encoding="utf-8") as file:
        json.dump(layout, file, ensure_ascii=False, indent=2)
    print(args.out)


if __name__ == "__main__":
    main()
