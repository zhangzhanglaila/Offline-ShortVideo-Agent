"""Modular pipeline steps — each independently callable, modifiable, rollbackable.

These are the building blocks that the ThinkingAgent orchestrates.
Unlike the monolithic build_graph_video_layout(), each step:
  - Takes a VideoProjectState as input
  - Modifies only its designated part
  - Can be called in isolation
  - Can be re-called to regenerate
"""

from __future__ import annotations

import json
import uuid
from typing import Optional

from compiler.thinking.state import (
    VideoProjectState,
    ModuleState,
    ScriptSentence,
    GraphSpec,
    GraphNode,
    GraphEdge,
    ShotPlan,
    AudioTrack,
    ThinkingPhase,
)


def analyze_topic(state: VideoProjectState, llm_client=None) -> list[str]:
    """Step 1: Analyze topic and generate outline (module structure).

    Populates state.modules with titles and indices.
    Returns a list of thinking/reasoning lines to show the user.

    Each line is prefixed with a type tag:
      [reasoning]  — LLM's analytical thinking (the "why")
      [decision]   — What was decided and why
      [status]     — Progress updates
    """
    thinking = []
    topic = state.topic

    # Use LLM if available, otherwise rule-based
    if llm_client:
        prompt = f"""你是一位专业的教学视频策划师。请分析主题"{topic}"，将其拆分为3-5个教学模块。

要求：
1. 每个模块是一个独立的知识点
2. 模块之间有逻辑递进关系
3. 总时长控制在8-12分钟（每模块约2分钟）
4. 每个模块需要一个简洁的中文标题

请以JSON格式输出：
{{
  "analysis": {{
    "topic_type": "主题类型（概念型/算法型/应用型）",
    "difficulty": "难度评估",
    "audience": "目标观众",
    "key_challenge": "观众最容易卡住的地方",
    "strategy": "整体教学策略"
  }},
  "modules": [
    {{
      "title": "模块标题",
      "key_concepts": ["概念1", "概念2"],
      "reasoning": "为什么这样安排这个模块",
      "hook_idea": "开场吸引观众的思路"
    }}
  ],
  "teaching_flow": "整体教学逻辑的说明"
}}"""

        try:
            response = llm_client.generate(prompt)
            data = json.loads(response)
            modules_data = data.get("modules", [])
            analysis = data.get("analysis", {})

            # Emit structured reasoning
            if analysis:
                thinking.append(f"[reasoning] 主题类型: {analysis.get('topic_type', '未知')}")
                thinking.append(f"[reasoning] 难度评估: {analysis.get('difficulty', '未知')}")
                thinking.append(f"[reasoning] 目标观众: {analysis.get('audience', '未知')}")
                thinking.append(f"[reasoning] 核心难点: {analysis.get('key_challenge', '未知')}")
                thinking.append(f"[decision] 教学策略: {analysis.get('strategy', '')}")

            for m in modules_data:
                thinking.append(f"[decision] 模块「{m.get('title', '')}」: {m.get('reasoning', '')}")

            flow = data.get("teaching_flow", "")
            if flow:
                thinking.append(f"[reasoning] 教学流: {flow}")

        except Exception as e:
            modules_data = _fallback_outline(topic)
            thinking.append(f"[status] ❌ LLM 调用失败: {e}")
            thinking.append("[status] 回退到规则引擎生成大纲")
            # Still produce reasoning for fallback
            thinking.append(f"[reasoning] 主题「{topic}」看起来是一个教学主题")
            thinking.append("[reasoning] 采用经典的「概念→原理→应用」三段式结构")
            thinking.append("[reasoning] 先建立基础认知，再深入核心，最后联系实际")
    else:
        modules_data = _fallback_outline(topic)
        thinking.append("[reasoning] 未配置 LLM，使用规则引擎分析")
        thinking.append(f"[reasoning] 主题「{topic}」— 采用标准教学结构")
        thinking.append("[decision] 拆分为3个模块: 基础概念 → 核心原理 → 实际应用")
        thinking.append("[reasoning] 这种结构适合入门观众，循序渐进")

    # Populate state
    state.modules = []
    for i, m in enumerate(modules_data):
        module = ModuleState(
            id=f"mod_{i:02d}",
            title=m.get("title", f"模块{i+1}"),
            index=i,
            status="pending",
        )
        concepts = m.get("key_concepts", [])
        if concepts:
            module.thinking_log.append(f"关键概念: {', '.join(concepts)}")
        hook = m.get("hook_idea", "")
        if hook:
            module.thinking_log.append(f"开场思路: {hook}")
        state.modules.append(module)

    thinking.append(f"[status] 共 {len(state.modules)} 个模块: " +
                    " → ".join(m.title for m in state.modules))
    return thinking


