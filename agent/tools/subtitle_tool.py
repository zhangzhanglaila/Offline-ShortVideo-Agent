# -*- coding: utf-8 -*-
"""
字幕生成工具
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.tools.tool_base import BaseTool, ToolDefinition, ToolParameter, ToolCategory, ToolResult


class SubtitleGenerateTool(BaseTool):
    """字幕生成工具 - generate_subtitle()"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="generate_subtitle",
            category=ToolCategory.SUBTITLE,
            description="为视频生成字幕文件并烧录到视频中",
            parameters=[
                ToolParameter(
                    name="video_path",
                    type="str",
                    description="输入视频路径",
                    required=True
                ),
                ToolParameter(
                    name="script",
                    type="str",
                    description="口播脚本内容",
                    required=True
                ),
                ToolParameter(
                    name="output_path",
                    type="str",
                    description="输出视频路径",
                    required=False
                ),
                ToolParameter(
                    name="duration",
                    type="float",
                    description="视频时长(秒)",
                    required=False,
                    default=None
                )
            ]
        )

    def execute(self, video_path: str, script: str, output_path: str = None,
                duration: float = None) -> ToolResult:
        """生成字幕"""
        import time
        start_time = time.time()

        try:
            from core.subtitle_module import SubtitleModule

            subtitle_mod = SubtitleModule()

            # 生成默认输出路径
            if not output_path:
                output_path = video_path.replace(".mp4", "_subtitled.mp4")

            # 调用字幕生成
            success, srt_path = subtitle_mod.generate_subtitle_video(
                video_path=video_path,
                script=script,
                output_path=output_path,
                duration=duration,
                use_whisper=False  # 默认使用脚本生成
            )

            return ToolResult(
                tool_name=self.definition.name,
                success=success,
                result={"video_path": output_path, "srt_path": srt_path} if success else None,
                error=None if success else "字幕生成失败",
                execution_time=time.time() - start_time
            )

        except Exception as e:
            return ToolResult(
                tool_name=self.definition.name,
                success=False,
                error=str(e),
                execution_time=time.time() - start_time
            )
