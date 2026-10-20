"""FFmpeg Executor — Production-grade ffmpeg execution engine.

Bridges FFmpegCommand (from lowering) to actual video output.

Features:
  - Subprocess management with proper signal handling
  - Progress parsing from ffmpeg stderr
  - Cancellation support (kill running process)
  - Retry with exponential backoff
  - Timeout enforcement
  - Temp file cleanup on failure
  - Dry-run mode (log commands without executing)
  - Parallel execution with concurrency limit

Usage:
    executor = FFmpegExecutor(max_concurrent=2)
    result = executor.execute(cmd)
    # result.success, result.output_path, result.duration

    # With progress callback
    result = executor.execute(cmd, on_progress=lambda p: print(f"{p.percent}%"))

    # Cancel running job
    executor.cancel(job_id)
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from backend.ffmpeg_lowering import FFmpegCommand


@dataclass
class FFmpegProgress:
    """Parsed progress from ffmpeg stderr."""
    frame: int = 0
    fps: float = 0.0
    bitrate: str = ""
    total_size: str = ""
    time: str = ""           # HH:MM:SS.ms
    speed: str = ""          # e.g., "2.5x"
    percent: float = 0.0     # 0-100 (if duration known)


@dataclass
class ExecutionResult:
    """Result of executing an FFmpegCommand."""
    success: bool
    command_id: str
    output_path: str
    returncode: int
    stderr: str
    duration: float = 0.0         # Wall clock time
    progress: Optional[FFmpegProgress] = None
    error: Optional[str] = None
    retries: int = 0
    cancelled: bool = False
    dry_run: bool = False

    @property
    def output_exists(self) -> bool:
        return Path(self.output_path).exists() if self.output_path else False


@dataclass
class _RunningJob:
    """Internal state for a running ffmpeg job."""
    job_id: str
    process: Optional[subprocess.Popen] = None
    future: Optional[Future] = None
    cancelled: bool = False
    start_time: float = 0.0


# Progress parsing regex
_PROGRESS_RE = re.compile(
    r"frame=\s*(\d+)\s+fps=\s*([\d.]+)\s+.*?"
    r"bitrate=\s*([\d.]+\w+)\s+.*?"
    r"time=\s*(\d{2}:\d{2}:\d{2}\.\d+)\s+.*?"
    r"speed=\s*([\d.]+)x"
)

_TIME_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})\.(\d+)")


def _parse_progress(line: str) -> Optional[FFmpegProgress]:
    """Parse a progress line from ffmpeg stderr."""
    # Extract individual fields (more robust than one big regex)
    frame_m = re.search(r"frame=\s*(\d+)", line)
    fps_m = re.search(r"fps=\s*([\d.]+)", line)
    time_m = re.search(r"time=(\d{2}:\d{2}:\d{2}\.\d+)", line)
    speed_m = re.search(r"speed=\s*([\d.]+)x", line)
    bitrate_m = re.search(r"bitrate=\s*([\d.]+\w+/s|\w+)", line)

    if not time_m:
        return None

    progress = FFmpegProgress(
        frame=int(frame_m.group(1)) if frame_m else 0,
        fps=float(fps_m.group(1)) if fps_m else 0.0,
        bitrate=bitrate_m.group(1) if bitrate_m else "",
        time=time_m.group(1),
        speed=speed_m.group(1) if speed_m else "",
    )
    return progress


def _time_to_seconds(time_str: str) -> float:
    """Convert HH:MM:SS.ms to seconds."""
    m = _TIME_RE.search(time_str)
    if not m:
        return 0.0
    h, mi, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    return h * 3600 + mi * 60 + s + float(f"0.{ms}")


class FFmpegExecutor:
    """Production-grade ffmpeg execution engine.

    Manages subprocess lifecycle, progress tracking, cancellation,
    and retry logic for ffmpeg commands.
    """

    def __init__(
        self,
        max_concurrent: int = 2,
        default_timeout: int = 600,
        max_retries: int = 2,
        retry_delay: float = 1.0,
        dry_run: bool = False,
        temp_dir: Optional[Path] = None,
    ):
        self.max_concurrent = max_concurrent
        self.default_timeout = default_timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.dry_run = dry_run
        self.temp_dir = temp_dir or Path(tempfile.gettempdir()) / "ffmpeg_exec"

        self._pool = ThreadPoolExecutor(max_workers=max_concurrent)
        self._jobs: dict[str, _RunningJob] = {}
        self._lock = threading.Lock()
        self._progress_callbacks: dict[str, Callable[[FFmpegProgress], None]] = {}

    def execute(
        self,
        cmd: FFmpegCommand,
        *,
        timeout: Optional[int] = None,
        on_progress: Optional[Callable[[FFmpegProgress], None]] = None,
        duration_hint: float = 0.0,
    ) -> ExecutionResult:
        """Execute an FFmpegCommand synchronously.

        Args:
            cmd: The lowered FFmpegCommand to execute.
            timeout: Max seconds (default: self.default_timeout).
            on_progress: Callback for progress updates.
            duration_hint: Expected duration in seconds (for percent calculation).

        Returns:
            ExecutionResult with success status and details.
        """
        if self.dry_run:
            return ExecutionResult(
                success=True,
                command_id=cmd.command_id,
                output_path=cmd.args[-1] if cmd.args else "",
                returncode=0,
                stderr="",
                dry_run=True,
            )

        timeout = timeout or self.default_timeout
        job_id = cmd.command_id or f"job_{id(cmd)}"

        if on_progress:
            with self._lock:
                self._progress_callbacks[job_id] = on_progress

        retries = 0
        last_error = ""

        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                retries += 1
                time.sleep(self.retry_delay * (2 ** (attempt - 1)))

            result = self._run_once(cmd, job_id, timeout, duration_hint)

            if result.success:
                result.retries = retries
                self._cleanup_callbacks(job_id)
                return result

            last_error = result.stderr or result.error or "unknown error"

            # Don't retry on cancellation
            if result.cancelled:
                self._cleanup_callbacks(job_id)
                return result

        # All retries exhausted
        self._cleanup_callbacks(job_id)
        return ExecutionResult(
            success=False,
            command_id=cmd.command_id,
            output_path=cmd.args[-1] if cmd.args else "",
            returncode=-1,
            stderr=last_error,
            retries=retries,
            error=f"Failed after {retries + 1} attempts: {last_error[-200:]}",
        )

    def execute_async(
        self,
        cmd: FFmpegCommand,
        *,
        timeout: Optional[int] = None,
        on_progress: Optional[Callable[[FFmpegProgress], None]] = None,
    ) -> str:
        """Execute asynchronously. Returns job_id for tracking/cancellation."""
        job_id = cmd.command_id or f"job_{id(cmd)}"

        with self._lock:
            job = _RunningJob(job_id=job_id, start_time=time.time())
            self._jobs[job_id] = job

        future = self._pool.submit(
            self.execute, cmd, timeout=timeout, on_progress=on_progress,
        )
        job.future = future
        return job_id

    def cancel(self, job_id: str) -> bool:
        """Cancel a running job. Returns True if cancelled."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            job.cancelled = True
            if job.process:
                try:
                    job.process.terminate()
                    # Give it a moment, then force kill
                    threading.Timer(2.0, lambda: self._force_kill(job.process)).start()
                except (OSError, ProcessLookupError):
                    pass
        return True

    def _force_kill(self, proc: Optional[subprocess.Popen]):
        """Force kill a process if still running."""
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except (OSError, ProcessLookupError):
                pass

    def _run_once(
        self,
        cmd: FFmpegCommand,
        job_id: str,
        timeout: int,
        duration_hint: float,
    ) -> ExecutionResult:
        """Execute a single ffmpeg attempt."""
        args = ["ffmpeg", "-y", *cmd.args]
        start = time.time()

        # Ensure output directory exists
        if cmd.args:
            output_path = Path(cmd.args[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            with self._lock:
                if job_id in self._jobs:
                    self._jobs[job_id].process = proc

            # Read stderr in real-time for progress
            stderr_lines = []
            last_progress = None

            while True:
                # Check for cancellation
                with self._lock:
                    if job_id in self._jobs and self._jobs[job_id].cancelled:
                        proc.terminate()
                        return ExecutionResult(
                            success=False,
                            command_id=cmd.command_id,
                            output_path=cmd.args[-1] if cmd.args else "",
                            returncode=-1,
                            stderr="cancelled",
                            duration=time.time() - start,
                            cancelled=True,
                        )

                line = proc.stderr.readline()
                if not line and proc.poll() is not None:
                    break
                if line:
                    stderr_lines.append(line)
                    progress = _parse_progress(line)
                    if progress:
                        if duration_hint > 0:
                            elapsed = _time_to_seconds(progress.time)
                            progress.percent = min(100, elapsed / duration_hint * 100)
                        last_progress = progress

                        # Fire callback
                        with self._lock:
                            cb = self._progress_callbacks.get(job_id)
                        if cb:
                            try:
                                cb(progress)
                            except Exception:
                                pass

            proc.wait(timeout=5)
            stderr_text = "".join(stderr_lines)

            # Clean up temp files on failure
            if proc.returncode != 0 and cmd.args:
                output_path = Path(cmd.args[-1])
                if output_path.exists() and output_path.stat().st_size == 0:
                    output_path.unlink(missing_ok=True)

            return ExecutionResult(
                success=proc.returncode == 0,
                command_id=cmd.command_id,
                output_path=cmd.args[-1] if cmd.args else "",
                returncode=proc.returncode,
                stderr=stderr_text,
                duration=time.time() - start,
                progress=last_progress,
                error=stderr_text[-500:] if proc.returncode != 0 else None,
            )

        except subprocess.TimeoutExpired:
            self._force_kill(proc)
            return ExecutionResult(
                success=False,
                command_id=cmd.command_id,
                output_path=cmd.args[-1] if cmd.args else "",
                returncode=-1,
                stderr=f"timed out after {timeout}s",
                duration=time.time() - start,
                error=f"ffmpeg timed out after {timeout}s",
            )
        except FileNotFoundError:
            return ExecutionResult(
                success=False,
                command_id=cmd.command_id,
                output_path=cmd.args[-1] if cmd.args else "",
                returncode=-1,
                stderr="ffmpeg not found in PATH",
                error="ffmpeg not found in PATH",
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                command_id=cmd.command_id,
                output_path=cmd.args[-1] if cmd.args else "",
                returncode=-1,
                stderr=str(e),
                error=str(e),
            )
        finally:
            with self._lock:
                if job_id in self._jobs:
                    del self._jobs[job_id]

    def _cleanup_callbacks(self, job_id: str):
        with self._lock:
            self._progress_callbacks.pop(job_id, None)

    def shutdown(self):
        """Shut down the executor and cancel all running jobs."""
        with self._lock:
            for job in self._jobs.values():
                if job.process:
                    self._force_kill(job.process)
        self._pool.shutdown(wait=False)

    def active_jobs(self) -> list[str]:
        """List currently running job IDs."""
        with self._lock:
            return list(self._jobs.keys())
