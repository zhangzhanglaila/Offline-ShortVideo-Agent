"""ThinkingAgent — the interactive video creation agent.

This is the core orchestrator that transforms the system from a one-shot
pipeline into a human-in-the-loop collaborative video creation environment.

Key capabilities:
  - Streaming thinking (shows reasoning in real-time)
  - Step-by-step execution with user checkpoints
  - Interruption handling (user can pause and redirect)
  - Partial regeneration (redo one module, not all)
  - Natural language instructions ("make sentence 3 shorter")
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Generator, Optional

from compiler.thinking.state import (
    VideoProjectState,
    ModuleState,
    ThinkingPhase,
)
from compiler.thinking.session import ThinkingSession
from compiler.thinking.intent_parser import IntentParser, EditIntent
from compiler.thinking.steps import (
    analyze_topic,
    generate_module_script,
    generate_module_graphs,
    generate_module_cards,
    generate_module_audio,
    assemble_module_layout,
)


class ThinkingAgent:
    """Interactive video creation agent with human-in-the-loop support.

    Usage:
        agent = ThinkingAgent(session)
        for event in agent.run():
            # Stream events to frontend (SSE)
            send_sse(event)

        # User interrupts
        session.interrupt("第3句改短一点")
        for event in agent.handle_interruption():
            send_sse(event)
    """

    def __init__(self, session: ThinkingSession, llm_client=None, tts_module=None):
        self.session = session
        self.llm = llm_client
        self.tts = tts_module
        self._llm_error: str | None = None
        self._try_load_llm()

    def _try_load_llm(self):
        """Try to load LLM client if not provided."""
        if self.llm is None:
            try:
                from agent.llm.ollama_client import get_llm_client
                self.llm = get_llm_client()
            except Exception as e:
                self._llm_error = str(e)
                self.llm = None

    # ── Main execution flow ──

    def run(self, start_phase: ThinkingPhase = None) -> Generator[dict, None, None]:
        """Run the full thinking workflow, yielding events for streaming.

        This is a generator that yields SSE-compatible event dicts.
        The caller streams these to the frontend.

        At each step, checks for user interruptions.
        """
        state = self.session.state
        phase = start_phase or state.phase

        yield self._event("start", f"开始分析: {state.topic}")

        if self.llm is None:
            err = self._llm_error or "未知错误"
            yield self._event("warning", f"⚠️ LLM 不可用 ({err})，将使用规则引擎回退")
        else:
            yield self._event("status", "✅ LLM 已连接，开始 AI 分析")

        # ── Phase 1: Analyze topic ──
        if phase in (ThinkingPhase.IDLE, ThinkingPhase.ANALYZING):
            yield self._event("phase", "正在分析主题...")
            self.session.set_phase(ThinkingPhase.ANALYZING)

            for line in analyze_topic(state, self.llm):
                yield self._emit_thinking_line(line)

            self.session.set_phase(ThinkingPhase.OUTLINE_READY)
            yield self._event("outline", {
                "modules": [{"id": m.id, "title": m.title, "index": m.index}
                            for m in state.modules],
            })

            if self._check_interrupt():
                yield from self._handle_interruption()
                return

        # ── Phase 2: Generate scripts for each module ──
        if phase in (ThinkingPhase.OUTLINE_READY, ThinkingPhase.SCRIPT_GENERATING):
            self.session.set_phase(ThinkingPhase.SCRIPT_GENERATING)

            for module in state.modules:
                if module.script_approved:
                    continue  # Skip already approved modules

                yield self._event("module_start", {
                    "module_id": module.id,
                    "title": module.title,
                    "step": "script",
                })

                for line in generate_module_script(state, module.id, self.llm):
                    yield self._emit_thinking_line(line)

                # Yield the generated script for user review
                yield self._event("script_ready", {
                    "module_id": module.id,
                    "title": module.title,
                    "sentences": [
                        {"id": s.id, "index": s.index, "text": s.text,
                         "purpose": s.purpose, "key_concept": s.key_concept}
                        for s in module.script
                    ],
                })

                if self._check_interrupt():
                    yield from self._handle_interruption()
                    return

            self.session.set_phase(ThinkingPhase.SCRIPT_READY)
            yield self._event("all_scripts_ready", {
                "modules": [
                    {"id": m.id, "title": m.title,
                     "sentences": len(m.script), "approved": m.script_approved}
                    for m in state.modules
                ],
            })

        # ── Phase 3: Generate graphs ──
        if phase in (ThinkingPhase.SCRIPT_READY, ThinkingPhase.GRAPH_GENERATING):
            self.session.set_phase(ThinkingPhase.GRAPH_GENERATING)

            for module in state.modules:
                if module.graphs_approved:
                    continue

                yield self._event("module_start", {
                    "module_id": module.id,
                    "title": module.title,
                    "step": "graph",
                })

                for line in generate_module_graphs(state, module.id, self.llm):
                    yield self._emit_thinking_line(line)

                for line in generate_module_cards(state, module.id, self.llm):
                    yield self._emit_thinking_line(line)

                yield self._event("graph_ready", {
                    "module_id": module.id,
                    "graph_a": _graph_summary(module.graph_a),
                    "graph_b": _graph_summary(module.graph_b),
                    "cards_a": {"title": module.cards_a_title, "items": module.cards_a_items},
                    "cards_b": {"title": module.cards_b_title, "items": module.cards_b_items},
                })

                if self._check_interrupt():
                    yield from self._handle_interruption()
                    return

            self.session.set_phase(ThinkingPhase.GRAPH_READY)

        # ── Phase 4: Generate audio ──
        if phase in (ThinkingPhase.GRAPH_READY, ThinkingPhase.AUDIO_GENERATING):
            self.session.set_phase(ThinkingPhase.AUDIO_GENERATING)

            for module in state.modules:
                if module.audio_approved:
                    continue

                yield self._event("module_start", {
                    "module_id": module.id,
                    "title": module.title,
                    "step": "audio",
                })

                for line in generate_module_audio(state, module.id, self.tts):
                    yield self._emit_thinking_line(line)

                yield self._event("audio_ready", {
                    "module_id": module.id,
                    "tracks": len(module.audio_tracks),
                })

                if self._check_interrupt():
                    yield from self._handle_interruption()
                    return

            self.session.set_phase(ThinkingPhase.AUDIO_READY)

        # ── Phase 5: Wait for user confirmation ──
        self.session.set_phase(ThinkingPhase.CONFIRMED)
        yield self._event("all_ready", {
            "message": "所有模块已准备就绪，等待确认后开始渲染",
            "summary": self.session.summary(),
        })

    # ── Rendering ──

    def render(self) -> Generator[dict, None, None]:
        """Render all modules to video. Yields progress events."""
        state = self.session.state
        self.session.set_phase(ThinkingPhase.RENDERING)

        yield self._event("render_start", {"modules": len(state.modules)})

        # Assemble layouts and render each module
        for module in state.modules:
            yield self._event("rendering_module", {
                "module_id": module.id,
                "title": module.title,
            })

            try:
                layout = assemble_module_layout(state, module.id)
                if not layout:
                    yield self._event("error", f"模块 {module.id} 布局组装失败")
                    continue

                # Save layout
                output_dir = Path(__file__).parent.parent / "output" / "thinking_sessions" / self.session.id
                output_dir.mkdir(parents=True, exist_ok=True)
                layout_path = output_dir / f"{module.id}_layout.json"
                layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")

                # Render via Remotion
                video_path = output_dir / f"{module.id}.mp4"
                yield from self._render_video(layout_path, video_path)

                module.output_path = str(video_path)
                module.status = "done"

                yield self._event("module_rendered", {
                    "module_id": module.id,
                    "video_path": str(video_path),
                })

            except Exception as e:
                yield self._event("error", f"渲染失败 {module.id}: {e}")

        # Concat all module videos
        try:
            final_path = self._concat_videos(state)
            state.final_video_path = final_path
            self.session.set_phase(ThinkingPhase.RENDERED)
            yield self._event("rendered", {"video_path": final_path})
        except Exception as e:
            yield self._event("error", f"拼接失败: {e}")

    def _render_video(self, layout_path: Path, output_path: Path):
        """Render a single video via Remotion."""
        import subprocess
        renderer = Path(__file__).parent.parent / "remotion-renderer"
        abs_l = str(layout_path.resolve()).replace("\\", "/")
        abs_o = str(output_path.resolve()).replace("\\", "/")
        r = subprocess.run(
            ["node", "render-agent-semantic.mjs", abs_l, abs_o],
            cwd=str(renderer), capture_output=True, text=True, encoding="utf-8", timeout=300,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-200:] if r.stderr else "Unknown error")

    def _concat_videos(self, state: VideoProjectState) -> str:
        """Concatenate all module videos into final output."""
        import subprocess
        output_dir = Path(__file__).parent.parent / "output" / "thinking_sessions" / self.session.id
        concat_list = output_dir / "concat.txt"
        final_path = output_dir / "final.mp4"

        videos = [m.output_path for m in state.modules if m.output_path]
        with open(concat_list, "w") as f:
            for v in videos:
                f.write(f"file '{Path(v).as_posix()}'\n")

        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_list), "-c", "copy", str(final_path)],
            capture_output=True, text=True, encoding="utf-8",
        )
        return str(final_path)

    # ── Interruption handling ──

    def _check_interrupt(self) -> bool:
        """Check if user has queued an interruption."""
        return self.session.check_interruption() is not None

    def _handle_interruption(self) -> Generator[dict, None, None]:
        """Handle a user interruption."""
        instruction = self.session.check_interruption()
        if not instruction:
            return

        yield self._event("interrupted", {"instruction": instruction})
        yield self._event("thinking", f"⏸ 用户打断: {instruction}")

        # Parse and execute the instruction
        yield from self._execute_instruction(instruction)

        # After handling, check if we should continue
        yield self._event("thinking", "✅ 指令已处理，等待下一步指示")

    def _execute_instruction(self, instruction: str) -> Generator[dict, None, None]:
        """Parse and execute a natural language instruction using IntentParser."""
        state = self.session.state
        parser = IntentParser(self.llm)
        intents = parser.parse(instruction, state)

        for intent in intents:
            yield self._event("intent_parsed", {
                "action": intent.action,
                "target": intent.target,
                "confidence": intent.confidence,
                "reasoning": intent.reasoning,
            })

            if intent.confidence < 0.4:
                yield self._event("feedback_recorded", {
                    "instruction": instruction,
                    "reasoning": intent.reasoning,
                })
                continue

            yield from self._execute_intent(intent)

    def _execute_intent(self, intent: EditIntent) -> Generator[dict, None, None]:
        """Execute a single EditIntent."""
        state = self.session.state
        current = state.get_current_module()
        action = intent.action
        target = intent.target
        params = intent.params

        if action == "rewrite":
            # Rewrite a specific sentence
            import re
            match = re.match(r'sentence:(\d+)', target)
            if match and current:
                idx = int(match.group(1)) - 1
                if 0 <= idx < len(current.script):
                    old = current.script[idx].text
                    new_text = params.get("text", old)
                    self.session.update_sentence(current.id, current.script[idx].id, new_text)
                    yield self._event("updated", {
                        "type": "sentence", "index": idx, "old": old, "new": new_text,
                    })

        elif action == "remove":
            import re
            match = re.match(r'sentence:(\d+)', target)
            if match and current:
                idx = int(match.group(1)) - 1
                if 0 <= idx < len(current.script):
                    sid = current.script[idx].id
                    self.session.remove_sentence(current.id, sid)
                    yield self._event("updated", {"type": "sentence_removed", "index": idx})

        elif action == "add":
            text = params.get("text", "")
            if text and current:
                self.session.add_sentence(current.id, text)
                yield self._event("updated", {"type": "sentence_added", "text": text})

        elif action == "regenerate":
            if current:
                current.script_approved = False
                current.graphs_approved = False
                current.audio_approved = False
                current.status = "pending"
                yield self._event("regenerating", {"module_id": current.id})
                yield from self.run(start_phase=ThinkingPhase.SCRIPT_GENERATING)

        elif action == "approve":
            if current:
                self.session.approve_module(current.id, "all")
                yield self._event("approved", {"module_id": current.id})
                state.current_module_index += 1
                yield from self.run()

        elif action == "shorten":
            # Shorten: remove less important sentences
            if current and len(current.script) > 5:
                # Remove sentences marked as less important
                to_remove = [s for s in current.script if s.purpose in ("example", "comparison")]
                if to_remove:
                    removed = to_remove[0]  # Remove one at a time
                    self.session.remove_sentence(current.id, removed.id)
                    yield self._event("updated", {
                        "type": "sentence_removed",
                        "reasoning": f"精简: 移除了一句话「{removed.text[:20]}...」",
                    })

        elif action == "style":
            style = params.get("style", "engaging")
            if current and current.script:
                # Rewrite first sentence with LLM if available
                if self.llm and style == "engaging":
                    first = current.script[0]
                    prompt = f"请用更吸引人的方式重写这句话，保持原意但更有吸引力:\n{first.text}\n只输出新句子。"
                    try:
                        new_text = self.llm.generate(prompt).strip()
                        if new_text:
                            self.session.update_sentence(current.id, first.id, new_text)
                            yield self._event("updated", {
                                "type": "sentence", "index": 0,
                                "old": first.text, "new": new_text,
                                "reasoning": "改写开场使其更吸引人",
                            })
                    except Exception:
                        pass

        elif action == "feedback":
            yield self._event("feedback_recorded", {
                "instruction": params.get("instruction", ""),
                "reasoning": intent.reasoning,
            })

        else:
            yield self._event("feedback_recorded", {
                "instruction": f"未识别的操作: {action}",
                "intent": intent.__dict__,
            })

    # ── Selective operations (called by API) ──

    def regenerate_module_script(self, module_id: str) -> Generator[dict, None, None]:
        """Regenerate script for a specific module."""
        module = self.session.state.get_module(module_id)
        if not module:
            yield self._event("error", f"模块 {module_id} 不存在")
            return

        module.script = []
        module.script_approved = False
        yield self._event("regenerating", {"module_id": module_id, "step": "script"})

        for line in generate_module_script(self.session.state, module_id, self.llm):
            yield self._event("thinking", line)

        yield self._event("script_ready", {
            "module_id": module_id,
            "title": module.title,
            "sentences": [
                {"id": s.id, "index": s.index, "text": s.text}
                for s in module.script
            ],
        })

    def regenerate_module_graph(self, module_id: str) -> Generator[dict, None, None]:
        """Regenerate graph for a specific module."""
        module = self.session.state.get_module(module_id)
        if not module:
            yield self._event("error", f"模块 {module_id} 不存在")
            return

        module.graph_a = None
        module.graph_b = None
        module.graphs_approved = False
        yield self._event("regenerating", {"module_id": module_id, "step": "graph"})

        for line in generate_module_graphs(self.session.state, module_id, self.llm):
            yield self._event("thinking", line)

        for line in generate_module_cards(self.session.state, module_id, self.llm):
            yield self._event("thinking", line)

        yield self._event("graph_ready", {
            "module_id": module_id,
            "graph_a": _graph_summary(module.graph_a),
            "graph_b": _graph_summary(module.graph_b),
        })

    def regenerate_single_sentence(self, module_id: str, sentence_id: str) -> Generator[dict, None, None]:
        """Regenerate a single sentence using LLM with context."""
        module = self.session.state.get_module(module_id)
        if not module:
            yield self._event("error", f"模块 {module_id} 不存在")
            return

        # Find the sentence
        target_idx = -1
        for i, s in enumerate(module.script):
            if s.id == sentence_id:
                target_idx = i
                break

        if target_idx < 0:
            yield self._event("error", f"句子 {sentence_id} 不存在")
            return

        # Build context from surrounding sentences
        context_before = " ".join(s.text for s in module.script[max(0, target_idx-2):target_idx])
        context_after = " ".join(s.text for s in module.script[target_idx+1:min(len(module.script), target_idx+3)])
        old_text = module.script[target_idx].text

        if self.llm:
            prompt = f"""你是一位数据结构讲师。请重新生成以下讲解句子的替代版本。

