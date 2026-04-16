# -*- coding: utf-8 -*-
"""
时间轴同步工具
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.tools.tool_base import BaseTool, ToolDefinition, ToolParameter, ToolCategory, ToolResult


class TimelineSyncTool(BaseTool):
    """时间轴同步工具 - sync_timeline()"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="sync_timeline",
            category=ToolCategory.VIDEO,
            description="用faster-whisper识别配音音频，自动对齐脚本分句、字幕时间轴、图片切换时机",
            parameters=[
                ToolParameter(
                    name="audio_path",
                    type="str",
                    description="配音音频文件路径",
                    required=True
                ),
                ToolParameter(
                    name="script_sentences",
                    type="list",
                    description="脚本分句列表",
                    required=False
                ),
                ToolParameter(
                    name="num_images",
                    type="int",
                    description="图片数量（用于自动分配时间轴）",
                    required=False
                ),
                ToolParameter(
                    name="output_path",
                    type="str",
                    description="导出的时间轴JSON文件路径",
                    required=False
                )
            ]
        )

    def execute(self, audio_path: str, script_sentences: list = None,
                num_images: int = None, output_path: str = None) -> ToolResult:
        """同步时间轴"""
        import time
        start_time = time.time()

        try:
            from core.timeline_sync_module import get_timeline_module

            timeline_mod = get_timeline_module()

            if script_sentences:
                # 对齐脚本和音频
                audio_segments = timeline_mod.transcribe_audio(audio_path)
                aligned = timeline_mod.align_script_with_audio(script_sentences, audio_segments)

                if output_path:
                    timeline_mod.export_timeline_json(aligned, output_path)

                return ToolResult(
                    tool_name=self.definition.name,
                    success=True,
                    result={"timeline": aligned, "exported": bool(output_path)},
                    execution_time=time.time() - start_time
                )
            else:
                # 自动从音频生成时间轴
                timeline = timeline_mod.generate_timeline_from_audio(audio_path, num_images)

                if output_path:
                    timeline_mod.export_timeline_json(timeline, output_path)

                return ToolResult(
                    tool_name=self.definition.name,
                    success=True,
                    result={"timeline": timeline, "segments": len(timeline), "exported": bool(output_path)},
                    execution_time=time.time() - start_time
                )

        except Exception as e:
            return ToolResult(
                tool_name=self.definition.name,
                success=False,
                error=str(e),
                execution_time=time.time() - start_time
            )
