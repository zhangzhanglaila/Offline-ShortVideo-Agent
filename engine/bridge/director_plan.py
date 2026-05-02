"""P3: LLM Director Brain — semantic intent layer.

LLM outputs abstract "director intent" (what to show, not how to show it).
The translator converts intents to concrete shots/camera/focus params.
Renderer never sees raw LLM output.

Architecture:
    User Query → LLM (DirectorPlan) → translator (intent→params) → Shots
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from engine.shared.path_utils import get_project_root


# ============================================================
# Schema: LLM outputs these, translator consumes them
# ============================================================

@dataclass
class ShotIntent:
    """Semantic shot intent — NOT concrete animation params."""
    intent: str   # "introduce_node" | "show_flow" | "highlight_result" | "reveal_all" | "emphasize" | "summarize"
    target: str   # Semantic label (e.g. "Redis", "Client→Redis") — translator resolves to node/edge IDs


@dataclass
class ScenePlan:
    """One scene's semantic plan."""
    type: str     # "hook" | "graph" | "cards"
    goal: str     # Natural language goal for this scene
    shots: list[ShotIntent] = field(default_factory=list)


@dataclass
class DirectorPlan:
    """Top-level director intent from LLM."""
    scenes: list[ScenePlan] = field(default_factory=list)
    pace: str = "medium"       # "fast" | "medium" | "slow"
    emphasis: list[str] = field(default_factory=list)  # Key terms to visually emphasize


# ============================================================
# Translator: semantic intent → concrete shot parameters
# ============================================================

# Intent → (camera, focus) mapping
# P3.5: Semantic atomization — atomic intents (one action per intent) + legacy aliases
INTENT_CAMERA_MAP: dict[str, tuple[str, str]] = {
    # ── Atomic intents (P3.5) — each does exactly one thing ──
    "focus_node":       ("static",   "node"),      # Static look at a single node
    "focus_edge":       ("static",   "edge"),      # Static look at an edge
    "trace_path":       ("pan",      "edge"),      # Camera pans along an edge flow
    "expand_view":      ("pull-out", "overview"),  # Zoom out to show full context
    "push_into":        ("push-in",  "node"),      # Push camera into focused node
    "spotlight":        ("zoom-in",  "node"),      # Dramatic zoom intro on a node
    "hold_frame":       ("static",   "overview"),  # Hold current view (breathing room)
    "ripple":           ("static",   "node"),      # Visual pulse emphasis on node
    # ── Legacy compound intents (backward compatible aliases) ──
    "introduce_node":   ("zoom-in",  "node"),
    "show_flow":        ("pan",      "edge"),
    "highlight_result": ("push-in",  "node"),
    "reveal_all":       ("pull-out", "overview"),
    "emphasize":        ("push-in",  "node"),
    "summarize":        ("static",   "overview"),
    "pause":            ("static",   "group"),
}


