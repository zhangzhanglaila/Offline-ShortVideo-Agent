"""Video Project State — the editable IR (Intermediate Representation).

This is the central data structure of the Thinking system.
Both LLM and user modify it. Renderer consumes it.
Director analyzes it. It is the "AST" of video engineering.

Design principles:
  - Every field is independently editable
  - Every mutation is tracked (AgentAction history)
  - State is serializable to JSON for persistence
  - Partial updates are supported (patch one sentence, not regenerate all)
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class ThinkingPhase(str, Enum):
    """Phases of the interactive thinking workflow."""
    IDLE = "idle"
    ANALYZING = "analyzing"           # LLM analyzing the topic
    OUTLINE_READY = "outline_ready"   # Outline presented to user
    SCRIPT_GENERATING = "script_generating"
    SCRIPT_READY = "script_ready"     # Script presented for review
    GRAPH_GENERATING = "graph_generating"
    GRAPH_READY = "graph_ready"       # Graph presented for review
    SHOT_PLANNING = "shot_planning"
    SHOTS_READY = "shots_ready"       # Shot plan presented for review
    AUDIO_GENERATING = "audio_generating"
    AUDIO_READY = "audio_ready"
    CONFIRMED = "confirmed"           # User confirmed, ready to render
    RENDERING = "rendering"
    RENDERED = "rendered"
    ERROR = "error"


@dataclass
class ScriptSentence:
    """A single sentence in the narration script."""
    id: str = ""
    index: int = 0
    text: str = ""
    # Metadata from LLM reasoning
    purpose: str = ""       # e.g., "definition", "comparison", "example"
    key_concept: str = ""   # e.g., "O(1) access"
    # Editable flags
    is_user_edited: bool = False
    is_approved: bool = False

    def __post_init__(self):
        if not self.id:
            self.id = f"s_{uuid.uuid4().hex[:8]}"


@dataclass
class GraphNode:
    """A node in the knowledge graph."""
    id: str = ""
    label: str = ""
    role: str = ""          # core, storage, processor, result, etc.
    x: float = 0
    y: float = 0
    width: float = 200
    height: float = 80
    is_user_edited: bool = False


@dataclass
class GraphEdge:
    """An edge in the knowledge graph."""
    id: str = ""
    from_node: str = ""
    to_node: str = ""
    label: str = ""
    kind: str = ""          # impl, uses, has, type, etc.
    is_user_edited: bool = False


@dataclass
class GraphSpec:
    """A complete knowledge graph specification."""
    title: str = ""
    summary: str = ""
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    layout_type: str = "auto"   # auto, tree, circular, grid

    def to_pipeline_format(self) -> dict:
        """Convert to the format expected by graph_pipeline functions."""
        return {
            "title": self.title,
            "summary": self.summary,
            "nodes": [asdict(n) for n in self.nodes],
            "edges": [asdict(e) for e in self.edges],
        }


@dataclass
class ShotPlan:
    """A single shot/beat in the animation plan."""
    id: str = ""
    start: int = 0
    duration: int = 0
    shot_type: str = ""     # hero, ensemble, flow, pulse, pan, finale
    camera: str = ""        # zoom_in, pan_left, static, etc.
    focus_node: str = ""
    text: str = ""
    is_user_edited: bool = False


@dataclass
class AudioTrack:
    """A single audio track (TTS output)."""
    id: str = ""
    src: str = ""
    start: int = 0
    duration: int = 0
    text: str = ""
    sentence_id: str = ""   # links back to ScriptSentence.id


@dataclass
class RenderJob:
    """A render job status."""
    job_id: str = ""
    status: str = "pending"     # pending, rendering, done, failed
    progress: float = 0.0
    output_path: str = ""
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0


@dataclass
class Clip:
    """A single clip/span on a timeline track.

    Represents a contiguous region of content: a subtitle, an audio segment,
    a visual element, a camera move, an animation, or a transition.
    """
    id: str = ""
    track_type: str = ""    # audio, subtitle, visual, camera, animation, transition
    start: int = 0          # start frame
    duration: int = 0       # duration in frames
    # Content (depends on track_type)
    text: str = ""          # subtitle text, or element label
    src: str = ""           # audio file path, image URL, etc.
    # Visual properties
    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0
    opacity: float = 1.0
    # Camera/animation
    camera_type: str = ""   # zoom_in, pan_left, static, etc.
    animation_type: str = ""  # fade_in, slide_up, blur_in, etc.
    # Linking
    sentence_id: str = ""   # links back to ScriptSentence
    node_id: str = ""       # links to GraphNode
    # Metadata
    is_user_edited: bool = False
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = f"clip_{uuid.uuid4().hex[:8]}"

    def end(self) -> int:
        return self.start + self.duration

    def overlaps(self, other: Clip) -> bool:
        return self.start < other.end() and other.start < self.end()


@dataclass
class Track:
    """A single track on the timeline.

    Tracks are typed containers for clips:
      - audio: TTS narration segments
      - subtitle: text overlays synced to audio
      - visual: graph scenes, card pages, hook screens
      - camera: zoom/pan/tilt instructions
      - animation: node highlights, particle effects
      - transition: crossfades, wipes between scenes
    """
    id: str = ""
    track_type: str = ""    # audio, subtitle, visual, camera, animation, transition
    label: str = ""
    clips: list[Clip] = field(default_factory=list)
    muted: bool = False
    locked: bool = False

    def __post_init__(self):
        if not self.id:
            self.id = f"track_{uuid.uuid4().hex[:8]}"

    def add_clip(self, clip: Clip) -> Clip:
        """Add a clip and keep clips sorted by start time."""
        self.clips.append(clip)
        self.clips.sort(key=lambda c: c.start)
        return clip

    def remove_clip(self, clip_id: str) -> bool:
        before = len(self.clips)
        self.clips = [c for c in self.clips if c.id != clip_id]
        return len(self.clips) < before

    def get_clip(self, clip_id: str) -> Optional[Clip]:
        for c in self.clips:
            if c.id == clip_id:
                return c
        return None

    def duration(self) -> int:
        """Total duration: end of last clip."""
        if not self.clips:
            return 0
        return max(c.end() for c in self.clips)

    def clips_in_range(self, start: int, end: int) -> list[Clip]:
        """Get all clips that overlap with [start, end)."""
        return [c for c in self.clips if c.start < end and c.end() > start]


class ConstraintType(str, Enum):
    """Types of temporal constraints between clips."""
    FOLLOWS = "follows"           # B starts after A ends (or at A's position)
    ALIGNS_START = "aligns_start" # B starts when A starts
    ALIGNS_END = "aligns_end"     # B ends when A ends
    SYNCED = "synced"             # B mirrors A's timing exactly
    OFFSET = "offset"             # B starts at A.start + offset
    BOUNDED_BY = "bounded_by"     # B is contained within A's time range


class SemanticAnchorType(str, Enum):
    """Semantic types for anchors — what the anchor means."""
    # Content semantics
    HOOK = "hook"                   # Attention-grabbing opening
    IMPORTANT_TERM = "important_term"  # Key concept/keyword
    DEFINITION = "definition"       # Formal definition moment
    EXAMPLE = "example"             # Illustrative example
    COMPARISON = "comparison"       # Contrast/comparison moment
    CONCEPT_TRANSITION = "concept_transition"  # Transition between ideas

    # Emotional/rhetorical semantics
    SURPRISE = "surprise"           # Unexpected reveal
    EMPHASIS = "emphasis"           # Speaker stresses this point
    PAUSE = "pause"                 # Dramatic pause
    QUESTION = "question"           # Rhetorical question
    CLIMAX = "climax"               # Peak of explanation

    # Visual semantics
    REVEAL = "reveal"               # Visual element appears
    ZOOM_TARGET = "zoom_target"     # Camera zooms here
    FOCUS_SHIFT = "focus_shift"     # Attention moves here

    # Audio semantics
    MUSIC_DROP = "music_drop"       # Music beat/drop
    SFX_TRIGGER = "sfx_trigger"     # Sound effect point
    VOICE_EMPHASIS = "voice_emphasis"  # Speaker's vocal emphasis


@dataclass
class Anchor:
    """A named point in time that constraints can reference.

    Anchors are the glue between tracks:
      - An audio clip's "emphasis" anchor marks where the speaker stresses a word
      - A camera clip syncs to that anchor
      - An animation triggers at that anchor

    Semantic anchors carry meaning beyond position:
      - "surprise" → camera zooms in, music drops, subtitle bold
      - "important_term" → highlight animation, pause, camera hold
      - "concept_transition" → scene change, camera pan, music shift
    """
    id: str = ""
    name: str = ""          # e.g. "emphasis", "hook", "reveal", "pause"
    clip_id: str = ""       # which clip this anchor belongs to
    frame: int = 0          # absolute frame position
    relative_pos: float = 0.5  # 0.0=start, 1.0=end, 0.5=middle
    # Semantic enrichment
    semantic_type: str = "" # SemanticAnchorType value
    confidence: float = 1.0  # How confident we are in this anchor (0-1)
    source: str = ""        # "llm", "rule", "user", "prosody"
    metadata: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = f"anchor_{uuid.uuid4().hex[:8]}"


@dataclass
class Constraint:
    """A temporal constraint between two clips or anchors.

    Constraints form a dependency graph that the timeline resolver
    uses to compute final clip positions.

    Examples:
      - "subtitle follows audio" (subtitle starts when audio starts)
      - "camera zoom synced with emphasis" (camera moves at emphasis point)
      - "animation bounded by visual clip" (animation within scene duration)
    """
    id: str = ""
    constraint_type: ConstraintType = ConstraintType.FOLLOWS
    source_clip_id: str = ""    # the clip that defines the timing
    target_clip_id: str = ""    # the clip that follows the timing
    source_anchor: str = ""     # optional: anchor name on source
    target_anchor: str = ""     # optional: anchor name on target
    offset_frames: int = 0      # for OFFSET type
    label: str = ""             # human-readable description
    is_active: bool = True

    def __post_init__(self):
        if not self.id:
            self.id = f"constraint_{uuid.uuid4().hex[:8]}"


@dataclass
class Timeline:
    """Multi-track timeline — the temporal IR for a video module.

    This is the bridge between the editable state and the renderer:
      - LLM fills tracks during generation
      - User edits clips (shift, resize, reorder)
      - Renderer reads tracks to produce Remotion layout JSON

    Track types:
      audio:      TTS narration segments
      subtitle:   text overlays (synced to audio)
      visual:     graph scenes, card pages, hook screens
      camera:     zoom/pan/tilt camera instructions
      animation:  node highlights, particle flows, pulses
      transition: crossfades, wipes between scenes
    """
    id: str = ""
    fps: int = 30
    tracks: list[Track] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    anchors: list[Anchor] = field(default_factory=list)

    def __post_init__(self):
        if not self.id:
            self.id = f"tl_{uuid.uuid4().hex[:8]}"

    def get_track(self, track_type: str) -> Optional[Track]:
        """Get the first track of a given type."""
        for t in self.tracks:
            if t.track_type == track_type:
                return t
        return None

    def get_or_create_track(self, track_type: str, label: str = "") -> Track:
        """Get or create a track of the given type."""
        track = self.get_track(track_type)
        if not track:
            track = Track(track_type=track_type, label=label or track_type)
            self.tracks.append(track)
        return track

    def duration_frames(self) -> int:
        """Total duration across all tracks."""
        if not self.tracks:
            return 0
        return max(t.duration() for t in self.tracks)

    def duration_seconds(self) -> float:
        return self.duration_frames() / self.fps if self.fps else 0

    # ── Constraint Management ──

    def add_constraint(self, constraint_type: ConstraintType,
                       source_clip_id: str, target_clip_id: str,
                       source_anchor: str = "", target_anchor: str = "",
                       offset_frames: int = 0, label: str = "") -> Constraint:
        """Add a temporal constraint between clips."""
        c = Constraint(
            constraint_type=constraint_type,
            source_clip_id=source_clip_id,
            target_clip_id=target_clip_id,
            source_anchor=source_anchor,
            target_anchor=target_anchor,
            offset_frames=offset_frames,
            label=label,
        )
        self.constraints.append(c)
        return c

    def add_anchor(self, clip_id: str, name: str,
                   relative_pos: float = 0.5,
                   semantic_type: str = "",
                   confidence: float = 1.0,
                   source: str = "") -> Anchor:
        """Add an anchor point to a clip.

        Args:
            clip_id: Which clip this anchor belongs to
            name: Human-readable name (e.g. "emphasis", "hook")
            relative_pos: 0.0=start, 0.5=middle, 1.0=end
            semantic_type: SemanticAnchorType value for semantic binding
            confidence: How confident we are (0-1)
            source: Where this anchor came from ("llm", "rule", "user", "prosody")
        """
        clip = self.find_clip(clip_id)
        frame = 0
        if clip:
            frame = clip.start + int(clip.duration * relative_pos)
        a = Anchor(
            clip_id=clip_id, name=name,
            frame=frame, relative_pos=relative_pos,
            semantic_type=semantic_type, confidence=confidence, source=source,
        )
        self.anchors.append(a)
        return a

    def add_semantic_anchor(self, clip_id: str, semantic_type: str,
                            relative_pos: float = 0.5,
                            confidence: float = 1.0,
                            source: str = "rule") -> Anchor:
        """Add a semantic anchor — the semantic_type IS the name."""
        return self.add_anchor(
            clip_id, name=semantic_type,
            relative_pos=relative_pos,
            semantic_type=semantic_type,
            confidence=confidence, source=source,
        )

    def get_semantic_anchors(self, semantic_type: str) -> list[Anchor]:
        """Get all anchors of a given semantic type."""
        return [a for a in self.anchors if a.semantic_type == semantic_type]

    def bind_to_semantic(self, target_clip_id: str, semantic_type: str,
                         source_clip_id: str = "",
                         constraint_type: ConstraintType = ConstraintType.OFFSET,
                         offset_frames: int = 0) -> Optional[Constraint]:
        """Bind a clip's timing to a semantic anchor.

        Example: bind camera zoom to "surprise" anchor
        """
        # Find the anchor
        anchors = self.get_semantic_anchors(semantic_type)
        if not anchors:
            return None

        anchor = anchors[0]  # Use first matching anchor

        # If no source clip specified, use the anchor's clip
        if not source_clip_id:
            source_clip_id = anchor.clip_id

        return self.add_constraint(
            constraint_type=constraint_type,
            source_clip_id=source_clip_id,
            target_clip_id=target_clip_id,
            source_anchor=semantic_type,
            offset_frames=offset_frames,
            label=f"绑定到语义锚点: {semantic_type}",
        )

    def find_clip(self, clip_id: str) -> Optional[Clip]:
        """Find a clip by ID across all tracks."""
        for track in self.tracks:
            for clip in track.clips:
                if clip.id == clip_id:
                    return clip
        return None

    def get_anchor(self, clip_id: str, name: str) -> Optional[Anchor]:
        """Get an anchor by clip ID and name."""
        for a in self.anchors:
            if a.clip_id == clip_id and a.name == name:
                return a
        return None

    def resolve_constraints(self):
        """Resolve all constraints to compute final clip positions.

        This is the constraint solver — it iterates through constraints
        and adjusts clip timing to satisfy all dependencies.

        Order of resolution:
          1. SYNCED constraints (exact copy)
          2. ALIGNS_START / ALIGNS_END (align edges)
          3. FOLLOWS (sequential ordering)
          4. OFFSET (shifted alignment)
          5. BOUNDED_BY (containment)
        """
        # Build clip index
        clip_map: dict[str, Clip] = {}
        for track in self.tracks:
            for clip in track.clips:
                clip_map[clip.id] = clip

        # Sort constraints by priority
        priority = {
            ConstraintType.SYNCED: 0,
            ConstraintType.ALIGNS_START: 1,
            ConstraintType.ALIGNS_END: 2,
            ConstraintType.FOLLOWS: 3,
            ConstraintType.OFFSET: 4,
            ConstraintType.BOUNDED_BY: 5,
        }
        sorted_constraints = sorted(
            [c for c in self.constraints if c.is_active],
            key=lambda c: priority.get(c.constraint_type, 99),
        )

        # Resolve each constraint
        for constraint in sorted_constraints:
            source = clip_map.get(constraint.source_clip_id)
            target = clip_map.get(constraint.target_clip_id)
            if not source or not target:
                continue

            if constraint.constraint_type == ConstraintType.SYNCED:
                target.start = source.start
                target.duration = source.duration

            elif constraint.constraint_type == ConstraintType.ALIGNS_START:
                target.start = source.start

            elif constraint.constraint_type == ConstraintType.ALIGNS_END:
                target.start = source.end() - target.duration

            elif constraint.constraint_type == ConstraintType.FOLLOWS:
                target.start = source.end()

            elif constraint.constraint_type == ConstraintType.OFFSET:
                # Check for anchors
                if constraint.source_anchor:
                    anchor = self.get_anchor(constraint.source_clip_id, constraint.source_anchor)
                    if anchor:
                        target.start = anchor.frame + constraint.offset_frames
                    else:
                        target.start = source.start + constraint.offset_frames
                else:
                    target.start = source.start + constraint.offset_frames

            elif constraint.constraint_type == ConstraintType.BOUNDED_BY:
                if target.start < source.start:
                    target.start = source.start
                if target.end() > source.end():
                    target.duration = source.end() - target.start

    def build_default_constraints(self):
        """Build standard constraints for a typical video module.

        Standard relationships:
          - subtitle follows audio (same timing)
          - camera synced with visual (bounded within scene)
          - animation follows audio emphasis
        """
        audio_track = self.get_track("audio")
        subtitle_track = self.get_track("subtitle")
        visual_track = self.get_track("visual")
        camera_track = self.get_track("camera")
        animation_track = self.get_track("animation")

        if audio_track and subtitle_track:
            # Subtitle clips follow audio clips (same sentence_id)
            for sub_clip in subtitle_track.clips:
                for audio_clip in audio_track.clips:
                    if sub_clip.sentence_id and sub_clip.sentence_id == audio_clip.sentence_id:
                        self.add_constraint(
                            ConstraintType.SYNCED,
                            audio_clip.id, sub_clip.id,
                            label=f"字幕跟随音频: {sub_clip.text[:15]}...",
                        )
                        break

        if visual_track and camera_track:
            # Camera bounded by visual scene
            for cam_clip in camera_track.clips:
                for vis_clip in visual_track.clips:
                    if cam_clip.start >= vis_clip.start and cam_clip.end() <= vis_clip.end():
                        self.add_constraint(
                            ConstraintType.BOUNDED_BY,
                            vis_clip.id, cam_clip.id,
                            label="镜头在画面范围内",
                        )
                        break

    # ── Serialization ──

    def to_dict(self) -> dict:
        """Serialize to dict for JSON persistence."""
        return {
            "id": self.id,
            "fps": self.fps,
            "tracks": [
                {
                    "id": t.id, "track_type": t.track_type, "label": t.label,
                    "muted": t.muted, "locked": t.locked,
                    "clips": [
                        {
                            "id": c.id, "track_type": c.track_type,
                            "start": c.start, "duration": c.duration,
                            "text": c.text, "src": c.src,
                            "x": c.x, "y": c.y, "width": c.width, "height": c.height,
                            "opacity": c.opacity, "camera_type": c.camera_type,
                            "animation_type": c.animation_type,
                            "sentence_id": c.sentence_id, "node_id": c.node_id,
                        }
                        for c in t.clips
                    ],
                }
                for t in self.tracks
            ],
            "constraints": [
                {
                    "id": c.id, "type": c.constraint_type.value,
                    "source": c.source_clip_id, "target": c.target_clip_id,
                    "source_anchor": c.source_anchor, "target_anchor": c.target_anchor,
                    "offset": c.offset_frames, "label": c.label,
                }
                for c in self.constraints
            ],
            "anchors": [
                {
                    "id": a.id, "name": a.name, "clip_id": a.clip_id,
                    "frame": a.frame, "relative_pos": a.relative_pos,
                    "semantic_type": a.semantic_type,
                    "confidence": a.confidence, "source": a.source,
                }
                for a in self.anchors
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Timeline:
        """Deserialize from dict."""
        tracks = []
        for t_data in data.get("tracks", []):
            clips = [Clip(**c) for c in t_data.get("clips", [])]
            tracks.append(Track(
                id=t_data.get("id", ""),
                track_type=t_data.get("track_type", ""),
                label=t_data.get("label", ""),
                muted=t_data.get("muted", False),
                locked=t_data.get("locked", False),
                clips=clips,
            ))

        constraints = []
        for c_data in data.get("constraints", []):
            constraints.append(Constraint(
                id=c_data.get("id", ""),
                constraint_type=ConstraintType(c_data.get("type", "follows")),
                source_clip_id=c_data.get("source", ""),
                target_clip_id=c_data.get("target", ""),
                source_anchor=c_data.get("source_anchor", ""),
                target_anchor=c_data.get("target_anchor", ""),
                offset_frames=c_data.get("offset", 0),
                label=c_data.get("label", ""),
            ))

        anchors = []
        for a_data in data.get("anchors", []):
            anchors.append(Anchor(
                id=a_data.get("id", ""),
                name=a_data.get("name", ""),
                clip_id=a_data.get("clip_id", ""),
                frame=a_data.get("frame", 0),
                relative_pos=a_data.get("relative_pos", 0.5),
                semantic_type=a_data.get("semantic_type", ""),
                confidence=a_data.get("confidence", 1.0),
                source=a_data.get("source", ""),
            ))

        return cls(
            id=data.get("id", ""),
            fps=data.get("fps", 30),
            tracks=tracks,
            constraints=constraints,
            anchors=anchors,
        )


@dataclass
class ModuleState:
    """State of a single topic module (e.g., "线性表")."""
    id: str = ""
    title: str = ""
    index: int = 0
    status: str = "pending"     # pending, generating, ready, approved, rendering, done

    # Script
    script: list[ScriptSentence] = field(default_factory=list)
    script_approved: bool = False

    # Graphs (typically 2 per module: overview + detail)
    graph_a: Optional[GraphSpec] = None
    graph_b: Optional[GraphSpec] = None
    graphs_approved: bool = False

    # Cards (summary pages)
    cards_a_title: str = ""
    cards_a_items: list[str] = field(default_factory=list)
    cards_b_title: str = ""
    cards_b_items: list[str] = field(default_factory=list)
    cards_approved: bool = False

    # Shot plan
    shots: list[ShotPlan] = field(default_factory=list)
    shots_approved: bool = False

    # Audio
    audio_tracks: list[AudioTrack] = field(default_factory=list)
    audio_approved: bool = False

    # Timeline (multi-track temporal IR)
    timeline: Optional[Timeline] = None
    timeline_approved: bool = False

    # Render
    render_job: Optional[RenderJob] = None
    output_path: str = ""

    # LLM reasoning (shown to user as "thinking")
    thinking_log: list[str] = field(default_factory=list)

    def get_script_text(self) -> list[str]:
        """Get script as plain text list."""
        return [s.text for s in self.script if s.text]

    def is_ready_for_render(self) -> bool:
        """Check if all components are approved."""
        return (
            self.script_approved
            and self.graphs_approved
            and self.audio_approved
        )


@dataclass
class AgentAction:
    """A recorded action in the thinking session history."""
    id: str = ""
    timestamp: float = 0.0
    action_type: str = ""       # generate, edit, approve, regenerate, interrupt
    target: str = ""            # e.g., "module_0.script", "module_1.graph_a"
    description: str = ""       # Human-readable description
    data_before: Any = None     # Snapshot before action (for undo)
    data_after: Any = None      # Snapshot after action
    is_user_action: bool = False

    def __post_init__(self):
        if not self.id:
            self.id = f"act_{uuid.uuid4().hex[:8]}"
        if not self.timestamp:
            self.timestamp = time.time()


@dataclass
class VideoProjectState:
    """The complete editable state of a video project.

    This is the "IR" — the single source of truth.
    LLM modifies it. User modifies it. Renderer consumes it.
    """
    # Identity
    session_id: str = ""
    topic: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    # Global settings
    width: int = 1920
    height: int = 1080
    fps: int = 30
    voice: str = "zh-CN-YunxiNeural"
    background: str = "#070b10"

    # Phase tracking
    phase: ThinkingPhase = ThinkingPhase.IDLE
    current_module_index: int = -1

    # Modules (the core content)
    modules: list[ModuleState] = field(default_factory=list)

    # Global elements (e.g., intro, outro)
    intro_text: str = ""
    intro_audio: Optional[AudioTrack] = None
    outro_text: str = ""

    # Director notes (LLM reasoning about overall structure)
    director_notes: list[str] = field(default_factory=list)

    # User feedback history
    user_feedback: list[str] = field(default_factory=list)

    # Render jobs
    render_jobs: list[RenderJob] = field(default_factory=list)

    # Full output
    final_video_path: str = ""

    def __post_init__(self):
        if not self.session_id:
            self.session_id = f"sess_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = time.time()
        self.updated_at = time.time()

    # ── Mutation helpers ──

    def touch(self):
        """Update the updated_at timestamp."""
        self.updated_at = time.time()

    def get_module(self, module_id: str) -> Optional[ModuleState]:
        """Find a module by ID."""
        for m in self.modules:
            if m.id == module_id:
                return m
        return None

    def get_current_module(self) -> Optional[ModuleState]:
        """Get the module currently being worked on."""
        if 0 <= self.current_module_index < len(self.modules):
            return self.modules[self.current_module_index]
        return None

    def update_sentence(self, module_id: str, sentence_id: str, new_text: str) -> bool:
        """Update a single sentence. Returns True if found."""
        module = self.get_module(module_id)
        if not module:
            return False
        for s in module.script:
            if s.id == sentence_id:
                s.text = new_text
                s.is_user_edited = True
                self.touch()
                return True
        return False

    def add_sentence(self, module_id: str, text: str, index: int = -1) -> Optional[ScriptSentence]:
        """Add a new sentence to a module's script."""
        module = self.get_module(module_id)
        if not module:
            return None
        sentence = ScriptSentence(text=text, is_user_edited=True)
        if index < 0 or index >= len(module.script):
            module.script.append(sentence)
        else:
            module.script.insert(index, sentence)
        # Re-index
        for i, s in enumerate(module.script):
            s.index = i
        self.touch()
        return sentence

    def remove_sentence(self, module_id: str, sentence_id: str) -> bool:
        """Remove a sentence from a module's script."""
        module = self.get_module(module_id)
        if not module:
            return False
        before = len(module.script)
        module.script = [s for s in module.script if s.id != sentence_id]
        if len(module.script) < before:
            for i, s in enumerate(module.script):
                s.index = i
            self.touch()
            return True
        return False

    def update_graph_node(self, module_id: str, graph_key: str, node_id: str,
                          label: str = None, role: str = None) -> bool:
        """Update a single graph node."""
        module = self.get_module(module_id)
        if not module:
            return False
        graph = module.graph_a if graph_key == "a" else module.graph_b
        if not graph:
            return False
        for n in graph.nodes:
            if n.id == node_id:
                if label is not None:
                    n.label = label
                if role is not None:
                    n.role = role
                n.is_user_edited = True
                self.touch()
                return True
        return False

    def add_graph_node(self, module_id: str, graph_key: str, label: str,
                       role: str = "", connect_to: str = "") -> Optional[GraphNode]:
        """Add a new node to a graph."""
        module = self.get_module(module_id)
        if not module:
            return None
        graph = module.graph_a if graph_key == "a" else module.graph_b
        if not graph:
            return None
        node = GraphNode(
            id=f"n_{uuid.uuid4().hex[:6]}",
            label=label,
            role=role,
            is_user_edited=True,
        )
        graph.nodes.append(node)
        if connect_to:
            edge = GraphEdge(
                id=f"e_{uuid.uuid4().hex[:6]}",
                from_node=connect_to,
                to_node=node.id,
                label="",
                is_user_edited=True,
            )
            graph.edges.append(edge)
        self.touch()
        return node

    def approve_module(self, module_id: str, component: str = "all") -> bool:
        """Approve a module's component (script/graphs/audio/all)."""
        module = self.get_module(module_id)
        if not module:
            return False
        if component in ("script", "all"):
            module.script_approved = True
            for s in module.script:
                s.is_approved = True
        if component in ("graphs", "all"):
            module.graphs_approved = True
        if component in ("cards", "all"):
            module.cards_approved = True
        if component in ("shots", "all"):
            module.shots_approved = True
        if component in ("audio", "all"):
            module.audio_approved = True
        if component == "all":
            module.status = "approved"
        self.touch()
        return True

    # ── Serialization ──

    def to_dict(self) -> dict:
        """Serialize to dict (JSON-compatible)."""
        return asdict(self)

    def to_json(self, path: Path = None) -> str:
        """Serialize to JSON string, optionally saving to file."""
        data = self.to_dict()
        text = json.dumps(data, ensure_ascii=False, indent=2)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return text

    @classmethod
    def from_dict(cls, data: dict) -> VideoProjectState:
        """Deserialize from dict."""
        # Handle nested dataclasses
        modules = []
        for m_data in data.get("modules", []):
            sentences = [ScriptSentence(**s) for s in m_data.get("script", [])]
            graph_a = _parse_graph(m_data.get("graph_a"))
            graph_b = _parse_graph(m_data.get("graph_b"))
            shots = [ShotPlan(**s) for s in m_data.get("shots", [])]
            audio = [AudioTrack(**a) for a in m_data.get("audio_tracks", [])]
            render = RenderJob(**m_data["render_job"]) if m_data.get("render_job") else None
            modules.append(ModuleState(
                **{k: v for k, v in m_data.items()
                   if k not in ("script", "graph_a", "graph_b", "shots",
                                "audio_tracks", "render_job")},
                script=sentences,
                graph_a=graph_a,
                graph_b=graph_b,
                shots=shots,
                audio_tracks=audio,
                render_job=render,
            ))

        intro_audio = None
        if data.get("intro_audio"):
            intro_audio = AudioTrack(**data["intro_audio"])

        render_jobs = [RenderJob(**r) for r in data.get("render_jobs", [])]

        return cls(
            **{k: v for k, v in data.items()
               if k not in ("modules", "intro_audio", "render_jobs",
                            "phase", "thinking_log")},
            phase=ThinkingPhase(data.get("phase", "idle")),
            modules=modules,
            intro_audio=intro_audio,
            render_jobs=render_jobs,
        )

    @classmethod
    def from_json(cls, path: Path) -> VideoProjectState:
        """Load from a JSON file."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(data)


def _parse_graph(data: Optional[dict]) -> Optional[GraphSpec]:
    """Parse a GraphSpec from dict."""
    if not data:
        return None
    nodes = [GraphNode(**n) for n in data.get("nodes", [])]
    edges = [GraphEdge(**e) for e in data.get("edges", [])]
    return GraphSpec(
        title=data.get("title", ""),
        summary=data.get("summary", ""),
        nodes=nodes,
        edges=edges,
        layout_type=data.get("layout_type", "auto"),
    )
