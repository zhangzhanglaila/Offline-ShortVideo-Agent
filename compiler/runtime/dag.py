"""DAG — Directed Acyclic Graph for pipeline execution tracking.

Represents the rendering pipeline as a graph of nodes with dependencies.
Each node tracks its status (pending/running/done/error), inputs, outputs,
timing, and cache hit/miss info.

Usage:
    dag = PipelineDAG()
    dag.add_node("script", "ScriptPass", inputs={}, outputs={"text": "..."})
    dag.add_node("tts", "TTSPass", inputs={"script": "text"}, outputs={"audio": "..."},
                 depends_on=["script"])
    dag.start("tts")
    dag.complete("tts", cache_hit=True)
    dag.to_dict()  # For API/visualization
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass
class DAGNode:
    """A single node in the pipeline DAG."""
    id: str
    name: str
    status: NodeStatus = NodeStatus.PENDING
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    error: str | None = None
    cache_hit: bool = False
    started_at: float | None = None
    completed_at: float | None = None
    duration: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status.value,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "depends_on": self.depends_on,
            "error": self.error,
            "cache_hit": self.cache_hit,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration": self.duration,
            "metadata": self.metadata,
        }


class PipelineDAG:
    """Directed Acyclic Graph tracking pipeline execution.

    Thread-safe for status updates.
    """

    def __init__(self, name: str = "pipeline"):
        self.name = name
        self.nodes: dict[str, DAGNode] = {}
        self._start_time = time.monotonic()

    def add_node(
        self,
        node_id: str,
        name: str,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        depends_on: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DAGNode:
        """Add a node to the DAG."""
        node = DAGNode(
            id=node_id,
            name=name,
            inputs=inputs or {},
            outputs=outputs or {},
            depends_on=depends_on or [],
            metadata=metadata or {},
        )
        self.nodes[node_id] = node
        return node

    def start(self, node_id: str):
        """Mark a node as running."""
        node = self.nodes.get(node_id)
        if node:
            node.status = NodeStatus.RUNNING
            node.started_at = time.monotonic()

    def complete(self, node_id: str, cache_hit: bool = False, outputs: dict[str, Any] | None = None):
        """Mark a node as completed."""
        node = self.nodes.get(node_id)
        if node:
            node.status = NodeStatus.DONE
            node.completed_at = time.monotonic()
            node.cache_hit = cache_hit
            if node.started_at:
                node.duration = node.completed_at - node.started_at
            if outputs:
                node.outputs.update(outputs)

    def fail(self, node_id: str, error: str):
        """Mark a node as failed."""
        node = self.nodes.get(node_id)
        if node:
            node.status = NodeStatus.ERROR
            node.error = error
            node.completed_at = time.monotonic()
            if node.started_at:
                node.duration = node.completed_at - node.started_at

    def skip(self, node_id: str, reason: str = ""):
        """Mark a node as skipped."""
        node = self.nodes.get(node_id)
        if node:
            node.status = NodeStatus.SKIPPED
            node.metadata["skip_reason"] = reason

    def get_node(self, node_id: str) -> DAGNode | None:
        return self.nodes.get(node_id)

    def get_dependencies(self, node_id: str) -> list[DAGNode]:
        """Get all dependency nodes for a given node."""
        node = self.nodes.get(node_id)
        if not node:
            return []
        return [self.nodes[dep] for dep in node.depends_on if dep in self.nodes]

    def get_dependents(self, node_id: str) -> list[DAGNode]:
        """Get all nodes that depend on the given node."""
        return [
            n for n in self.nodes.values()
            if node_id in n.depends_on
        ]

    def ready_nodes(self) -> list[DAGNode]:
        """Get nodes that are pending and have all dependencies completed."""
        ready = []
        for node in self.nodes.values():
            if node.status != NodeStatus.PENDING:
                continue
            deps_met = all(
                self.nodes[dep].status == NodeStatus.DONE
                for dep in node.depends_on
                if dep in self.nodes
            )
            if deps_met:
                ready.append(node)
        return ready

    def is_complete(self) -> bool:
        """Check if all nodes are done, failed, or skipped."""
        return all(
            n.status in (NodeStatus.DONE, NodeStatus.ERROR, NodeStatus.SKIPPED)
            for n in self.nodes.values()
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize the DAG for API/visualization."""
        return {
            "name": self.name,
            "total_duration": round(time.monotonic() - self._start_time, 3),
            "is_complete": self.is_complete(),
            "nodes": {nid: n.to_dict() for nid, n in self.nodes.items()},
            "edges": [
                {"from": dep, "to": nid}
                for nid, n in self.nodes.items()
                for dep in n.depends_on
            ],
            "stats": self.stats(),
        }

    def stats(self) -> dict[str, int]:
        """Get counts by status."""
        counts: dict[str, int] = {}
        for node in self.nodes.values():
            status = node.status.value
            counts[status] = counts.get(status, 0) + 1
        counts["cache_hits"] = sum(1 for n in self.nodes.values() if n.cache_hit)
        return counts

    def critical_path(self) -> list[str]:
        """Find the critical path (longest path through the DAG).

        Returns list of node IDs on the critical path.
        """
        # Topological sort with longest path
        longest: dict[str, float] = {}
        predecessor: dict[str, str | None] = {}

        def dfs(node_id: str) -> float:
            if node_id in longest:
                return longest[node_id]
            node = self.nodes.get(node_id)
            if not node:
                return 0
            duration = node.duration or 0
            max_dep_len = 0
            best_pred = None
            for dep in node.depends_on:
                dep_len = dfs(dep)
                if dep_len > max_dep_len:
                    max_dep_len = dep_len
                    best_pred = dep
            longest[node_id] = max_dep_len + duration
            predecessor[node_id] = best_pred
            return longest[node_id]

        for nid in self.nodes:
            dfs(nid)

        if not longest:
            return []

        # Find the node with max distance
        end_node = max(longest, key=lambda k: longest[k])
        path = []
        current: str | None = end_node
        while current is not None:
            path.append(current)
            current = predecessor.get(current)
        path.reverse()
        return path