def generate_module_script(state: VideoProjectState, module_id: str,
                           llm_client=None, sentence_count: int = 20) -> list[str]:
    """Step 2: Generate narration script for a single module.

    Populates module.script with ScriptSentence objects.
    Returns thinking lines.
    """
    thinking = []
    module = state.get_module(module_id)
    if not module:
        return [f"❌ 模块 {module_id} 不存在"]

    topic = state.topic
    title = module.title
    prev_titles = [m.title for m in state.modules if m.index < module.index]

    if llm_client:
        context = ""
        if prev_titles:
            context = f"\n前面已经讲过: {', '.join(prev_titles)}"

        prompt = f"""你是一位数据结构讲师，正在录制讲解视频。请为"{title}"这个知识点写一段讲解文案。

主题: {topic}
当前模块: {title}{context}

要求：
1. 写{sentence_count}句话，每句话是一个完整的讲解单元
2. 口语化、自然，像一个人在对着学生讲解
3. 遵循: 概念定义→原理分析→对比说明→实际应用 的递进逻辑
4. 第一句话要有吸引力（hook）
5. 最后一句话要有总结性

请以JSON格式输出：
{{
  "sentences": [{{"text": "句子内容", "purpose": "definition/comparison/example/summary", "key_concept": "核心概念"}}],
  "reasoning": {{
    "audience_analysis": "观众可能的基础和困惑点",
    "structure_choice": "为什么选择这样的递进结构",
    "hook_strategy": "开场如何抓住注意力",
    "pacing": "节奏安排：哪里快哪里慢",
    "key_risk": "观众最容易走神或卡住的地方"
  }}
}}"""

        try:
            response = llm_client.generate(prompt)
            data = json.loads(response)
            sentences_data = data.get("sentences", [])
            reasoning = data.get("reasoning", {})

            if isinstance(reasoning, dict):
                if reasoning.get("audience_analysis"):
                    thinking.append(f"[reasoning] 观众分析: {reasoning['audience_analysis']}")
                if reasoning.get("structure_choice"):
                    thinking.append(f"[decision] 结构选择: {reasoning['structure_choice']}")
                if reasoning.get("hook_strategy"):
                    thinking.append(f"[decision] 开场策略: {reasoning['hook_strategy']}")
                if reasoning.get("pacing"):
                    thinking.append(f"[reasoning] 节奏安排: {reasoning['pacing']}")
                if reasoning.get("key_risk"):
                    thinking.append(f"[reasoning] 风险点: {reasoning['key_risk']}")
            else:
                thinking.append(f"[reasoning] {reasoning}")
        except Exception as e:
            sentences_data = [{"text": s, "purpose": "", "key_concept": ""}
                              for s in _fallback_script(title, sentence_count)]
            thinking.append(f"[status] ❌ LLM 调用失败: {e}")
            thinking.append("[status] 回退到规则引擎生成文案")
            thinking.append(f"[reasoning] 模块「{title}」— 采用标准讲解模板")
            thinking.append("[reasoning] 结构: 概念引入 → 原理分析 → 对比说明 → 总结应用")
    else:
        sentences_data = [{"text": s, "purpose": "", "key_concept": ""}
                          for s in _fallback_script(title, sentence_count)]
        thinking.append(f"[reasoning] 模块「{title}」— 使用规则引擎生成文案")
        thinking.append("[decision] 采用标准教学递进: 是什么 → 为什么 → 怎么用")

    # Populate module.script
    module.script = []
    for i, s in enumerate(sentences_data):
        sentence = ScriptSentence(
            id=f"{module_id}_s_{i:02d}",
            index=i,
            text=s.get("text", ""),
            purpose=s.get("purpose", ""),
            key_concept=s.get("key_concept", ""),
        )
        module.script.append(sentence)

    module.status = "generating"
    thinking.append(f"📝 {title}: 生成 {len(module.script)} 句文案")
    return thinking


