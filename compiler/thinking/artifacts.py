"""Artifact Graph — Content-addressable dependency DAG for the video pipeline.

Models the video pipeline's intermediate products (script, shots, TTS audio,
timeline, render plan) as immutable artifacts in a dependency graph.

This bridges the video compiler (engine/bridge/) with the reactive runtime
(thinking/scheduler.py, thinking/runtime_graph.py):
  - Each pipeline stage produces a VideoArtifact
  - Artifacts declare dependencies on upstream artifacts
  - Content hashing enables memoization (same inputs → skip recompute)
  - The Scheduler queries the graph to determine what needs invalidation

Dependency DAG:
    script → shots ─────┐
    script → tts_audio ─┤
    shots  → timeline ←─┤
    tts_audio → timeline┤
    timeline → render_plan
    render_plan → video

Usage:
    graph = ArtifactGraph()
    script_art = graph.create(ArtifactType.SCRIPT, content={"sentences": [...]})
    tts_art = graph.create(ArtifactType.TTS_AUDIO, content={...}, depends_on=[script_art])
    # When script changes:
    graph.invalidate(script_art.id)
    # Check what needs recomputation:
    stale = graph.get_stale_artifacts()  # → [tts_art, timeline_art, ...]
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from compiler.thinking.canonicalize import canonicalize, content_hash as _content_hash


# ============================================================
# Artifact Types — pipeline stage identifiers
# ============================================================

class ArtifactType(str, Enum):
    """Stages of the video pipeline that produce artifacts."""
    SCRIPT = "script"               # LLM-generated narration sentences
    KNOWLEDGE_GRAPH = "knowledge_graph"  # Node/edge graph structure
    SHOTS = "shots"                 # Director plan → concrete shot params
    TTS_AUDIO = "tts_audio"         # Text-to-speech audio files (aggregator)
    TTS_SENTENCE = "tts_sentence"   # Per-sentence TTS audio (fine-grained)
    TIMELINE = "timeline"           # Merged multi-track timeline
    RENDER_PLAN = "render_plan"     # Final layout JSON for Remotion
    SCENE_IR = "scene_ir"           # Single scene intermediate representation
    SCENE_VIDEO = "scene_video"     # Rendered per-scene mp4
    VIDEO = "video"                 # Final composed video output


# Canonical dependency edges: downstream → upstream
ARTifact_DEPENDENCIES: dict[ArtifactType, list[ArtifactType]] = {
    ArtifactType.SCRIPT: [],                          # Root — no deps
    ArtifactType.KNOWLEDGE_GRAPH: [],                 # Root — no deps
    ArtifactType.SHOTS: [ArtifactType.SCRIPT, ArtifactType.KNOWLEDGE_GRAPH],
    ArtifactType.TTS_AUDIO: [ArtifactType.SCRIPT],
    ArtifactType.TTS_SENTENCE: [ArtifactType.SCRIPT],
    ArtifactType.TIMELINE: [ArtifactType.SHOTS, ArtifactType.TTS_SENTENCE],
    ArtifactType.RENDER_PLAN: [ArtifactType.TIMELINE, ArtifactType.KNOWLEDGE_GRAPH],
    ArtifactType.SCENE_IR: [ArtifactType.RENDER_PLAN],
    ArtifactType.SCENE_VIDEO: [ArtifactType.SCENE_IR],
    ArtifactType.VIDEO: [ArtifactType.SCENE_VIDEO],
}


# ============================================================
# Artifact Status
# ============================================================

class ArtifactStatus(str, Enum):
    """Lifecycle status of an artifact."""
    FRESH = "fresh"             # Computed, content matches inputs
    STALE = "stale"             # Upstream changed, needs recomputation
    COMPUTING = "computing"     # Currently being recomputed
    FAILED = "failed"           # Computation failed
    CACHED = "cached"           # Loaded from cache, not yet verified


# ============================================================
# Core Data Structures
# ============================================================

@dataclass
class VideoArtifact:
    """An immutable snapshot of a pipeline stage's output.

    Artifacts are content-addressable: two artifacts with the same content
    have the same hash, regardless of when they were created.
    """
    id: str = ""
    type: ArtifactType = ArtifactType.SCRIPT
    content: Any = None               # The actual data (dict, list, path, etc.)
    content_hash: str = ""            # SHA-256 prefix for dedup
    created_at: float = 0.0
    status: ArtifactStatus = ArtifactStatus.FRESH
    depends_on: list[str] = field(default_factory=list)  # Upstream artifact IDs
    metadata: dict = field(default_factory=dict)          # Arbitrary extra info
    error: Optional[str] = None       # Error message if FAILED
    version: int = 1                  # Increments on content change

    def __post_init__(self):
        if not self.id:
            self.id = f"art_{self.type.value}_{uuid.uuid4().hex[:8]}"
        if not self.created_at:
            self.created_at = time.time()
        if not self.content_hash and self.content is not None:
            self.content_hash = _content_hash(self.content)

    def with_content(self, content: Any, bump_version: bool = True) -> VideoArtifact:
        """Return a new artifact with updated content (immutable update)."""
        new_hash = _content_hash(content)
        if new_hash == self.content_hash:
            return self  # No change — return same instance
        return VideoArtifact(
            id=self.id,
            type=self.type,
            content=content,
            content_hash=new_hash,
            created_at=time.time(),
            status=ArtifactStatus.FRESH,
            depends_on=list(self.depends_on),
            metadata=dict(self.metadata),
            version=self.version + (1 if bump_version else 0),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict (content excluded — too large for JSON)."""
        return {
            "id": self.id,
            "type": self.type.value,
            "content_hash": self.content_hash,
            "created_at": self.created_at,
            "status": self.status.value,
            "depends_on": self.depends_on,
            "metadata": self.metadata,
            "error": self.error,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], content: Any = None) -> VideoArtifact:
        """Deserialize from dict. Content must be supplied separately."""
        return cls(
            id=data.get("id", ""),
            type=ArtifactType(data.get("type", "script")),
            content=content,
            content_hash=data.get("content_hash", ""),
            created_at=data.get("created_at", 0),
            status=ArtifactStatus(data.get("status", "fresh")),
            depends_on=data.get("depends_on", []),
            metadata=data.get("metadata", {}),
            error=data.get("error"),
            version=data.get("version", 1),
        )