上下文（前文）: {context_before}
当前句子: {old_text}
上下文（后文）: {context_after}

要求：
1. 保持语义连贯
2. 替代版本应有不同的表述方式
3. 保持口语化风格

只输出新的句子内容，不要其他文字。"""

            try:
                new_text = self.llm.generate(prompt).strip()
                if new_text:
                    self.session.update_sentence(module_id, sentence_id, new_text)
                    yield self._event("sentence_regenerated", {
                        "sentence_id": sentence_id,
                        "old": old_text,
                        "new": new_text,
                    })
                    return
            except Exception:
                pass

        yield self._event("error", "无法重新生成句子（LLM 不可用）")

    # ── Failure Analysis & Optimization ──

    def analyze_failure(self, node_id: str, error: str) -> list[dict]:
        """Use LLM to analyze a node failure and suggest recovery actions.

        Returns a list of suggested actions (dicts with 'action' and 'detail').
        """
        if not self.llm:
            return [{"action": "retry", "detail": "LLM not available for analysis"}]

        prompt = f"""视频生成流水线中节点 "{node_id}" 执行失败。

错误信息:
{error}

请分析失败原因并给出恢复建议。输出JSON数组，每个元素格式:
{{"action": "retry|skip|fallback|manual", "detail": "具体建议"}}