def generate_module_graphs(state: VideoProjectState, module_id: str,
                           llm_client=None) -> list[str]:
    """Step 3: Generate knowledge graph structures for a module.

    Populates module.graph_a and module.graph_b.
    Returns thinking lines.
    """
    thinking = []
    module = state.get_module(module_id)
    if not module:
        return [f"❌ 模块 {module_id} 不存在"]

    title = module.title
    script_text = " ".join(s.text for s in module.script[:5])

    if llm_client:
        prompt = f"""你是一位知识图谱设计专家。请为"{title}"设计两个知识图谱。

上下文: {script_text[:200]}

图谱A: 概览图（展示概念的层次关系，6-8个节点）
图谱B: 对比/细节图（展示具体实现或对比，5-7个节点）

节点role类型: core, storage, processor, result, rule, input, output, pointer
边kind类型: impl, uses, has, type, shows, child, variant, follows, constrains

请以JSON格式输出：
{{
  "graph_a": {{"nodes": [{{"id": "n1", "label": "...", "role": "core"}}], "edges": [{{"id": "e1", "from": "n1", "to": "n2", "label": "...", "kind": "impl"}}], "reasoning": "..."}},
  "graph_b": {{"nodes": [...], "edges": [...], "reasoning": "..."}}
}}"""

        try:
            response = llm_client.generate(prompt)
            data = json.loads(response)
            ga = data.get("graph_a", {})
            gb = data.get("graph_b", {})
            thinking.append(f"🧠 图谱A: {ga.get('reasoning', '')}")
            thinking.append(f"🧠 图谱B: {gb.get('reasoning', '')}")
        except Exception as e:
            ga, gb = _fallback_graphs(title)
            thinking.append(f"🧠 ❌ LLM 调用失败: {e}，使用规则引擎生成图谱")
    else:
        ga, gb = _fallback_graphs(title)
        thinking.append("🧠 使用规则引擎生成图谱")

    def parse_graph(data: dict) -> GraphSpec:
        nodes = [GraphNode(id=n["id"], label=n["label"], role=n.get("role", ""))
                 for n in data.get("nodes", [])]
        edges = [GraphEdge(id=e["id"], from_node=e["from"], to_node=e["to"],
                           label=e.get("label", ""), kind=e.get("kind", ""))
                 for e in data.get("edges", [])]
        return GraphSpec(nodes=nodes, edges=edges)

    module.graph_a = parse_graph(ga)
    module.graph_b = parse_graph(gb)

    thinking.append(f"🗺️ 图谱A: {len(module.graph_a.nodes)} 节点, {len(module.graph_a.edges)} 边")
    thinking.append(f"🗺️ 图谱B: {len(module.graph_b.nodes)} 节点, {len(module.graph_b.edges)} 边")
    return thinking


