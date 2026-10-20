"""P10.1 — FFmpeg Lowering Tests.

Verifies:
  - Each CommandType lowers to valid ffmpeg args
  - Concat with xfade generates correct filter graph
  - Composite overlay chain is correct
  - Filter commands include requested operations
  - Audio mix generates amix filter
  - Transition generates xfade filter
  - Lowering is deterministic
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.ir.render_ir import (
    RenderIR, RenderCommand, RenderInput,
    RenderBackend, CommandType,
)
from compiler.backend.ffmpeg_lowering import FFmpegLowering, FFmpegCommand


# ═══════════════════════════════════════════════════════════════════════
# COPY
# ═══════════════════════════════════════════════════════════════════════


class TestCopyLowering:
    """COPY command lowering."""

    def test_copy_command(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.COPY,
                    backend=RenderBackend.FFMPEG,
                    inputs=(RenderInput(path="/in.mp4", content_hash="h1"),),
                    output_path="/out.mp4",
                ),
            ),
            final_output="/out.mp4",
        )
        lowering = FFmpegLowering()
        cmds = lowering.lower(rir)
        assert len(cmds) == 1
        assert "-c" in cmds[0].args
        assert "copy" in cmds[0].args
        assert "/out.mp4" in cmds[0].args


# ═══════════════════════════════════════════════════════════════════════
# CONCAT
# ═══════════════════════════════════════════════════════════════════════


class TestConcatLowering:
    """CONCAT command lowering."""

    def test_two_input_concat(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.CONCAT,
                    backend=RenderBackend.FFMPEG,
                    inputs=(
                        RenderInput(path="/a.mp4", content_hash="h1"),
                        RenderInput(path="/b.mp4", content_hash="h2"),
                    ),
                    output_path="/out.mp4",
                    params={"fps": 30, "durations": [5.0, 5.0], "overlaps": [8]},
                ),
            ),
            final_output="/out.mp4",
        )
        lowering = FFmpegLowering()
        cmds = lowering.lower(rir)
        assert len(cmds) == 1
        cmd = cmds[0]
        assert "xfade" in " ".join(cmd.args)
        assert "/out.mp4" in cmd.args

    def test_single_input_falls_back_to_copy(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.CONCAT,
                    backend=RenderBackend.FFMPEG,
                    inputs=(RenderInput(path="/a.mp4", content_hash="h1"),),
                    output_path="/out.mp4",
                ),
            ),
            final_output="/out.mp4",
        )
        lowering = FFmpegLowering()
        cmds = lowering.lower(rir)
        assert len(cmds) == 1
        assert "-c" in cmds[0].args

    def test_three_input_concat(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.CONCAT,
                    backend=RenderBackend.FFMPEG,
                    inputs=(
                        RenderInput(path="/a.mp4", content_hash="h1"),
                        RenderInput(path="/b.mp4", content_hash="h2"),
                        RenderInput(path="/c.mp4", content_hash="h3"),
                    ),
                    output_path="/out.mp4",
                    params={"fps": 30, "durations": [5.0, 5.0, 5.0], "overlaps": [8, 8]},
                ),
            ),
            final_output="/out.mp4",
        )
        lowering = FFmpegLowering()
        cmds = lowering.lower(rir)
        assert len(cmds) == 1
        filter_str = " ".join(cmds[0].args)
        assert "xfade" in filter_str
        assert "acrossfade" in filter_str


# ═══════════════════════════════════════════════════════════════════════
# COMPOSITE
# ═══════════════════════════════════════════════════════════════════════


class TestCompositeLowering:
    """COMPOSITE command lowering."""

    def test_overlay(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.COMPOSITE,
                    backend=RenderBackend.FFMPEG,
                    inputs=(
                        RenderInput(path="/bg.mp4", content_hash="h1"),
                        RenderInput(path="/sub.mp4", content_hash="h2"),
                    ),
                    output_path="/out.mp4",
                ),
            ),
            final_output="/out.mp4",
        )
        lowering = FFmpegLowering()
        cmds = lowering.lower(rir)
        assert len(cmds) == 1
        assert "overlay" in " ".join(cmds[0].args)


# ═══════════════════════════════════════════════════════════════════════
# FILTER
# ═══════════════════════════════════════════════════════════════════════


class TestFilterLowering:
    """FILTER command lowering."""

    def test_scale_filter(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.FILTER,
                    backend=RenderBackend.FFMPEG,
                    inputs=(RenderInput(path="/in.mp4", content_hash="h1"),),
                    output_path="/out.mp4",
                    params={"scale": "1920:1080"},
                ),
            ),
            final_output="/out.mp4",
        )
        lowering = FFmpegLowering()
        cmds = lowering.lower(rir)
        assert len(cmds) == 1
        filter_str = " ".join(cmds[0].args)
        assert "scale=1920:1080" in filter_str

    def test_crop_filter(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.FILTER,
                    backend=RenderBackend.FFMPEG,
                    inputs=(RenderInput(path="/in.mp4", content_hash="h1"),),
                    output_path="/out.mp4",
                    params={"crop": "1080:1920:0:0"},
                ),
            ),
            final_output="/out.mp4",
        )
        lowering = FFmpegLowering()
        cmds = lowering.lower(rir)
        assert "crop=1080:1920:0:0" in " ".join(cmds[0].args)


# ═══════════════════════════════════════════════════════════════════════
# AUDIO MIX
# ═══════════════════════════════════════════════════════════════════════


class TestAudioMixLowering:
    """AUDIO_MIX command lowering."""

    def test_two_track_mix(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.AUDIO_MIX,
                    backend=RenderBackend.FFMPEG,
                    inputs=(
                        RenderInput(path="/voice.wav", content_hash="h1"),
                        RenderInput(path="/bgm.wav", content_hash="h2"),
                    ),
                    output_path="/mixed.wav",
                    params={"duck_volume": 0.3},
                ),
            ),
            final_output="/mixed.wav",
        )
        lowering = FFmpegLowering()
        cmds = lowering.lower(rir)
        assert len(cmds) == 1
        assert "amix" in " ".join(cmds[0].args)


# ═══════════════════════════════════════════════════════════════════════
# TRANSITION
# ═══════════════════════════════════════════════════════════════════════


class TestTransitionLowering:
    """TRANSITION command lowering."""

    def test_fade_transition(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.TRANSITION,
                    backend=RenderBackend.FFMPEG,
                    inputs=(
                        RenderInput(path="/a.mp4", content_hash="h1"),
                        RenderInput(path="/b.mp4", content_hash="h2"),
                    ),
                    output_path="/out.mp4",
                    params={"effect": "fade", "duration_frames": 8, "fps": 30},
                ),
            ),
            final_output="/out.mp4",
        )
        lowering = FFmpegLowering()
        cmds = lowering.lower(rir)
        assert len(cmds) == 1
        filter_str = " ".join(cmds[0].args)
        assert "xfade=transition=fade" in filter_str


# ═══════════════════════════════════════════════════════════════════════
# DETERMINISM
# ═══════════════════════════════════════════════════════════════════════


class TestDeterminism:
    """Lowering must be deterministic."""

    def test_same_ir_same_commands(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.CONCAT,
                    backend=RenderBackend.FFMPEG,
                    inputs=(
                        RenderInput(path="/a.mp4", content_hash="h1"),
                        RenderInput(path="/b.mp4", content_hash="h2"),
                    ),
                    output_path="/out.mp4",
                    params={"fps": 30, "durations": [5.0, 5.0], "overlaps": [8]},
                ),
            ),
            final_output="/out.mp4",
        )
        lowering = FFmpegLowering()
        cmds1 = lowering.lower(rir)
        cmds2 = lowering.lower(rir)
        assert cmds1[0].args == cmds2[0].args

    def test_to_shell(self):
        cmd = FFmpegCommand(args=["-y", "-i", "/in.mp4", "/out.mp4"])
        shell = cmd.to_shell()
        assert "ffmpeg" in shell
        assert "/in.mp4" in shell


# ═══════════════════════════════════════════════════════════════════════
# MULTI-COMMAND DAG
# ═══════════════════════════════════════════════════════════════════════


class TestMultiCommand:
    """Multi-command RenderIR lowering."""

    def test_dag_lowering(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="render_hook",
                    command_type=CommandType.FILTER,
                    backend=RenderBackend.FFMPEG,
                    inputs=(RenderInput(path="/hook.mp4", content_hash="h1"),),
                    output_path="/hook_out.mp4",
                    params={"scale": "1080:1920"},
                ),
                RenderCommand(
                    command_id="render_graph",
                    command_type=CommandType.FILTER,
                    backend=RenderBackend.FFMPEG,
                    inputs=(RenderInput(path="/graph.mp4", content_hash="h2"),),
                    output_path="/graph_out.mp4",
                    params={"scale": "1080:1920"},
                ),
                RenderCommand(
                    command_id="concat",
                    command_type=CommandType.CONCAT,
                    backend=RenderBackend.FFMPEG,
                    inputs=(
                        RenderInput(path="/hook_out.mp4", content_hash="h3"),
                        RenderInput(path="/graph_out.mp4", content_hash="h4"),
                    ),
                    output_path="/final.mp4",
                    params={"fps": 30, "durations": [5.0, 5.0], "overlaps": [8]},
                    depends_on=("render_hook", "render_graph"),
                ),
            ),
            final_output="/final.mp4",
        )
        lowering = FFmpegLowering()
        cmds = lowering.lower(rir)
        assert len(cmds) == 3
        assert cmds[2].command_id == "concat"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
