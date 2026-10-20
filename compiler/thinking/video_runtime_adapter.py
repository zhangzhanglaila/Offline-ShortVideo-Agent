"""Video Runtime Adapter — Decomposes the video pipeline into artifact-producing nodes.

This is the bridge between:
  - engine/bridge/graph_pipeline.py (monolithic build_graph_video_layout)
  - thinking/artifacts.py (ArtifactGraph, VideoArtifact)
  - thinking/scheduler.py (incremental recomputation)

Instead of one giant function that returns a dict, the pipeline is decomposed
into atomic node functions. Each node:
  - Takes upstream artifacts as input
  - Produces a single artifact as output
  - Is memoizable (content-addressable)
  - Emits UpdateArtifactPatch when its output changes

The Scheduler uses the ArtifactGraph to determine which nodes need
recomputation when a user edits something (e.g. one sentence).

Usage:
    adapter = VideoRuntimeAdapter()
    adapter.run("Redis缓存原理", total_ms=12000, enable_audio=True)
    # ... user edits sentence 3 ...
    adapter.on_sentence_edit(module_id="mod_00", sentence_id="s_03", new_text="...")
    adapter.run_invalidated()  # only recomputes TTS + timeline + render plan
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


def build_scene_ir(
    scene: dict,
    audio_tracks: list[dict],
    elements: list[dict],
    *,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    theme: str = "light",
) -> dict:
    """Build a scene IR dict from a scene and global lists (pure function).

    Extracts local audio slices and local subtitle elements, then builds
    the scene IR content (for content-addressable hashing) plus transition
    metadata (excluded from hash).

    Returns:
        {"content": {...}, "transition": {...}, "scene_id": str}
    """
    scene_id = scene.get("id", "")
    scene_start = scene.get("start", 0)
    scene_duration = scene.get("duration", 0)
    scene_type = scene.get("type", "graph")

    # Local audio slices
    local_audio = []
    for track in audio_tracks:
        t_start = track.get("start", 0)
        t_dur = track.get("duration", 0)
        t_end = t_start + t_dur
        s_end = scene_start + scene_duration
        if t_start < s_end and t_end > scene_start:
            clip_start = max(t_start, scene_start)
            clip_end = min(t_end, s_end)
            local_audio.append({
                "id": track["id"],
                "src": track.get("src", ""),
                "local_start": clip_start - scene_start,
                "duration": clip_end - clip_start,
                "text": track.get("text", ""),
            })

    # Local elements
    local_elements = []
    for elem in elements:
        e_start = elem.get("start", 0)
        e_dur = elem.get("duration", 0)
        e_end = e_start + e_dur
        s_end = scene_start + scene_duration
        if e_start < s_end and e_end > scene_start:
            local_elem = dict(elem)
            local_elem["start"] = max(0, e_start - scene_start)
            local_elements.append(local_elem)

    overlap_in = scene.get("overlapIn", 0)
    overlap_out = scene.get("overlapOut", 0)

    # Content (hashable — excludes _transition)
    scene_ir_content = {
        "scene_id": scene_id,
        "scene_type": scene_type,
        "duration_in_frames": scene_duration,
        "width": width,
        "height": height,
        "fps": fps,
        "theme": theme,
        "audio_tracks": local_audio,
        "elements": local_elements,
    }
    if scene_type == "hook":
        scene_ir_content["text"] = scene.get("text", "")
    elif scene_type == "graph":
        scene_ir_content["graph"] = scene.get("graph", {})
    elif scene_type == "cards":
        scene_ir_content["title"] = scene.get("title", "")
        scene_ir_content["items"] = scene.get("items", [])

    # Transition metadata (excluded from content hash)
    transition = {
        "overlap_in": overlap_in,
        "overlap_out": overlap_out,
        "render_pad_in": overlap_in + 2,
        "render_pad_out": overlap_out + 2,
    }

    return {
        "content": scene_ir_content,
        "transition": transition,
        "scene_id": scene_id,
    }

from compiler.thinking.artifacts import (
    ArtifactGraph,
    ArtifactStatus,
    ArtifactType,
    UpdateArtifactPatch,
    VideoArtifact,
)


# ============================================================
# Pipeline Node — atomic computation step
# ============================================================

@dataclass
class PipelineNode:
    """An atomic step in the video pipeline.

    Each node maps to one ArtifactType and has a compute function
    that takes upstream artifacts and produces one output artifact.
    """
    id: str
    artifact_type: ArtifactType
    compute_fn: Callable[..., Any]
    description: str = ""
    depends_on: list[ArtifactType] = field(default_factory=list)


# ============================================================
# Video Runtime Adapter
# ============================================================

class VideoRuntimeAdapter:
    """Decomposes build_graph_video_layout() into reactive artifact nodes.

    Pipeline stages (each produces one artifact):
        1. script         → explainer sentences from LLM
        2. knowledge_graph → node/edge graph + layout
        3. shots          → director plan + concrete shot params
        4. tts_audio      → TTS audio files from script
        5. timeline       → merged multi-track timeline
        6. render_plan    → final layout JSON for Remotion
    """

    def __init__(self):
        self.artifact_graph = ArtifactGraph()
        self._nodes: dict[str, PipelineNode] = {}
        self._last_layout: dict[str, Any] | None = None
        self._last_patches: list[UpdateArtifactPatch] = []
        self._config: dict[str, Any] = {}
        self._current_artifact: VideoArtifact | None = None  # Set during run_invalidated
        self._render_cache: Any = None  # Lazy-loaded RenderCache
        self._register_default_nodes()

    def _register_default_nodes(self):
        """Register the standard video pipeline nodes."""
        self.register_node(PipelineNode(
            id="script_gen",
            artifact_type=ArtifactType.SCRIPT,
            compute_fn=self._compute_script,
            description="Generate explainer sentences from topic",
            depends_on=[],
        ))
        self.register_node(PipelineNode(
            id="graph_gen",
            artifact_type=ArtifactType.KNOWLEDGE_GRAPH,
            compute_fn=self._compute_knowledge_graph,
            description="Generate knowledge graph (nodes + edges + layout)",
            depends_on=[],
        ))
        self.register_node(PipelineNode(
            id="shots_gen",
            artifact_type=ArtifactType.SHOTS,
            compute_fn=self._compute_shots,
            description="Director plan → concrete shot params",
            depends_on=[ArtifactType.SCRIPT, ArtifactType.KNOWLEDGE_GRAPH],
        ))
        self.register_node(PipelineNode(
            id="tts_sentence_gen",
            artifact_type=ArtifactType.TTS_SENTENCE,
            compute_fn=self._compute_tts_sentence,
            description="TTS audio for a single sentence",
            depends_on=[ArtifactType.SCRIPT],
        ))
        self.register_node(PipelineNode(
            id="timeline_gen",
            artifact_type=ArtifactType.TIMELINE,
            compute_fn=self._compute_timeline,
            description="Merge shots + audio into timeline",
            depends_on=[ArtifactType.SHOTS, ArtifactType.TTS_SENTENCE],
        ))
        self.register_node(PipelineNode(
            id="render_plan_gen",
            artifact_type=ArtifactType.RENDER_PLAN,
            compute_fn=self._compute_render_plan,
            description="Build final layout JSON for Remotion",
            depends_on=[ArtifactType.TIMELINE, ArtifactType.KNOWLEDGE_GRAPH],
        ))
        self.register_node(PipelineNode(
            id="scene_ir_gen",
            artifact_type=ArtifactType.SCENE_IR,
            compute_fn=self._compute_scene_ir,
            description="Decompose render plan into per-scene IRs",
            depends_on=[ArtifactType.RENDER_PLAN],
        ))
        self.register_node(PipelineNode(
            id="scene_video_gen",
            artifact_type=ArtifactType.SCENE_VIDEO,
            compute_fn=self._compute_scene_video,
            description="Render a single scene IR to mp4",
            depends_on=[ArtifactType.SCENE_IR],
        ))

    def register_node(self, node: PipelineNode):
        """Register a pipeline node."""
        self._nodes[node.id] = node

    # ── Full Pipeline Run ──

    def run(
        self,
        topic: str,
        total_ms: int = 12000,
        width: int = 1080,
        height: int = 1920,
        enable_audio: bool = False,
        voice: str = "zh-CN-YunxiNeural",
        rate: int = 0,
        use_llm_director: bool = False,
        theme: str = "light",
    ) -> dict[str, Any]:
        """Run the full pipeline, creating artifacts at each stage.

        Returns the final layout dict (same as build_graph_video_layout).
        """
        # Import here to avoid circular dependency
        from engine.bridge.graph_pipeline import build_graph_video_layout

        # Store config for incremental recomputation
        self._config = {
            "topic": topic,
            "total_ms": total_ms,
            "width": width,
            "height": height,
            "enable_audio": enable_audio,
            "voice": voice,
            "rate": rate,
            "use_llm_director": use_llm_director,
            "theme": theme,
        }

        # Delegate to the existing monolithic function
        # and decompose its output into artifacts.
        layout = build_graph_video_layout(
            text=topic,
            total_ms=total_ms,
            width=width,
            height=height,
            enable_audio=enable_audio,
            voice=voice,
            rate=rate,
            use_llm_director=use_llm_director,
            theme=theme,
        )

        self._decompose_layout_into_artifacts(layout, topic)
        self._last_layout = layout
        return layout

    def run_invalidated(self) -> dict[str, Any] | None:
        """Recompute only stale artifacts in dependency order.

        For each stale artifact, calls the real compute function with
        upstream artifact content as inputs. This is the incremental
        recomputation path — only changed stages recompute.

        Returns updated layout, or None if nothing is stale.
        """
        stale = self.artifact_graph.get_stale()
        if not stale:
            return self._last_layout

        # Recompute in topological order
        for artifact in stale:
            node = self._find_node_for_type(artifact.type)
            if not node:
                continue

            # Gather upstream artifacts as inputs
            upstream = self.artifact_graph.get_upstream(artifact.id)
            upstream_map = {up.type: up for up in upstream}

            # Check that all upstream are fresh
            if not all(up.status == ArtifactStatus.FRESH for up in upstream):
                continue  # Skip — upstream still stale

            try:
                self._current_artifact = artifact
                new_content = node.compute_fn(upstream_map)
                updated = artifact.with_content(new_content)
                updated.status = ArtifactStatus.FRESH
                self.artifact_graph._artifacts[updated.id] = updated
                # Emit patch
                patch = UpdateArtifactPatch(
                    artifact_type=updated.type,
                    new_content=new_content,
                )
                self._last_patches.append(patch)
            except Exception as e:
                artifact.status = ArtifactStatus.FAILED
                artifact.error = str(e)

        # Rebuild layout from fresh artifacts
        return self._rebuild_layout()

    def run_partial_render(self, output_path: str = "") -> dict[str, Any]:
        """Incremental render: only re-render changed scenes, compose the rest.

        This is the main entry point for partial rendering:
        1. Recompute stale artifacts (including scene IRs)
        2. For each scene IR, check render cache → hit: reuse, miss: render
        3. Compose scene videos with ffmpeg xfade transitions

        Args:
            output_path: Path for the final composed video. Defaults to
                output/{topic}/video.mp4.

        Returns:
            Dict with video path, scenes rendered count, and cache stats.
        """
        # Lazy-load render cache
        if self._render_cache is None:
            from engine.bridge.render_cache import RenderCache
            self._render_cache = RenderCache()

        # Step 1: Recompute stale artifacts
        self.run_invalidated()

        # Step 2: Get scene IR artifacts
        scene_irs = self.artifact_graph.find_all_by_type(ArtifactType.SCENE_IR)
        if not scene_irs:
            return {"error": "No scene IRs found", "video": None}

        # Sort by scene order (determined by render plan's scene list)
        render_plan = self.artifact_graph.find_by_type(ArtifactType.RENDER_PLAN)
        scene_order = []
        if render_plan and render_plan.content:
            scene_order = [s["id"] for s in render_plan.content.get("scenes", [])]
        scene_irs.sort(key=lambda a: scene_order.index(a.metadata.get("scene_id", ""))
                       if a.metadata.get("scene_id", "") in scene_order else 999)

        # Step 3: Render or reuse each scene
        scene_videos = []
        cache_hits = 0
        cache_misses = 0

        for scene_ir_art in scene_irs:
            content_hash = scene_ir_art.content_hash
            scene_id = scene_ir_art.metadata.get("scene_id", "")

            # Check cache
            cached_path = self._render_cache.lookup(content_hash)
            if cached_path:
                scene_videos.append(cached_path)
                cache_hits += 1
                continue

            # Cache miss — render
            cache_misses += 1
            scene_ir = scene_ir_art.content
            try:
                from engine.bridge.graph_pipeline import render_scene_ir
                video_path = render_scene_ir(scene_ir, scene_id)
                from pathlib import Path
                stored_path = self._render_cache.store(
                    content_hash, Path(video_path), scene_id,
                )
                scene_videos.append(stored_path)
            except Exception as e:
                return {"error": f"Scene render failed for {scene_id}: {e}", "video": None}

        # Step 4: Compose scene videos
        if not output_path:
            topic = self._config.get("topic", "output")
            output_path = f"output/{topic}/video.mp4"

        from pathlib import Path

        # Gather overlap values
        overlaps = []
        for scene_ir_art in scene_irs[:-1]:
            transition = scene_ir_art.metadata.get("_transition", {})
            overlaps.append(transition.get("overlap_out", 8))

        try:
            from engine.bridge.compose import compose_scenes
            final_path = compose_scenes(
                scene_videos=scene_videos,
                overlaps=overlaps,
                output_path=Path(output_path),
                fps=self._config.get("fps", 30),
            )
        except Exception as e:
            return {"error": f"Compose failed: {e}", "video": None}

        return {
            "video": str(final_path),
            "scenes_total": len(scene_irs),
            "scenes_rendered": cache_misses,
            "cache_hits": cache_hits,
            "cache_stats": self._render_cache.stats(),
        }

    # ── Patch-Driven Invalidation ──

    def on_sentence_edit(
        self,
        module_id: str,
        sentence_id: str,
        new_text: str,
        old_text: str = "",
    ) -> list[str]:
        """Handle a sentence edit — invalidate only the affected TTS sentence.

        Instead of invalidating the entire script (which cascades to ALL
        TTS_SENTENCE artifacts), we surgically invalidate only the one
        TTS_SENTENCE matching this sentence_id. The script content is
        updated but NOT invalidated — its hash changes but downstream
        artifacts that don't depend on this specific sentence stay fresh.

        Returns list of invalidated artifact IDs.
        """
        script_art = self.artifact_graph.find_by_type(ArtifactType.SCRIPT)
        if not script_art:
            return []

        # Update script content (but don't invalidate — be surgical)
        content = script_art.content or {}
        sentences = content.get("sentences", [])
        sentence_index = -1
        for s in sentences:
            if s.get("id") == sentence_id:
                s["text"] = new_text
                sentence_index = s.get("index", -1)
                break

        # Update script artifact in-place (content changes, but we
        # don't call invalidate on it — that would cascade to ALL sentences)
        updated = script_art.with_content(content)
        self.artifact_graph._artifacts[updated.id] = updated

        # Find and invalidate only the matching TTS_SENTENCE artifact
        invalidated: list[str] = []
        if sentence_index >= 0:
            tts_id = f"tts_sentence_{sentence_index}"
            if tts_id in self.artifact_graph:
                invalidated = self.artifact_graph.invalidate(
                    tts_id,
                    reason=f"sentence_edit:{sentence_id}",
                )

        return invalidated

    def on_graph_edit(
        self,
        node_id: str,
        new_label: str = "",
        new_role: str = "",
    ) -> list[str]:
        """Handle a graph node edit — invalidate downstream artifacts."""
        graph_art = self.artifact_graph.find_by_type(ArtifactType.KNOWLEDGE_GRAPH)
        if not graph_art:
            return []

        content = graph_art.content or {}
        for n in content.get("nodes", []):
            if n.get("id") == node_id:
                if new_label:
                    n["label"] = new_label
                if new_role:
                    n["role"] = new_role
                break

        updated = graph_art.with_content(content)
        self.artifact_graph._artifacts[updated.id] = updated

        invalidated = self.artifact_graph.invalidate(
            updated.id,
            reason=f"graph_edit:{node_id}",
        )

        return invalidated

    # ── Internal: Decompose layout into artifacts ──

    def _decompose_layout_into_artifacts(self, layout: dict[str, Any], topic: str):
        """Extract artifacts from a completed layout dict."""
        graph = layout.get("graph", {})
        audio_tracks = layout.get("audioTracks", [])
        explainer_script = layout.get("explainerScript", [])

        # 1. Script artifact
        script_content = {
            "topic": topic,
            "sentences": [
                {"id": f"s_{i}", "index": i, "text": t}
                for i, t in enumerate(explainer_script)
            ],
        }
        script_art = self.artifact_graph.create(
            ArtifactType.SCRIPT,
            content=script_content,
            metadata={"topic": topic},
        )

        # 2. Knowledge graph artifact
        graph_content = {
            "title": graph.get("title", ""),
            "summary": graph.get("summary", ""),
            "nodes": graph.get("nodes", []),
            "edges": graph.get("edges", []),
        }
        graph_art = self.artifact_graph.create(
            ArtifactType.KNOWLEDGE_GRAPH,
            content=graph_content,
            metadata={"topic": topic},
        )

        # 3. Shots artifact
        shots_content = {
            "shots": graph.get("shots", []),
            "animation_plan": graph.get("animation_plan", {}),
            "scenes_llm": graph.get("_scenes_llm"),
            "emphasis": graph.get("_emphasis", []),
            "pace": graph.get("_pace", "medium"),
        }
        shots_art = self.artifact_graph.create(
            ArtifactType.SHOTS,
            content=shots_content,
            depends_on=[script_art, graph_art],
        )

        # 4. Per-sentence TTS artifacts (fine-grained invalidation)
        tts_sentence_arts = []
        sentences = script_content.get("sentences", [])
        for i, track in enumerate(audio_tracks):
            sentence_text = sentences[i]["text"] if i < len(sentences) else track.get("text", "")
            tts_art = self.artifact_graph.create(
                ArtifactType.TTS_SENTENCE,
                content={"track": track, "sentence_index": i, "sentence_text": sentence_text},
                depends_on=[script_art],
                metadata={"sentence_index": i, "sentence_id": f"s_{i}"},
                artifact_id=f"tts_sentence_{i}",
            )
            tts_sentence_arts.append(tts_art)

        # 5. Timeline artifact
        timeline_content = {
            "timeline": graph.get("timeline", []),
            "steps": graph.get("steps", []),
            "total_frames": layout.get("durationInFrames", 0),
        }
        timeline_art = self.artifact_graph.create(
            ArtifactType.TIMELINE,
            content=timeline_content,
            depends_on=[shots_art] + tts_sentence_arts,
        )

        # 6. Render plan artifact
        render_content = {
            "width": layout.get("width", 1080),
            "height": layout.get("height", 1920),
            "fps": layout.get("fps", 30),
            "durationInFrames": layout.get("durationInFrames", 0),
            "scenes": layout.get("scenes", []),
            "elements": layout.get("elements", []),
            "audioTracks": audio_tracks,
        }
        render_plan_art = self.artifact_graph.create(
            ArtifactType.RENDER_PLAN,
            content=render_content,
            depends_on=[timeline_art, graph_art],
        )

        # 7. Scene IR artifacts (per-scene, local coordinates)
        scenes = layout.get("scenes", [])
        all_elements = layout.get("elements", [])
        for scene in scenes:
            scene_id = scene.get("id", "")
            ir = build_scene_ir(
                scene, audio_tracks, all_elements,
                width=render_content["width"],
                height=render_content["height"],
                fps=render_content["fps"],
                theme=self._config.get("theme", "light"),
            )

            self.artifact_graph.create(
                ArtifactType.SCENE_IR,
                content=ir["content"],
                depends_on=[render_plan_art],
                metadata={"scene_id": ir["scene_id"], "_transition": ir["transition"]},
                artifact_id=f"scene_ir_{ir['scene_id']}",
            )

    # ── Internal: Node compute functions ──
    # Each function calls the real pipeline code and returns a content dict.

    def _compute_script(self, upstream: dict[ArtifactType, VideoArtifact]) -> dict:
        """Generate explainer sentences from topic via LLM."""
        from engine.bridge.graph_pipeline import _generate_explainer_script

        topic = self._config.get("topic", "")
        sentences = _generate_explainer_script(topic, num_sentences=5)
        return {
            "topic": topic,
            "sentences": [
                {"id": f"s_{i}", "index": i, "text": t}
                for i, t in enumerate(sentences)
            ],
        }

    def _compute_knowledge_graph(self, upstream: dict[ArtifactType, VideoArtifact]) -> dict:
        """Generate knowledge graph (scene DSL + layout) from topic."""
        from engine.bridge.graph_pipeline import generate_scene_dsl, apply_graph_layout

        topic = self._config.get("topic", "")
        width = self._config.get("width", 1080)
        height = self._config.get("height", 1920)

        dsl = generate_scene_dsl(topic)
        graph = apply_graph_layout(dsl, width=width, height=height)
        return {
            "title": graph.get("title", ""),
            "summary": graph.get("summary", ""),
            "nodes": graph.get("nodes", []),
            "edges": graph.get("edges", []),
        }

    def _compute_shots(self, upstream: dict[ArtifactType, VideoArtifact]) -> dict:
        """Director plan → concrete shot params."""
        from engine.bridge.graph_pipeline import (
            build_default_plan, classify_graph,
            _normalize_audio_tracks, _extract_audio_emphasis,
        )

        graph_art = upstream.get(ArtifactType.KNOWLEDGE_GRAPH)
        script_art = upstream.get(ArtifactType.SCRIPT)

        if not graph_art or not graph_art.content:
            return {}

        graph = dict(graph_art.content)
        topic = self._config.get("topic", "")
        total_ms = self._config.get("total_ms", 12000)
        fps = 30
        total_frames = max(1, round(total_ms / 1000 * fps))

        # Gather audio tracks from per-sentence TTS artifacts
        tts_arts = self.artifact_graph.find_all_by_type(ArtifactType.TTS_SENTENCE)
        tts_arts.sort(key=lambda a: a.metadata.get("sentence_index", 0))
        audio_tracks = []
        for art in tts_arts:
            if art.content and art.content.get("track"):
                audio_tracks.append(art.content["track"])

        # Rule-based baseline plan
        animation_plan = build_default_plan(graph, total_frames, audio_tracks or None)
        shots = animation_plan.get("shots", [])

        # Optional LLM director
        llm_scenes = None
        if self._config.get("use_llm_director"):
            try:
                from engine.bridge.director_plan import (
                    call_llm_for_director_plan,
                    plan_to_scenes_and_shots,
                )
                director_plan = call_llm_for_director_plan(topic, graph)
                if director_plan:
                    translated = plan_to_scenes_and_shots(
                        director_plan, graph, total_frames, audio_tracks,
                    )
                    llm_scenes = translated.get("scenes")
                    if llm_scenes:
                        all_llm_shots = []
                        for sc in llm_scenes:
                            if sc["type"] == "graph":
                                for s in sc.get("shots", []):
                                    if "start" in s and "duration" in s:
                                        all_llm_shots.append(s)
                        if all_llm_shots:
                            shots = all_llm_shots
            except Exception:
                pass

        # Audio emphasis
        emphasis = []
        if audio_tracks:
            emphasis = _extract_audio_emphasis(audio_tracks, graph)

        return {
            "shots": shots,
            "animation_plan": animation_plan,
            "scenes_llm": llm_scenes,
            "emphasis": emphasis,
            "pace": "medium",
        }

    def _compute_tts_sentence(self, upstream: dict[ArtifactType, VideoArtifact]) -> dict:
        """Generate TTS audio for a single sentence (fine-grained).

        Uses self._current_artifact to determine which sentence index
        to regenerate. Returns a single track dict.
        """
        from engine.bridge.graph_pipeline import (
            _generate_explainer_audio_tracks, _normalize_audio_tracks,
        )

        script_art = upstream.get(ArtifactType.SCRIPT)
        if not script_art or not script_art.content:
            return {"track": None, "sentence_index": -1, "sentence_text": ""}

        # Determine which sentence this artifact is for
        sentence_index = -1
        sentence_text = ""
        if self._current_artifact and self._current_artifact.metadata:
            sentence_index = self._current_artifact.metadata.get("sentence_index", -1)

        sentences = script_art.content.get("sentences", [])
        if 0 <= sentence_index < len(sentences):
            sentence_text = sentences[sentence_index]["text"]
        else:
            return {"track": None, "sentence_index": sentence_index, "sentence_text": ""}

        voice = self._config.get("voice", "zh-CN-YunxiNeural")
        rate = self._config.get("rate", 0)

        # Generate audio for just this one sentence
        raw_tracks = _generate_explainer_audio_tracks(
            [sentence_text], total_ms=120000, voice=voice, rate=rate,
        )
        normalized = _normalize_audio_tracks(raw_tracks)

        track = normalized[0] if normalized else None
        return {
            "track": track,
            "sentence_index": sentence_index,
            "sentence_text": sentence_text,
        }

    def _compute_timeline(self, upstream: dict[ArtifactType, VideoArtifact]) -> dict:
        """Merge shots + audio into a unified timeline.

        Gathers audio tracks from all TTS_SENTENCE artifacts (sorted by
        sentence index) instead of a single monolithic TTS_AUDIO blob.
        """
        shots_art = upstream.get(ArtifactType.SHOTS)

        if not shots_art or not shots_art.content:
            return {"timeline": [], "steps": [], "total_frames": 0}

        shots_data = shots_art.content

        # Gather audio from all TTS_SENTENCE artifacts, sorted by index
        tts_arts = self.artifact_graph.find_all_by_type(ArtifactType.TTS_SENTENCE)
        tts_arts.sort(key=lambda a: a.metadata.get("sentence_index", 0))
        audio_tracks = []
        for art in tts_arts:
            if art.content and art.content.get("track"):
                audio_tracks.append(art.content["track"])

        total_ms = self._config.get("total_ms", 12000)
        fps = 30
        total_frames = max(1, round(total_ms / 1000 * fps))

        # Extend total_frames if audio is longer
        if audio_tracks:
            audio_end = max(
                (t["start"] + t["duration"] for t in audio_tracks),
                default=0,
            )
            if audio_end > total_frames:
                total_frames = audio_end

        # Build timeline from animation_plan shots
        animation_plan = shots_data.get("animation_plan", {})
        raw_timeline = animation_plan.get("timeline", [])

        timeline = []
        if raw_timeline:
            max_end_ms = max(
                int(e.get("time", 0)) + int(e.get("duration", 0))
                for e in raw_timeline
            ) or total_ms
            scale = total_ms / max_end_ms
            for idx, event in enumerate(raw_timeline):
                start = round(int(event.get("time", 0)) * scale / 1000 * fps)
                duration = max(1, round(int(event.get("duration", 2000)) * scale / 1000 * fps))
                if idx == len(raw_timeline) - 1:
                    duration = max(1, total_frames - start)
                timeline.append({**event, "start": start, "duration": duration})

        # Build steps from timeline or animation_plan
        steps = []
        source_steps = timeline if timeline else animation_plan.get("steps", [])
        for step in source_steps:
            steps.append({
                **step,
                "start": int(step["start"]),
                "duration": int(step["duration"]),
            })

        return {
            "timeline": timeline,
            "steps": steps,
            "total_frames": total_frames,
        }

    def _compute_render_plan(self, upstream: dict[ArtifactType, VideoArtifact]) -> dict:
        """Build final layout JSON for Remotion: subtitles, scenes, crossfade."""
        from engine.bridge.graph_pipeline import (
            _build_hook_text, _build_summary_items, _extract_audio_emphasis,
        )

        timeline_art = upstream.get(ArtifactType.TIMELINE)
        graph_art = upstream.get(ArtifactType.KNOWLEDGE_GRAPH)
        # We also need shots and tts — get from artifact graph
        shots_art = self.artifact_graph.find_by_type(ArtifactType.SHOTS)

        if not timeline_art or not timeline_art.content:
            return {}

        timeline_data = timeline_art.content
        total_frames = timeline_data.get("total_frames", 0)
        steps = timeline_data.get("steps", [])

        graph = dict(graph_art.content) if graph_art and graph_art.content else {}

        # Gather audio from per-sentence TTS artifacts
        tts_arts = self.artifact_graph.find_all_by_type(ArtifactType.TTS_SENTENCE)
        tts_arts.sort(key=lambda a: a.metadata.get("sentence_index", 0))
        audio_tracks = []
        for art in tts_arts:
            if art.content and art.content.get("track"):
                audio_tracks.append(art.content["track"])

        shots_data = shots_art.content if shots_art and shots_art.content else {}
        llm_scenes = shots_data.get("scenes_llm")

        width = self._config.get("width", 1080)
        height = self._config.get("height", 1920)
        topic = self._config.get("topic", "")

        # Subtitle elements
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
            for idx, step in enumerate(steps):
                elements.append({
                    "id": f"graph_caption_{idx}",
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

        # Scene assembly: hook → graph → cards
        scenes: list[dict[str, Any]] = []
        if audio_tracks and len(audio_tracks) >= 3:
            hook_track = audio_tracks[0]
            middle_tracks = audio_tracks[1:-1]
            graph_start = middle_tracks[0]["start"]
            graph_end = middle_tracks[-1]["start"] + middle_tracks[-1]["duration"]
            cards_track = audio_tracks[-1]

            hook_text = _build_hook_text(topic, hook_track.get("text", ""))
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
                },
                {
                    "id": "scene_graph",
                    "type": "graph",
                    "start": graph_start,
                    "duration": graph_end - graph_start,
                    "graph": {**graph, "shots": shots_data.get("shots", []), "animation_plan": shots_data.get("animation_plan", {}), "_emphasis": shots_data.get("emphasis", []), "_pace": shots_data.get("pace", "medium")},
                },
                {
                    "id": "scene_cards",
                    "type": "cards",
                    "start": cards_track["start"],
                    "duration": cards_track["duration"],
                    "title": cards_title,
                    "items": cards_items,
                },
            ]
        else:
            scenes = [{
                "id": "scene_graph",
                "type": "graph",
                "start": 0,
                "duration": total_frames,
                "graph": {**graph, "shots": shots_data.get("shots", []), "animation_plan": shots_data.get("animation_plan", {}), "_emphasis": shots_data.get("emphasis", []), "_pace": shots_data.get("pace", "medium")},
            }]

        # Crossfade overlap
        for i in range(len(scenes) - 1):
            curr_end = scenes[i]["start"] + scenes[i]["duration"]
            next_start = scenes[i + 1]["start"]
            pause_frames = max(0, next_start - curr_end)
            overlap = max(6, min(12, round(pause_frames * 0.75)))
            scenes[i]["overlapOut"] = overlap
            scenes[i + 1]["overlapIn"] = overlap

        # Seal gaps
        for i in range(len(scenes) - 1):
            next_start = scenes[i + 1]["start"]
            current_end = scenes[i]["start"] + scenes[i]["duration"]
            if next_start > current_end:
                scenes[i]["duration"] = next_start - scenes[i]["start"]

        return {
            "width": width,
            "height": height,
            "fps": 30,
            "durationInFrames": total_frames,
            "scenes": scenes,
            "elements": elements,
            "audioTracks": audio_tracks,
        }

    def _compute_scene_ir(self, upstream: dict[ArtifactType, VideoArtifact]) -> list[dict]:
        """Decompose render plan into per-scene intermediate representations.

        Each scene IR uses LOCAL coordinates (no absolute timeline positions).
        The content hash excludes _transition metadata so overlap changes
        don't invalidate the scene's render cache.
        """
        render_art = upstream.get(ArtifactType.RENDER_PLAN)
        if not render_art or not render_art.content:
            return []

        render = render_art.content
        scenes = render.get("scenes", [])
        audio_tracks = render.get("audioTracks", [])
        elements = render.get("elements", [])
        width = render.get("width", 1080)
        height = render.get("height", 1920)
        fps = render.get("fps", 30)
        theme = self._config.get("theme", "light")

        return [
            build_scene_ir(scene, audio_tracks, elements, width=width, height=height, fps=fps, theme=theme)
            for scene in scenes
        ]

    def _compute_scene_video(self, upstream: dict[ArtifactType, VideoArtifact]) -> dict:
        """Render a single scene IR to mp4 via Remotion.

        Checks render cache first. On cache miss, builds a single-scene
        layout JSON and invokes Remotion.
        """
        scene_ir_art = upstream.get(ArtifactType.SCENE_IR)
        if not scene_ir_art or not scene_ir_art.content:
            return {"video_path": None, "scene_id": "", "cached": False}

        scene_ir = scene_ir_art.content
        scene_id = scene_ir.get("scene_id", "")
        content_hash = scene_ir_art.content_hash

        # Check cache
        if hasattr(self, '_render_cache') and self._render_cache:
            cached_path = self._render_cache.lookup(content_hash)
            if cached_path:
                return {
                    "video_path": str(cached_path),
                    "scene_id": scene_id,
                    "cached": True,
                    "content_hash": content_hash,
                }

        # Cache miss — render via Remotion
        try:
            from engine.bridge.graph_pipeline import render_scene_ir
            output_path = render_scene_ir(scene_ir, scene_id)
            # Store in cache
            if hasattr(self, '_render_cache') and self._render_cache:
                from pathlib import Path
                self._render_cache.store(content_hash, Path(output_path), scene_id)
            return {
                "video_path": output_path,
                "scene_id": scene_id,
                "cached": False,
                "content_hash": content_hash,
            }
        except Exception as e:
            return {
                "video_path": None,
                "scene_id": scene_id,
                "cached": False,
                "error": str(e),
            }

    # ── Internal: Helpers ──

    def _find_node_for_type(self, artifact_type: ArtifactType) -> PipelineNode | None:
        """Find the pipeline node that produces a given artifact type."""
        for node in self._nodes.values():
            if node.artifact_type == artifact_type:
                return node
        return None

    def _rebuild_layout(self) -> dict[str, Any]:
        """Rebuild the layout dict from fresh artifacts."""
        render_art = self.artifact_graph.find_by_type(ArtifactType.RENDER_PLAN)
        if not render_art or not render_art.content:
            return self._last_layout or {}

        # Merge render plan content with graph metadata
        graph_art = self.artifact_graph.find_by_type(ArtifactType.KNOWLEDGE_GRAPH)
        shots_art = self.artifact_graph.find_by_type(ArtifactType.SHOTS)
        script_art = self.artifact_graph.find_by_type(ArtifactType.SCRIPT)

        render = render_art.content
        graph_content = graph_art.content if graph_art and graph_art.content else {}
        shots_content = shots_art.content if shots_art and shots_art.content else {}

        # Reconstruct the layout dict in the same shape as build_graph_video_layout
        graph_dict = {
            **graph_content,
            "shots": shots_content.get("shots", []),
            "animation_plan": shots_content.get("animation_plan", {}),
            "_emphasis": shots_content.get("emphasis", []),
            "_pace": shots_content.get("pace", "medium"),
        }

        return {
            "width": render.get("width", 1080),
            "height": render.get("height", 1920),
            "fps": render.get("fps", 30),
            "durationInFrames": render.get("durationInFrames", 0),
            "background": "#070b10",
            "scene_type": "graph",
            "graph": graph_dict,
            "nodes": graph_content.get("nodes", []),
            "edges": graph_content.get("edges", []),
            "elements": render.get("elements", []),
            "shots": [],
            "scenes": render.get("scenes", []),
            "audioTracks": render.get("audioTracks", []),
            "explainerScript": [
                s["text"] for s in (script_art.content or {}).get("sentences", [])
            ] if script_art else [],
        }

    # ── Public API ──

    @property
    def last_layout(self) -> dict[str, Any] | None:
        """The most recently computed layout dict."""
        return self._last_layout

    @property
    def last_patches(self) -> list[UpdateArtifactPatch]:
        """Patches emitted during the last run."""
        return self._last_patches

    def summary(self) -> dict[str, Any]:
        """Summary of the adapter's state."""
        return {
            "artifact_graph": self.artifact_graph.summary(),
            "registered_nodes": list(self._nodes.keys()),
            "has_layout": self._last_layout is not None,
            "patch_count": len(self._last_patches),
            "config": {k: v for k, v in self._config.items() if k != "topic"},
        }