def generate_module_cards(state: VideoProjectState, module_id: str,
                          llm_client=None) -> list[str]:
    """Step 4: Generate card page content for a module."""
    thinking = []
    module = state.get_module(module_id)
    if not module:
        return [f"❌ 模块 {module_id} 不存在"]

    # Simple rule-based card generation from script
    title = module.title
    sentences = [s.text for s in module.script]

    # Cards A: key concepts
    module.cards_a_title = f"{title}核心概念"
    module.cards_a_items = []
    for s in module.script:
        if s.purpose in ("definition", "comparison") and len(s.text) > 10:
            # Extract key point (first clause)
            key = s.text.split("，")[0] if "，" in s.text else s.text[:30]
            if len(key) > 5:
                module.cards_a_items.append(key)
    module.cards_a_items = module.cards_a_items[:4]

    # Cards B: applications/tips
    module.cards_b_title = f"{title}要点总结"
    module.cards_b_items = []
    for s in module.script:
        if s.purpose in ("example", "summary") and len(s.text) > 10:
            key = s.text.split("，")[0] if "，" in s.text else s.text[:30]
            if len(key) > 5:
                module.cards_b_items.append(key)
    module.cards_b_items = module.cards_b_items[:4]

    # Ensure we have items
    if not module.cards_a_items:
        module.cards_a_items = [f"{title}基本概念", f"{title}核心原理",
                                f"{title}实现方式", f"{title}应用场景"]
    if not module.cards_b_items:
        module.cards_b_items = [f"{title}重点1", f"{title}重点2",
                                f"{title}重点3", f"{title}重点4"]

    thinking.append(f"🃏 卡片A: {module.cards_a_title} ({len(module.cards_a_items)} 项)")
    thinking.append(f"🃏 卡片B: {module.cards_b_title} ({len(module.cards_b_items)} 项)")
    return thinking


def generate_module_audio(state: VideoProjectState, module_id: str,
                          tts_module=None) -> list[str]:
    """Step 5: Generate TTS audio for a module's script.

    Populates module.audio_tracks.
    Returns thinking lines.
    """
    thinking = []
    module = state.get_module(module_id)
    if not module:
        return [f"❌ 模块 {module_id} 不存在"]

    if not tts_module:
        try:
            from core.tts_module import get_tts_module
            tts_module = get_tts_module(state.voice)
        except Exception:
            thinking.append("❌ TTS 模块不可用")
            return thinking

    from engine.bridge.graph_pipeline import _generate_explainer_audio_tracks, _normalize_audio_tracks

    texts = [s.text for s in module.script if s.text]
    if not texts:
        thinking.append("❌ 无文案可配音")
        return thinking

    thinking.append(f"🎙️ 正在生成 {len(texts)} 段语音...")

    try:
        tracks = _generate_explainer_audio_tracks(
            texts, total_ms=120_000, voice=state.voice, rate=0,
        )
        tracks = _normalize_audio_tracks(tracks)

        module.audio_tracks = []
        for i, t in enumerate(tracks):
            track = AudioTrack(
                id=t["id"],
                src=t["src"],
                start=t["start"],
                duration=t["duration"],
                text=t["text"],
                sentence_id=module.script[i].id if i < len(module.script) else "",
            )
            module.audio_tracks.append(track)

        audio_end = max((t.start + t.duration for t in module.audio_tracks), default=0)
        thinking.append(f"🔊 生成完成: {len(module.audio_tracks)} 段, "
                        f"总时长 {audio_end/30:.1f}s")
    except Exception as e:
        thinking.append(f"❌ TTS 生成失败: {e}")

    return thinking


