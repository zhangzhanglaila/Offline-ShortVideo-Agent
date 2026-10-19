# -*- coding: utf-8 -*-
"""
Timeline API — REST endpoints for the Timeline Editor frontend.

Provides:
  - GET /api/timeline/{session_id} — fetch timeline tracks + metadata
  - POST /api/timeline/{session_id}/edit — apply edit operations
  - GET /api/timeline/{session_id}/assets — list available assets
  - POST /api/timeline/{session_id}/render — trigger partial render
"""
import sys
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

router = APIRouter(prefix="/api/timeline")


# ── Request / Response Models ──


class EditRequest(BaseModel):
    """Edit operation from the frontend."""
    operation: str = Field(..., description="moveScene | resizeScene | updateContent")
    scene_id: str = Field(..., description="Target scene ID")
    # For moveScene
    delta_frames: Optional[int] = Field(None, description="Frame offset for moveScene")
    # For resizeScene
    new_end_frame: Optional[int] = Field(None, description="New end frame for resizeScene")
    # For updateContent
    field: Optional[str] = Field(None, description="Field name for updateContent")
    value: Optional[str] = Field(None, description="New value for updateContent")


class EditResponse(BaseModel):
    success: bool
    recomputed_nodes: list[str] = []
    cache_hits: list[str] = []
    duration: float = 0.0
    error: Optional[str] = None


class TimelineResponse(BaseModel):
    session_id: str
    topic: str
    tracks: list[dict]
    duration_frames: int
    fps: int
    width: int
    height: int


# ── In-memory session references ──
# Import from thinking_api to share session store


def _get_session(session_id: str):
    """Get a ThinkingSession by ID."""
    try:
        from api.thinking_api import _get_session as _get
        return _get(session_id)
    except Exception:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")


def _extract_timeline_data(session) -> dict:
    """Extract timeline tracks from a ThinkingSession."""
    # Try to get timeline from the session's artifact graph
    tracks = []
    duration_frames = 900
    fps = 30
    width = 1080
    height = 1920

    if hasattr(session, 'artifact_graph'):
        from thinking.artifacts import ArtifactType
        graph = session.artifact_graph

        # Find timeline artifact
        timeline_art = graph.find_by_type(ArtifactType.TIMELINE)
        if timeline_art and timeline_art.content:
            tc = timeline_art.content
            if isinstance(tc, dict):
                tracks_raw = tc.get("tracks", [])
                for t in tracks_raw:
                    if isinstance(t, dict):
                        tracks.append(t)
                    elif hasattr(t, 'to_dict'):
                        tracks.append(t.to_dict())
                    elif hasattr(t, '__dict__'):
                        tracks.append({
                            "track_id": getattr(t, 'track_id', ''),
                            "track_type": getattr(t, 'track_type', 'video'),
                            "layer": getattr(t, 'layer', 0),
                            "start_frame": getattr(t, 'start_frame', 0),
                            "end_frame": getattr(t, 'end_frame', 0),
                            "scene_id": getattr(t, 'scene_id', ''),
                            "content": getattr(t, 'content', {}),
                        })
                fps = tc.get("fps", 30)
                width = tc.get("width", 1080)
                height = tc.get("height", 1920)

        # Compute duration from tracks
        if tracks:
            duration_frames = max(t.get("end_frame", 0) for t in tracks)

    return {
        "tracks": tracks,
        "duration_frames": duration_frames,
        "fps": fps,
        "width": width,
        "height": height,
    }


# ── Endpoints ──


@router.get("/{session_id}")
async def get_timeline(session_id: str) -> TimelineResponse:
    """Get timeline data for the editor."""
    session = _get_session(session_id)
    data = _extract_timeline_data(session)

    topic = ""
    if hasattr(session, 'config'):
        topic = session.config.get("topic", "")
    elif hasattr(session, '_config'):
        topic = session._config.get("topic", "")

    return TimelineResponse(
        session_id=session_id,
        topic=topic,
        tracks=data["tracks"],
        duration_frames=data["duration_frames"],
        fps=data["fps"],
        width=data["width"],
        height=data["height"],
    )


