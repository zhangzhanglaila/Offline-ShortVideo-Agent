"""Timeline → Render Compiler Pass.

Transforms a TimelineIR into a RenderIR (concrete render commands).

    TimelineIR(tracks=[...], transitions=[...])
    ↓
    RenderIR(commands=[
        RenderCommand(COMPOSITE, scene_0_video, subtitle_0),
        RenderCommand(COMPOSITE, scene_1_video, subtitle_1),
        RenderCommand(CONCAT, scene_0_out, scene_1_out),
    ])

This is the final lowering pass before ffmpeg execution.
Generates the minimal set of render commands to produce the output video.

Key decisions:
  - Each scene: composite video + subtitle layer
  - All scenes: concat with xfade transitions
  - Audio: separate mix pass if ducking needed
  - Batched: minimize ffmpeg invocations
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from compiler.ir.timeline_ir import (
    TimelineIR, Track, TrackType, Transition, TransitionEffect,
)
from compiler.ir.render_ir import (
    RenderIR, RenderCommand, RenderInput,
    RenderBackend, CommandType,
)
from compiler.thinking.canonicalize import content_hash
from compiler.base import CompilerPass


class TimelineToRenderPass(CompilerPass):
    """Transform TimelineIR → RenderIR.

    Generates the minimal set of render commands:
      1. Per-scene: composite video + subtitle (if subtitles exist)
      2. Final: concat all scenes with xfade transitions
      3. Optional: audio mix pass

    The output RenderIR is ready for FFmpegLowering.
    """

    name = "timeline_to_render"

    def __init__(self, output_path: str = "output/final.mp4"):
        self.output_path = output_path

    def transform(self, timeline: TimelineIR) -> RenderIR:
        commands = []
        scene_video_paths = []
        scene_hashes = []

        # Group tracks by scene (video tracks on layer 0)
        video_tracks = sorted(
            [t for t in timeline.tracks if t.track_type == TrackType.VIDEO],
            key=lambda t: t.start_frame,
        )
        subtitle_tracks = {
            t.content.get("scene_id", t.track_id): t
            for t in timeline.tracks if t.track_type == TrackType.SUBTITLE
        }

        # 1. Per-scene render commands
        for i, vt in enumerate(video_tracks):
            scene_id = vt.content.get("scene_id", f"scene_{i}")
            scene_input = RenderInput(
                path=f"cache/{scene_id}.mp4",
                content_hash=content_hash(vt.content),
                track_id=vt.track_id,
            )

            # Check if scene has subtitles
            sub_track = subtitle_tracks.get(scene_id)
            if sub_track and sub_track.content.get("text"):
                # Composite: video + subtitle overlay
                scene_output = f"output/.render/{scene_id}_composite.mp4"
                cmd = RenderCommand(
                    command_id=f"composite_{scene_id}",
                    command_type=CommandType.COMPOSITE,
                    backend=RenderBackend.FFMPEG,
                    inputs=(
                        scene_input,
                        RenderInput(
                            path=f"cache/{scene_id}_sub.mp4",
                            content_hash=content_hash(sub_track.content),
                            track_id=sub_track.track_id,
                        ),
                    ),
                    output_path=scene_output,
                )
                commands.append(cmd)
                scene_video_paths.append(scene_output)
            else:
                scene_video_paths.append(scene_input.path)

            scene_hashes.append(content_hash(vt.content))

        # 2. Concat all scenes with transitions
        if len(scene_video_paths) > 1:
            overlaps = []
            durations = []
            for vt in video_tracks:
                durations.append((vt.end_frame - vt.start_frame) / timeline.fps)

            # Match transitions to scene pairs
            for i in range(len(video_tracks) - 1):
                vt_from = video_tracks[i]
                vt_to = video_tracks[i + 1]
                transition = timeline.transition_between(vt_from.track_id, vt_to.track_id)
                if transition:
                    overlaps.append(transition.duration_frames)
                else:
                    overlaps.append(8)  # default

            concat_inputs = tuple(
                RenderInput(path=p, content_hash=h)
                for p, h in zip(scene_video_paths, scene_hashes)
            )

            concat_cmd = RenderCommand(
                command_id="concat_all",
                command_type=CommandType.CONCAT,
                backend=RenderBackend.FFMPEG,
                inputs=concat_inputs,
                output_path=self.output_path,
                params={
                    "fps": timeline.fps,
                    "overlaps": overlaps,
                    "durations": durations,
                    "transition": "fade",
                },
                depends_on=tuple(f"composite_{vt.content.get('scene_id', f'scene_{i}')}"
                                for i, vt in enumerate(video_tracks)
                                if vt.content.get("scene_id") in subtitle_tracks),
            )
            commands.append(concat_cmd)
        elif scene_video_paths:
            # Single scene — copy
            copy_cmd = RenderCommand(
                command_id="copy_single",
                command_type=CommandType.COPY,
                backend=RenderBackend.COPY,
                inputs=(RenderInput(
                    path=scene_video_paths[0],
                    content_hash=scene_hashes[0] if scene_hashes else "",
                ),),
                output_path=self.output_path,
            )
            commands.append(copy_cmd)

        return RenderIR(
            commands=tuple(commands),
            final_output=self.output_path,
            backend=RenderBackend.FFMPEG,
            metadata={
                "scene_count": len(video_tracks),
                "total_frames": timeline.duration_frames,
                "duration_seconds": timeline.duration_seconds,
            },
        )
