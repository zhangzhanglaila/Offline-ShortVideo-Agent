"""P2 — Fixpoint Scheduler Tests.

Verifies that the monotone fixpoint evaluation:
  - Runs until stable (no single-pass under-computation)
  - Propagates content changes to downstream
  - Stops when content doesn't change (cache hit terminates cascade)
  - Handles diamond dependencies correctly
  - Is deterministic regardless of queue ordering
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.thinking.artifacts import ArtifactGraph, ArtifactType, _content_hash
from compiler.thinking.fixpoint_scheduler import FixpointScheduler, ArtifactState


# ── Helpers ──────────────────────────────────────────────────────────

def _make_propagating_compute(graph: ArtifactGraph):
    """Create a compute function that propagates upstream content.

    Each node's new content = {"v": upstream_v + own_v}.
    This models real data flow: changing upstream produces different downstream.
    """
    def compute(art):
        upstream_v = 0
        for dep_id in art.depends_on:
            dep = graph.get(dep_id)
            if dep and dep.content:
                upstream_v += dep.content.get("v", 0)
        own_v = art.content.get("v", 0) if art.content else 0
        return {"v": upstream_v + own_v}
    return compute


_counter = {"n": 100}
def _changing_root(art):
    """Compute function for root node that always produces different content."""
    _counter["n"] += 1
    return {"v": _counter["n"]}


def _build_chain_graph():
    """Build: A → B → C → D (linear chain).

    Content models data flow: each node's content includes its upstream value.
    """
    graph = ArtifactGraph()
    a = graph.create(ArtifactType.SCRIPT, content={"v": 1}, artifact_id="A")
    b = graph.create(ArtifactType.SHOTS, content={"v": 1}, depends_on=[a], artifact_id="B")
    c = graph.create(ArtifactType.TIMELINE, content={"v": 1}, depends_on=[b], artifact_id="C")
    d = graph.create(ArtifactType.VIDEO, content={"v": 1}, depends_on=[c], artifact_id="D")
    return graph, {"A": a, "B": b, "C": c, "D": d}


def _build_diamond_graph():
    """Build: A → B, A → C, B → D, C → D (diamond)."""
    graph = ArtifactGraph()
    a = graph.create(ArtifactType.SCRIPT, content={"v": 1}, artifact_id="A")
    b = graph.create(ArtifactType.SHOTS, content={"v": 1}, depends_on=[a], artifact_id="B")
    c = graph.create(ArtifactType.TTS_SENTENCE, content={"v": 1}, depends_on=[a], artifact_id="C")
    d = graph.create(ArtifactType.TIMELINE, content={"v": 1}, depends_on=[b, c], artifact_id="D")
    return graph, {"A": a, "B": b, "C": c, "D": d}


def _build_scene_pipeline():
    """Build: script → render_plan → [scene_ir_hook, scene_ir_graph] → [scene_vid_hook, scene_vid_graph] → video."""
    graph = ArtifactGraph()
    rp = graph.create(ArtifactType.RENDER_PLAN, content={"v": 1}, artifact_id="render_plan")
    ir_h = graph.create(ArtifactType.SCENE_IR, content={"scene": "hook", "v": 1}, depends_on=[rp], artifact_id="ir_hook")
    ir_g = graph.create(ArtifactType.SCENE_IR, content={"scene": "graph", "v": 1}, depends_on=[rp], artifact_id="ir_graph")
    sv_h = graph.create(ArtifactType.SCENE_VIDEO, content={"scene": "hook"}, depends_on=[ir_h], artifact_id="sv_hook")
    sv_g = graph.create(ArtifactType.SCENE_VIDEO, content={"scene": "graph"}, depends_on=[ir_g], artifact_id="sv_graph")
    vid = graph.create(ArtifactType.VIDEO, content={"final": True}, depends_on=[sv_h, sv_g], artifact_id="video")
    return graph, {"rp": rp, "ir_h": ir_h, "ir_g": ir_g, "sv_h": sv_h, "sv_g": sv_g, "vid": vid}


# ═══════════════════════════════════════════════════════════════════════
# Basic Fixpoint Behavior
# ═══════════════════════════════════════════════════════════════════════


class TestBasicFixpoint:
    """Fixpoint loop must run until no more state changes."""

    def test_single_stale_node_computes(self):
        graph, arts = _build_chain_graph()
        sched = FixpointScheduler(graph)
        sched.register_compute_fn("script", _make_propagating_compute(graph))

        sched.mark_stale("A")
        stats = sched.run()

        assert stats.computed >= 1
        assert sched.get_state("A") == ArtifactState.READY

    def test_empty_queue_immediate_termination(self):
        graph, arts = _build_chain_graph()
        sched = FixpointScheduler(graph)

        stats = sched.run()
        assert stats.iterations == 0
        assert stats.computed == 0

    def test_mark_stale_many(self):
        graph, arts = _build_chain_graph()
        sched = FixpointScheduler(graph)
        compute = _make_propagating_compute(graph)
        sched.register_compute_fn("script", compute)
        sched.register_compute_fn("shots", compute)

        sched.mark_stale_many(["A", "B"])
        stats = sched.run()
        assert stats.computed >= 2


# ═══════════════════════════════════════════════════════════════════════
# Cascade Propagation
# ═══════════════════════════════════════════════════════════════════════


class TestCascadePropagation:
    """Content change must propagate downstream; no change must stop cascade."""

    def test_change_propagates_downstream(self):
        """When A's content changes, B, C, D should also be recomputed."""
        global _counter
        _counter["n"] = 100
        graph, arts = _build_chain_graph()

        compute = _make_propagating_compute(graph)
        sched = FixpointScheduler(graph)
        sched.register_compute_fn("script", _changing_root)
        sched.register_compute_fn("shots", compute)
        sched.register_compute_fn("timeline", compute)
        sched.register_compute_fn("video", compute)

        sched.mark_stale("A")
        stats = sched.run()

        # A changed → B stale → B computed → C stale → C computed → D stale → D computed
        assert stats.computed >= 4  # A, B, C, D
        assert stats.propagations >= 3  # A→B, B→C, C→D

    def test_no_change_stops_cascade(self):
        """When A recomputes to same content, downstream should NOT be invalidated."""
        graph, arts = _build_chain_graph()

        def stable(art):
            return art.content  # Returns same content

        sched = FixpointScheduler(graph)
        sched.register_compute_fn("script", stable)

        sched.mark_stale("A")
        stats = sched.run()

        # A recomputed but content unchanged → no propagation
        assert stats.computed == 1  # Only A
        assert stats.propagations == 0
        assert stats.cache_hits >= 1


