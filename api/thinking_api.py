# -*- coding: utf-8 -*-
"""
Thinking Session API — Interactive Video Agent REST endpoints.

Exposes the ThinkingAgent runtime as REST + SSE endpoints:
  - Session lifecycle (create, list, status, delete)
  - Pipeline execution with streaming thinking (SSE)
  - User interactions (interrupt, approve, edit, regenerate)
  - Video rendering with progress events (SSE)
"""
import sys
import os
import json
import time
import queue
import asyncio
import threading
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

router = APIRouter(prefix="/api/thinking")

# ── In-memory session store ──

_sessions: dict = {}  # session_id -> ThinkingSession
_sessions_lock = threading.Lock()


def _get_session(session_id: str):
    """Get a session by ID, raising 404 if not found."""
    with _sessions_lock:
        session = _sessions.get(session_id)
    if not session:
        # Try loading from disk
        from compiler.thinking.session import ThinkingSession
        session = ThinkingSession.load(session_id)
        if not session:
            raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
        with _sessions_lock:
            _sessions[session_id] = session
    return session


# ── Request models ──

class StartRequest(BaseModel):
    topic: str = Field(..., description="视频主题")
    width: int = Field(1920, description="视频宽度")
    height: int = Field(1080, description="视频高度")
    voice: str = Field("zh-CN-YunxiNeural", description="TTS语音")


class InterruptRequest(BaseModel):
    instruction: str = Field(..., description="用户指令，如'第3句改短一点'")


class ApproveRequest(BaseModel):
    module_id: str = Field(..., description="模块ID")
    component: str = Field("all", description="确认的组件: script/graphs/audio/cards/all")


class EditSentenceRequest(BaseModel):
    module_id: str = Field(..., description="模块ID")
    sentence_id: str = Field(..., description="句子ID")
    new_text: str = Field(..., description="新的文案内容")


class AddSentenceRequest(BaseModel):
    module_id: str = Field(..., description="模块ID")
    text: str = Field(..., description="新句子内容")
    index: int = Field(-1, description="插入位置，-1表示末尾")


class RemoveSentenceRequest(BaseModel):
    module_id: str = Field(..., description="模块ID")
    sentence_id: str = Field(..., description="句子ID")


class RegenerateRequest(BaseModel):
    module_id: str = Field(..., description="模块ID")
    component: str = Field("script", description="重新生成的组件: script/graph/audio_sentence")
    sentence_id: Optional[str] = Field(None, description="句子ID（仅 component=audio_sentence 时需要）")


# ── Session lifecycle ──

@router.post("/start")
async def start_session(req: StartRequest):
    """创建新的 Thinking Session"""
    from compiler.thinking.session import ThinkingSession
    from compiler.thinking.state import VideoProjectState

    state = VideoProjectState(
        topic=req.topic,
        width=req.width,
        height=req.height,
        voice=req.voice,
    )
    session = ThinkingSession(state=state)

    with _sessions_lock:
        _sessions[session.id] = session

    session.save()

    return JSONResponse({
        "session_id": session.id,
        "topic": req.topic,
        "phase": session.state.phase.value,
        "message": f"Session created for topic: {req.topic}",
    })


@router.get("/sessions")
async def list_sessions():
    """列出所有 Session"""
    from compiler.thinking.session import ThinkingSession
    sessions = ThinkingSession.list_sessions()
    return JSONResponse({"sessions": sessions})


@router.get("/{session_id}/status")
async def get_status(session_id: str):
    """获取 Session 状态"""
    session = _get_session(session_id)
    return JSONResponse(session.summary())


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    """删除 Session"""
    with _sessions_lock:
        _sessions.pop(session_id, None)
    from compiler.thinking.session import ThinkingSession
    deleted = ThinkingSession.delete_session(session_id)
    return JSONResponse({"deleted": deleted, "session_id": session_id})


