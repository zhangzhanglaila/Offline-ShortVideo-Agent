"""Render IR — Backend-specific rendering commands.

The final IR level. Maps to concrete rendering operations:
  - FFmpeg filter graphs
  - Remotion component trees
  - Shader graphs

    TimelineIR(tracks=[...])
    ↓ render lowering
    RenderIR(commands=[
        FFmpegCommand(inputs=[...], filter_graph="...", output="..."),
    ])

Design principles:
  - Backend-agnostic (FFmpeg, Remotion, WebGPU)
  - Declarative (WHAT to render, not HOW to execute)
  - Composable (commands can be parallelized)
  - Hashable (for render cache)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from compiler.thinking.canonicalize import canonicalize, content_hash


class RenderBackend(str, Enum):
    FFMPEG = "ffmpeg"
    REMOTION = "remotion"
    WEBGPU = "webgpu"
    COPY = "copy"          # Simple file copy (no render)


class CommandType(str, Enum):
    COMPOSITE = "composite"     # Overlay multiple layers
    CONCAT = "concat"           # Sequence clips
    TRANSCODE = "transcode"     # Format conversion
    FILTER = "filter"           # Apply filter (scale, crop, etc.)
    AUDIO_MIX = "audio_mix"     # Mix audio tracks
    SUBTITLE = "subtitle"       # Burn subtitles
    TRANSITION = "transition"   # Apply transition effect
    COPY = "copy"               # Copy file as-is


@dataclass(frozen=True)
class RenderInput:
    """Input to a render command."""
    path: str                     # File path or artifact ID
    content_hash: str             # For cache lookup
    track_id: str = ""            # Source track ID
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {"path": self.path, "content_hash": self.content_hash}
        if self.track_id:
            d["track_id"] = self.track_id
        if self.metadata:
            d["metadata"] = self.metadata
        return d


@dataclass(frozen=True)
class RenderCommand:
    """A single rendering operation.

    Declarative — describes WHAT to produce, not HOW to execute.
    The backend adapter translates this to ffmpeg args / remotion components.
    """
    command_id: str
    command_type: CommandType
    backend: RenderBackend
    inputs: tuple[RenderInput, ...]
    output_path: str
    params: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()     # command_ids this depends on

    def __post_init__(self):
        if not self.command_id:
            raise ValueError("command_id must be non-empty")
        if not self.output_path:
            raise ValueError("output_path must be non-empty")

    @property
    def input_hashes(self) -> list[str]:
        return [inp.content_hash for inp in self.inputs]

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "command_id": self.command_id,
            "command_type": self.command_type.value,
            "backend": self.backend.value,
            "inputs": [inp.to_dict() for inp in self.inputs],
            "output_path": self.output_path,
        }
        if self.params:
            d["params"] = self.params
        if self.depends_on:
            d["depends_on"] = list(self.depends_on)
        return d


@dataclass(frozen=True)
class RenderIR:
    """Final IR — ordered rendering commands.

    A RenderIR is a DAG of render commands. Each command produces
    an output file that may be consumed by downstream commands.
    The final output is the video file.

    The RenderIR is cacheable: if all input hashes and command
    params are unchanged, the output can be reused.
    """
    commands: tuple[RenderCommand, ...]
    final_output: str                   # Path to the final output file
    backend: RenderBackend = RenderBackend.FFMPEG
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.commands:
            raise ValueError("commands must be non-empty")
        self._validate_dag()

    def _validate_dag(self):
        """Validate command dependency DAG is valid."""
        ids = {c.command_id for c in self.commands}
        for cmd in self.commands:
            for dep_id in cmd.depends_on:
                if dep_id not in ids:
                    raise ValueError(
                        f"Command {cmd.command_id} depends on unknown "
                        f"command: {dep_id}"
                    )

    # ── Derived Properties ──

    @property
    def command_count(self) -> int:
        return len(self.commands)

    def command_by_id(self, command_id: str) -> Optional[RenderCommand]:
        for cmd in self.commands:
            if cmd.command_id == command_id:
                return cmd
        return None

    def leaf_commands(self) -> list[RenderCommand]:
        """Commands with no downstream dependents."""
        depended_on = set()
        for cmd in self.commands:
            depended_on.update(cmd.depends_on)
        return [cmd for cmd in self.commands if cmd.command_id not in depended_on]

    def root_commands(self) -> list[RenderCommand]:
        """Commands with no dependencies (entry points)."""
        return [cmd for cmd in self.commands if not cmd.depends_on]

    # ── Canonical Form ──

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "commands": [c.to_dict() for c in self.commands],
            "final_output": self.final_output,
            "backend": self.backend.value,
        }
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    def canonical(self) -> dict[str, Any]:
        return canonicalize(self.to_dict())

    def content_hash(self) -> str:
        return content_hash(self.canonical())

    # ── Display ──

    def summary(self) -> str:
        return (
            f"RenderIR({self.command_count} commands, "
            f"backend={self.backend.value}, "
            f"output={self.final_output})"
        )

    def command_graph(self) -> str:
        """ASCII representation of the command DAG."""
        lines = []
        for cmd in self.commands:
            deps = ", ".join(cmd.depends_on) if cmd.depends_on else "(root)"
            lines.append(
                f"  {cmd.command_id} [{cmd.command_type.value}] "
                f"← {deps} → {cmd.output_path}"
            )
        return "\n".join(lines)