@router.post("/{session_id}/edit")
async def edit_timeline(session_id: str, req: EditRequest) -> EditResponse:
    """Apply an edit operation to the timeline."""
    session = _get_session(session_id)
    import time as _time
    start = _time.time()

    try:
        recomputed = []
        cache_hits = []

        if req.operation == "moveScene" and req.delta_frames is not None:
            _move_scene(session, req.scene_id, req.delta_frames)
            recomputed = _recompute_affected(session, req.scene_id)

        elif req.operation == "resizeScene" and req.new_end_frame is not None:
            _resize_scene(session, req.scene_id, req.new_end_frame)
            recomputed = _recompute_affected(session, req.scene_id)

        elif req.operation == "updateContent" and req.field and req.value is not None:
            _update_content(session, req.scene_id, req.field, req.value)
            recomputed = _recompute_affected(session, req.scene_id)

        else:
            return EditResponse(
                success=False,
                error=f"Unknown operation or missing params: {req.operation}",
            )

        return EditResponse(
            success=True,
            recomputed_nodes=recomputed,
            cache_hits=cache_hits,
            duration=_time.time() - start,
        )

    except Exception as e:
        return EditResponse(success=False, error=str(e))


@router.get("/{session_id}/assets")
async def list_assets(session_id: str, asset_type: str = ""):
    """List available assets from the AssetStore."""
    try:
        from backend.asset_store import AssetStore
        store = AssetStore()
        assets = store.list_assets(asset_type)
        return {"assets": assets, "total": len(assets)}
    except Exception as e:
        return {"assets": [], "total": 0, "error": str(e)}


# ── Persistence Endpoints ──


class SaveRequest(BaseModel):
    tracks: list[dict] = Field(default_factory=list)
    undo_stack: list[dict] = Field(default_factory=list)
    redo_stack: list[dict] = Field(default_factory=list)
    meta: Optional[dict] = None
    expected_version: Optional[int] = Field(None, description="For optimistic locking. Set to current version to detect conflicts.")


class SaveResponse(BaseModel):
    success: bool
    session_id: str
    last_saved: float = 0.0
    version: int = 0
    conflict: bool = False
    current_version: Optional[int] = None
    error: Optional[str] = None


class LoadResponse(BaseModel):
    success: bool
    session_id: str
    tracks: list[dict] = []
    undo_stack: list[dict] = []
    redo_stack: list[dict] = []
    meta: dict = {}
    version: int = 0
    error: Optional[str] = None


@router.post("/{session_id}/save")
async def save_session(session_id: str, req: SaveRequest) -> SaveResponse:
    """Save session state (timeline + undo/redo) to disk."""
    try:
        from backend.session_store import SessionStore, SaveConflictError
        store = SessionStore()

        if req.expected_version is not None:
            # Versioned save (optimistic locking)
            try:
                result = store.save_versioned(
                    session_id=session_id,
                    expected_version=req.expected_version,
                    tracks=req.tracks,
                    undo_stack=req.undo_stack,
                    redo_stack=req.redo_stack,
                    meta=req.meta,
                )
                return SaveResponse(
                    success=True,
                    session_id=session_id,
                    last_saved=result.get("last_saved", 0),
                    version=result.get("version", 0),
                )
            except SaveConflictError as e:
                current = store.load(session_id)
                return SaveResponse(
                    success=False,
                    session_id=session_id,
                    conflict=True,
                    error=str(e),
                    version=req.expected_version,
                    current_version=current.get("meta", {}).get("version", 0) if current else 0,
                )
        else:
            # Last-write-wins save
            result = store.save(
                session_id=session_id,
                tracks=req.tracks,
                undo_stack=req.undo_stack,
                redo_stack=req.redo_stack,
                meta=req.meta,
            )
            return SaveResponse(
                success=True,
                session_id=session_id,
                last_saved=result.get("last_saved", 0),
                version=result.get("version", 0),
            )
    except Exception as e:
        return SaveResponse(success=False, session_id=session_id, error=str(e))


@router.get("/{session_id}/load")
async def load_session(session_id: str) -> LoadResponse:
    """Load saved session state from disk."""
    try:
        from backend.session_store import SessionStore
        store = SessionStore()
        state = store.load(session_id)
        if state is None:
            return LoadResponse(
                success=False,
                session_id=session_id,
                error=f"Session {session_id} not found",
            )
        return LoadResponse(
            success=True,
            session_id=session_id,
            tracks=state.get("tracks", []),
            undo_stack=state.get("undo_stack", []),
            redo_stack=state.get("redo_stack", []),
            meta=state.get("meta", {}),
            version=state.get("meta", {}).get("version", 0),
        )
    except Exception as e:
        return LoadResponse(success=False, session_id=session_id, error=str(e))