# ── Pipeline execution (SSE streaming) ──

@router.post("/{session_id}/run")
async def run_pipeline(session_id: str, start_phase: str = ""):
    """执行完整的 Thinking 流程，返回 SSE 事件流。

    前端通过 EventSource 或 fetch + ReadableStream 消费事件。
    每个事件格式: data: {"type": "...", "data": ..., "phase": "..."}\n\n
    """
    session = _get_session(session_id)

    # Resolve start phase
    phase = None
    if start_phase:
        from compiler.thinking.state import ThinkingPhase
        try:
            phase = ThinkingPhase(start_phase)
        except ValueError:
            pass

    event_queue = queue.Queue()

    def run_agent():
        """Run the agent in a background thread, pushing events to queue."""
        try:
            from compiler.thinking.agent_loop import ThinkingAgent
            agent = ThinkingAgent(session)
            for event in agent.run(start_phase=phase):
                event_queue.put(event)
        except Exception as e:
            event_queue.put({
                "type": "error",
                "session_id": session_id,
                "timestamp": time.time(),
                "data": str(e),
            })
        finally:
            event_queue.put(None)  # Sentinel: stream complete

    thread = threading.Thread(target=run_agent, daemon=True)
    thread.start()

    loop = asyncio.get_event_loop()

    async def event_generator():
        yield f"data: {json.dumps({'type': 'connected', 'session_id': session_id}, ensure_ascii=False)}\n\n"
        while True:
            try:
                event = await loop.run_in_executor(
                    None, lambda: event_queue.get(timeout=1)
                )
                if event is None:
                    yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── User interactions ──

@router.post("/{session_id}/interrupt")
async def interrupt_session(session_id: str, req: InterruptRequest):
    """用户打断当前操作，发送自然语言指令。"""
    session = _get_session(session_id)
    session.interrupt(req.instruction)
    return JSONResponse({
        "status": "interrupted",
        "instruction": req.instruction,
        "message": "Interruption queued. Agent will process at next checkpoint.",
    })


@router.post("/{session_id}/approve")
async def approve_module(session_id: str, req: ApproveRequest):
    """确认某个模块的组件。"""
    session = _get_session(session_id)
    success = session.approve_module(req.module_id, req.component)
    if not success:
        raise HTTPException(status_code=400, detail=f"Module {req.module_id} not found")
    return JSONResponse({
        "status": "approved",
        "module_id": req.module_id,
        "component": req.component,
    })


@router.post("/{session_id}/edit")
async def edit_sentence(session_id: str, req: EditSentenceRequest):
    """修改一个句子的文案。"""
    session = _get_session(session_id)
    success = session.update_sentence(req.module_id, req.sentence_id, req.new_text)
    if not success:
        raise HTTPException(status_code=400, detail="Sentence not found")
    return JSONResponse({
        "status": "updated",
        "module_id": req.module_id,
        "sentence_id": req.sentence_id,
        "new_text": req.new_text,
    })


@router.post("/{session_id}/add-sentence")
async def add_sentence(session_id: str, req: AddSentenceRequest):
    """添加一个新句子。"""
    session = _get_session(session_id)
    result = session.add_sentence(req.module_id, req.text, req.index)
    if not result:
        raise HTTPException(status_code=400, detail="Module not found")
    return JSONResponse({
        "status": "added",
        "sentence_id": result.id,
        "index": result.index,
        "text": result.text,
    })


@router.post("/{session_id}/remove-sentence")
async def remove_sentence(session_id: str, req: RemoveSentenceRequest):
    """删除一个句子。"""
    session = _get_session(session_id)
    success = session.remove_sentence(req.module_id, req.sentence_id)
    if not success:
        raise HTTPException(status_code=400, detail="Sentence not found")
    return JSONResponse({
        "status": "removed",
        "sentence_id": req.sentence_id,
    })


