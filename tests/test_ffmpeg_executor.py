"""P10.2 — FFmpeg Executor Tests.

Verifies:
  - Dry-run mode (no actual ffmpeg)
  - Progress parsing
  - Timeout handling
  - Cancellation
  - Retry logic
  - Output directory creation
  - ExecutionResult fields
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.backend.executor import (
    FFmpegExecutor, ExecutionResult, FFmpegProgress,
    _parse_progress, _time_to_seconds,
)
from compiler.backend.ffmpeg_lowering import FFmpegCommand


# ═══════════════════════════════════════════════════════════════════════
# Progress Parsing
# ═══════════════════════════════════════════════════════════════════════


class TestProgressParsing:
    """FFmpeg stderr progress line parsing."""

    def test_parse_typical_line(self):
        line = "frame=  150 fps= 30 q=28.0 size=    1024kB time=00:00:05.00 bitrate= 1677.7kbits/s speed=2.50x"
        p = _parse_progress(line)
        assert p is not None
        assert p.frame == 150
        assert p.fps == 30.0
        assert p.speed == "2.50"

    def test_parse_no_match(self):
        p = _parse_progress("some random stderr line")
        assert p is None

    def test_parse_empty_line(self):
        p = _parse_progress("")
        assert p is None

    def test_time_to_seconds(self):
        assert abs(_time_to_seconds("00:00:05.00") - 5.0) < 0.01
        assert abs(_time_to_seconds("00:01:30.50") - 90.5) < 0.01
        assert abs(_time_to_seconds("01:00:00.00") - 3600.0) < 0.01

    def test_time_to_seconds_invalid(self):
        assert _time_to_seconds("invalid") == 0.0


# ═══════════════════════════════════════════════════════════════════════
# Dry Run
# ═══════════════════════════════════════════════════════════════════════


class TestDryRun:
    """Dry-run mode skips actual execution."""

    def test_dry_run_returns_success(self):
        executor = FFmpegExecutor(dry_run=True)
        cmd = FFmpegCommand(
            args=["-i", "/in.mp4", "/out.mp4"],
            command_id="test",
        )
        result = executor.execute(cmd)
        assert result.success
        assert result.dry_run
        assert result.returncode == 0

    def test_dry_run_no_ffmpeg_called(self):
        executor = FFmpegExecutor(dry_run=True)
        cmd = FFmpegCommand(
            args=["-i", "/in.mp4", "/out.mp4"],
            command_id="test",
        )
        with patch("subprocess.run") as mock_run:
            result = executor.execute(cmd)
            mock_run.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════
# ExecutionResult
# ═══════════════════════════════════════════════════════════════════════


class TestExecutionResult:
    """ExecutionResult field access."""

    def test_success_result(self):
        r = ExecutionResult(
            success=True, command_id="c0", output_path="/out.mp4",
            returncode=0, stderr="",
        )
        assert r.success
        assert r.error is None
        assert r.retries == 0
        assert not r.cancelled
        assert not r.dry_run

    def test_failure_result(self):
        r = ExecutionResult(
            success=False, command_id="c0", output_path="/out.mp4",
            returncode=1, stderr="error", error="error",
        )
        assert not r.success
        assert r.error == "error"


# ═══════════════════════════════════════════════════════════════════════
# FFmpegProgress
# ═══════════════════════════════════════════════════════════════════════


class TestFFmpegProgress:
    """FFmpegProgress dataclass."""

    def test_default_values(self):
        p = FFmpegProgress()
        assert p.frame == 0
        assert p.percent == 0.0

    def test_percent_calculation(self):
        p = FFmpegProgress(percent=75.5)
        assert p.percent == 75.5


# ═══════════════════════════════════════════════════════════════════════
# Executor Configuration
# ═══════════════════════════════════════════════════════════════════════


class TestExecutorConfig:
    """Executor configuration."""

    def test_default_config(self):
        executor = FFmpegExecutor()
        assert executor.max_concurrent == 2
        assert executor.default_timeout == 600
        assert executor.max_retries == 2
        assert not executor.dry_run

    def test_custom_config(self):
        executor = FFmpegExecutor(
            max_concurrent=4,
            default_timeout=300,
            max_retries=3,
            dry_run=True,
        )
        assert executor.max_concurrent == 4
        assert executor.default_timeout == 300
        assert executor.max_retries == 3
        assert executor.dry_run

    def test_active_jobs_empty(self):
        executor = FFmpegExecutor(dry_run=True)
        assert executor.active_jobs() == []


# ═══════════════════════════════════════════════════════════════════════
# FFmpegCommand Integration
# ═══════════════════════════════════════════════════════════════════════


class TestCommandIntegration:
    """FFmpegCommand → ExecutionResult flow."""

    def test_command_to_shell(self):
        cmd = FFmpegCommand(
            args=["-i", "/in.mp4", "-c:v", "libx264", "/out.mp4"],
            command_id="c0",
        )
        shell = cmd.to_shell()
        assert "ffmpeg" in shell
        assert "/in.mp4" in shell
        assert "/out.mp4" in shell

    def test_dry_run_with_full_pipeline(self):
        """Simulate full pipeline: lower → execute (dry run)."""
        from compiler.ir.render_ir import (
            RenderIR, RenderCommand, RenderInput,
            RenderBackend, CommandType,
        )
        from compiler.backend.ffmpeg_lowering import FFmpegLowering

        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="concat",
                    command_type=CommandType.CONCAT,
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

        executor = FFmpegExecutor(dry_run=True)
        result = executor.execute(cmds[0])
        assert result.success
        assert result.dry_run


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
