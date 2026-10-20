# -*- coding: utf-8 -*-
"""
LangGraph Agent 视频渲染管道 — 用图编排替代固定线性管道
覆盖面试模块：Agent架构 / LangGraph / Function Calling / 状态管理 / HITL

架构:
  [START] → load_context
    → decide_charts (如果有chart_data → generate_charts, 否则 → render_frames)
    → generate_charts → render_frames
    → compose_animation
    → composite_audio
    → finalize → [END]

支持:
  - 条件路由：根据 chart_data 是否存在动态决定是否生成图表
  - 检查点持久化：SQLite checkpointer，支持断点恢复
  - 流式进度：stream_mode="updates" 实时推送渲染进度
  - HITL 人工审核：在 composite_audio 前可插入审核节点
"""
import json
import sqlite3
import traceback
from pathlib import Path
from typing import TypedDict, Optional

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

import config


# ═══════════════════════════════════════════════════════════════
# State
# ═══════════════════════════════════════════════════════════════

class PipelineState(TypedDict, total=False):
    video_id: int
    table_name: str
    # DB fields (populated by load_context)
    script_content: str
    storyboard: list
    tts_audio_path: str
    materials: dict
    duration: int
    animation_style: str
    orientation: str
    visual_style: str
    video_width: int
    video_height: int
    work_dir: str
    chart_data_raw: list
    # Intermediate results
    chart_materials: dict
    manga_frames: list
    segments: list
    raw_video_path: str
    final_video_path: str
    # Flow control
    has_charts: bool
    status: str
    error_message: str
    current_step: str


# ═══════════════════════════════════════════════════════════════
# Graph Builder
# ═══════════════════════════════════════════════════════════════