# ═══════════════════════════════════════════════════════════════════════
# Diamond Dependencies
# ═══════════════════════════════════════════════════════════════════════


class TestDiamondDependencies:
    """Diamond: A → B, A → C, B → D, C → D. D must be computed exactly once."""

    def test_diamond_d_computed_once(self):
        """D has two parents B and C. It should only be computed once after fixpoint."""
        graph, arts = _build_diamond_graph()

        compute_log = []
        compute = _make_propagating_compute(graph)
        def log_compute(art):
            compute_log.append(art.id)
            return compute(art)

        sched = FixpointScheduler(graph)
        for t in ["script", "shots", "tts_sentence", "timeline"]:
            sched.register_compute_fn(t, log_compute)

        sched.mark_stale("A")
        stats = sched.run()

        # D should appear at most once in the compute log
        d_count = compute_log.count("D")
        assert d_count <= 1, f"D computed {d_count} times"

    def test_diamond_both_branches_propagate(self):
        """When A changes, both B and C must propagate to D."""
        global _counter
        _counter["n"] = 100
        graph, arts = _build_diamond_graph()

        compute = _make_propagating_compute(graph)
        sched = FixpointScheduler(graph)
        sched.register_compute_fn("script", _changing_root)
        for t in ["shots", "tts_sentence", "timeline"]:
            sched.register_compute_fn(t, compute)

        sched.mark_stale("A")
        stats = sched.run()

        # All 4 should be computed
        assert stats.computed >= 4


# ═══════════════════════════════════════════════════════════════════════
# Scene Pipeline Specific
# ═══════════════════════════════════════════════════════════════════════


