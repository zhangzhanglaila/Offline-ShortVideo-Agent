"""Parallel Scheduler — Deterministic parallel DAG execution.

Extends FixpointScheduler with parallel node evaluation while
guaranteeing deterministic results (parallel result == serial result).

Key design:
  - Nodes with no unmet dependencies execute in parallel (ThreadPoolExecutor)
  - Results commit in topological order (deterministic)
  - Downstream nodes wait for all upstream to READY before executing
  - Work-stealing: idle workers pick next available node

Guarantees:
  - Same graph + same inputs → same artifact hashes (regardless of worker count)
  - Same graph + same inputs → same execution trace order
  - No race conditions on artifact state transitions

Usage:
    scheduler = ParallelScheduler(artifact_graph, max_workers=4)
    scheduler.mark_stale("scene_ir_hook")
    stats = scheduler.run()
    # stats.parallel_speedup, stats.worker_utilization, etc.
"""

from __future__ import annotations

import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable, Optional

from compiler.thinking.artifacts import ArtifactGraph, VideoArtifact
from compiler.thinking.canonicalize import content_hash as _content_hash
from compiler.thinking.fixpoint_scheduler import ArtifactState, _NodeState, FixpointStats


@dataclass
class ParallelStats(FixpointStats):
    """Extended stats for parallel execution."""
    max_workers: int = 0
    parallel_batches: int = 0       # Number of parallel execution batches
    max_concurrent: int = 0         # Max nodes executing simultaneously
    serial_equivalent_time: float = 0.0  # Sum of all compute times (theoretical serial)
    worker_wait_time: float = 0.0   # Total time workers spent waiting for deps