def assemble_module_layout(state: VideoProjectState, module_id: str) -> dict:
    """Step 6: Assemble a module's data into a Remotion layout JSON.

    This is the final step before rendering — converts the editable state
    into the format that Remotion expects.
    """
    module = state.get_module(module_id)
    if not module:
        return {}

    from engine.bridge.graph_pipeline import apply_graph_layout, build_default_plan

    width, height = state.width, state.height

    # Build graphs
    graph_a = {}
    graph_b = {}
    if module.graph_a:
        dsl_a = module.graph_a.to_pipeline_format()
        dsl_a["steps"] = []
        dsl_a["timeline"] = []
        graph_a = apply_graph_layout(dsl_a, width=width, height=height)
        graph_a["title"] = ""
        graph_a["summary"] = ""

    if module.graph_b:
        dsl_b = module.graph_b.to_pipeline_format()
        dsl_b["steps"] = []
        dsl_b["timeline"] = []
        graph_b = apply_graph_layout(dsl_b, width=width, height=height)
        graph_b["title"] = ""
        graph_b["summary"] = ""

    # Build audio tracks in pipeline format
    audio_tracks = [
        {"id": t.id, "src": t.src, "start": t.start,
         "duration": t.duration, "text": t.text}
        for t in module.audio_tracks
    ]

    # Build animation plans
    total_frames = max(3600, max((t["start"] + t["duration"] for t in audio_tracks), default=0))
    for g in [graph_a, graph_b]:
        if g:
            plan = build_default_plan(g, total_frames, audio_tracks)
            for step in plan.get("steps", []):
                step.pop("text", None)
            for shot in plan.get("shots", []):
                shot.pop("text", None)
            g["animation_plan"] = plan
            g["shots"] = plan.get("shots", [])
            g["timeline"] = []
            g["steps"] = []

    # Build subtitle elements
    elements = []
    for track in audio_tracks:
        elements.append({
            "id": f"sub_{track['id']}",
            "type": "text",
            "text": track["text"],
            "x": width // 2, "y": int(height * 0.89),
            "fontSize": 28, "color": "#f8fbff", "fontWeight": 600,
            "textAlign": "center", "lineHeight": 1.35,
            "maxWidth": int(width * 0.67),
            "start": track["start"], "duration": track["duration"],
            "zIndex": 20,
            "animation": {"enter": "blur-in", "exit": "fade", "duration": 8},
        })

    # Build scene sequence
    n = len(audio_tracks)
    seg_size = max(1, n // 5)
    segments = []
    for i in range(5):
        si = i * seg_size
        ei = (i + 1) * seg_size if i < 4 else n
        seg = audio_tracks[si:ei]
        if seg:
            seg_start = seg[0]["start"]
            seg_end = seg[-1]["start"] + seg[-1]["duration"]
            segments.append({"start": seg_start, "duration": seg_end - seg_start})

    scenes = []
    scene_types = ["hook", "graph_a", "cards_a", "graph_b", "cards_b"]
    for i, (stype, seg) in enumerate(zip(scene_types, segments)):
        scene = {"id": f"scene_{stype}", "start": seg["start"], "duration": seg["duration"]}
        if stype == "hook":
            scene["type"] = "hook"
            scene["text"] = module.title
        elif stype == "graph_a":
            scene["type"] = "graph"
            scene["graph"] = graph_a
        elif stype == "cards_a":
            scene["type"] = "cards"
            scene["title"] = module.cards_a_title
            scene["items"] = module.cards_a_items
        elif stype == "graph_b":
            scene["type"] = "graph"
            scene["graph"] = graph_b
        elif stype == "cards_b":
            scene["type"] = "cards"
            scene["title"] = module.cards_b_title
            scene["items"] = module.cards_b_items
        scenes.append(scene)

    for i in range(len(scenes) - 1):
        scenes[i]["overlapOut"] = 8
        scenes[i + 1]["overlapIn"] = 8

    layout = {
        "width": width, "height": height, "fps": state.fps,
        "durationInFrames": total_frames,
        "background": state.background,
        "scene_type": "graph",
        "graph": graph_a,
        "nodes": graph_a.get("nodes", []),
        "edges": graph_a.get("edges", []),
        "elements": elements,
        "shots": [],
        "scenes": scenes,
        "audioTracks": audio_tracks,
    }
    return layout


# ── Fallback generators (rule-based, no LLM) ──

def _fallback_outline(topic: str) -> list[dict]:
    """Generate a basic outline without LLM."""
    # Generic 3-module outline
    return [
        {"title": f"{topic}基础概念", "key_concepts": ["定义", "分类"]},
        {"title": f"{topic}核心原理", "key_concepts": ["实现", "对比"]},
        {"title": f"{topic}实际应用", "key_concepts": ["场景", "总结"]},
    ]


def _fallback_script(title: str, count: int = 20) -> list[str]:
    """Generate basic script sentences without LLM."""
    templates = [
        f"{title}是一个非常重要的知识点，我们来详细了解一下。",
        f"首先，我们需要理解{title}的基本概念。",
        f"{title}的核心思想在于它的结构化设计。",
        f"从实现角度来看，{title}有多种方式。",
        f"第一种方式是最常见的实现方法。",
        f"第二种方式在特定场景下更加高效。",
        f"我们需要根据实际需求来选择合适的实现。",
        f"在性能方面，{title}有着明确的时间复杂度。",
        f"最坏情况下的时间复杂度需要特别注意。",
        f"平均情况下，{title}的表现是非常优秀的。",
        f"空间复杂度也是一个重要的考量因素。",
        f"在实际开发中，{title}有着广泛的应用。",
        f"最常见的应用场景包括数据处理和算法设计。",
        f"面试中也经常考察{title}相关的题目。",
        f"掌握{title}的关键是理解其底层原理。",
        f"多画图、多练习是学习{title}的最好方法。",
        f"我们来总结一下{title}的核心要点。",
        f"首先是概念层面的理解。",
        f"其次是实现层面的掌握。",
        f"最后要能够在实际问题中灵活运用。",
    ]
    return templates[:count]


def _fallback_graphs(title: str) -> tuple[dict, dict]:
    """Generate basic graph structures without LLM."""
    graph_a = {
        "nodes": [
            {"id": "n1", "label": title, "role": "core"},
            {"id": "n2", "label": "分类A", "role": "storage"},
            {"id": "n3", "label": "分类B", "role": "storage"},
            {"id": "n4", "label": "特点1", "role": "processor"},
            {"id": "n5", "label": "特点2", "role": "processor"},
            {"id": "n6", "label": "应用", "role": "result"},
        ],
        "edges": [
            {"id": "e1", "from": "n1", "to": "n2", "label": "类型", "kind": "type"},
            {"id": "e2", "from": "n1", "to": "n3", "label": "类型", "kind": "type"},
            {"id": "e3", "from": "n2", "to": "n4", "label": "特性", "kind": "has"},
            {"id": "e4", "from": "n3", "to": "n5", "label": "特性", "kind": "has"},
            {"id": "e5", "from": "n1", "to": "n6", "label": "用于", "kind": "uses"},
        ],
    }
    graph_b = {
        "nodes": [
            {"id": "a1", "label": "方案A", "role": "storage"},
            {"id": "a2", "label": "优点", "role": "processor"},
            {"id": "a3", "label": "缺点", "role": "result"},
            {"id": "b1", "label": "方案B", "role": "storage"},
            {"id": "b2", "label": "优点", "role": "processor"},
            {"id": "b3", "label": "缺点", "role": "result"},
        ],
        "edges": [
            {"id": "ae1", "from": "a1", "to": "a2", "label": "", "kind": "has"},
            {"id": "ae2", "from": "a1", "to": "a3", "label": "", "kind": "has"},
            {"id": "be1", "from": "b1", "to": "b2", "label": "", "kind": "has"},
            {"id": "be2", "from": "b1", "to": "b3", "label": "", "kind": "has"},
        ],
    }
    return graph_a, graph_b


def analyze_semantic_anchors(state: VideoProjectState, module_id: str,
                             llm_client=None) -> list[str]:
    """Step: Analyze script to generate semantic anchors.

    Identifies key moments in the narration that should drive
    camera, animation, and music decisions:
      - hook: attention-grabbing opening
      - important_term: key concept/keyword
      - surprise: unexpected reveal
      - emphasis: speaker stresses this point
      - concept_transition: transition between ideas
      - climax: peak of explanation

    Populates module.timeline.anchors.
    Returns thinking lines.
    """
    thinking = []
    module = state.get_module(module_id)
    if not module:
        return [f"[status] 模块 {module_id} 不存在"]

    sentences = module.script
    if not sentences:
        return ["[status] 无文案可分析"]

    if llm_client:
        script_text = "\n".join(f"[{i+1}] {s.text}" for i, s in enumerate(sentences))

        prompt = f"""你是一位视频导演。请分析以下讲解文案，标记出关键的语义锚点。

文案:
{script_text}

请识别以下类型的锚点（可以有多个）：
- hook: 吸引注意力的开场
- important_term: 重要概念/术语
- surprise: 出乎意料的揭示
- emphasis: 需要强调的点
- concept_transition: 概念之间的过渡
- climax: 讲解的高潮

请以JSON格式输出：
{{
  "anchors": [
    {{"sentence_index": 0, "type": "hook", "relative_pos": 0.2, "reason": "为什么这里重要"}}
  ]
}}"""

        try:
            response = llm_client.generate(prompt)
            data = json.loads(response)
            anchors_data = data.get("anchors", [])

            for a in anchors_data:
                idx = a.get("sentence_index", 0)
                if 0 <= idx < len(sentences):
                    sentence = sentences[idx]
                    # Create a Clip-like anchor on the sentence's clip
                    module.timeline = module.timeline or Timeline(fps=state.fps)
                    audio_track = module.timeline.get_track("audio")
                    if audio_track and idx < len(audio_track.clips):
                        clip = audio_track.clips[idx]
                        module.timeline.add_semantic_anchor(
                            clip_id=clip.id,
                            semantic_type=a.get("type", "emphasis"),
                            relative_pos=a.get("relative_pos", 0.5),
                            confidence=0.8,
                            source="llm",
                        )
                        thinking.append(f"[decision] 锚点「{a.get('type')}」在第{idx+1}句: {a.get('reason', '')}")

        except Exception as e:
            thinking.append(f"[status] ❌ LLM 分析失败: {e}，使用规则引擎")
            _fallback_semantic_anchors(module, sentences, thinking)
    else:
        _fallback_semantic_anchors(module, sentences, thinking)

    anchor_count = len(module.timeline.anchors) if module.timeline else 0
    thinking.append(f"[status] 共生成 {anchor_count} 个语义锚点")
    return thinking


def _fallback_semantic_anchors(module, sentences, thinking):
    """Rule-based semantic anchor generation."""
    from compiler.thinking.state import Timeline, SemanticAnchorType

    module.timeline = module.timeline or Timeline(fps=30)

    for i, s in enumerate(sentences):
        text = s.text

        # First sentence → hook
        if i == 0:
            module.timeline.add_semantic_anchor(
                clip_id=f"audio_{i}", semantic_type=SemanticAnchorType.HOOK,
                relative_pos=0.3, source="rule",
            )
            thinking.append("[decision] 锚点「hook」在第1句 (开场)")

        # Sentences with keywords → important_term
        if any(kw in text for kw in ['关键', '核心', '重要', '本质', '重点']):
            module.timeline.add_semantic_anchor(
                clip_id=f"audio_{i}", semantic_type=SemanticAnchorType.IMPORTANT_TERM,
                relative_pos=0.5, source="rule",
            )
            thinking.append(f"[decision] 锚点「important_term」在第{i+1}句")

        # Transition words → concept_transition
        if any(kw in text for kw in ['接下来', '然后', '接下来', '另一方面', '相比之下', '现在']):
            module.timeline.add_semantic_anchor(
                clip_id=f"audio_{i}", semantic_type=SemanticAnchorType.CONCEPT_TRANSITION,
                relative_pos=0.1, source="rule",
            )
            thinking.append(f"[decision] 锚点「concept_transition」在第{i+1}句")

        # Last sentence → climax/summary
        if i == len(sentences) - 1:
            module.timeline.add_semantic_anchor(
                clip_id=f"audio_{i}", semantic_type=SemanticAnchorType.CLIMAX,
                relative_pos=0.5, source="rule",
            )
            thinking.append("[decision] 锚点「climax」在最后一句")
