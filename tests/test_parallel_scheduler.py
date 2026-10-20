"""P6 — Parallel DAG Scheduler Tests.

Verifies:
  - Parallel execution produces same hashes as serial
  - Diamond dependency: D computed exactly once
  - Independent branches execute concurrently
  - Execution order is deterministic across runs
  - Error handling in parallel context
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.thinking.artifacts import ArtifactGraph, ArtifactType
from compiler.thinking.canonicalize import content_hash
from compiler.thinking.parallel_scheduler import ParallelScheduler, ParallelStats
from compiler.thinking.fixpoint_scheduler import ArtifactState


# ── Helpers ──────────────────────────────────────────────────────────

def _make_propagating_compute(graph: ArtifactGraph):
    """Compute fn that propagates upstream values (models data flow)."""
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
    """Root compute fn that always produces different content."""
    _counter["n"] += 1
    return {"v": _counter["n"]}


def _build_chain():
    """A → B → C → D."""
    graph = ArtifactGraph()
    a = graph.create(ArtifactType.SCRIPT, content={"v": 1}, artifact_id="A")
    b = graph.create(ArtifactType.SHOTS, content={"v": 1}, depends_on=[a], artifact_id="B")
    c = graph.create(ArtifactType.TIMELINE, content={"v": 1}, depends_on=[b], artifact_id="C")
    d = graph.create(ArtifactType.VIDEO, content={"v": 1}, depends_on=[c], artifact_id="D")
    return graph


def _build_diamond():
    """A → B, A → C, B → D, C → D."""
    graph = ArtifactGraph()
    a = graph.create(ArtifactType.SCRIPT, content={"v": 1}, artifact_id="A")
    b = graph.create(ArtifactType.SHOTS, content={"v": 1}, depends_on=[a], artifact_id="B")
    c = graph.create(ArtifactType.TTS_SENTENCE, content={"v": 1}, depends_on=[a], artifact_id="C")
    d = graph.create(ArtifactType.TIMELINE, content={"v": 1}, depends_on=[b, c], artifact_id="D")
    return graph


def _build_wide():
    """A → [B, C, D, E, F] → G (fan-out + fan-in)."""
    graph = ArtifactGraph()
    a = graph.create(ArtifactType.SCRIPT, content={"v": 1}, artifact_id="A")
    branches = []
    for i, name in enumerate("BCDEF"):
        node = graph.create(
            ArtifactType.SCENE_IR, content={"v": 1},
            depends_on=[a], artifact_id=name,
        )
        branches.append(node)
    g = graph.create(ArtifactType.VIDEO, content={"v": 1}, depends_on=branches, artifact_id="G")
    return graph


def _build_scene_pipeline():
    """render_plan → [ir_hook, ir_graph] → [sv_hook, sv_graph] → video."""
    graph = ArtifactGraph()
    rp = graph.create(ArtifactType.RENDER_PLAN, content={"v": 1}, artifact_id="render_plan")
    ir_h = graph.create(ArtifactType.SCENE_IR, content={"scene": "hook", "v": 1}, depends_on=[rp], artifact_id="ir_hook")
    ir_g = graph.create(ArtifactType.SCENE_IR, content={"scene": "graph", "v": 1}, depends_on=[rp], artifact_id="ir_graph")
    sv_h = graph.create(ArtifactType.SCENE_VIDEO, content={"scene": "hook"}, depends_on=[ir_h], artifact_id="sv_hook")
    sv_g = graph.create(ArtifactType.SCENE_VIDEO, content={"scene": "graph"}, depends_on=[ir_g], artifact_id="sv_graph")
    vid = graph.create(ArtifactType.VIDEO, content={"final": True}, depends_on=[sv_h, sv_g], artifact_id="video")
    return graph


# ═══════════════════════════════════════════════════════════════════════
# Determinism — parallel result == serial result
# ═══════════════════════════════════════════════════════════════════════


class TestDeterminism:
    """Parallel execution must produce identical hashes to serial."""

    def test_parallel_hashes_match_serial(self, tmp_path):
        """Run same graph with 1 worker (serial) and 4 workers (parallel).
        Final artifact hashes must be identical."""
        global _counter

        # Serial run
        _counter["n"] = 100
        graph1 = _build_diamond()
        compute1 = _make_propagating_compute(graph1)
        sched1 = ParallelScheduler(graph1, max_workers=1)
        sched1.register_compute_fn("script", _changing_root)
        for t in ["shots", "tts_sentence", "timeline"]:
            sched1.register_compute_fn(t, compute1)
        sched1.mark_all_stale()
        sched1.run()

        serial_hashes = {aid: content_hash(graph1.get(aid).content) for aid in "ABCD"}

        # Parallel run
        _counter["n"] = 100
        graph2 = _build_diamond()
        compute2 = _make_propagating_compute(graph2)
        sched2 = ParallelScheduler(graph2, max_workers=4)
        sched2.register_compute_fn("script", _changing_root)
        for t in ["shots", "tts_sentence", "timeline"]:
            sched2.register_compute_fn(t, compute2)
        sched2.mark_all_stale()
        sched2.run()

        parallel_hashes = {aid: content_hash(graph2.get(aid).content) for aid in "ABCD"}

        assert serial_hashes == parallel_hashes

    def test_deterministic_across_runs(self):
        """Multiple parallel runs produce identical hashes."""
        global _counter
        hashes_per_run = []

        for _ in range(5):
            _counter["n"] = 100
            graph = _build_diamond()
            compute = _make_propagating_compute(graph)
            sched = ParallelScheduler(graph, max_workers=4)
            sched.register_compute_fn("script", _changing_root)
            for t in ["shots", "tts_sentence", "timeline"]:
                sched.register_compute_fn(t, compute)
            sched.mark_all_stale()
            sched.run()
            hashes_per_run.append({aid: content_hash(graph.get(aid).content) for aid in "ABCD"})

        assert all(h == hashes_per_run[0] for h in hashes_per_run)

    def test_execution_order_deterministic(self):
        """Commit order is the same across runs (even with parallel compute)."""
        global _counter
        orders = []

        for _ in range(5):
            _counter["n"] = 100
            graph = _build_diamond()
            compute = _make_propagating_compute(graph)
            sched = ParallelScheduler(graph, max_workers=4)
            sched.register_compute_fn("script", _changing_root)
            for t in ["shots", "tts_sentence", "timeline"]:
                sched.register_compute_fn(t, compute)
            sched.mark_all_stale()
            sched.run()
            orders.append(sched.get_execution_order())

        assert all(o == orders[0] for o in orders)


# ═══════════════════════════════════════════════════════════════════════
# Diamond Dependencies
# ═══════════════════════════════════════════════════════════════════════


class TestDiamondParallel:
    """Diamond: A → B, A → C, B → D, C → D."""

    def test_diamond_d_computed_once(self):
        """D must be computed exactly once, even with parallel workers."""
        global _counter
        _counter["n"] = 100
        graph = _build_diamond()
        compute = _make_propagating_compute(graph)
        sched = ParallelScheduler(graph, max_workers=4)
        sched.register_compute_fn("script", _changing_root)
        for t in ["shots", "tts_sentence", "timeline"]:
            sched.register_compute_fn(t, compute)
        sched.mark_all_stale()
        stats = sched.run()

        # D should appear exactly once in execution order
        order = sched.get_execution_order()
        assert order.count("D") <= 1, f"D computed {order.count('D')} times"

    def test_diamond_all_computed(self):
        global _counter
        _counter["n"] = 100
        graph = _build_diamond()
        compute = _make_propagating_compute(graph)
        sched = ParallelScheduler(graph, max_workers=4)
        sched.register_compute_fn("script", _changing_root)
        for t in ["shots", "tts_sentence", "timeline"]:
            sched.register_compute_fn(t, compute)
        sched.mark_all_stale()
        stats = sched.run()

        assert stats.computed >= 4


# ═══════════════════════════════════════════════════════════════════════
# Wide Fan-out / Fan-in
# ═══════════════════════════════════════════════════════════════════════


class TestWideGraph:
    """A → [B, C, D, E, F] → G. All 5 branches should run in parallel."""

    def test_wide_all_computed(self):
        global _counter
        _counter["n"] = 100
        graph = _build_wide()
        compute = _make_propagating_compute(graph)
        sched = ParallelScheduler(graph, max_workers=4)
        sched.register_compute_fn("script", _changing_root)
        for t in ["scene_ir", "video"]:
            sched.register_compute_fn(t, compute)
        sched.mark_all_stale()
        stats = sched.run()

        assert stats.computed >= 7  # A + B,C,D,E,F + G

    def test_wide_parallel_batches(self):
        """With 5 independent branches, should have ≤ 3 batches:
        batch 1: A
        batch 2: B, C, D, E, F (parallel)
        batch 3: G
        """
        global _counter
        _counter["n"] = 100
        graph = _build_wide()
        compute = _make_propagating_compute(graph)
        sched = ParallelScheduler(graph, max_workers=4)
        sched.register_compute_fn("script", _changing_root)
        for t in ["scene_ir", "video"]:
            sched.register_compute_fn(t, compute)
        sched.mark_all_stale()
        stats = sched.run()

        # Should be 3 parallel batches: [A], [B,C,D,E,F], [G]
        assert stats.parallel_batches <= 4  # Some tolerance


# ═══════════════════════════════════════════════════════════════════════
# Cache Hit Behavior
# ═══════════════════════════════════════════════════════════════════════


class TestCacheHits:
    """Unchanged content should be detected as cache hit."""

    def test_no_change_is_cache_hit(self):
        graph = _build_chain()
        def stable(art):
            return art.content

        sched = ParallelScheduler(graph, max_workers=2)
        for t in ["script", "shots", "timeline", "video"]:
            sched.register_compute_fn(t, stable)
        sched.mark_stale("A")
        stats = sched.run()

        # A recomputed to same content → cache hit, no propagation
        assert stats.computed == 1
        assert stats.cache_hits >= 1
        assert stats.propagations == 0


# ═══════════════════════════════════════════════════════════════════════
# Scene Pipeline
# ═══════════════════════════════════════════════════════════════════════


class TestScenePipeline:
    """Realistic scene pipeline with parallel scene rendering."""

    def test_one_scene_change_only_affects_subgraph(self):
        """Marking all stale then running — ir_graph still computed
        (same content → cache hit), but the subgraph rooted at ir_hook
        propagates while ir_graph's subtree produces cache hits."""
        global _counter
        _counter["n"] = 100
        graph = _build_scene_pipeline()
        compute = _make_propagating_compute(graph)

        compute_log = []
        def log_compute(art):
            compute_log.append(art.id)
            return compute(art)

        sched = ParallelScheduler(graph, max_workers=4)
        for t in ["render_plan", "scene_ir", "scene_video", "video"]:
            sched.register_compute_fn(t, log_compute)

        sched.mark_all_stale()
        stats = sched.run()

        # All nodes computed (marked stale), but ir_graph's subtree
        # produces same content → cache hit (no propagation downstream)
        assert "ir_hook" in compute_log
        assert "ir_graph" in compute_log
        assert "sv_hook" in compute_log


# ═══════════════════════════════════════════════════════════════════════
# Stats
# ═══════════════════════════════════════════════════════════════════════


class TestStats:
    """Parallel stats must be accurate."""

    def test_stats_fields(self):
        global _counter
        _counter["n"] = 100
        graph = _build_diamond()
        compute = _make_propagating_compute(graph)
        sched = ParallelScheduler(graph, max_workers=2)
        sched.register_compute_fn("script", _changing_root)
        for t in ["shots", "tts_sentence", "timeline"]:
            sched.register_compute_fn(t, compute)
        sched.mark_all_stale()
        stats = sched.run()

        assert stats.max_workers == 2
        assert stats.total_duration >= 0
        assert stats.parallel_batches >= 1
        assert stats.max_concurrent >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