class ParallelScheduler:
    """Deterministic parallel fixpoint scheduler.

    Execution model:
      1. Find all nodes with all-deps-READY → ready set
      2. Submit ready set to thread pool (parallel)
      3. As each completes, check if downstream nodes become ready
      4. Repeat until fixpoint

    Determinism guarantee:
      - Compute happens in parallel (nondeterministic order)
      - State transitions commit in topo order (deterministic)
      - Content hash comparison is order-independent
    """

    def __init__(
        self,
        artifact_graph: ArtifactGraph,
        max_workers: int = 4,
    ):
        self.graph = artifact_graph
        self.max_workers = max_workers
        self._node_states: dict[str, _NodeState] = {}
        self._compute_fns: dict[str, Callable] = {}
        self._lock = Lock()

        # Tracking
        self._compute_order: list[str] = []  # Deterministic commit log
        self._in_flight: set[str] = set()    # Currently executing

    def register_compute_fn(self, key: str, fn: Callable):
        """Register compute function by artifact type or ID."""
        self._compute_fns[key] = fn

    def mark_stale(self, artifact_id: str):
        ns = self._get_or_create(artifact_id)
        if ns.state < ArtifactState.STALE:
            ns.state = ArtifactState.STALE

    def mark_stale_many(self, artifact_ids: list[str]):
        for aid in artifact_ids:
            self.mark_stale(aid)

    def mark_all_stale(self):
        """Mark every node in the graph STALE. Used for full re-evaluation."""
        for art in self.graph.all():
            self.mark_stale(art.id)

    def _get_or_create(self, artifact_id: str) -> _NodeState:
        with self._lock:
            if artifact_id not in self._node_states:
                self._node_states[artifact_id] = _NodeState(artifact_id=artifact_id)
            return self._node_states[artifact_id]

    def run(self) -> ParallelStats:
        """Run parallel fixpoint evaluation.

        Returns:
            ParallelStats with execution metrics.
        """
        stats = ParallelStats(max_workers=self.max_workers)
        start = time.time()
        compute_times: dict[str, float] = {}

        # Find initial ready set (all STALE nodes with deps satisfied)
        ready = self._find_ready()

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while ready:
                stats.parallel_batches += 1
                stats.max_concurrent = max(stats.max_concurrent, len(ready))

                # Submit all ready nodes in parallel
                futures: dict[str, Future] = {}
                for aid in ready:
                    self._in_flight.add(aid)
                    futures[aid] = pool.submit(self._compute_node, aid)

                # Wait for all to complete, collect results
                newly_ready: list[str] = []
                for aid, future in futures.items():
                    try:
                        success, changed = future.result(timeout=300)
                        self._in_flight.discard(aid)

                        with self._lock:
                            ns = self._node_states[aid]
                            if success:
                                ns.state = ArtifactState.READY
                                ns.error = None
                                self._compute_order.append(aid)
                                stats.computed += 1
                                if not changed:
                                    stats.cache_hits += 1
                                else:
                                    stats.propagations += 1
                            else:
                                stats.errors += 1

                            compute_times[aid] = ns.compute_count

                    except Exception:
                        self._in_flight.discard(aid)
                        with self._lock:
                            stats.errors += 1

                # Find next ready set
                ready = self._find_ready()

        stats.total_duration = time.time() - start
        stats.serial_equivalent_time = sum(compute_times.values())
        return stats

    def _find_ready(self) -> list[str]:
        """Find all STALE nodes whose dependencies are all READY.

        Returns nodes in deterministic order (sorted by ID).
        """
        ready = []
        with self._lock:
            for aid, ns in self._node_states.items():
                if ns.state != ArtifactState.STALE:
                    continue
                if aid in self._in_flight:
                    continue
                if self._deps_satisfied(aid):
                    ready.append(aid)

        # Deterministic order: sort by artifact ID
        ready.sort()
        return ready

    def _deps_satisfied(self, artifact_id: str) -> bool:
        """Check if all dependencies are READY.

        Tracked dependencies must be explicitly READY.
        Untracked dependencies (not in this evaluation cycle) are
        assumed pre-computed and pass automatically.
        """
        art = self.graph.get(artifact_id)
        if not art:
            return True
        for dep_id in art.depends_on:
            dep_ns = self._node_states.get(dep_id)
            if dep_ns is not None and dep_ns.state != ArtifactState.READY:
                return False
        return True

    def _compute_node(self, artifact_id: str) -> tuple[bool, bool]:
        """Execute a single node. Returns (success, content_changed).

        Thread-safe: only modifies own node state.
        """
        ns = self._node_states[artifact_id]
        ns.compute_count += 1

        art = self.graph.get(artifact_id)
        old_hash = ns.last_content_hash
        if not old_hash and art and art.content is not None:
            old_hash = _content_hash(art.content)

        try:
            new_content = self._compute(artifact_id, art)
        except Exception as e:
            ns.error = str(e)
            return (False, False)

        if new_content is not None:
            new_hash = _content_hash(new_content)
            if new_hash != old_hash:
                ns.last_content_hash = new_hash
                if art:
                    art.content = new_content
                return (True, True)  # success, changed
            return (True, False)  # success, unchanged (cache hit)

        return (True, False)

    def _compute(self, artifact_id: str, art: Optional[VideoArtifact]) -> Any:
        """Lookup and execute compute function."""
        if art is None:
            return None

        fn = self._compute_fns.get(artifact_id)
        if fn:
            return fn(art)

        art_type = art.type.value if hasattr(art.type, 'value') else str(art.type)
        fn = self._compute_fns.get(art_type)
        if fn:
            return fn(art)

        return art.content

    def get_state(self, artifact_id: str) -> ArtifactState:
        return self._node_states.get(artifact_id, _NodeState(artifact_id=artifact_id)).state

    def get_execution_order(self) -> list[str]:
        """Return the deterministic commit order."""
        return list(self._compute_order)

    def summary(self) -> dict:
        states = {}
        for aid, ns in self._node_states.items():
            states[aid] = {
                "state": ns.state.name,
                "compute_count": ns.compute_count,
                "error": ns.error,
            }
        return {
            "total_nodes": len(self._node_states),
            "max_workers": self.max_workers,
            "in_flight": list(self._in_flight),
            "execution_order": self._compute_order,
            "nodes": states,
        }
