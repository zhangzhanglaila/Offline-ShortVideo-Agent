"""Renderer Backends — Lower RenderIR to concrete commands.

    RenderIR(commands=[...])
    ↓ ffmpeg_lowering.py
    FFmpegCommand(args=[...])
    ↓ ffmpeg_executor.py
    output.mp4

Backends:
  - FFmpeg: production video rendering
  - Copy: simple file copy (for cached scenes)
"""

from backend.ffmpeg_lowering import FFmpegLowering, FFmpegCommand
from backend.executor import FFmpegExecutor, ExecutionResult, FFmpegProgress
from backend.asset_store import AssetStore
