"""Unified ffmpeg subprocess runner — single implementation for all modules."""
import subprocess
from typing import List, Optional, Callable


def _filter_stderr(stderr: str) -> str:
    """Strip FFmpeg version/boilerplate lines, return the real error message."""
    skip_patterns = [
        'ffmpeg version', 'built with', 'configuration:', 'Copyright',
        'libavformat', 'libavcodec', 'libavutil', 'libavfilter',
        'libswscale', 'libswresample', 'libpostproc', 'FFmpeg',
    ]
    lines = stderr.strip().split('\n')
    errors = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if any(p.lower() in stripped.lower() for p in skip_patterns):
            continue
        errors.append(stripped)
    if errors:
        return ' | '.join(errors[:3])
    return "ffmpeg exited with non-zero code"


def run_ffmpeg(
    cmd: List[str],
    timeout: int = 300,
) -> None:
    """Execute ffmpeg command. Raises RuntimeError on any failure.

    Callers that need a bool should use `run_ffmpeg_safe()` or catch RuntimeError
    themselves — the runner does NOT swallow errors.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("FFmpeg执行超时")
    except Exception as e:
        raise RuntimeError(f"FFmpeg执行失败: {e}")

    if result.returncode != 0:
        msg = _filter_stderr(result.stderr)
        raise RuntimeError(f"[FFmpeg错误] {msg}")


def run_ffmpeg_safe(
    cmd: List[str],
    timeout: int = 300,
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> bool:
    """Execute ffmpeg, return True on success / False on failure.

    Convenience wrapper for modules whose existing API contract expects a bool.
    Prefer `run_ffmpeg()` in core pipeline paths so errors don't get swallowed.
    """
    try:
        run_ffmpeg(cmd, timeout=timeout)
        return True
    except RuntimeError as e:
        msg = str(e)
        print(msg)
        if log_callback:
            log_callback(msg, 'error')
        return False


def get_video_duration(path, timeout: int = 10) -> float:
    """Return video duration in seconds via ffprobe; 0.0 if it cannot be read.

    合并自 media/ffmpeg_executor.py：全项目只保留这一处 ffprobe 封装。
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        pass
    return 0.0
