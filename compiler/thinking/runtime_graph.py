"""Runtime Graph — Dependency-aware execution graph for the Thinking Agent.

Replaces the linear procedural pipeline with a reactive dependency graph:
  - Nodes represent computation steps (script_gen, tts, graph_gen, etc.)
  - Edges declare dependencies (depends_on), invalidation (invalidates),
    and triggers (triggers)
  - When a node's input changes, only downstream nodes recompute

This is the foundation for:
  - Incremental recomputation (change one sentence → only re-sync affected clips)
  - Parallel execution (independent nodes can run concurrently)
  - Partial failure recovery (retry one node, not the whole pipeline)
  - Dependency visualization (show the user what will be affected by a change)

Usage:
    graph = RuntimeGraph()
    graph.add_node("script_gen", compute_fn, depends_on=["topic_analysis"])
    graph.add_node("tts", compute_fn, depends_on=["script_gen"])
    graph.invalidate("script_gen")  # marks tts as needing recomputation
    graph.execute(state)            # only recomputes invalidated nodes
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


class NodeStatus(str, Enum):
    """Status of a runtime graph node."""
    PENDING = "pending"         # Not yet computed
    COMPUTING = "computing"     # Currently being computed
    DONE = "done"              # Successfully computed
    FAILED = "failed"          # Computation failed
    INVALIDATED = "invalidated"  # Needs recomputation due to upstream change
    SKIPPED = "skipped"        # Skipped (not needed for current request)


class EdgeType(str, Enum):
    """Types of edges in the runtime graph."""
    DEPENDS_ON = "depends_on"      # A must complete before B
    INVALIDATES = "invalidates"    # If A changes, B must recompute
    TRIGGERS = "triggers"          # When A completes, trigger B
    CONSTRAINS = "constrains"      # A imposes constraints on B


@dataclass
class GraphNode:
    """A single node in the runtime dependency graph."""
    id: str = ""
    name: str = ""
    description: str = ""
    status: NodeStatus = NodeStatus.PENDING
    # Computation
    compute_fn: Optional[Callable] = None
    result: Any = None
    error: Optional[str] = None
    # Timing
    created_at: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0
    # Dependencies (populated from edges)
    depends_on: list[str] = field(default_factory=list)
    invalidated_by: list[str] = field(default_factory=list)
    # Metadata
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = f"node_{uuid.uuid4().hex[:8]}"
        if not self.created_at:
            self.created_at = time.time()

    @property
    def duration(self) -> float:
        if self.started_at and self.finished_at:
            return self.finished_at - self.started_at
        return 0.0

    def reset(self):
        """Reset node for recomputation."""
        self.status = NodeStatus.PENDING
        self.result = None
        self.error = None
        self.started_at = 0.0
        self.finished_at = 0.0


@dataclass
class GraphEdge:
    """A directed edge in the runtime dependency graph."""
    id: str = ""
    from_node: str = ""
    to_node: str = ""
    edge_type: EdgeType = EdgeType.DEPENDS_ON
    label: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = f"edge_{uuid.uuid4().hex[:8]}"


class RuntimeGraph:
    """Dependency-aware execution graph.

    Manages computation nodes and their dependencies. Supports:
      - Topological execution (respect dependency order)
      - Invalidation propagation (change upstream → downstream invalidated)
      - Incremental recomputation (only recompute invalidated nodes)
      - Parallel execution (independent nodes can run concurrently)
    """

    def __init__(self):
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self._adjacency: dict[str, list[str]] = defaultdict(list)  # from → [to]
        self._reverse_adj: dict[str, list[str]] = defaultdict(list)  # to → [from]

    def add_node(self, node_id: str, compute_fn: Callable = None,
                 name: str = "", description: str = "",
                 depends_on: list[str] = None, metadata: dict = None) -> GraphNode:
        """Add a computation node to the graph."""
        node = GraphNode(
            id=node_id, name=name or node_id,
            description=description, compute_fn=compute_fn,
            depends_on=depends_on or [], metadata=metadata or {},
        )
        self.nodes[node_id] = node

        # Create dependency edges
        for dep_id in (depends_on or []):
            self.add_edge(dep_id, node_id, EdgeType.DEPENDS_ON)
            node.invalidated_by.append(dep_id)

        return node

    def add_edge(self, from_id: str, to_id: str,
                 edge_type: EdgeType = EdgeType.DEPENDS_ON,
                 label: str = "") -> GraphEdge:
        """Add a directed edge between nodes."""
        edge = GraphEdge(
            from_node=from_id, to_node=to_id,
            edge_type=edge_type, label=label,
        )
        self.edges.append(edge)
        self._adjacency[from_id].append(to_id)
        self._reverse_adj[to_id].append(from_id)

        # Update node's invalidated_by for INVALIDATES edges
        if edge_type == EdgeType.INVALIDATES:
            if from_id in self.nodes:
                self.nodes[to_id].invalidated_by.append(from_id)

        return edge

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        return self.nodes.get(node_id)

    def get_dependencies(self, node_id: str) -> list[GraphNode]:
        """Get all nodes that this node depends on."""
        deps = []
        for dep_id in self._reverse_adj.get(node_id, []):
            if dep_id in self.nodes:
                deps.append(self.nodes[dep_id])
        return deps

    def get_downstream(self, node_id: str) -> list[GraphNode]:
        """Get all nodes that depend on this node (direct and transitive)."""
        visited = set()
        queue = [node_id]
        downstream = []
        while queue:
            current = queue.pop(0)
            for child_id in self._adjacency.get(current, []):
                if child_id not in visited:
                    visited.add(child_id)
                    if child_id in self.nodes:
                        downstream.append(self.nodes[child_id])
                    queue.append(child_id)
        return downstream

    def invalidate(self, node_id: str):
        """Invalidate a node and all its downstream dependents.

        This is the core of incremental recomputation:
        when source data changes, mark all affected nodes as needing recomputation.
        """
        if node_id in self.nodes:
            self.nodes[node_id].status = NodeStatus.INVALIDATED

        # Propagate to all downstream nodes
        for downstream in self.get_downstream(node_id):
            downstream.status = NodeStatus.INVALIDATED

    def get_ready_nodes(self) -> list[GraphNode]:
        """Get nodes that are ready to execute (all dependencies done, not yet computed)."""
        ready = []
        for node in self.nodes.values():
            if node.status != NodeStatus.PENDING and node.status != NodeStatus.INVALIDATED:
                continue
            # Check if all dependencies are done
            all_deps_done = all(
                self.nodes[dep_id].status == NodeStatus.DONE
                for dep_id in self._reverse_adj.get(node.id, [])
                if dep_id in self.nodes
            )
            if all_deps_done:
                ready.append(node)
        return ready

    def topological_order(self) -> list[GraphNode]:
        """Get nodes in topological order (respecting dependencies)."""
        in_degree = defaultdict(int)
        for node_id in self.nodes:
            in_degree[node_id] = len(self._reverse_adj.get(node_id, []))

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        order = []

        while queue:
            nid = queue.pop(0)
            if nid in self.nodes:
                order.append(self.nodes[nid])
            for child_id in self._adjacency.get(nid, []):
                in_degree[child_id] -= 1
                if in_degree[child_id] == 0:
                    queue.append(child_id)

        return order

    def execute(self, state: Any, only_invalidated: bool = True) -> list[GraphNode]:
        """Execute all ready nodes in topological order.

        Args:
            state: The VideoProjectState to pass to compute functions
            only_invalidated: If True, only execute invalidated/pending nodes

        Returns:
            List of executed nodes (with results)
        """
        executed = []

        for node in self.topological_order():
            # Skip if only doing incremental and node is already done
            if only_invalidated and node.status == NodeStatus.DONE:
                continue

            # Check dependencies
            if node.status not in (NodeStatus.PENDING, NodeStatus.INVALIDATED):
                continue

            # Verify all dependencies completed
            deps_ok = all(
                self.nodes[dep_id].status == NodeStatus.DONE
                for dep_id in self._reverse_adj.get(node.id, [])
                if dep_id in self.nodes
            )
            if not deps_ok:
                node.status = NodeStatus.SKIPPED
                continue

            # Execute
            if node.compute_fn:
                node.status = NodeStatus.COMPUTING
                node.started_at = time.time()
                try:
                    # Collect dependency results as kwargs
                    dep_results = {}
                    for dep_id in self._reverse_adj.get(node.id, []):
                        if dep_id in self.nodes and self.nodes[dep_id].result is not None:
                            dep_results[dep_id] = self.nodes[dep_id].result

                    node.result = node.compute_fn(state, **dep_results)
                    node.status = NodeStatus.DONE
                    node.finished_at = time.time()
                except Exception as e:
                    node.status = NodeStatus.FAILED
                    node.error = str(e)
                    node.finished_at = time.time()
            else:
                # No compute function — mark as done (passthrough)
                node.status = NodeStatus.DONE
                node.finished_at = time.time()

            executed.append(node)

        return executed

    def summary(self) -> dict:
        """Get a summary of the graph state."""
        status_counts = defaultdict(int)
        for node in self.nodes.values():
            status_counts[node.status.value] += 1
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "status": dict(status_counts),
            "ready": len(self.get_ready_nodes()),
        }

    def to_dict(self) -> dict:
        """Serialize for visualization/debugging."""
        return {
            "nodes": [
                {
                    "id": n.id, "name": n.name,
                    "status": n.status.value,
                    "description": n.description,
                    "duration": n.duration,
                    "error": n.error,
                    "depends_on": n.depends_on,
                }
                for n in self.nodes.values()
            ],
            "edges": [
                {
                    "id": e.id, "from": e.from_node,
                    "to": e.to_node, "type": e.edge_type.value,
                    "label": e.label,
                }
                for e in self.edges
            ],
        }


# ── Pre-built graph for the Thinking Agent pipeline ──

def build_thinking_runtime_graph() -> RuntimeGraph:
    """Build the standard runtime graph for the video thinking pipeline.

    This replaces the linear if/elif chain in agent_loop.run() with
    a proper dependency graph.

    Nodes:
      topic_analysis → script_gen → graph_gen → card_gen → tts → layout → render

    With invalidation:
      - If script_gen changes → invalidate tts, layout
      - If graph_gen changes → invalidate layout
      - If tts changes → invalidate layout
    """
    graph = RuntimeGraph()

    # Topic analysis (no dependencies)
    graph.add_node("topic_analysis", name="主题分析", description="分析主题，生成大纲")

    # Script generation (depends on topic analysis)
    graph.add_node("script_gen", name="文案生成", description="为每个模块生成讲解文案",
                   depends_on=["topic_analysis"])

    # Graph generation (depends on script for context)
    graph.add_node("graph_gen", name="图谱生成", description="生成知识图谱结构",
                   depends_on=["script_gen"])

    # Card generation (depends on script for key points)
    graph.add_node("card_gen", name="卡片生成", description="生成摘要卡片",
                   depends_on=["script_gen"])

    # TTS (depends on script)
    graph.add_node("tts", name="语音合成", description="将文案转为语音",
                   depends_on=["script_gen"])

    # Layout assembly (depends on everything)
    graph.add_node("layout", name="布局组装", description="组装 Remotion 布局 JSON",
                   depends_on=["graph_gen", "card_gen", "tts"])

    # Render (depends on layout)
    graph.add_node("render", name="视频渲染", description="通过 Remotion 渲染视频",
                   depends_on=["layout"])

    # Add invalidation edges
    graph.add_edge("script_gen", "tts", EdgeType.INVALIDATES, "文案变化→重配音")
    graph.add_edge("script_gen", "layout", EdgeType.INVALIDATES, "文案变化→重排布局")
    graph.add_edge("graph_gen", "layout", EdgeType.INVALIDATES, "图谱变化→重排布局")
    graph.add_edge("tts", "layout", EdgeType.INVALIDATES, "音频变化→重排布局")

    return graph
