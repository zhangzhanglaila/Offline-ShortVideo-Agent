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

from compiler.backend.ffmpeg_lowering import FFmpegLowering, FFmpegCommand
from compiler.backend.executor import FFmpegExecutor, ExecutionResult, FFmpegProgress
from compiler.backend.asset_store import AssetStore