只输出JSON，不要其他文字。"""

        try:
            response = self.llm.generate(prompt).strip()
            # Try to parse JSON from response
            import json
            # Find JSON array in response
            start = response.find("[")
            end = response.rfind("]") + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except Exception:
            pass

        return [{"action": "retry", "detail": f"自动重试节点 {node_id}"}]

    def optimize_schedule(self, scheduler) -> list[str]:
        """Suggest optimal execution order based on historical timing data.

        Analyzes node duration metadata to suggest reordering that
        minimizes total pipeline time (longest path first).
        """
        nodes = scheduler.graph.nodes
        if not nodes:
            return []

        # Collect timing data
        timings = {}
        for node_id, node in nodes.items():
            if node.duration > 0:
                timings[node_id] = node.duration

        if not timings:
            return list(nodes.keys())

        # Topological sort weighted by duration (critical path first)
        from collections import defaultdict, deque
        in_degree = defaultdict(int)
        adj = defaultdict(list)
        for node_id, node in nodes.items():
            for dep_id in scheduler.graph._reverse_adj.get(node_id, []):
                adj[dep_id].append(node_id)
                in_degree[node_id] += 1

        # Kahn's with priority (longest duration first)
        queue = []
        for nid in nodes:
            if in_degree[nid] == 0:
                queue.append((-(timings.get(nid, 0)), nid))
        import heapq
        heapq.heapify(queue)

        order = []
        while queue:
            _, nid = heapq.heappop(queue)
            order.append(nid)
            for child in adj[nid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    heapq.heappush(queue, (-(timings.get(child, 0)), child))

        return order

    # ── Helpers ──

    def _emit_thinking_line(self, line: str) -> dict:
        """Parse a tagged thinking line and emit the appropriate event type.

        Tags:
          [reasoning]  → "reasoning" event (LLM's analytical thinking)
          [decision]   → "decision" event (what was decided)
          [status]     → "status" event (progress updates)
          (no tag)     → "thinking" event (legacy)
        """
        if line.startswith("[reasoning]"):
            return self._event("reasoning", line[len("[reasoning]"):].strip())
        elif line.startswith("[decision]"):
            return self._event("decision", line[len("[decision]"):].strip())
        elif line.startswith("[status]"):
            return self._event("status", line[len("[status]"):].strip())
        else:
            return self._event("thinking", line)

    def _event(self, event_type: str, data: Any = None) -> dict:
        """Create an SSE-compatible event dict."""
        return {
            "type": event_type,
            "session_id": self.session.id,
            "timestamp": time.time(),
            "phase": self.session.state.phase.value,
            "data": data,
        }


def _graph_summary(graph) -> dict:
    """Create a summary of a graph for the frontend."""
    if not graph:
        return {"nodes": 0, "edges": 0, "labels": []}
    return {
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "labels": [n.label for n in graph.nodes],
        "structure": [
            {"from": e.from_node, "to": e.to_node, "label": e.label}
            for e in graph.edges
        ],
    }
