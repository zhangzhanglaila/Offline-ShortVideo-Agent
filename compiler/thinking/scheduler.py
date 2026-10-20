"""Scheduler — Incremental Recomputation Engine.

When a user edits a sentence (or any patch is applied), the scheduler:
  1. Determines which Runtime Graph nodes are affected
  2. Invalidates those nodes and their downstream dependents
  3. Recomputes only the invalidated nodes in dependency order

This is the bridge between:
  - Patch System (what changed)
  - Runtime Graph (what depends on what)
  - Execution (recompute only what's needed)

Example:
    User edits sentence 3 in module mod_00
    → Scheduler invalidates: tts(mod_00), subtitle(mod_00), layout(mod_00)
    → Does NOT invalidate: topic_analysis, graph_gen, other modules
    → Recomputes only the 3 invalidated nodes

Usage:
    scheduler = Scheduler(runtime_graph)
    scheduler.on_patch(patch, state)  # called after every patch
    scheduler.run_invalidated(state)  # recompute only what changed
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from compiler.thinking.runtime_graph import RuntimeGraph, GraphNode, NodeStatus
from compiler.thinking.patch import (
    PatchOperation, EditSentencePatch, AddSentencePatch,
    RemoveSentencePatch, ApproveModulePatch, EditGraphNodePatch, BatchPatch,
)
from compiler.thinking.event_bus import Event, get_event_bus
from compiler.thinking.artifacts import UpdateArtifactPatch, ArtifactType


# ── Patch-to-Node mapping ──
# Defines which graph nodes are invalidated by which patch types

PATCH_NODE_MAP: dict[type, list[str]] = {
    EditSentencePatch: ["tts", "layout"],
    AddSentencePatch: ["tts", "layout"],
    RemoveSentencePatch: ["tts", "layout"],
    EditGraphNodePatch: ["layout"],
    ApproveModulePatch: [],  # Approval doesn't require recomputation
}

# Artifact type → Runtime Graph node IDs affected when this artifact changes
_ARTIFACT_NODE_MAP: dict[ArtifactType, list[str]] = {
    ArtifactType.SCRIPT: ["tts", "layout"],
    ArtifactType.KNOWLEDGE_GRAPH: ["layout"],
    ArtifactType.SHOTS: ["layout"],
    ArtifactType.TTS_AUDIO: ["layout"],
    ArtifactType.TTS_SENTENCE: ["layout"],
    ArtifactType.TIMELINE: ["layout"],
    ArtifactType.RENDER_PLAN: ["render"],
    ArtifactType.SCENE_IR: ["render"],
    ArtifactType.SCENE_VIDEO: ["render"],
    ArtifactType.VIDEO: [],
}


class Scheduler:
    """Incremental recomputation scheduler with memoization.

    Integrates the Runtime Graph with the Patch System:
      - When a patch is applied, determine affected nodes
      - Invalidate those nodes in the graph
      - Execute only the invalidated nodes
      - Memoize: same inputs → cached result (no recompute)
    """

    def __init__(self, graph: RuntimeGraph = None, max_retries: int = 3, base_delay: float = 0.1):
        self.graph = graph or RuntimeGraph()
        self._compute_fns: dict[str, Callable] = {}
        self._last_run: dict[str, float] = {}
        # Memoization cache: node_id → (input_hash, result)
        self._cache: dict[str, tuple[str, Any]] = {}
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        # Retry config
        self.max_retries = max_retries
        self.base_delay = base_delay

    def register_compute_fn(self, node_id: str, fn: Callable):
        """Register a compute function for a graph node."""
        self._compute_fns[node_id] = fn
        if node_id in self.graph.nodes:
            self.graph.nodes[node_id].compute_fn = fn

    def on_patch(self, patch: PatchOperation, state: Any = None):
        """Called after a patch is applied. Invalidates affected nodes.

        This is the core of incremental recomputation:
        instead of re-running the whole pipeline, only invalidate
        what's actually affected by this change.
        """
        affected_nodes = self._get_affected_nodes(patch)

        for node_id in affected_nodes:
            self.graph.invalidate(node_id)

        # Emit event for observability
        bus = get_event_bus()
        bus.publish(Event(
            type="scheduler_invalidation",
            source="scheduler",
            data={
                "patch_type": patch.__class__.__name__,
                "patch_target": patch.target,
                "affected_nodes": affected_nodes,
                "total_invalidated": sum(
                    1 for n in self.graph.nodes.values()
                    if n.status == NodeStatus.INVALIDATED
                ),
            },
        ))

    def _get_affected_nodes(self, patch: PatchOperation) -> list[str]:
        """Determine which graph nodes are affected by a patch."""
        affected = []

        # Direct mapping from patch type
        patch_type = type(patch)
        if patch_type in PATCH_NODE_MAP:
            affected.extend(PATCH_NODE_MAP[patch_type])

        # Artifact-level patches: map artifact type → runtime graph nodes
        if isinstance(patch, UpdateArtifactPatch):
            affected.extend(_ARTIFACT_NODE_MAP.get(patch.artifact_type, []))

        # Special handling for BatchPatch
        if isinstance(patch, BatchPatch):
            for sub_patch in patch.patches:
                affected.extend(self._get_affected_nodes(sub_patch))

        return list(set(affected))  # Deduplicate

    def run_invalidated(self, state: Any) -> list[GraphNode]:
        """Execute only the invalidated nodes in dependency order.

        With memoization: if a node's inputs haven't changed,
        restore from cache instead of recomputing.

        Returns:
            List of nodes that were re-executed (or cache-restored)
        """
        executed = []

        for node in self.graph.topological_order():
            if node.status not in (NodeStatus.PENDING, NodeStatus.INVALIDATED):
                continue

            # Check dependencies
            deps_ok = all(
                self.graph.nodes[dep_id].status == NodeStatus.DONE
                for dep_id in self.graph._reverse_adj.get(node.id, [])
                if dep_id in self.graph.nodes
            )
            if not deps_ok:
                node.status = NodeStatus.SKIPPED
                continue

            # Compute cache key
            cache_key = self._compute_cache_key(node, state)

            # Check cache
            cached = self._cache.get(node.id)
            if cached and cached[0] == cache_key:
                # Cache hit — restore without recomputing
                node.result = cached[1]
                node.status = NodeStatus.DONE
                node.started_at = time.time()
                node.finished_at = time.time()
                self._cache_hits += 1
                executed.append(node)
                continue

            # Cache miss — execute with retry
            self._cache_misses += 1
            if node.compute_fn:
                node.status = NodeStatus.COMPUTING
                node.started_at = time.time()
                dep_results = {}
                for dep_id in self.graph._reverse_adj.get(node.id, []):
                    if dep_id in self.graph.nodes and self.graph.nodes[dep_id].result is not None:
                        dep_results[dep_id] = self.graph.nodes[dep_id].result

                last_error = None
                for attempt in range(self.max_retries):
                    try:
                        node.result = node.compute_fn(state, **dep_results)
                        node.status = NodeStatus.DONE
                        node.finished_at = time.time()
                        self._cache[node.id] = (cache_key, node.result)
                        last_error = None
                        break
                    except Exception as e:
                        last_error = e
                        if attempt < self.max_retries - 1:
                            import threading
                            threading.Event().wait(self.base_delay * (2 ** attempt))

                if last_error is not None:
                    node.status = NodeStatus.FAILED
                    node.error = str(last_error)
                    node.finished_at = time.time()
            else:
                node.status = NodeStatus.DONE
                node.finished_at = time.time()

            self._last_run[node.id] = time.time()
            executed.append(node)

            # Emit completion event
            bus = get_event_bus()
            bus.publish(Event(
                type="scheduler_node_done",
                source="scheduler",
                data={
                    "node_id": node.id,
                    "status": node.status.value,
                    "duration": node.duration,
                    "error": node.error,
                    "cache_hit": cached and cached[0] == cache_key,
                },
            ))

        return executed

    def _compute_cache_key(self, node: GraphNode, state: Any) -> str:
        """Compute a hash of the node's inputs for memoization.

        The cache key includes:
          - The node's own input data
          - Results from all dependency nodes
          - A version counter (for forced invalidation)
        """
        import hashlib
        parts = [node.id]

        # Include dependency results
        for dep_id in self.graph._reverse_adj.get(node.id, []):
            dep = self.graph.nodes.get(dep_id)
            if dep and dep.result is not None:
                parts.append(f"{dep_id}:{hash(str(dep.result))}")

        # Include relevant state
        if hasattr(state, 'modules'):
            for m in state.modules:
                parts.append(f"mod:{m.id}:{len(m.script)}:{m.status}")

        return hashlib.md5("|".join(parts).encode()).hexdigest()

    def invalidate_cache(self, node_id: str = ""):
        """Clear cache for a specific node or all nodes."""
        if node_id:
            self._cache.pop(node_id, None)
        else:
            self._cache.clear()

    def run_all(self, state: Any) -> list[GraphNode]:
        """Execute all nodes (full pipeline run)."""
        for node in self.graph.nodes.values():
            node.reset()
        return self.run_invalidated(state)

    def get_status(self) -> dict:
        """Get scheduler status with cache stats."""
        summary = self.graph.summary()
        summary["last_run_times"] = {
            nid: ts for nid, ts in self._last_run.items()
        }
        summary["cache"] = {
            "entries": len(self._cache),
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "hit_rate": f"{self._cache_hits / max(1, self._cache_hits + self._cache_misses) * 100:.1f}%",
        }
        return summary

    def get_plan(self) -> list[dict]:
        """Get the execution plan (what will be recomputed)."""
        plan = []
        for node in self.graph.topological_order():
            if node.status in (NodeStatus.PENDING, NodeStatus.INVALIDATED):
                deps = [n.id for n in self.graph.get_dependencies(node.id)]
                plan.append({
                    "node_id": node.id,
                    "name": node.name,
                    "status": node.status.value,
                    "depends_on": deps,
                    "will_recompute": True,
                })
            else:
                plan.append({
                    "node_id": node.id,
                    "name": node.name,
                    "status": node.status.value,
                    "will_recompute": False,
                })
        return plan


# ── Integration helper ──

def create_thinking_scheduler() -> Scheduler:
    """Create a scheduler pre-configured for the Thinking Agent pipeline."""
    from compiler.thinking.runtime_graph import build_thinking_runtime_graph

    graph = build_thinking_runtime_graph()
    scheduler = Scheduler(graph)

    # Register compute functions (these would be the actual pipeline steps)
    # For now, they're placeholders that the agent_loop fills in
    return scheduler