@dataclass
class ArtifactRef:
    """Lightweight reference to an artifact (no content payload).

    Used for dependency declarations where you don't need the full artifact.
    """
    artifact_type: ArtifactType
    artifact_id: str

    def matches(self, artifact: VideoArtifact) -> bool:
        return artifact.type == self.artifact_type and artifact.id == self.artifact_id


# ============================================================
# Artifact Graph — the dependency DAG
# ============================================================

class ArtifactGraph:
    """Manages the dependency DAG between video pipeline artifacts.

    Core operations:
      - create(): register a new artifact
      - get(): retrieve by ID
      - invalidate(): mark stale + propagate to downstream
      - get_stale(): find all artifacts needing recomputation
      - resolve_order(): topological sort for recomputation
      - find_by_type(): get latest artifact of a given type
    """

    def __init__(self):
        self._artifacts: dict[str, VideoArtifact] = {}
        self._by_type: dict[ArtifactType, list[str]] = defaultdict(list)
        # Forward dependency: artifact_id → [downstream artifact_ids]
        self._downstream: dict[str, set[str]] = defaultdict(set)
        # Reverse dependency: artifact_id → [upstream artifact_ids]
        self._upstream: dict[str, set[str]] = defaultdict(set)

    # ── Create & Retrieve ──

    def create(
        self,
        artifact_type: ArtifactType,
        content: Any = None,
        depends_on: list[str] | list[VideoArtifact] | None = None,
        metadata: dict | None = None,
        artifact_id: str = "",
    ) -> VideoArtifact:
        """Register a new artifact in the graph.

        Args:
            artifact_type: Pipeline stage this artifact represents.
            content: The artifact's data payload.
            depends_on: Upstream artifact IDs or VideoArtifact objects.
            metadata: Arbitrary extra info (e.g. voice name, model used).
            artifact_id: Override auto-generated ID.

        Returns:
            The created VideoArtifact.
        """
        # Resolve depends_on to list of IDs
        dep_ids: list[str] = []
        if depends_on:
            for dep in depends_on:
                if isinstance(dep, VideoArtifact):
                    dep_ids.append(dep.id)
                else:
                    dep_ids.append(dep)

        artifact = VideoArtifact(
            id=artifact_id,
            type=artifact_type,
            content=content,
            depends_on=dep_ids,
            metadata=metadata or {},
        )

        self._artifacts[artifact.id] = artifact
        self._by_type[artifact_type].append(artifact.id)

        # Wire dependency edges
        for upstream_id in dep_ids:
            self._downstream[upstream_id].add(artifact.id)
            self._upstream[artifact.id].add(upstream_id)

        return artifact

    def get(self, artifact_id: str) -> VideoArtifact | None:
        """Retrieve an artifact by ID."""
        return self._artifacts.get(artifact_id)

    def find_by_type(self, artifact_type: ArtifactType) -> VideoArtifact | None:
        """Get the most recent artifact of a given type."""
        ids = self._by_type.get(artifact_type, [])
        if not ids:
            return None
        return self._artifacts.get(ids[-1])

    def find_all_by_type(self, artifact_type: ArtifactType) -> list[VideoArtifact]:
        """Get all artifacts of a given type, oldest first."""
        return [
            self._artifacts[aid]
            for aid in self._by_type.get(artifact_type, [])
            if aid in self._artifacts
        ]

    def all(self) -> list[VideoArtifact]:
        """Get all artifacts in insertion order."""
        return list(self._artifacts.values())

    # ── Invalidation ──

    def invalidate(self, artifact_id: str, reason: str = "") -> list[str]:
        """Mark an artifact and all its transitive downstream as stale.

        Returns list of all invalidated artifact IDs.
        """
        if artifact_id not in self._artifacts:
            return []

        invalidated: list[str] = []
        queue = [artifact_id]
        visited: set[str] = set()

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            art = self._artifacts.get(current)
            if art and art.status != ArtifactStatus.STALE:
                art.status = ArtifactStatus.STALE
                if reason:
                    art.metadata["stale_reason"] = reason
                invalidated.append(current)

            # Propagate to downstream
            for downstream_id in self._downstream.get(current, set()):
                if downstream_id not in visited:
                    queue.append(downstream_id)

        return invalidated

    def mark_fresh(self, artifact_id: str):
        """Mark an artifact as fresh (after recomputation)."""
        art = self._artifacts.get(artifact_id)
        if art:
            art.status = ArtifactStatus.FRESH
            art.metadata.pop("stale_reason", None)

    # ── Query ──

    def get_stale(self) -> list[VideoArtifact]:
        """Return all artifacts with STALE status, in topological order."""
        stale_ids = [
            aid for aid, art in self._artifacts.items()
            if art.status == ArtifactStatus.STALE
        ]
        return [self._artifacts[sid] for sid in self._resolve_order(stale_ids)]

    def get_fresh(self) -> list[VideoArtifact]:
        """Return all fresh artifacts."""
        return [
            art for art in self._artifacts.values()
            if art.status == ArtifactStatus.FRESH
        ]

    def is_upstream_fresh(self, artifact_id: str) -> bool:
        """Check if all upstream dependencies are fresh."""
        art = self._artifacts.get(artifact_id)
        if not art:
            return False
        for upstream_id in art.depends_on:
            upstream = self._artifacts.get(upstream_id)
            if not upstream or upstream.status != ArtifactStatus.FRESH:
                return False
        return True

    # ── Topological Sort ──

    def _resolve_order(self, artifact_ids: list[str]) -> list[str]:
        """Topological sort of given artifact IDs based on dependency edges."""
        # Build subgraph
        subgraph: dict[str, set[str]] = {}
        id_set = set(artifact_ids)
        for aid in artifact_ids:
            deps = self._upstream.get(aid, set()) & id_set
            subgraph[aid] = deps

        # Kahn's algorithm
        in_degree: dict[str, int] = {aid: len(subgraph[aid]) for aid in artifact_ids}
        queue = [aid for aid in artifact_ids if in_degree[aid] == 0]
        result: list[str] = []

        while queue:
            current = queue.pop(0)
            result.append(current)
            for aid in artifact_ids:
                if current in subgraph.get(aid, set()):
                    in_degree[aid] -= 1
                    if in_degree[aid] == 0:
                        queue.append(aid)

        return result

    def get_recompute_order(self) -> list[VideoArtifact]:
        """Get stale artifacts in the order they should be recomputed."""
        stale = self.get_stale()
        return stale  # Already topologically sorted by get_stale()

    # ── Dependency Queries ──

    def get_upstream(self, artifact_id: str) -> list[VideoArtifact]:
        """Get all direct upstream dependencies."""
        art = self._artifacts.get(artifact_id)
        if not art:
            return []
        return [
            self._artifacts[uid]
            for uid in art.depends_on
            if uid in self._artifacts
        ]

    def get_downstream(self, artifact_id: str) -> list[VideoArtifact]:
        """Get all direct downstream dependents."""
        return [
            self._artifacts[did]
            for did in self._downstream.get(artifact_id, set())
            if did in self._artifacts
        ]

    def get_transitive_downstream(self, artifact_id: str) -> list[VideoArtifact]:
        """Get all transitive downstream dependents (BFS)."""
        visited: set[str] = set()
        queue = [artifact_id]
        result: list[VideoArtifact] = []

        while queue:
            current = queue.pop(0)
            for did in self._downstream.get(current, set()):
                if did not in visited:
                    visited.add(did)
                    if did in self._artifacts:
                        result.append(self._artifacts[did])
                    queue.append(did)

        return result

    # ── Summary & Serialization ──

    def summary(self) -> dict[str, Any]:
        """Human-readable summary of the artifact graph."""
        by_status = defaultdict(int)
        for art in self._artifacts.values():
            by_status[art.status.value] += 1

        return {
            "total_artifacts": len(self._artifacts),
            "by_type": {
                t.value: len(ids)
                for t, ids in self._by_type.items()
                if ids
            },
            "by_status": dict(by_status),
            "stale_count": by_status.get("stale", 0),
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialize the graph (artifacts without content payloads)."""
        return {
            "artifacts": [art.to_dict() for art in self._artifacts.values()],
            "edges": [
                {"from": uid, "to": did}
                for uid, downstreams in self._downstream.items()
                for did in downstreams
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], content_map: dict[str, Any] | None = None) -> ArtifactGraph:
        """Deserialize a graph. Content must be supplied via content_map."""
        graph = cls()
        content_map = content_map or {}

        for art_data in data.get("artifacts", []):
            art_id = art_data["id"]
            content = content_map.get(art_id)
            artifact = VideoArtifact.from_dict(art_data, content=content)
            graph._artifacts[artifact.id] = artifact
            graph._by_type[artifact.type].append(artifact.id)

        for edge in data.get("edges", []):
            graph._downstream[edge["from"]].add(edge["to"])
            graph._upstream[edge["to"]].add(edge["from"])

        return graph

    def __len__(self) -> int:
        return len(self._artifacts)

    def __contains__(self, artifact_id: str) -> bool:
        return artifact_id in self._artifacts

    def __repr__(self) -> str:
        s = self.summary()
        return (
            f"ArtifactGraph({s['total_artifacts']} artifacts, "
            f"{s['stale_count']} stale)"
        )


# ============================================================
# Patch Operations for Artifact Manipulation
# ============================================================

@dataclass
class UpdateArtifactPatch:
    """Patch that updates an artifact's content and invalidates downstream.

    This is the bridge between the Patch System and the Artifact Graph:
    when a pipeline stage produces new output, it emits this patch.
    """
    id: str = ""
    timestamp: float = 0.0
    target: str = ""                    # artifact_id
    description: str = ""
    artifact_type: ArtifactType = ArtifactType.SCRIPT
    new_content: Any = None
    new_metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = f"patch_art_{uuid.uuid4().hex[:10]}"
        if not self.timestamp:
            self.timestamp = time.time()
        if not self.target:
            self.target = f"artifact:{self.artifact_type.value}"

    def apply_to_graph(self, graph: ArtifactGraph) -> list[str]:
        """Apply this patch to the artifact graph.

        Returns list of invalidated artifact IDs.
        """
        existing = graph.find_by_type(self.artifact_type)

        if existing:
            # Update existing artifact (immutable — returns new instance)
            new_art = existing.with_content(self.new_content)
            new_art.metadata.update(self.new_metadata)
            graph._artifacts[new_art.id] = new_art
            # Replace in _by_type
            type_ids = graph._by_type[self.artifact_type]
            idx = type_ids.index(existing.id) if existing.id in type_ids else -1
            if idx >= 0:
                type_ids[idx] = new_art.id
            return graph.invalidate(new_art.id, reason=f"content_update:{self.id}")
        else:
            # Create new artifact
            new_art = graph.create(
                artifact_type=self.artifact_type,
                content=self.new_content,
                metadata=self.new_metadata,
            )
            return [new_art.id]