@router.post("/{session_id}/regenerate")
async def regenerate_component(session_id: str, req: RegenerateRequest):
    """重新生成模块的某个组件，返回 SSE 事件流。"""
    session = _get_session(session_id)

    event_queue = queue.Queue()

    def run_regen():
        try:
            from compiler.thinking.agent_loop import ThinkingAgent
            agent = ThinkingAgent(session)

            if req.component == "script":
                for event in agent.regenerate_module_script(req.module_id):
                    event_queue.put(event)
            elif req.component == "graph":
                for event in agent.regenerate_module_graph(req.module_id):
                    event_queue.put(event)
            elif req.component == "sentence" and req.sentence_id:
                for event in agent.regenerate_single_sentence(
                    req.module_id, req.sentence_id
                ):
                    event_queue.put(event)
            else:
                event_queue.put({
                    "type": "error",
                    "data": f"Unknown component: {req.component}",
                })
        except Exception as e:
            event_queue.put({"type": "error", "data": str(e)})
        finally:
            event_queue.put(None)

    thread = threading.Thread(target=run_regen, daemon=True)
    thread.start()

    loop = asyncio.get_event_loop()

    async def event_generator():
        yield f"data: {json.dumps({'type': 'connected'}, ensure_ascii=False)}\n\n"
        while True:
            try:
                event = await loop.run_in_executor(
                    None, lambda: event_queue.get(timeout=1)
                )
                if event is None:
                    yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Module data access ──

@router.get("/{session_id}/modules")
async def get_modules(session_id: str):
    """获取所有模块概览。"""
    session = _get_session(session_id)
    modules = []
    for m in session.state.modules:
        modules.append({
            "id": m.id,
            "title": m.title,
            "index": m.index,
            "status": m.status,
            "sentences": len(m.script),
            "has_graph_a": m.graph_a is not None,
            "has_graph_b": m.graph_b is not None,
            "script_approved": m.script_approved,
            "graphs_approved": m.graphs_approved,
            "audio_approved": m.audio_approved,
        })
    return JSONResponse({"modules": modules})


@router.get("/{session_id}/modules/{module_id}/script")
async def get_module_script(session_id: str, module_id: str):
    """获取模块的完整脚本。"""
    session = _get_session(session_id)
    module = session.state.get_module(module_id)
    if not module:
        raise HTTPException(status_code=404, detail=f"Module {module_id} not found")
    return JSONResponse({
        "module_id": module_id,
        "title": module.title,
        "script_approved": module.script_approved,
        "sentences": [
            {
                "id": s.id,
                "index": s.index,
                "text": s.text,
                "purpose": s.purpose,
                "key_concept": s.key_concept,
                "is_user_edited": s.is_user_edited,
                "is_approved": s.is_approved,
            }
            for s in module.script
        ],
    })


@router.get("/{session_id}/modules/{module_id}/graph")
async def get_module_graph(session_id: str, module_id: str):
    """获取模块的知识图谱。"""
    session = _get_session(session_id)
    module = session.state.get_module(module_id)
    if not module:
        raise HTTPException(status_code=404, detail=f"Module {module_id} not found")

    def graph_to_dict(g):
        if not g:
            return None
        return {
            "title": g.title,
            "summary": g.summary,
            "nodes": [
                {"id": n.id, "label": n.label, "role": n.role}
                for n in g.nodes
            ],
            "edges": [
                {"id": e.id, "from": e.from_node, "to": e.to_node,
                 "label": e.label, "kind": e.kind}
                for e in g.edges
            ],
        }

    return JSONResponse({
        "module_id": module_id,
        "graphs_approved": module.graphs_approved,
        "graph_a": graph_to_dict(module.graph_a),
        "graph_b": graph_to_dict(module.graph_b),
        "cards_a": {"title": module.cards_a_title, "items": module.cards_a_items},
        "cards_b": {"title": module.cards_b_title, "items": module.cards_b_items},
    })