@router.get("/sessions/list")
async def list_saved_sessions():
    """List all saved sessions."""
    try:
        from backend.session_store import SessionStore
        store = SessionStore()
        sessions = store.list_sessions()
        return {"sessions": sessions, "total": len(sessions)}
    except Exception as e:
        return {"sessions": [], "total": 0, "error": str(e)}


# ── Internal helpers ──


def _move_scene(session, scene_id: str, delta_frames: int):
    """Move all tracks for a scene by delta_frames."""
    if not hasattr(session, 'artifact_graph'):
        return
    from thinking.artifacts import ArtifactType
    graph = session.artifact_graph

    timeline_art = graph.find_by_type(ArtifactType.TIMELINE)
    if not timeline_art or not timeline_art.content:
        return

    tracks = timeline_art.content.get("tracks", [])
    for t in tracks:
        if isinstance(t, dict) and t.get("scene_id") == scene_id:
            t["start_frame"] = max(0, t.get("start_frame", 0) + delta_frames)
            t["end_frame"] = max(1, t.get("end_frame", 0) + delta_frames)


def _resize_scene(session, scene_id: str, new_end_frame: int):
    """Resize a scene's end frame."""
    if not hasattr(session, 'artifact_graph'):
        return
    from thinking.artifacts import ArtifactType
    graph = session.artifact_graph

    timeline_art = graph.find_by_type(ArtifactType.TIMELINE)
    if not timeline_art or not timeline_art.content:
        return

    tracks = timeline_art.content.get("tracks", [])
    for t in tracks:
        if isinstance(t, dict) and t.get("scene_id") == scene_id:
            t["end_frame"] = max(t.get("start_frame", 0) + 1, new_end_frame)


def _update_content(session, scene_id: str, field: str, value: str):
    """Update a field in a scene's content."""
    if not hasattr(session, 'artifact_graph'):
        return
    from thinking.artifacts import ArtifactType
    graph = session.artifact_graph

    timeline_art = graph.find_by_type(ArtifactType.TIMELINE)
    if not timeline_art or not timeline_art.content:
        return

    tracks = timeline_art.content.get("tracks", [])
    for t in tracks:
        if isinstance(t, dict) and t.get("scene_id") == scene_id:
            content = t.get("content", {})
            content[field] = value
            t["content"] = content


def _recompute_affected(session, scene_id: str) -> list[str]:
    """Recompute artifacts affected by a scene change."""
    recomputed = []
    if hasattr(session, 'artifact_graph'):
        from thinking.artifacts import ArtifactType
        graph = session.artifact_graph
        # Mark downstream artifacts as needing recomputation
        render_art = graph.find_by_type(ArtifactType.RENDER_PLAN)
        if render_art:
            recomputed.append(render_art.id)
    return recomputed


# ── DAG / Observability Endpoint ──


# In-memory DAG cache (per session)
_dag_cache: dict[str, Any] = {}


