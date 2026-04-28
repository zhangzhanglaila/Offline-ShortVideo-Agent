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
from typing import Any

FPS = 30
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920


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


def build_graph_video_layout(
    text: str,
    total_ms: int = 12000,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
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

    elements = [
        {
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
        }
        for index, step in enumerate(steps)
    ]

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
        "audioTracks": [],
    }


def render_graph_video(
    text: str,
    layout_out: str = "output/graph_layout.json",
    video_out: str = "output/graph_scene.mp4",
    total_ms: int = 12000,
) -> tuple[str, str]:
    layout = build_graph_video_layout(text, total_ms=total_ms)
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
    args = parser.parse_args()

    if args.render:
        layout_out, video_out = render_graph_video(
            args.text,
            layout_out=args.out,
            video_out=args.video_out,
            total_ms=args.duration_ms,
        )
        print(json.dumps({"layout": layout_out, "video": video_out}, ensure_ascii=False))
        return

    layout = build_graph_video_layout(args.text, total_ms=args.duration_ms)
    with open(args.out, "w", encoding="utf-8") as file:
        json.dump(layout, file, ensure_ascii=False, indent=2)
    print(args.out)


if __name__ == "__main__":
    main()