@router.get("/{session_id}/modules/{module_id}/audio")
async def get_module_audio(session_id: str, module_id: str):
    """获取模块的音频轨道信息。"""
    session = _get_session(session_id)
    module = session.state.get_module(module_id)
    if not module:
        raise HTTPException(status_code=404, detail=f"Module {module_id} not found")
    return JSONResponse({
        "module_id": module_id,
        "audio_approved": module.audio_approved,
        "tracks": [
            {
                "id": t.id,
                "src": t.src,
                "start": t.start,
                "duration": t.duration,
                "text": t.text,
                "sentence_id": t.sentence_id,
            }
            for t in module.audio_tracks
        ],
    })


# ── Rendering ──

@router.post("/{session_id}/render")
async def render_video(session_id: str):
    """渲染视频，返回 SSE 进度事件流。"""
    session = _get_session(session_id)

    # Check all modules are ready
    not_ready = [
        m.id for m in session.state.modules
        if not (m.script_approved and m.graphs_approved and m.audio_approved)
    ]
    if not_ready:
        raise HTTPException(
            status_code=400,
            detail=f"Modules not ready for render: {not_ready}. Approve all components first.",
        )

    event_queue = queue.Queue()

    def run_render():
        try:
            from compiler.thinking.agent_loop import ThinkingAgent
            agent = ThinkingAgent(session)
            for event in agent.render():
                event_queue.put(event)
        except Exception as e:
            event_queue.put({"type": "error", "data": str(e)})
        finally:
            event_queue.put(None)

    thread = threading.Thread(target=run_render, daemon=True)
    thread.start()

    loop = asyncio.get_event_loop()

    async def event_generator():
        yield f"data: {json.dumps({'type': 'connected'}, ensure_ascii=False)}\n\n"
        while True:
            try:
                event = await loop.run_in_executor(
                    None, lambda: event_queue.get(timeout=1)
                )
                if event is None:
                    yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Action history ──

@router.get("/{session_id}/history")
async def get_history(session_id: str):
    """获取 Session 的操作历史。"""
    session = _get_session(session_id)
    return JSONResponse({
        "history": [
            {
                "id": a.id,
                "timestamp": a.timestamp,
                "type": a.action_type,
                "target": a.target,
                "description": a.description,
                "is_user": a.is_user_action,
            }
            for a in session.history
        ]
    })


# ── Undo / Redo ──

@router.post("/{session_id}/undo")
async def undo_patch(session_id: str):
    """撤销上一个操作。"""
    session = _get_session(session_id)
    success = session.undo()
    return JSONResponse({
        "status": "undone" if success else "nothing_to_undo",
        "can_undo": session.patch_history.can_undo,
        "can_redo": session.patch_history.can_redo,
    })


@router.post("/{session_id}/redo")
async def redo_patch(session_id: str):
    """重做上一个撤销的操作。"""
    session = _get_session(session_id)
    success = session.redo()
    return JSONResponse({
        "status": "redone" if success else "nothing_to_redo",
        "can_undo": session.patch_history.can_undo,
        "can_redo": session.patch_history.can_redo,
    })


# ── EventBus SSE Stream ──

@router.get("/{session_id}/events")
async def event_stream(session_id: str, event_type: str = ""):
    """SSE stream of all EventBus events for this session.

    Subscribe to all events or filter by event_type.
    """
    from compiler.thinking.event_bus import get_event_bus, Event

    bus = get_event_bus()
    event_queue = queue.Queue()

    def handler(event: Event):
        if event.session_id == session_id or not event.session_id:
            event_queue.put(event)

    bus.subscribe(event_type or "*", handler)

    loop = asyncio.get_event_loop()

    async def generator():
        try:
            yield f"data: {json.dumps({'type': 'connected', 'session_id': session_id}, ensure_ascii=False)}\n\n"
            while True:
                try:
                    event = await loop.run_in_executor(
                        None, lambda: event_queue.get(timeout=3)
                    )
                    yield event.to_sse()
                except queue.Empty:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        except GeneratorExit:
            pass
        finally:
            bus.unsubscribe(event_type or "*", handler)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── LLM Configuration ──