class VideoPipelineGraph:
    """LangGraph视频渲染图 — 封装整个渲染流程为可检查点、可流式、可恢复的图。"""

    def __init__(self, checkpointer_db: str = None):
        db_path = checkpointer_db or str(config.DATA_DIR / "pipeline_checkpoints.db")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.checkpointer = SqliteSaver.from_conn_string(db_path)
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        builder = StateGraph(PipelineState)

        # 注册节点
        builder.add_node("load_context", self._load_context)
        builder.add_node("generate_charts", self._generate_charts)
        builder.add_node("render_frames", self._render_frames)
        builder.add_node("compose_animation", self._compose_animation)
        builder.add_node("composite_audio", self._composite_audio)
        builder.add_node("finalize", self._finalize)

        # 边
        builder.add_edge(START, "load_context")
        builder.add_conditional_edges(
            "load_context",
            self._decide_charts,
            {"charts": "generate_charts", "no_charts": "render_frames"}
        )
        builder.add_edge("generate_charts", "render_frames")
        builder.add_edge("render_frames", "compose_animation")
        builder.add_edge("compose_animation", "composite_audio")
        builder.add_edge("composite_audio", "finalize")
        builder.add_edge("finalize", END)

        return builder.compile(checkpointer=self.checkpointer)

    # ═══ Routing ═══

    def _decide_charts(self, state: PipelineState) -> str:
        if state.get("has_charts"):
            return "charts"
        return "no_charts"

    # ═══ Nodes ═══

    def _load_context(self, state: PipelineState) -> PipelineState:
        """从DB加载视频上下文，准备渲染环境。"""
        video_id = state["video_id"]
        table_name = state["table_name"]

        conn = sqlite3.connect(config.TOPICS_DB)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {table_name} WHERE id = ?", (video_id,))
        row = dict(cursor.fetchone())
        conn.close()

        work_dir = config.OUTPUT_DIR / "_work" / f"{'topic' if 'topic' in table_name else 'ecom'}_{video_id}"
        work_dir.mkdir(parents=True, exist_ok=True)

        storyboard = json.loads(str(row.get("storyboard") or "[]"))
        materials = json.loads(str(row.get("materials_json") or "{}"))
        chart_data_raw = json.loads(str(row.get("chart_data") or "[]")) if isinstance(row.get("chart_data"), str) else (row.get("chart_data") or [])
        if not isinstance(chart_data_raw, list):
            chart_data_raw = []

        has_charts = any(
            isinstance(c, dict) and c.get("chart_type") in ("bar", "pie", "line", "flowchart",
                "bar_chart", "pie_chart", "line_chart", "diagram", "architecture")
            for c in chart_data_raw
        )

        return {
            **state,
            "script_content": str(row.get("script_content") or ""),
            "storyboard": storyboard,
            "tts_audio_path": str(row.get("tts_audio_path") or ""),
            "materials": materials,
            "duration": int(row.get("duration") or 30),
            "animation_style": str(row.get("animation_style") or "manga_frame"),
            "orientation": str(row.get("orientation") or "portrait"),
            "visual_style": str(row.get("visual_style") or "manga"),
            "video_width": int(row.get("video_width") or 1080),
            "video_height": int(row.get("video_height") or 1920),
            "work_dir": str(work_dir),
            "chart_data_raw": chart_data_raw,
            "has_charts": has_charts,
            "status": "running",
            "current_step": "loaded",
        }

    def _generate_charts(self, state: PipelineState) -> PipelineState:
        """生成图表PNG素材（复用 pipeline_helpers 中的 generate_chart_materials）。"""
        from core.pipeline_helpers import generate_chart_materials

        chart_script = {
            "chart_data": state["chart_data_raw"],
            "diagram_layout": "",
        }
        try:
            chart_materials = generate_chart_materials(
                chart_script, Path(state["work_dir"]),
                state["visual_style"], state["video_width"], state["video_height"]
            )
        except Exception:
            chart_materials = {}

        # 将图表素材合并到 materials（用户上传素材优先）
        materials = dict(state.get("materials", {}))
        for k, v in chart_materials.items():
            if k not in materials or not materials[k]:
                materials[k] = v

        return {**state, "chart_materials": chart_materials, "materials": materials, "current_step": "charts_done"}

    def _render_frames(self, state: PipelineState) -> PipelineState:
        """渲染漫画讲解帧（复用 pipeline_helpers 中的逻辑）。"""
        from core.pipeline_helpers import generate_manga_frames

        frames = generate_manga_frames(
            storyboard=state["storyboard"],
            script=state["script_content"],
            work_dir=state["work_dir"],
            materials=state.get("materials", {}),
            width=state["video_width"],
            height=state["video_height"],
            visual_style=state["visual_style"],
        )

        if not frames:
            return {**state, "status": "failed", "error_message": "漫画帧生成失败", "current_step": "frames_failed"}

        return {**state, "manga_frames": frames, "current_step": "frames_done"}

    def _compose_animation(self, state: PipelineState) -> PipelineState:
        """动画合成 — xfade转场 + 强调动效 + 电影调色。"""
        from core.animation_module import get_animation_module

        frames = state.get("manga_frames", [])
        work_dir = Path(state["work_dir"])
        raw_video_path = str(work_dir / "raw_video.mp4")

        # 生成 segments
        duration = state["duration"]
        n_frames = max(len(frames), 1)
        segments = []
        for i in range(n_frames):
            seg_dur = duration / n_frames
            segments.append({
                "start": i * seg_dur,
                "end": (i + 1) * seg_dur,
                "text": "",
                "image_index": i,
                "emphasis": "hook" if i == 0 else ("cta" if i == n_frames - 1 else None),
            })

        animation = get_animation_module()
        animation.output_width = state["video_width"]
        animation.output_height = state["video_height"]

        ok = animation.create_animated_video_from_segments(
            images=frames,
            segments=segments,
            output_path=raw_video_path,
            animation_style=state.get("animation_style", "manga_frame"),
            transition="fadegrays",
            film_look=True,
        )
        if not ok:
            return {**state, "status": "failed", "error_message": "动画视频生成失败", "current_step": "animation_failed"}

        return {**state, "raw_video_path": raw_video_path, "segments": segments, "current_step": "animation_done"}

    def _composite_audio(self, state: PipelineState) -> PipelineState:
        """多轨道合成 — 视频 + TTS + BGM + 音效。"""
        from core.dual_mode_module import multitrack_composite

        work_dir = Path(state["work_dir"])
        final_video_path = str(work_dir / "final_video.mp4")

        # BGM
        bgm_path = None
        bgm_dir = config.ASSETS_DIR / "bgm"
        if bgm_dir.exists():
            bgm_files = list(bgm_dir.glob("*.mp3")) + list(bgm_dir.glob("*.wav"))
            if bgm_files:
                bgm_path = str(bgm_files[0])

        ok = multitrack_composite(
            video_path=state["raw_video_path"],
            audio_path=state.get("tts_audio_path", ""),
            subtitle_path=None,  # manga_frame模式自带文字
            bgm_path=bgm_path,
            output_path=final_video_path,
        )
        if not ok:
            return {**state, "status": "failed", "error_message": "多轨道合成失败", "current_step": "composite_failed"}

        # 音效混合
        try:
            from core.sfx_module import generate_sfx_for_scenes, mix_sfx_to_video
            sfx_map = generate_sfx_for_scenes(state.get("segments", []), str(work_dir / "sfx"))
            if sfx_map:
                sfx_output = str(work_dir / "sfx_mixed.mp4")
                mixed = mix_sfx_to_video(final_video_path, state.get("tts_audio_path", ""), sfx_map, bgm_path, sfx_output)
                if mixed != final_video_path and Path(mixed).exists():
                    import shutil
                    shutil.move(mixed, final_video_path)
        except Exception:
            pass  # SFX失败非致命

        return {**state, "final_video_path": final_video_path, "current_step": "composite_done"}

    def _finalize(self, state: PipelineState) -> PipelineState:
        """更新DB、标记完成。"""
        video_id = state["video_id"]
        table_name = state["table_name"]

        conn = sqlite3.connect(config.TOPICS_DB)
        conn.execute(
            f"UPDATE {table_name} SET pipeline_step=?, status=?, video_path=? WHERE id=?",
            ("done", "done", state.get("final_video_path", ""), video_id)
        )
        conn.commit()
        conn.close()

        print(f"[GraphPipeline] {table_name} video_id={video_id} done: {state.get('final_video_path')}")
        return {**state, "status": "done", "current_step": "finalized"}

    # ═══ Public API ═══

    def run(self, video_id: int, table_name: str = "topic_videos",
            thread_id: str = None) -> PipelineState:
        """同步运行渲染管道。thread_id用于跨会话恢复。"""
        config_ = {"configurable": {"thread_id": thread_id or str(video_id)}}
        initial = PipelineState(video_id=video_id, table_name=table_name)
        result = self.graph.invoke(initial, config_)
        return result

    async def run_stream(self, video_id: int, table_name: str = "topic_videos",
                         thread_id: str = None):
        """流式运行渲染管道，yield每个节点的状态更新。"""
        config_ = {"configurable": {"thread_id": thread_id or str(video_id)}}
        initial = PipelineState(video_id=video_id, table_name=table_name)
        async for event in self.graph.astream(initial, config_, stream_mode="updates"):
            yield event

    def resume(self, thread_id: str) -> PipelineState:
        """从检查点恢复运行（用于HITL人工审核后继续）。"""
        config_ = {"configurable": {"thread_id": thread_id}}
        # 传入 None 让 graph 从最后保存的状态继续
        result = self.graph.invoke(None, config_)
        return result

    def get_state(self, thread_id: str) -> Optional[PipelineState]:
        """查询管道的当前状态（用户轮询/前端查询）。"""
        config_ = {"configurable": {"thread_id": thread_id}}
        snapshot = self.graph.get_state(config_)
        if snapshot and snapshot.values:
            return snapshot.values
        return None


# ═══════════════════════════════════════════════════════════════
# 便捷函数 — 向后兼容现有API
# ═══════════════════════════════════════════════════════════════

_graph_instance = None


def get_graph_pipeline() -> VideoPipelineGraph:
    global _graph_instance
    if _graph_instance is None:
        _graph_instance = VideoPipelineGraph()
    return _graph_instance


def run_graph_pipeline(video_id: int, table_name: str = "topic_videos") -> dict:
    """LangGraph版本的后台渲染入口 — 与 run_render_pipeline() 接口兼容。"""
    try:
        graph = get_graph_pipeline()
        state = graph.run(video_id, table_name)
        return {"success": state.get("status") == "done", "video_path": state.get("final_video_path", ""),
                "error": state.get("error_message", "")}
    except Exception as e:
        traceback.print_exc()
        # 回退到旧管道
        from core.pipeline_helpers import run_render_pipeline
        run_render_pipeline(video_id, table_name)
        return {"success": False, "error": f"GraphPipeline异常(已降级): {e}"}
