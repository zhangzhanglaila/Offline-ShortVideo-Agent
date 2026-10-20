"""Live Session — Reactive incremental recompilation.

The main entry point for interactive editing. Wraps the artifact graph,
parallel scheduler, and semantic diff into a single editing API.

    session = LiveSession(artifact_graph, compute_fns)
    result = session.edit("hook", {"text": "Redis为什么快？"})
    # result.recomputed_nodes = ["hook", "hook_video"]
    # result.cache_hits = ["graph", "cards"]
    # result.duration = 0.05s

Lifecycle:
  1. User calls edit(target_id, patch)
  2. Semantic diff determines change depth
  3. Invalidation propagates to downstream
  4. Parallel scheduler recomputes affected nodes
  5. Returns stats (what changed, what was cached)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from compiler.thinking.artifacts import ArtifactGraph, VideoArtifact
from compiler.thinking.canonicalize import content_hash
from compiler.thinking.parallel_scheduler import ParallelScheduler, ParallelStats
from compiler.thinking.fixpoint_scheduler import ArtifactState
from compiler.runtime.edit_operations import EditOperation, EditType, EditResult
from compiler.runtime.invalidation import SemanticDiff, DiffResult, InvalidationDepth


@dataclass
class SessionStats:
    """Aggregate stats across all edits in a session."""
    total_edits: int = 0
    total_recomputes: int = 0
    total_cache_hits: int = 0
    total_duration: float = 0.0
    edit_details: list = field(default_factory=list)


@dataclass
class EditResponse:
    """Result of a single edit operation."""
    edit_result: EditResult
    diff_result: DiffResult
    scheduler_stats: ParallelStats
    recomputed_nodes: list[str]
    cache_hit_nodes: list[str]
    duration: float = 0.0

    @property
    def target_id(self) -> str:
        return self.edit_result.target_id

    @property
    def changed(self) -> bool:
        return self.diff_result.has_change


class LiveSession:
    """Reactive editing session.

    Manages the lifecycle of incremental edits:
      1. Apply edit to artifact
      2. Semantic diff → invalidation depth
      3. Propagate invalidation
      4. Recompute affected nodes
      5. Return stats

    Usage:
        session = LiveSession(graph, compute_fns={"scene_ir": my_fn, ...})
        session.edit("hook", {"text": "new text"})
        session.edit("graph", {"duration": 200})
        print(session.stats())
    """

    def __init__(
        self,
        artifact_graph: ArtifactGraph,
        compute_fns: dict[str, Callable] | None = None,
        max_workers: int = 4,
    ):
        self.graph = artifact_graph
        self.compute_fns = compute_fns or {}
        self.max_workers = max_workers
        self._diff = SemanticDiff()
        self._edit_history: list[EditResponse] = []

    def edit(
        self,
        target_id: str,
        patch: Any,
        *,
        edit_type: EditType = EditType.PATCH,
        path: str = "",
    ) -> EditResponse:
        """Apply an edit and incrementally recompute.

        Args:
            target_id: Artifact ID to modify.
            patch: New content (full or partial depending on edit_type).
            edit_type: Kind of edit (PATCH, REPLACE, etc.).
            path: Dot-separated field path for targeted edits.

        Returns:
            EditResponse with recomputation stats.
        """
        start = time.time()

        # 1. Get current artifact
        art = self.graph.get(target_id)
        old_content = art.content if art else None

        # 2. Build and apply edit operation
        op = EditOperation(
            target_id=target_id,
            edit_type=edit_type,
            patch=patch,
            path=path,
        )
        new_content = op.apply(old_content)

        old_hash = content_hash(old_content) if old_content is not None else ""
        new_hash = content_hash(new_content) if new_content is not None else ""

        edit_result = EditResult(
            operation=op,
            old_hash=old_hash,
            new_hash=new_hash,
            changed=old_hash != new_hash,
            old_content=old_content,
            new_content=new_content,
        )

        # 3. Semantic diff
        diff_result = self._diff.diff(old_content, new_content)

        # 4. Apply content to artifact (if changed)
        if edit_result.changed and art:
            art.content = new_content

        # 5. Build scheduler and recompute
        recomputed = []
        cache_hits = []
        scheduler_stats = ParallelStats()

        if diff_result.has_change:
            recomputed, cache_hits, scheduler_stats = self._recompute(
                target_id, diff_result.depth,
            )

        duration = time.time() - start

        response = EditResponse(
            edit_result=edit_result,
            diff_result=diff_result,
            scheduler_stats=scheduler_stats,
            recomputed_nodes=recomputed,
            cache_hit_nodes=cache_hits,
            duration=duration,
        )
        self._edit_history.append(response)
        return response

    def _recompute(
        self,
        target_id: str,
        depth: InvalidationDepth,
    ) -> tuple[list[str], list[str], ParallelStats]:
        """Recompute affected nodes based on invalidation depth."""
        sched = ParallelScheduler(self.graph, max_workers=self.max_workers)

        # Register compute functions
        for key, fn in self.compute_fns.items():
            sched.register_compute_fn(key, fn)

        # Mark nodes as stale based on depth
        if depth == InvalidationDepth.LOCAL:
            sched.mark_stale(target_id)
        elif depth == InvalidationDepth.SUBTREE:
            self._mark_subtree_stale(sched, target_id)
        elif depth == InvalidationDepth.GLOBAL:
            sched.mark_all_stale()

        stats = sched.run()

        # Collect results
        recomputed = sched.get_execution_order()
        cache_hits = [
            aid for aid in sched._node_states
            if aid not in recomputed
            and sched._node_states[aid].state == ArtifactState.READY
        ]

        return recomputed, cache_hits, stats

    def _mark_subtree_stale(
        self,
        sched: ParallelScheduler,
        root_id: str,
    ):
        """Mark root and all transitive downstream as stale."""
        to_mark = [root_id]
        visited = set()

        while to_mark:
            aid = to_mark.pop()
            if aid in visited:
                continue
            visited.add(aid)
            sched.mark_stale(aid)

            # Find downstream dependents
            art = self.graph.get(aid)
            if art:
                for downstream_id in self.graph._downstream.get(aid, set()):
                    if downstream_id not in visited:
                        to_mark.append(downstream_id)

    # ── History & Stats ──

    @property
    def edit_count(self) -> int:
        return len(self._edit_history)

    def last_edit(self) -> Optional[EditResponse]:
        return self._edit_history[-1] if self._edit_history else None

    def stats(self) -> SessionStats:
        """Aggregate stats across all edits."""
        total_recomputes = sum(len(e.recomputed_nodes) for e in self._edit_history)
        total_cache_hits = sum(len(e.cache_hit_nodes) for e in self._edit_history)
        total_duration = sum(e.duration for e in self._edit_history)

        return SessionStats(
            total_edits=len(self._edit_history),
            total_recomputes=total_recomputes,
            total_cache_hits=total_cache_hits,
            total_duration=total_duration,
            edit_details=tuple(self._edit_history),
        )

    def reset_history(self):
        """Clear edit history."""
        self._edit_history.clear()