@router.get("/llm/check")
async def check_llm_config():
    """检查 LLM 是否已配置。返回配置状态和可用模型信息。"""
    env_path = Path(__file__).parent.parent / ".env"

    # Check for API key in env
    has_key = False
    key_source = ""
    api_base = ""
    model = ""

    # Check .env file
    if env_path.exists():
        env_content = env_path.read_text(encoding="utf-8")
        for line in env_content.splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY") and v:
                has_key = True
                key_source = k
            if k in ("OPENAI_API_BASE", "DEEPSEEK_API_BASE") and v:
                api_base = v
            if k in ("OPENAI_MODEL", "DEEPSEEK_MODEL") and v:
                model = v

    # Check env vars
    if not has_key:
        for k in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
            if os.environ.get(k):
                has_key = True
                key_source = k
                break

    # Check Ollama (local)
    ollama_available = False
    try:
        import urllib.request
        req = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
        if req.status == 200:
            ollama_available = True
    except Exception:
        pass

    return JSONResponse({
        "configured": has_key or ollama_available,
        "has_cloud_key": has_key,
        "key_source": key_source,
        "api_base": api_base,
        "model": model,
        "ollama_available": ollama_available,
    })


class LLMConfigRequest(BaseModel):
    api_key: str = Field(..., description="API Key")
    api_base: str = Field("https://api.deepseek.com/v1", description="API Base URL")
    model: str = Field("deepseek-chat", description="Model name")
    provider: str = Field("deepseek", description="Provider: deepseek/openai")


@router.post("/llm/configure")
async def configure_llm(req: LLMConfigRequest):
    """保存 LLM 配置到 .env 文件。"""
    env_path = Path(__file__).parent.parent / ".env"

    # Read existing .env
    existing = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            existing[k.strip()] = v.strip()

    # Update with new values
    if req.provider == "deepseek":
        existing["DEEPSEEK_API_KEY"] = req.api_key
        existing["DEEPSEEK_API_BASE"] = req.api_base
        existing["DEEPSEEK_MODEL"] = req.model
    else:
        existing["OPENAI_API_KEY"] = req.api_key
        existing["OPENAI_API_BASE"] = req.api_base
        existing["OPENAI_MODEL"] = req.model

    # Write back
    lines = []
    for k, v in existing.items():
        lines.append(f'{k}="{v}"')

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Also set in current process
    os.environ[req.provider.upper() + "_API_KEY"] = req.api_key
    os.environ[req.provider.upper() + "_API_BASE"] = req.api_base
    os.environ[req.provider.upper() + "_MODEL"] = req.model

    return JSONResponse({
        "status": "saved",
        "message": "LLM 配置已保存到 .env 文件",
    })


# ── Branch & Merge (Git for Media) ──

class CreateBranchRequest(BaseModel):
    name: str = Field(..., description="分支名称")
    description: str = Field("", description="分支描述")


class MergeRequest(BaseModel):
    source_branch: str = Field(..., description="要合并的源分支")
    target_branch: str = Field("", description="目标分支，默认当前分支")
    default_strategy: str = Field("ours", description="冲突解决策略: ours/theirs/manual")


class ResolveConflictRequest(BaseModel):
    conflict_index: int = Field(..., description="冲突索引")
    strategy: str = Field(..., description="解决策略: ours/theirs/manual")
    resolved_value: Optional[str] = Field(None, description="手动解决时的值")


@router.get("/{session_id}/branches")
async def list_branches(session_id: str):
    """列出所有分支。"""
    session = _get_session(session_id)
    if not hasattr(session, 'branch_manager'):
        from compiler.thinking.branch import BranchManager
        session.branch_manager = BranchManager(session.patch_store)
    branches = session.branch_manager.list_branches()
    return JSONResponse({"branches": branches})


