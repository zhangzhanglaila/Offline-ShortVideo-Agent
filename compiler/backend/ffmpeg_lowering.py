"""FFmpeg Lowering — Translate RenderIR to ffmpeg command-line args.

    RenderIR(commands=[
        RenderCommand(command_type=CONCAT, inputs=[...], output="final.mp4"),
    ])
    ↓ lowering
    FFmpegCommand(args=["-i", "a.mp4", "-i", "b.mp4", "-filter_complex", "...", "final.mp4"])

This is the bridge between the abstract render layer and actual ffmpeg execution.
Each CommandType maps to a specific ffmpeg filter graph pattern.

Supports:
  - CONCAT: xfade transitions between scenes
  - COMPOSITE: overlay layers (video + subtitle + effect)
  - FILTER: scale, crop, format conversion
  - AUDIO_MIX: ducking, fade, bgm mixing
  - SUBTITLE: burn subtitles via ass/srt
  - TRANSITION: xfade at boundaries
  - COPY: direct file copy (no ffmpeg)
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any, Optional

from compiler.ir.render_ir import (
    RenderIR, RenderCommand, RenderInput,
    RenderBackend, CommandType,
)


@dataclass(frozen=True)
class FFmpegCommand:
    """A lowered ffmpeg command ready for execution."""
    args: list[str]
    command_id: str = ""
    description: str = ""

    def to_shell(self) -> str:
        """Full shell command string."""
        return "ffmpeg " + " ".join(shlex.quote(str(a)) for a in self.args)

    def __str__(self) -> str:
        return self.to_shell()


class FFmpegLowering:
    """Lower RenderIR to FFmpegCommand list.

    Each RenderCommand is lowered independently. The lowering is
    deterministic — same RenderIR → same FFmpegCommand args.

    Usage:
        lowering = FFmpegLowering()
        commands = lowering.lower(render_ir)
        for cmd in commands:
            subprocess.run(cmd.args)
    """

    def lower(self, render_ir: RenderIR) -> list[FFmpegCommand]:
        """Lower a RenderIR to a list of FFmpegCommands."""
        commands = []
        for cmd in render_ir.commands:
            lowered = self._lower_command(cmd)
            if lowered:
                commands.append(lowered)
        return commands

    def _lower_command(self, cmd: RenderCommand) -> Optional[FFmpegCommand]:
        """Lower a single RenderCommand."""
        if cmd.command_type == CommandType.COPY:
            return self._lower_copy(cmd)
        elif cmd.command_type == CommandType.CONCAT:
            return self._lower_concat(cmd)
        elif cmd.command_type == CommandType.COMPOSITE:
            return self._lower_composite(cmd)
        elif cmd.command_type == CommandType.FILTER:
            return self._lower_filter(cmd)
        elif cmd.command_type == CommandType.AUDIO_MIX:
            return self._lower_audio_mix(cmd)
        elif cmd.command_type == CommandType.SUBTITLE:
            return self._lower_subtitle(cmd)
        elif cmd.command_type == CommandType.TRANSITION:
            return self._lower_transition(cmd)
        elif cmd.command_type == CommandType.TRANSCODE:
            return self._lower_transcode(cmd)
        return None

    # ── COPY ──

    def _lower_copy(self, cmd: RenderCommand) -> FFmpegCommand:
        """Simple copy — no ffmpeg, just file copy."""
        args = [
            "-y", "-i", cmd.inputs[0].path,
            "-c", "copy",
            cmd.output_path,
        ]
        return FFmpegCommand(
            args=args,
            command_id=cmd.command_id,
            description=f"copy {cmd.inputs[0].path} → {cmd.output_path}",
        )

    # ── CONCAT ──

    def _lower_concat(self, cmd: RenderCommand) -> FFmpegCommand:
        """Concat with xfade transitions.

        For N inputs with overlaps:
          [0][1]xfade=transition=fade:duration=D:offset=O[v01];
          [v01][2]xfade=transition=fade:duration=D:offset=O[vout];
          [0:a][1:a]acrossfade=d=D[a01];
          [a01][2:a]acrossfade=d=D[aout]
        """
        if len(cmd.inputs) < 2:
            # Single input — just copy
            return self._lower_copy(cmd)

        params = cmd.params
        fps = params.get("fps", 30)
        transition = params.get("transition", "fade")
        overlaps = params.get("overlaps", [8] * (len(cmd.inputs) - 1))
        durations = params.get("durations", [])

        # Build inputs
        input_args = []
        for inp in cmd.inputs:
            input_args.extend(["-i", inp.path])

        # Build filter graph
        filter_parts = []
        audio_parts = []

        if durations and len(durations) == len(cmd.inputs):
            # Compute xfade offsets from durations
            offsets = []
            accumulated = 0.0
            for i in range(len(overlaps)):
                overlap_sec = overlaps[i] / fps
                offset = accumulated + durations[i] - overlap_sec
                offsets.append(max(0, offset))
                accumulated += durations[i] - overlap_sec

            prev_video = "[0:v]"
            prev_audio = "[0:a]"

            for i in range(1, len(cmd.inputs)):
                overlap_sec = overlaps[i - 1] / fps
                offset = offsets[i - 1]
                is_last = i == len(cmd.inputs) - 1
                v_label = "[vout]" if is_last else f"[v{i}]"
                a_label = "[aout]" if is_last else f"[a{i}]"

                filter_parts.append(
                    f"{prev_video}[{i}:v]xfade=transition={transition}:"
                    f"duration={overlap_sec:.3f}:offset={offset:.3f}{v_label}"
                )
                audio_parts.append(
                    f"{prev_audio}[{i}:a]acrossfade=d={overlap_sec:.3f}{a_label}"
                )
                prev_video = v_label
                prev_audio = a_label
        else:
            # Simple concat without transitions
            n = len(cmd.inputs)
            filter_parts.append(
                "".join(f"[{i}:v]" for i in range(n)) +
                f"concat=n={n}:v=1:a=0[vout]"
            )
            audio_parts.append(
                "".join(f"[{i}:a]" for i in range(n)) +
                f"concat=n={n}:v=0:a=1[aout]"
            )

        filter_graph = ";".join(filter_parts + audio_parts)

        args = [
            "-y",
            *input_args,
            "-filter_complex", filter_graph,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            cmd.output_path,
        ]
        return FFmpegCommand(
            args=args,
            command_id=cmd.command_id,
            description=f"concat {len(cmd.inputs)} inputs → {cmd.output_path}",
        )

    # ── COMPOSITE ──

    def _lower_composite(self, cmd: RenderCommand) -> FFmpegCommand:
        """Overlay multiple layers (video + subtitle + effect).

        For 2 inputs: [0:v][1:v]overlay[vout]
        For N inputs: chain overlays.
        """
        if len(cmd.inputs) < 2:
            return self._lower_copy(cmd)

        input_args = []
        for inp in cmd.inputs:
            input_args.extend(["-i", inp.path])

        # Chain overlays
        filter_parts = []
        prev = "[0:v]"
        for i in range(1, len(cmd.inputs)):
            is_last = i == len(cmd.inputs) - 1
            label = "[vout]" if is_last else f"[ov{i}]"
            x = cmd.params.get("x", 0)
            y = cmd.params.get("y", 0)
            filter_parts.append(f"{prev}[{i}:v]overlay={x}:{y}{label}")
            prev = label

        filter_graph = ";".join(filter_parts)

        args = [
            "-y",
            *input_args,
            "-filter_complex", filter_graph,
            "-map", "[vout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p",
            cmd.output_path,
        ]
        return FFmpegCommand(
            args=args,
            command_id=cmd.command_id,
            description=f"composite {len(cmd.inputs)} layers → {cmd.output_path}",
        )

    # ── FILTER ──

    def _lower_filter(self, cmd: RenderCommand) -> FFmpegCommand:
        """Apply video filter (scale, crop, etc.)."""
        params = cmd.params
        filters = []

        if "scale" in params:
            filters.append(f"scale={params['scale']}")
        if "crop" in params:
            filters.append(f"crop={params['crop']}")
        if "fps" in params:
            filters.append(f"fps={params['fps']}")
        if "format" in params:
            filters.append(f"format={params['format']}")

        vf = ",".join(filters) if filters else "null"

        args = [
            "-y", "-i", cmd.inputs[0].path,
            "-vf", vf,
            "-c:a", "copy",
            cmd.output_path,
        ]
        return FFmpegCommand(
            args=args,
            command_id=cmd.command_id,
            description=f"filter ({vf}) → {cmd.output_path}",
        )

    # ── AUDIO MIX ──

    def _lower_audio_mix(self, cmd: RenderCommand) -> FFmpegCommand:
        """Mix audio tracks with ducking/fade."""
        if len(cmd.inputs) < 2:
            args = [
                "-y", "-i", cmd.inputs[0].path,
                "-c", "copy",
                cmd.output_path,
            ]
            return FFmpegCommand(args=args, command_id=cmd.command_id)

        input_args = []
        for inp in cmd.inputs:
            input_args.extend(["-i", inp.path])

        params = cmd.params
        n = len(cmd.inputs)
        duck_volume = params.get("duck_volume", 0.3)

        # Simple amix with volume adjustment
        filter_parts = []
        for i in range(n):
            vol = duck_volume if i > 0 else 1.0
            filter_parts.append(f"[{i}:a]volume={vol}[a{i}]")

        mix_inputs = "".join(f"[a{i}]" for i in range(n))
        filter_parts.append(f"{mix_inputs}amix=inputs={n}:duration=first[aout]")

        filter_graph = ";".join(filter_parts)

        args = [
            "-y",
            *input_args,
            "-filter_complex", filter_graph,
            "-map", "[aout]",
            "-c:a", "aac", "-b:a", "192k",
            cmd.output_path,
        ]
        return FFmpegCommand(
            args=args,
            command_id=cmd.command_id,
            description=f"audio mix {n} tracks → {cmd.output_path}",
        )

    # ── SUBTITLE ──

    def _lower_subtitle(self, cmd: RenderCommand) -> FFmpegCommand:
        """Burn subtitles onto video."""
        params = cmd.params
        sub_path = params.get("subtitle_path", cmd.inputs[-1].path)
        style = params.get("style", "")

        args = [
            "-y", "-i", cmd.inputs[0].path,
            "-vf", f"subtitles={sub_path}",
            "-c:a", "copy",
            cmd.output_path,
        ]
        return FFmpegCommand(
            args=args,
            command_id=cmd.command_id,
            description=f"subtitle burn → {cmd.output_path}",
        )

    # ── TRANSITION ──

    def _lower_transition(self, cmd: RenderCommand) -> FFmpegCommand:
        """Apply transition effect between two clips."""
        if len(cmd.inputs) < 2:
            return self._lower_copy(cmd)

        params = cmd.params
        effect = params.get("effect", "fade")
        duration = params.get("duration_frames", 8)
        fps = params.get("fps", 30)
        overlap_sec = duration / fps

        args = [
            "-y",
            "-i", cmd.inputs[0].path,
            "-i", cmd.inputs[1].path,
            "-filter_complex",
            f"[0:v][1:v]xfade=transition={effect}:duration={overlap_sec:.3f}[vout];"
            f"[0:a][1:a]acrossfade=d={overlap_sec:.3f}[aout]",
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            cmd.output_path,
        ]
        return FFmpegCommand(
            args=args,
            command_id=cmd.command_id,
            description=f"transition ({effect}) → {cmd.output_path}",
        )

    # ── TRANSCODE ──

    def _lower_transcode(self, cmd: RenderCommand) -> FFmpegCommand:
        """Format conversion / encoding change."""
        params = cmd.params
        codec = params.get("codec", "libx264")
        preset = params.get("preset", "medium")
        crf = params.get("crf", "18")

        args = [
            "-y", "-i", cmd.inputs[0].path,
            "-c:v", codec, "-preset", preset, "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            cmd.output_path,
        ]
        return FFmpegCommand(
            args=args,
            command_id=cmd.command_id,
            description=f"transcode → {cmd.output_path}",
        )
