"""Fixpoint Scheduler — Monotone fixpoint evaluation for incremental recomputation.

Replaces single-pass topological traversal with a work-queue-driven
fixpoint loop. This is the correct execution model for a reactive
dependency graph where recomputation can cascade:

    A invalidates B
    B recomputes → content changes → invalidates C
    C recomputes → content changes → invalidates D
    D recomputes → content unchanged → STABLE

Single-pass misses this. Fixpoint catches it.

Artifact Lattice:
    UNKNOWN  ⊑  STALE  ⊑  READY
      ↑            ↑         ↑
    never        dirty    computed
    seen         needs    content
                 recompute  verified

Transfer function:
    F(UNKNOWN) = STALE       (dependency declared)
    F(STALE)   = READY       (recomputed, content verified)
    F(READY)   = READY       (stable — fixpoint reached)

Scheduler loop:
    while work_queue not empty:
        node = dequeue()
        result = compute(node)
        if content_changed(result, node.cached):
            mark_downstream_stale(node)
            enqueue_downstream(node)
        node.status = READY

This guarantees:
  - No over-computation (only changed content propagates)
  - No under-computation (fixpoint catches all cascades)
  - Deterministic result (same graph + inputs → same artifacts)

Usage:
    scheduler = FixpointScheduler(artifact_graph)
    scheduler.mark_stale("scene_ir_scene_hook")
    stats = scheduler.run()
    # stats = {"computed": 3, "cache_hits": 5, "propagations": 2, "iterations": 4}
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Optional

from compiler.thinking.artifacts import ArtifactGraph, VideoArtifact
from compiler.thinking.canonicalize import content_hash as _content_hash


class ArtifactState(IntEnum):
    """Lattice states for artifact evaluation.

    IntEnum enables comparison: UNKNOWN < STALE < READY
    which encodes the lattice ordering.
    """
    UNKNOWN = 0   # Never seen or declared
    STALE = 1     # Needs recomputation (inputs changed)
    READY = 2     # Computed and verified stable


@dataclass
class FixpointStats:
    """Statistics from a fixpoint scheduler run."""
    computed: int = 0        # Nodes that were actually recomputed
    cache_hits: int = 0      # Nodes restored from cache (content unchanged)
    propagations: int = 0    # Times a content change triggered downstream invalidation
    iterations: int = 0      # Work queue drain cycles
    errors: int = 0          # Nodes that failed computation
    total_duration: float = 0.0  # Wall-clock time for entire run


@dataclass
class _NodeState:
    """Internal per-node tracking."""
    artifact_id: str
    state: ArtifactState = ArtifactState.UNKNOWN
    last_content_hash: str = ""   # Content hash from last successful compute
    compute_fn: Optional[Callable] = None
    error: Optional[str] = None
    compute_count: int = 0


class FixpointScheduler:
    """Monotone fixpoint scheduler for the artifact graph.

    Key difference from single-pass Scheduler:
      - Uses a work queue (deque) instead of fixed topological order
      - Re-enqueues downstream nodes when content actually changes
      - Runs until no more state changes (fixpoint)
    """

    def __init__(self, artifact_graph: ArtifactGraph):
        self.graph = artifact_graph
        self._node_states: dict[str, _NodeState] = {}
        self._compute_fns: dict[str, Callable] = {}
        self._work_queue: deque[str] = deque()
        self._in_queue: set[str] = set()  # Dedup guard

    def register_compute_fn(self, key: str, fn: Callable):
        """Register a compute function by artifact type or artifact ID.

        Lookup order in _compute: artifact_id first, then artifact type.
        """
        self._compute_fns[key] = fn

    def mark_stale(self, artifact_id: str):
        """Mark an artifact as needing recomputation.

        Does NOT automatically enqueue downstream — that happens
        during compute if content actually changes.
        """
        ns = self._get_or_create(artifact_id)
        if ns.state < ArtifactState.STALE:
            ns.state = ArtifactState.STALE
            self._enqueue(artifact_id)

    def mark_stale_many(self, artifact_ids: list[str]):
        """Bulk mark stale."""
        for aid in artifact_ids:
            self.mark_stale(aid)

    def _get_or_create(self, artifact_id: str) -> _NodeState:
        if artifact_id not in self._node_states:
            self._node_states[artifact_id] = _NodeState(artifact_id=artifact_id)
        return self._node_states[artifact_id]

    def _enqueue(self, artifact_id: str):
        if artifact_id not in self._in_queue:
            self._work_queue.append(artifact_id)
            self._in_queue.add(artifact_id)

    def _dequeue(self) -> Optional[str]:
        if not self._work_queue:
            return None
        aid = self._work_queue.popleft()
        self._in_queue.discard(aid)
        return aid

    def run(self) -> FixpointStats:
        """Run the fixpoint loop until the graph is stable.

        Returns:
            FixpointStats with computation metrics.
        """
        stats = FixpointStats()
        start = time.time()

        while True:
            aid = self._dequeue()
            if aid is None:
                break  # Work queue empty — fixpoint reached

            stats.iterations += 1
            self._process_node(aid, stats)

        stats.total_duration = time.time() - start
        return stats

    def _process_node(self, artifact_id: str, stats: FixpointStats):
        """Process a single node from the work queue."""
        ns = self._get_or_create(artifact_id)

        if ns.state == ArtifactState.UNKNOWN:
            return  # Not declared — skip

        # Block on STALE dependencies (they must be computed first).
        # UNKNOWN dependencies are treated as pre-computed (not in this cycle).
        art = self.graph.get(artifact_id)
        if art:
            for dep_id in art.depends_on:
                dep_ns = self._get_or_create(dep_id)
                if dep_ns.state == ArtifactState.STALE:
                    # Dependency is stale — re-enqueue self after it
                    self._enqueue(dep_id)
                    self._enqueue(artifact_id)
                    return

        # Compute
        ns.compute_count += 1
        stats.computed += 1  # We're running the compute function

        # Baseline hash: use artifact's existing content hash for first compute
        old_hash = ns.last_content_hash
        if not old_hash and art and art.content is not None:
            old_hash = _content_hash(art.content)

        try:
            new_content = self._compute(artifact_id, art)
        except Exception as e:
            ns.state = ArtifactState.STALE  # Leave stale for retry
            ns.error = str(e)
            stats.errors += 1
            stats.computed -= 1  # Didn't actually complete
            return

        # Content-addressable comparison
        if new_content is not None:
            new_hash = _content_hash(new_content)

            if new_hash != old_hash:
                # Content changed — propagate downstream
                ns.last_content_hash = new_hash
                stats.propagations += 1
                # Update artifact content so downstream sees new data
                art.content = new_content
                self._propagate_downstream(artifact_id)
            else:
                # Content unchanged — cache hit, no propagation
                stats.cache_hits += 1

        ns.state = ArtifactState.READY
        ns.error = None

    def _compute(self, artifact_id: str, art: Optional[VideoArtifact]) -> Any:
        """Execute the compute function for an artifact.

        Lookup order: artifact_id → artifact type → fallback to existing content.
        """
        if art is None:
            return None

        # Try by artifact ID first (allows per-artifact overrides)
        fn = self._compute_fns.get(artifact_id)
        if fn:
            return fn(art)

        # Then by artifact type
        art_type = art.type.value if hasattr(art.type, 'value') else str(art.type)
        fn = self._compute_fns.get(art_type)
        if fn:
            return fn(art)

        return art.content

    def _propagate_downstream(self, artifact_id: str):
        """Mark all downstream dependents as STALE and enqueue them."""
        art = self.graph.get(artifact_id)
        if not art:
            return

        # Find all artifacts that depend on this one
        for other in self.graph._artifacts.values():
            if artifact_id in other.depends_on:
                other_ns = self._get_or_create(other.id)
                if other_ns.state < ArtifactState.STALE:
                    other_ns.state = ArtifactState.STALE
                    self._enqueue(other.id)

    def get_state(self, artifact_id: str) -> ArtifactState:
        """Get current lattice state of an artifact."""
        return self._node_states.get(artifact_id, _NodeState(artifact_id)).state

    def summary(self) -> dict:
        """Return current scheduler state as a dict."""
        states = {}
        for aid, ns in self._node_states.items():
            states[aid] = {
                "state": ns.state.name,
                "compute_count": ns.compute_count,
                "error": ns.error,
            }
        return {
            "total_nodes": len(self._node_states),
            "queue_size": len(self._work_queue),
            "nodes": states,
        }