def _resolve_target_ids(
    target_label: str,
    graph: dict[str, Any],
) -> tuple[list[str], str]:
    """Match a semantic target label to actual node/edge IDs in the graph.

    Returns (targetIds, resolved_focus_type).
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    # Direct ID match
    for node in nodes:
        if node["id"].lower() == target_label.lower():
            return [node["id"]], "node"
    for edge in edges:
        if edge["id"].lower() == target_label.lower():
            return [edge["id"]], "edge"

    # Arrow notation: "A→B" matches edge from A to B (before label match!)
    if "→" in target_label or "->" in target_label:
        parts = re.split(r"→|->", target_label, maxsplit=1)
        if len(parts) == 2:
            a, b = parts[0].strip(), parts[1].strip()
            for edge in edges:
                from_node = next((n for n in nodes if n["id"] == edge["from"]), None)
                to_node = next((n for n in nodes if n["id"] == edge["to"]), None)
                if from_node and to_node:
                    fl = from_node.get("label", "").lower()
                    tl = to_node.get("label", "").lower()
                    if (a.lower() in fl or a.lower() in from_node["id"].lower()) and \
                       (b.lower() in tl or b.lower() in to_node["id"].lower()):
                        return [edge["id"]], "edge"

    # Label substring match (after arrow notation)
    label_lower = target_label.lower()
    for node in nodes:
        nl = (node.get("label", "")).lower()
        if label_lower in nl or nl in label_lower:
            return [node["id"]], "node"
    for edge in edges:
        el = (edge.get("label", "")).lower()
        if label_lower in el or el in label_lower:
            return [edge["id"]], "edge"

    # Fallback: return the label as-is (caller handles unknown targets)
    return [target_label], "node"


def translate_director_plan(
    plan: DirectorPlan,
    graph: dict[str, Any],
    total_frames: int,
    existing_shots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Convert semantic DirectorPlan → concrete params.

    Returns dict with:
        scenes: list of scene dicts with shots embedded
        pace_multiplier: 0.8 (fast) | 1.0 (medium) | 1.3 (slow)
    """
    pace_name = plan.pace if plan.pace in ("fast", "medium", "slow") else "medium"
    pace_mult = {"fast": 0.8, "medium": 1.0, "slow": 1.3}[pace_name]

    # ── Guard 1: Valid intent set ──
    VALID_INTENTS = set(INTENT_CAMERA_MAP.keys())

    scenes_out: list[dict[str, Any]] = []
    all_node_ids = [n["id"] for n in graph.get("nodes", [])]
    all_edge_ids = [e["id"] for e in graph.get("edges", [])]
    MAX_SHOTS_PER_SCENE = 6  # Guard 3

    for sp in plan.scenes:
        shots: list[dict[str, Any]] = []
        dropped_count = 0

        for si in sp.shots:
            # ── Guard 1: clamp unknown intents ──
            intent = si.intent if si.intent in VALID_INTENTS else "emphasize"

            # ── Guard 2: validate target resolves to actual graph element ──
            target_ids, resolved_focus = _resolve_target_ids(si.target, graph)

            target_valid = False
            if resolved_focus == "node":
                target_valid = any(n["id"] in target_ids for n in graph.get("nodes", []))
            elif resolved_focus == "edge":
                target_valid = any(e["id"] in target_ids for e in graph.get("edges", []))

            if not target_valid:
                # ── P3.5 Graded fallback: per-shot → safe overview ──
                dropped_count += 1
                shots.append({
                    "focus": "overview",
                    "targetIds": all_node_ids if all_node_ids else [],
                    "camera": "static",
                    "intent": "reveal_all",
                    "text": si.target,
                    "_fallback": True,
                })
                continue

            camera, focus = INTENT_CAMERA_MAP.get(intent, ("static", "overview"))

            shots.append({
                "focus": resolved_focus,
                "targetIds": target_ids,
                "camera": camera,
                "intent": intent,
                "text": si.target,
            })

        # ── Guard 3: shot count limit ──
        shots = shots[:MAX_SHOTS_PER_SCENE]

        # ── P3.3: Shot dedup — drop consecutive duplicate targets ──
        deduped: list[dict[str, Any]] = []
        last_targets: str = ""
        for s in shots:
            key = ",".join(s["targetIds"])
            if key != last_targets:
                deduped.append(s)
                last_targets = key
        shots = deduped

        # ── P3.3: Auto-fill — ensure min 3 shots per scene ──
        while len(shots) < 3:
            shots.append({
                "focus": "overview",
                "targetIds": all_node_ids if all_node_ids else [],
                "camera": "static",
                "intent": "reveal_all",
            })

        # ── P3.5: Scene-level fallback detection ──
        all_fallback = all(s.get("_fallback") for s in shots)

        scenes_out.append({
            "type": sp.type,
            "goal": sp.goal,
            "shots": shots,
            "_dropped": dropped_count,
            "_allFallback": all_fallback,
        })

    return {
        "scenes": scenes_out,
        "pace_multiplier": pace_mult,
        "emphasis": plan.emphasis,
    }


def _extract_json_object(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    match = re.search(r"\{[\s\S]*\}", text)
    candidate = match.group(0) if match else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def call_llm_for_director_plan(
    topic: str,
    graph: dict[str, Any],
) -> DirectorPlan | None:
    """LLM generates semantic director intent.

    The prompt is carefully constrained: LLM only outputs scene types,
    goals, and shot intents — NO concrete camera params, timing, or scale values.
    """
    nodes_summary = [
        {"id": n["id"], "label": n["label"]}
        for n in graph.get("nodes", [])
    ]
    edges_summary = [
        {"from": e["from"], "to": e["to"], "label": e.get("label", "")}
        for e in graph.get("edges", [])
    ]

    prompt = f"""You are a video director for a short explainer video (30 seconds).

Given the topic and graph structure below, create a DIRECTOR PLAN.
IMPORTANT: You only describe WHAT to show, not HOW to animate it.

TOPIC: {topic}

GRAPH STRUCTURE:
Nodes: {json.dumps(nodes_summary, ensure_ascii=False)}
Edges: {json.dumps(edges_summary, ensure_ascii=False)}

Return JSON only:
{{
  "pace": "fast | medium | slow",
  "emphasis": ["keyword1", "keyword2"],
  "scenes": [
    {{
      "type": "hook | graph | cards",
      "goal": "one sentence describing what this scene should achieve",
      "shots": [
        {{
          "intent": "focus_node | trace_path | expand_view | push_into | spotlight | hold_frame | ripple | introduce_node | show_flow | highlight_result | reveal_all | emphasize | summarize",
          "target": "node label or edge label (e.g. Redis or Client→Redis)"
        }}
      ]
    }}
  ]
}}

RULES:
- First scene must be "hook" type (1 shot, intent=spotlight or introduce_node).
- Middle scene(s) must be "graph" type (3-5 shots covering data flow).
  Vary atomic intents: focus_node (pause at a node), trace_path (follow edge flow),
  expand_view (show full context), push_into (emphasize a node), ripple (pulse effect).
- Last scene must be "cards" type (1 shot, intent=hold_frame or summarize).
- "target" must use actual labels from the graph nodes/edges above.
- shot intents must use ONLY the allowed values.
- Do NOT include camera, scale, timing, or animation parameters.
- pace: "fast" for quick topics, "medium" for normal, "slow" for complex.

DIRECTOR PLAN:
""".strip()

    try:
        from agent.llm.ollama_client import get_llm_client

        client = get_llm_client()
        response = client.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            timeout=90,
            max_tokens=800,
        )
        data = _extract_json_object(response)
        if not data:
            return None

        scenes = []
        for s in data.get("scenes", []):
            shots = []
            for si in s.get("shots", []):
                intent = si.get("intent", "").strip()
                target = si.get("target", "").strip()
                if intent in INTENT_CAMERA_MAP and target:
                    shots.append(ShotIntent(intent=intent, target=target))
            if shots or s.get("type") in ("hook", "graph", "cards"):
                scenes.append(ScenePlan(
                    type=s.get("type", "graph"),
                    goal=s.get("goal", ""),
                    shots=shots,
                ))

        if not scenes:
            return None

        return DirectorPlan(
            scenes=scenes,
            pace=data.get("pace", "medium"),
            emphasis=data.get("emphasis", []),
        )
    except Exception:
        return None


