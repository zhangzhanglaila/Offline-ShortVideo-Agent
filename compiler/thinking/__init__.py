"""Thinking module — Interactive Video Agent Runtime.

Transforms the system from a one-shot pipeline into a human-in-the-loop
collaborative video creation environment.
"""

from compiler.thinking.state import (
    VideoProjectState,
    ModuleState,
    ScriptSentence,
    GraphSpec,
    GraphNode,
    GraphEdge,
    ShotPlan,
    AudioTrack,
    RenderJob,
    AgentAction,
    ThinkingPhase,
    Clip,
    Track,
    Timeline,
    ConstraintType,
    Constraint,
    Anchor,
)

from compiler.thinking.session import ThinkingSession

from compiler.thinking.agent_loop import ThinkingAgent

from compiler.thinking.event_bus import EventBus, Event, get_event_bus

from compiler.thinking.patch import (
    PatchOperation, PatchHistory,
    EditSentencePatch, AddSentencePatch, RemoveSentencePatch,
    ApproveModulePatch, EditGraphNodePatch, BatchPatch,
)

from compiler.thinking.branch import (
    BranchManager, MergeEngine, MergeConflict, MergeResult,
    ConflictType, ResolutionStrategy,
)

from compiler.thinking.ir_layers import (
    SemanticIR, TemporalIR, RenderIR, ExecutionIR,
    IRTransformer, DeterminismConfig, DeterminismChecker,
)

from compiler.thinking.artifacts import (
    ArtifactType, ArtifactStatus,
    VideoArtifact, ArtifactRef, ArtifactGraph,
    UpdateArtifactPatch, ARTifact_DEPENDENCIES,
)

from compiler.thinking.video_runtime_adapter import VideoRuntimeAdapter, PipelineNode
from compiler.thinking.inspector import inspect_all, print_dag_tree, print_artifact_table, print_invalidation_trace

__all__ = [
    "VideoProjectState",
    "ModuleState",
    "ScriptSentence",
    "GraphSpec",
    "GraphNode",
    "GraphEdge",
    "ShotPlan",
    "AudioTrack",
    "RenderJob",
    "AgentAction",
    "ThinkingPhase",
    "Clip",
    "Track",
    "Timeline",
    "ConstraintType",
    "Constraint",
    "Anchor",
    "ThinkingSession",
    "ThinkingAgent",
    "EventBus",
    "Event",
    "get_event_bus",
    "PatchOperation",
    "PatchHistory",
    "EditSentencePatch",
    "AddSentencePatch",
    "RemoveSentencePatch",
    "ApproveModulePatch",
    "EditGraphNodePatch",
    "BatchPatch",
    # Branch & Merge
    "BranchManager",
    "MergeEngine",
    "MergeConflict",
    "MergeResult",
    "ConflictType",
    "ResolutionStrategy",
    # IR Layers
    "SemanticIR",
    "TemporalIR",
    "RenderIR",
    "ExecutionIR",
    "IRTransformer",
    "DeterminismConfig",
    "DeterminismChecker",
    # Artifact Graph
    "ArtifactType",
    "ArtifactStatus",
    "VideoArtifact",
    "ArtifactRef",
    "ArtifactGraph",
    "UpdateArtifactPatch",
    # Video Runtime Adapter
    "VideoRuntimeAdapter",
    "PipelineNode",
    # Inspector
    "inspect_all",
]