class TestScenePipeline:
    """Test with realistic scene IR → scene video → final video pipeline."""

    def test_one_scene_change_other_survives(self):
        """Changing ir_hook should NOT cause ir_graph to be recomputed."""
        graph, arts = _build_scene_pipeline()

        compute_log = []
        compute = _make_propagating_compute(graph)
        def log_compute(art):
            compute_log.append(art.id)
            return compute(art)

        sched = FixpointScheduler(graph)
        for t in ["render_plan", "scene_ir", "scene_video", "video"]:
            sched.register_compute_fn(t, log_compute)

        sched.mark_stale("ir_hook")
        stats = sched.run()

        assert "ir_hook" in compute_log
        assert "ir_graph" not in compute_log  # Not affected
        assert "sv_hook" in compute_log
        assert "video" in compute_log

    def test_render_plan_change_propagates_to_all_scenes(self):
        """Changing render_plan should invalidate all scene IRs."""
        global _counter
        _counter["n"] = 100
        graph, arts = _build_scene_pipeline()

        compute_log = []
        compute = _make_propagating_compute(graph)
        def log_compute(art):
            compute_log.append(art.id)
            return compute(art)
        def changing_log(art):
            compute_log.append(art.id)
            return _changing_root(art)

        sched = FixpointScheduler(graph)
        sched.register_compute_fn("render_plan", changing_log)
        for t in ["scene_ir", "scene_video", "video"]:
            sched.register_compute_fn(t, log_compute)

        sched.mark_stale("render_plan")
        stats = sched.run()

        assert "render_plan" in compute_log
        assert "ir_hook" in compute_log
        assert "ir_graph" in compute_log
        assert "video" in compute_log


# ═══════════════════════════════════════════════════════════════════════
# Lattice State Transitions
# ═══════════════════════════════════════════════════════════════════════


class TestLatticeStates:
    """Verify state transitions follow the lattice: UNKNOWN → STALE → READY."""

    def test_initial_state_unknown(self):
        graph, arts = _build_chain_graph()
        sched = FixpointScheduler(graph)
        assert sched.get_state("A") == ArtifactState.UNKNOWN

    def test_mark_stale_transitions(self):
        graph, arts = _build_chain_graph()
        sched = FixpointScheduler(graph)
        sched.mark_stale("A")
        assert sched.get_state("A") == ArtifactState.STALE

    def test_after_run_ready(self):
        graph, arts = _build_chain_graph()
        sched = FixpointScheduler(graph)
        sched.mark_stale("A")
        sched.run()
        assert sched.get_state("A") == ArtifactState.READY

    def test_state_monotonicity(self):
        """State can only increase: UNKNOWN → STALE → READY, never backwards."""
        graph, arts = _build_chain_graph()
        sched = FixpointScheduler(graph)
        sched.mark_stale("A")
        assert sched.get_state("A") == ArtifactState.STALE
        sched.run()
        assert sched.get_state("A") == ArtifactState.READY
        # READY can't go back to STALE without explicit mark_stale
        assert sched.get_state("A") >= ArtifactState.STALE


# ═══════════════════════════════════════════════════════════════════════
# Error Handling
# ═══════════════════════════════════════════════════════════════════════


class TestErrorHandling:
    """Errors during computation should not crash the scheduler."""

    def test_error_counted_and_node_stays_stale(self):
        graph, arts = _build_chain_graph()

        def failing(art):
            raise ValueError("compute failed")

        sched = FixpointScheduler(graph)
        sched.register_compute_fn("script", failing)
        sched.mark_stale("A")
        stats = sched.run()

        assert stats.errors >= 1
        # Node stays STALE (not READY) after error
        assert sched.get_state("A") == ArtifactState.STALE


# ═══════════════════════════════════════════════════════════════════════
# Determinism
# ═══════════════════════════════════════════════════════════════════════


class TestDeterminism:
    """Same inputs must produce same final artifact hashes regardless of queue order."""

    def test_deterministic_output_hashes(self):
        """Run fixpoint 10 times — all final hashes must be identical."""
        final_hashes = []

        for _ in range(10):
            graph, arts = _build_diamond_graph()

            compute = _make_propagating_compute(graph)
            sched = FixpointScheduler(graph)
            for t in ["script", "shots", "tts_sentence", "timeline"]:
                sched.register_compute_fn(t, compute)

            sched.mark_stale("A")
            sched.run()

            # Capture final content hash of D
            d_art = graph.get("D")
            final_hashes.append(_content_hash(d_art.content))

        # All should be identical
        assert len(set(final_hashes)) == 1


# ═══════════════════════════════════════════════════════════════════════
# Summary / Observability
# ═══════════════════════════════════════════════════════════════════════


class TestSummary:
    """Scheduler summary must be introspectable."""

    def test_summary_after_run(self):
        graph, arts = _build_chain_graph()
        sched = FixpointScheduler(graph)
        sched.mark_stale("A")
        sched.run()

        s = sched.summary()
        assert s["total_nodes"] > 0
        assert s["queue_size"] == 0  # Queue drained
        assert "A" in s["nodes"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