def plan_to_scenes_and_shots(
    plan: DirectorPlan,
    graph: dict[str, Any],
    total_frames: int,
    audio_tracks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Full translation: DirectorPlan → concrete scene + shot structure.

    P4.2: Uniform shot timeline — all shots get equal duration.
    Total frames distributed evenly across all shots in all scenes.
    No jitter, no gaps, no complex weighting.
    """
    translated = translate_director_plan(plan, graph, total_frames)
    pace_mult = translated["pace_multiplier"]

    # P4.2: Flatten all shots across all scenes for uniform distribution
    all_shot_data: list[tuple[int, dict[str, Any]]] = []
    for si, ts in enumerate(translated["scenes"]):
        for shot in ts["shots"]:
            all_shot_data.append((si, shot))

    total_shots = len(all_shot_data)
    if total_shots == 0:
        return {"scenes": [], "emphasis": translated["emphasis"], "pace": plan.pace, "pace_multiplier": pace_mult}

    # P6.1: Intent-based rhythm curve — natural pacing per shot intent
    INTENT_WEIGHT: dict[str, float] = {
        # introduce: breathe, let viewer absorb (1.3x)
        "introduce": 1.3, "introduce_node": 1.3, "expand_view": 1.3, "reveal_all": 1.2,
        # focus: standard pace (1.0x)
        "focus": 1.0, "focus_node": 1.0, "push_into": 1.0, "spotlight": 1.0,
        "highlight_result": 1.0, "emphasize": 1.0,
        # flow: faster, keep momentum (0.75x)
        "flow": 0.75, "show_flow": 0.75, "trace_path": 0.75, "focus_edge": 0.75,
        # summary: slow down, let it land (1.4x)
        "summary": 1.4, "summarize": 1.4, "hold_frame": 1.4,
        # pulse: quick accent (0.55x)
        "pulse": 0.55, "ripple": 0.55, "pause": 0.6,
    }
    DEFAULT_WEIGHT = 1.0

    weights = [
        INTENT_WEIGHT.get(shot.get("intent", ""), DEFAULT_WEIGHT)
        for _, shot in all_shot_data
    ]
    total_weight = sum(weights)

    # Distribute frames by weight
    unit_frame = max(1, total_frames / total_weight)
    allocated: list[int] = []
    for w in weights:
        dur = max(15, round(unit_frame * w * pace_mult))
        dur = min(dur, 150)  # Cap: no shot longer than 5 seconds
        allocated.append(dur)

    # Absorb rounding remainder into last shot
    total_allocated = sum(allocated)
    remainder = total_frames - total_allocated
    if total_shots > 0 and remainder != 0:
        allocated[-1] = max(15, allocated[-1] + remainder)

    # Build scene output
    scene_map: dict[int, dict[str, Any]] = {}
    for si, ts in enumerate(translated["scenes"]):
        scene_map[si] = {
            "type": ts["type"],
            "goal": ts["goal"],
            "start": 0,
            "duration": 0,
            "shots": [],
            "pace_multiplier": pace_mult,
        }

    t = 0
    for idx, (si, shot) in enumerate(all_shot_data):
        shot_with_timing = {
            **shot,
            "start": t,
            "duration": allocated[idx],
        }
        scene_map[si]["shots"].append(shot_with_timing)
        t += shot_with_timing["duration"]

    # Set scene start/duration from shot boundaries
    for si, scene in scene_map.items():
        shots = scene["shots"]
        if shots:
            scene["start"] = shots[0]["start"]
            scene["duration"] = sum(s["duration"] for s in shots)

    scenes_out = [scene_map[si] for si in sorted(scene_map.keys())]

    return {
        "scenes": scenes_out,
        "emphasis": translated["emphasis"],
        "pace": plan.pace,
        "pace_multiplier": pace_mult,
    }
