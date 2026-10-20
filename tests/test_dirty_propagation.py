"""P1.4 — Mutation Isolation / Dirty Propagation Tests.

Verifies that the incremental invalidation cascade works correctly:
  - Changed artifact → downstream invalidated
  - Unchanged artifacts → cache survives
  - Invalidation closure is minimal (no over-invalidation)

This is the correctness proof for the reactive runtime.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.thinking.artifacts import ArtifactGraph, ArtifactType


# ── Fixture: build a mini pipeline graph ──────────────────────────────

def _build_pipeline_graph() -> tuple[ArtifactGraph, dict[str, any]]:
    """Build a realistic mini pipeline for testing propagation.

    Dependency chain:
        script → shots → timeline → render_plan → scene_ir → scene_video → video
        script → tts_sentence → timeline
    """
    graph = ArtifactGraph()

    # script
    script = graph.create(
        ArtifactType.SCRIPT,
        content={"sentences": [{"id": "s0", "text": "hello"}, {"id": "s1", "text": "world"}]},
    )

    # shots (depends on script)
    shots = graph.create(
        ArtifactType.SHOTS,
        content={"scenes": [{"id": "scene_hook"}, {"id": "scene_graph"}]},
        depends_on=[script],
    )

    # tts_sentence (depends on script)
    tts_0 = graph.create(
        ArtifactType.TTS_SENTENCE,
        content={"sentence_index": 0, "audio_path": "/audio/s0.mp3", "duration_ms": 3000},
        depends_on=[script],
        artifact_id="tts_sentence_0",
    )
    tts_1 = graph.create(
        ArtifactType.TTS_SENTENCE,
        content={"sentence_index": 1, "audio_path": "/audio/s1.mp3", "duration_ms": 2500},
        depends_on=[script],
        artifact_id="tts_sentence_1",
    )

    # timeline (depends on shots + tts)
    timeline = graph.create(
        ArtifactType.TIMELINE,
        content={"total_frames": 450, "audio_tracks": []},
        depends_on=[shots, tts_0, tts_1],
    )

    # render_plan (depends on timeline)
    render_plan = graph.create(
        ArtifactType.RENDER_PLAN,
        content={
            "width": 1080, "height": 1920, "fps": 30, "durationInFrames": 450,
            "scenes": [
                {"id": "scene_hook", "type": "hook", "start": 0, "duration": 150, "text": "hello"},
                {"id": "scene_graph", "type": "graph", "start": 150, "duration": 300, "graph": {}},
            ],
            "elements": [],
            "audioTracks": [],
        },
        depends_on=[timeline],
    )

    # scene_ir (depends on render_plan)
    scene_ir_hook = graph.create(
        ArtifactType.SCENE_IR,
        content={"scene_id": "scene_hook", "scene_type": "hook", "text": "hello", "duration_in_frames": 150},
        depends_on=[render_plan],
        artifact_id="scene_ir_scene_hook",
    )
    scene_ir_graph = graph.create(
        ArtifactType.SCENE_IR,
        content={"scene_id": "scene_graph", "scene_type": "graph", "graph": {}, "duration_in_frames": 300},
        depends_on=[render_plan],
        artifact_id="scene_ir_scene_graph",
    )

    # scene_video (depends on scene_ir)
    scene_vid_hook = graph.create(
        ArtifactType.SCENE_VIDEO,
        content={"scene_id": "scene_hook", "video_path": "/output/hook.mp4"},
        depends_on=[scene_ir_hook],
        artifact_id="scene_video_scene_hook",
    )
    scene_vid_graph = graph.create(
        ArtifactType.SCENE_VIDEO,
        content={"scene_id": "scene_graph", "video_path": "/output/graph.mp4"},
        depends_on=[scene_ir_graph],
        artifact_id="scene_video_scene_graph",
    )

    # video (depends on all scene_videos)
    video = graph.create(
        ArtifactType.VIDEO,
        content={"output_path": "/output/final.mp4"},
        depends_on=[scene_vid_hook, scene_vid_graph],
    )

    artifacts = {
        "script": script,
        "shots": shots,
        "tts_0": tts_0,
        "tts_1": tts_1,
        "timeline": timeline,
        "render_plan": render_plan,
        "scene_ir_hook": scene_ir_hook,
        "scene_ir_graph": scene_ir_graph,
        "scene_vid_hook": scene_vid_hook,
        "scene_vid_graph": scene_vid_graph,
        "video": video,
    }
    return graph, artifacts


# ═══════════════════════════════════════════════════════════════════════
# P1.4a — Invalidation Cascade Correctness
# ═══════════════════════════════════════════════════════════════════════


class TestInvalidationCascade:
    """When an upstream artifact changes, all downstream must be invalidated."""

    def test_script_change_invalidates_everything(self):
        graph, arts = _build_pipeline_graph()

        graph.invalidate(arts["script"].id)
        stale = {a.id for a in graph.get_stale()}

        # Everything downstream of script should be stale
        assert arts["script"].id in stale
        assert arts["shots"].id in stale
        assert arts["tts_0"].id in stale
        assert arts["tts_1"].id in stale
        assert arts["timeline"].id in stale
        assert arts["render_plan"].id in stale
        assert arts["scene_ir_hook"].id in stale
        assert arts["scene_ir_graph"].id in stale
        assert arts["scene_vid_hook"].id in stale
        assert arts["scene_vid_graph"].id in stale
        assert arts["video"].id in stale

    def test_timeline_change_propagates_downward(self):
        graph, arts = _build_pipeline_graph()

        graph.invalidate(arts["timeline"].id)
        stale = {a.id for a in graph.get_stale()}

        # Downstream: render_plan → scene_ir → scene_video → video
        assert arts["timeline"].id in stale
        assert arts["render_plan"].id in stale
        assert arts["scene_ir_hook"].id in stale
        assert arts["scene_ir_graph"].id in stale
        assert arts["video"].id in stale

        # Upstream must NOT be stale
        assert arts["script"].id not in stale
        assert arts["shots"].id not in stale
        assert arts["tts_0"].id not in stale

    def test_scene_ir_hook_change_affects_only_hook_subgraph(self):
        """Invalidating scene_ir_hook should NOT affect scene_ir_graph."""
        graph, arts = _build_pipeline_graph()

        graph.invalidate(arts["scene_ir_hook"].id)
        stale = {a.id for a in graph.get_stale()}

        # Hook subgraph stale
        assert arts["scene_ir_hook"].id in stale
        assert arts["scene_vid_hook"].id in stale
        assert arts["video"].id in stale  # final video depends on both

        # Graph subgraph must NOT be stale
        assert arts["scene_ir_graph"].id not in stale
        assert arts["scene_vid_graph"].id not in stale

        # Upstream must NOT be stale
        assert arts["script"].id not in stale
        assert arts["render_plan"].id not in stale


# ═══════════════════════════════════════════════════════════════════════
# P1.4b — Unaffected Subgraph Preservation
# ═══════════════════════════════════════════════════════════════════════


class TestSubgraphPreservation:
    """Verify that unrelated branches of the DAG survive invalidation."""

    def test_siblings_preserved(self):
        """scene_ir_graph survives when scene_ir_hook is invalidated."""
        graph, arts = _build_pipeline_graph()

        graph.invalidate(arts["scene_ir_hook"].id)
        stale_ids = {a.id for a in graph.get_stale()}

        assert arts["scene_ir_graph"].id not in stale_ids
        assert arts["scene_vid_graph"].id not in stale_ids

    def test_tts_independence(self):
        """tts_1 survives when tts_0 is invalidated (same parent, different branch)."""
        graph, arts = _build_pipeline_graph()

        graph.invalidate(arts["tts_0"].id)
        stale_ids = {a.id for a in graph.get_stale()}

        assert arts["tts_0"].id in stale_ids
        # tts_1 has same parent (script) but is not downstream of tts_0
        assert arts["tts_1"].id not in stale_ids

    def test_upstream_never_contaminated(self):
        """Invalidating a leaf never makes upstream stale."""
        graph, arts = _build_pipeline_graph()

        graph.invalidate(arts["video"].id)
        stale_ids = {a.id for a in graph.get_stale()}

        # Only video itself is stale (it's a leaf)
        assert stale_ids == {arts["video"].id}


# ═══════════════════════════════════════════════════════════════════════
# P1.4c — Invalidation Closure Minimality
# ═══════════════════════════════════════════════════════════════════════


class TestClosureMinimality:
    """The invalidation set must be exactly the downward closure — no more, no less."""

    def test_exact_downward_closure(self):
        """Invalidating shots → exactly {shots, timeline, render_plan, all scene_ir, all scene_video, video}."""
        graph, arts = _build_pipeline_graph()

        graph.invalidate(arts["shots"].id)
        stale_ids = {a.id for a in graph.get_stale()}

        expected = {
            arts["shots"].id,
            arts["timeline"].id,
            arts["render_plan"].id,
            arts["scene_ir_hook"].id,
            arts["scene_ir_graph"].id,
            arts["scene_vid_hook"].id,
            arts["scene_vid_graph"].id,
            arts["video"].id,
        }
        assert stale_ids == expected

    def test_double_invalidate_idempotent(self):
        """Invalidating the same artifact twice produces same stale set."""
        graph, arts = _build_pipeline_graph()

        graph.invalidate(arts["script"].id)
        stale1 = {a.id for a in graph.get_stale()}

        graph.invalidate(arts["script"].id)
        stale2 = {a.id for a in graph.get_stale()}

        assert stale1 == stale2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