@router.post("/{session_id}/branches")
async def create_branch(session_id: str, req: CreateBranchRequest):
    """创建新分支。"""
    session = _get_session(session_id)
    if not hasattr(session, 'branch_manager'):
        from compiler.thinking.branch import BranchManager
        session.branch_manager = BranchManager(session.patch_store)
    branch_dir = session.branch_manager.create_branch(req.name, req.description)
    return JSONResponse({
        "status": "created",
        "name": req.name,
        "path": str(branch_dir),
    })


@router.post("/{session_id}/branches/switch")
async def switch_branch(session_id: str, name: str):
    """切换到指定分支。"""
    session = _get_session(session_id)
    if not hasattr(session, 'branch_manager'):
        from compiler.thinking.branch import BranchManager
        session.branch_manager = BranchManager(session.patch_store)
    session.branch_manager.switch_branch(name)
    return JSONResponse({
        "status": "switched",
        "current": name,
    })


@router.get("/{session_id}/branches/divergence")
async def get_divergence(session_id: str, branch_a: str, branch_b: str):
    """分析两个分支的差异。"""
    session = _get_session(session_id)
    if not hasattr(session, 'branch_manager'):
        from compiler.thinking.branch import BranchManager
        session.branch_manager = BranchManager(session.patch_store)
    div = session.branch_manager.get_divergence(branch_a, branch_b)
    return JSONResponse(div)


@router.post("/{session_id}/merge")
async def merge_branches(session_id: str, req: MergeRequest):
    """合并分支。"""
    session = _get_session(session_id)
    if not hasattr(session, 'branch_manager'):
        from compiler.thinking.branch import BranchManager
        session.branch_manager = BranchManager(session.patch_store)

    from compiler.thinking.branch import MergeEngine, ResolutionStrategy
    engine = MergeEngine(session.branch_manager)

    strategy_map = {
        "ours": ResolutionStrategy.OURS,
        "theirs": ResolutionStrategy.THEIRS,
        "manual": ResolutionStrategy.MANUAL,
    }
    strategy = strategy_map.get(req.default_strategy, ResolutionStrategy.OURS)

    result = engine.merge(req.source_branch, req.target_branch or None)

    # If no unresolved conflicts, apply immediately
    if not result.has_conflicts:
        count = engine.apply_merge(result, session.state, strategy)
        result.total_merged = count

    return JSONResponse(result.to_dict())


@router.post("/{session_id}/merge/resolve")
async def resolve_merge_conflict(session_id: str, req: ResolveConflictRequest):
    """解决一个合并冲突。"""
    session = _get_session(session_id)
    if not hasattr(session, '_merge_result'):
        raise HTTPException(status_code=400, detail="No active merge to resolve")

    result = session._merge_result
    if req.conflict_index >= len(result.conflicts):
        raise HTTPException(status_code=400, detail="Invalid conflict index")

    from compiler.thinking.branch import ResolutionStrategy
    strategy_map = {
        "ours": ResolutionStrategy.OURS,
        "theirs": ResolutionStrategy.THEIRS,
        "manual": ResolutionStrategy.MANUAL,
    }
    strategy = strategy_map.get(req.strategy, ResolutionStrategy.MANUAL)

    conflict = result.conflicts[req.conflict_index]
    from compiler.thinking.branch import MergeEngine
    engine = MergeEngine(session.branch_manager)
    engine.resolve_conflict(conflict, strategy, req.resolved_value, session.state)

    # Check if all resolved
    all_resolved = all(c.resolved for c in result.conflicts)

    return JSONResponse({
        "resolved": conflict.resolved,
        "all_resolved": all_resolved,
        "remaining": sum(1 for c in result.conflicts if not c.resolved),
    })