@router.get("/{session_id}/dag")
async def get_dag(session_id: str):
    """Get pipeline DAG for observability."""
    # Return cached DAG if available
    if session_id in _dag_cache:
        return _dag_cache[session_id]

    # Build a mock DAG from the session's artifact graph
    try:
        session = _get_session(session_id)
        from runtime.dag import PipelineDAG

        dag = PipelineDAG(name=f"pipeline_{session_id}")

        if hasattr(session, 'artifact_graph'):
            from thinking.artifacts import ArtifactType
            graph = session.artifact_graph

            # Map artifact types to DAG nodes
            type_map = {
                ArtifactType.SCRIPT: ("script", "ScriptPass"),
                ArtifactType.TTS_SENTENCE: ("tts", "TTSPass"),
                ArtifactType.SHOTS: ("scene", "ScenePass"),
                ArtifactType.TIMELINE: ("timeline", "TimelinePass"),
                ArtifactType.RENDER_PLAN: ("render", "RenderPass"),
            }

            deps_map = {
                "tts": ["script"],
                "scene": ["script"],
                "timeline": ["tts", "scene"],
                "render": ["timeline"],
            }

            added = set()
            for art_type, (node_id, node_name) in type_map.items():
                arts = graph.find_all_by_type(art_type) if hasattr(graph, 'find_all_by_type') else []
                if not arts:
                    single = graph.find_by_type(art_type)
                    if single:
                        arts = [single]
                for art in arts:
                    nid = f"{node_id}_{art.id}" if len(arts) > 1 else node_id
                    if nid not in added:
                        dag.add_node(
                            nid, node_name,
                            inputs={"artifact_id": art.id},
                            depends_on=deps_map.get(node_id, []),
                        )
                        added.add(nid)

            # Mark all as completed (since we have the artifacts)
            for nid in added:
                dag.start(nid)
                dag.complete(nid, cache_hit=True)

        result = dag.to_dict()
        _dag_cache[session_id] = result
        return result

    except Exception:
        # Return empty DAG on error
        return {
            "name": "pipeline",
            "total_duration": 0,
            "is_complete": False,
            "nodes": {},
            "edges": [],
            "stats": {},
        }


class DAGUpdateRequest(BaseModel):
    """Update a DAG node's status."""
    node_id: str
    status: str  # pending | running | done | error | skipped
    cache_hit: bool = False
    error: Optional[str] = None
    outputs: Optional[dict] = None


@router.post("/{session_id}/dag/update")
async def update_dag_node(session_id: str, req: DAGUpdateRequest):
    """Update a DAG node's status (for real-time pipeline tracking)."""
    if session_id not in _dag_cache:
        return {"success": False, "error": "No DAG found for session"}

    dag_data = _dag_cache[session_id]
    nodes = dag_data.get("nodes", {})

    if req.node_id not in nodes:
        return {"success": False, "error": f"Node {req.node_id} not found"}

    node = nodes[req.node_id]
    node["status"] = req.status
    node["cache_hit"] = req.cache_hit
    if req.error:
        node["error"] = req.error
    if req.outputs:
        node["outputs"].update(req.outputs)

    # Recalculate stats
    stats: dict[str, int] = {}
    for n in nodes.values():
        s = n.get("status", "pending")
        stats[s] = stats.get(s, 0) + 1
    stats["cache_hits"] = sum(1 for n in nodes.values() if n.get("cache_hit"))
    dag_data["stats"] = stats
    dag_data["is_complete"] = all(
        n.get("status") in ("done", "error", "skipped") for n in nodes.values()
    )

    return {"success": True, "node": req.node_id, "status": req.status}


# ── WebSocket for real-time DAG updates ──

# Active WebSocket connections per session
_ws_connections: dict[str, list[Any]] = {}


@router.websocket("/{session_id}/dag/ws")
async def dag_websocket(websocket: Any, session_id: str):
    """WebSocket endpoint for real-time DAG status updates.

    Clients connect to receive live node status changes.
    Send JSON messages: {"type": "subscribe"} to start receiving updates.
    Server pushes: {"type": "node_update", "node_id": "...", "status": "..."}
    """
    from fastapi import WebSocket
    await websocket.accept()
    _ws_connections.setdefault(session_id, []).append(websocket)

    try:
        # Send current DAG state
        if session_id in _dag_cache:
            await websocket.send_json({
                "type": "dag_snapshot",
                "data": _dag_cache[session_id],
            })

        # Keep connection alive and handle messages
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "get_dag":
                if session_id in _dag_cache:
                    await websocket.send_json({
                        "type": "dag_snapshot",
                        "data": _dag_cache[session_id],
                    })

    except Exception:
        pass
    finally:
        if session_id in _ws_connections:
            _ws_connections[session_id] = [
                ws for ws in _ws_connections[session_id] if ws != websocket
            ]


async def broadcast_dag_update(session_id: str, node_id: str, status: str, **kwargs):
    """Broadcast a DAG node update to all connected WebSocket clients."""
    if session_id not in _ws_connections:
        return

    message = {
        "type": "node_update",
        "node_id": node_id,
        "status": status,
        **kwargs,
    }

    dead = []
    for ws in _ws_connections[session_id]:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)

    for ws in dead:
        _ws_connections[session_id].remove(ws)
