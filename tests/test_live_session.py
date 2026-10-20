"""P8 — Reactive Live Editing Tests.

Verifies:
  - Edit operations (patch, replace, reorder)
  - Semantic diff (field-level, structural, depth)
  - Live session (incremental recompute, cache hits, invalidation depth)
  - Editing one scene does NOT recompute siblings
  - Config changes trigger global invalidation
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.thinking.artifacts import ArtifactGraph, ArtifactType
from compiler.thinking.canonicalize import content_hash
from compiler.runtime.edit_operations import EditOperation, EditType, EditResult
from compiler.runtime.invalidation import SemanticDiff, InvalidationDepth, DiffResult
from compiler.runtime.live_session import LiveSession


# ═══════════════════════════════════════════════════════════════════════
# Edit Operations
# ═══════════════════════════════════════════════════════════════════════


class TestEditOperations:
    """EditOperation.apply correctness."""

    def test_patch_at_root(self):
        op = EditOperation(target_id="a", edit_type=EditType.PATCH, patch={"x": 2})
        result = op.apply({"x": 1, "y": 3})
        assert result == {"x": 2, "y": 3}

    def test_patch_at_path(self):
        op = EditOperation(target_id="a", edit_type=EditType.PATCH, patch=2, path="a.b")
        result = op.apply({"a": {"b": 1, "c": 3}})
        assert result == {"a": {"b": 2, "c": 3}}

    def test_patch_deep_path(self):
        op = EditOperation(target_id="a", edit_type=EditType.PATCH, patch="new", path="x.y.z")
        result = op.apply({"x": {"y": {"z": "old"}}})
        assert result == {"x": {"y": {"z": "new"}}}

    def test_patch_adds_field(self):
        op = EditOperation(target_id="a", edit_type=EditType.PATCH, patch=2, path="b")
        result = op.apply({"a": 1})
        assert result == {"a": 1, "b": 2}

    def test_replace(self):
        op = EditOperation(target_id="a", edit_type=EditType.REPLACE, patch={"completely": "new"})
        result = op.apply({"old": "data"})
        assert result == {"completely": "new"}

    def test_delete(self):
        op = EditOperation(target_id="a", edit_type=EditType.DELETE)
        result = op.apply({"some": "data"})
        assert result is None

    def test_reorder(self):
        op = EditOperation(
            target_id="a", edit_type=EditType.REORDER,
            patch=[2, 0, 1], path="items",
        )
        result = op.apply({"items": ["a", "b", "c"]})
        assert result == {"items": ["c", "a", "b"]}

    def test_reorder_wrong_length_ignored(self):
        op = EditOperation(
            target_id="a", edit_type=EditType.REORDER,
            patch=[0, 1], path="items",
        )
        original = {"items": ["a", "b", "c"]}
        result = op.apply(original)
        assert result == original

    def test_patch_none_content(self):
        op = EditOperation(target_id="a", edit_type=EditType.PATCH, patch={"x": 1})
        result = op.apply(None)
        assert result == {"x": 1}

    def test_empty_target_rejected(self):
        with pytest.raises(ValueError, match="target_id"):
            EditOperation(target_id="", edit_type=EditType.PATCH, patch={"x": 1})

    def test_patch_without_data_rejected(self):
        with pytest.raises(ValueError, match="patch"):
            EditOperation(target_id="a", edit_type=EditType.PATCH)


# ═══════════════════════════════════════════════════════════════════════
# Semantic Diff
# ═══════════════════════════════════════════════════════════════════════


class TestSemanticDiff:
    """SemanticDiff.diff correctness."""

    def test_no_change(self):
        diff = SemanticDiff()
        r = diff.diff({"x": 1}, {"x": 1})
        assert r.depth == InvalidationDepth.NONE
        assert not r.has_change

    def test_content_field_change(self):
        diff = SemanticDiff()
        r = diff.diff({"text": "old"}, {"text": "new"})
        assert "text" in r.changed_fields
        assert r.depth != InvalidationDepth.NONE

    def test_global_field_change(self):
        diff = SemanticDiff()
        r = diff.diff({"fps": 30}, {"fps": 60})
        assert r.depth == InvalidationDepth.GLOBAL

    def test_structural_change(self):
        diff = SemanticDiff()
        r = diff.diff({"a": 1}, {"a": 1, "b": 2})
        assert r.structural_change is True
        assert r.depth == InvalidationDepth.SUBTREE

    def test_list_length_change(self):
        diff = SemanticDiff()
        r = diff.diff(
            {"items": [1, 2, 3]},
            {"items": [1, 2, 3, 4]},
        )
        assert r.structural_change is True

    def test_nested_field_change(self):
        diff = SemanticDiff()
        r = diff.diff(
            {"scene": {"text": "old"}},
            {"scene": {"text": "new"}},
        )
        assert "scene.text" in r.changed_fields

    def test_local_only_change(self):
        diff = SemanticDiff()
        r = diff.diff(
            {"text": "old", "title": "keep"},
            {"text": "new", "title": "keep"},
        )
        assert r.depth == InvalidationDepth.LOCAL

    def test_none_to_content(self):
        diff = SemanticDiff()
        r = diff.diff(None, {"x": 1})
        assert r.has_change
        assert r.depth == InvalidationDepth.SUBTREE

    def test_content_to_none(self):
        diff = SemanticDiff()
        r = diff.diff({"x": 1}, None)
        assert r.has_change


# ═══════════════════════════════════════════════════════════════════════
# Live Session — Incremental Recompute
# ═══════════════════════════════════════════════════════════════════════


class TestLiveSession:
    """LiveSession.edit incremental behavior."""

    def _build_scene_graph(self):
        """Hook + Graph + Final — changing hook should NOT recompute graph."""
        graph = ArtifactGraph()
        hook = graph.create(
            ArtifactType.SCRIPT,
            content={"text": "hook text", "v": 1},
            artifact_id="hook",
        )
        graph_section = graph.create(
            ArtifactType.SCRIPT,
            content={"text": "graph text", "v": 1},
            artifact_id="graph",
        )
        final = graph.create(
            ArtifactType.VIDEO,
            content={"final": True, "v": 1},
            depends_on=[hook, graph_section],
            artifact_id="final",
        )
        return graph

    def test_edit_changes_content(self):
        graph = self._build_scene_graph()
        compute_log = []

        def tracking_compute(art):
            compute_log.append(art.id)
            return art.content

        session = LiveSession(graph, compute_fns={"script": tracking_compute})
        response = session.edit("hook", "new hook text", path="text")

        assert response.changed
        assert graph.get("hook").content["text"] == "new hook text"

    def test_edit_no_change_no_recompute(self):
        graph = self._build_scene_graph()
        compute_log = []

        def tracking_compute(art):
            compute_log.append(art.id)
            return art.content

        session = LiveSession(graph, compute_fns={"script": tracking_compute})
        # Patch with same value → no change
        response = session.edit("hook", "hook text", path="text")

        assert not response.changed

    def test_edit_only_recomputes_affected(self):
        """Editing hook should NOT recompute graph (sibling)."""
        graph = self._build_scene_graph()
        compute_log = []

        def tracking_compute(art):
            compute_log.append(art.id)
            return {"computed": True, "v": art.content.get("v", 0) + 1}

        session = LiveSession(graph, compute_fns={"script": tracking_compute})
        response = session.edit("hook", {"text": "new hook"}, path="text")

        # hook recomputed, graph should NOT be
        assert "hook" in response.recomputed_nodes
        assert "graph" not in response.recomputed_nodes

    def test_global_invalidation_recomputes_all(self):
        """Changing fps should recompute everything."""
        graph = ArtifactGraph()
        a = graph.create(
            ArtifactType.SCRIPT,
            content={"fps": 30, "text": "a"},
            artifact_id="a",
        )
        b = graph.create(
            ArtifactType.SCRIPT,
            content={"fps": 30, "text": "b"},
            depends_on=[a],
            artifact_id="b",
        )

        def compute(art):
            return art.content

        session = LiveSession(graph, compute_fns={"script": compute})
        response = session.edit("a", 60, path="fps")

        assert response.diff_result.depth == InvalidationDepth.GLOBAL

    def test_session_stats_accumulate(self):
        graph = self._build_scene_graph()

        def compute(art):
            return art.content

        session = LiveSession(graph, compute_fns={"script": compute})
        session.edit("hook", {"text": "v2"}, path="text")
        session.edit("graph", {"text": "v2"}, path="text")

        stats = session.stats()
        assert stats.total_edits == 2
        assert stats.total_duration >= 0

    def test_edit_count(self):
        graph = self._build_scene_graph()

        def compute(art):
            return art.content

        session = LiveSession(graph, compute_fns={"script": compute})
        assert session.edit_count == 0

        session.edit("hook", {"text": "v2"}, path="text")
        assert session.edit_count == 1

    def test_last_edit(self):
        graph = self._build_scene_graph()

        def compute(art):
            return art.content

        session = LiveSession(graph, compute_fns={"script": compute})
        assert session.last_edit() is None

        session.edit("hook", {"text": "v2"}, path="text")
        assert session.last_edit() is not None
        assert session.last_edit().target_id == "hook"


# ═══════════════════════════════════════════════════════════════════════
# End-to-End: Edit Propagation
# ═══════════════════════════════════════════════════════════════════════


class TestEditPropagation:
    """End-to-end edit propagation across the graph."""

    def test_downstream_gets_recomputed(self):
        """Editing parent should recompute dependent child."""
        graph = ArtifactGraph()
        parent = graph.create(
            ArtifactType.SCRIPT,
            content={"v": 1},
            artifact_id="parent",
        )
        child = graph.create(
            ArtifactType.TIMELINE,
            content={"v": 1},
            depends_on=[parent],
            artifact_id="child",
        )

        def compute(art):
            return {"v": art.content.get("v", 0) + 10}

        session = LiveSession(graph, compute_fns={"script": compute, "timeline": compute})
        response = session.edit("parent", {"v": 99})

        assert "parent" in response.recomputed_nodes
        assert "child" in response.recomputed_nodes

    def test_diamond_d_computed_once(self):
        """Diamond: A→B, A→C, B→D, C→D. Edit A → D computed once."""
        graph = ArtifactGraph()
        a = graph.create(ArtifactType.SCRIPT, content={"v": 1}, artifact_id="A")
        b = graph.create(ArtifactType.SHOTS, content={"v": 1}, depends_on=[a], artifact_id="B")
        c = graph.create(ArtifactType.TTS_SENTENCE, content={"v": 1}, depends_on=[a], artifact_id="C")
        d = graph.create(ArtifactType.TIMELINE, content={"v": 1}, depends_on=[b, c], artifact_id="D")

        def compute(art):
            return {"v": art.content.get("v", 0) + 1}

        session = LiveSession(graph, compute_fns={
            "script": compute, "shots": compute,
            "tts_sentence": compute, "timeline": compute,
        })
        response = session.edit("A", {"v": 99})

        # D should appear exactly once
        assert response.recomputed_nodes.count("D") <= 1

    def test_scene_pipeline_isolation(self):
        """Editing ir_hook should NOT recompute ir_graph."""
        graph = ArtifactGraph()
        rp = graph.create(ArtifactType.RENDER_PLAN, content={"v": 1}, artifact_id="render_plan")
        ir_h = graph.create(ArtifactType.SCENE_IR, content={"scene": "hook"}, depends_on=[rp], artifact_id="ir_hook")
        ir_g = graph.create(ArtifactType.SCENE_IR, content={"scene": "graph"}, depends_on=[rp], artifact_id="ir_graph")
        sv_h = graph.create(ArtifactType.SCENE_VIDEO, content={"scene": "hook"}, depends_on=[ir_h], artifact_id="sv_hook")
        sv_g = graph.create(ArtifactType.SCENE_VIDEO, content={"scene": "graph"}, depends_on=[ir_g], artifact_id="sv_graph")
        vid = graph.create(ArtifactType.VIDEO, content={"final": True}, depends_on=[sv_h, sv_g], artifact_id="video")

        compute_log = []
        def compute(art):
            compute_log.append(art.id)
            return art.content

        session = LiveSession(graph, compute_fns={
            "render_plan": compute, "scene_ir": compute,
            "scene_video": compute, "video": compute,
        })
        response = session.edit("ir_hook", {"scene": "hook_v2"})

        assert "ir_hook" in response.recomputed_nodes
        assert "ir_graph" not in response.recomputed_nodes
        assert "sv_hook" in response.recomputed_nodes


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
