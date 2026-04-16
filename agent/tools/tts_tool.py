# -*- coding: utf-8 -*-
"""
TTS配音工具
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.tools.tool_base import BaseTool, ToolDefinition, ToolParameter, ToolCategory, ToolResult


class TTSGenerateTool(BaseTool):
    """TTS配音工具 - generate_tts()"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="generate_tts",
            category=ToolCategory.SUBTITLE,
            description="将文本转换为语音配音，支持edge-tts中文配音，生成与脚本分句匹配的配音音频",
            parameters=[
                ToolParameter(
                    name="text",
                    type="str",
                    description="要转换的文本",
                    required=True
                ),
                ToolParameter(
                    name="output_path",
                    type="str",
                    description="输出音频文件路径",
                    required=False
                ),
                ToolParameter(
                    name="voice",
                    type="str",
                    description="配音人声音",
                    required=False,
                    default="zh-CN-XiaoxiaoNeural",
                    enum_values=[
                        "zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural", "zh-CN-YunyangNeural",
                        "zh-CN-Xiaoyi", "zh-CN-Zhiyu", "zh-CN-Xiaomo"
                    ]
                ),
                ToolParameter(
                    name="rate",
                    type="int",
                    description="语速调节 (-10到+10)",
                    required=False,
                    default=0
                )
            ]
        )

    def execute(self, text: str, output_path: str = None,
                voice: str = "zh-CN-XiaoxiaoNeural", rate: int = 0) -> ToolResult:
        """生成TTS配音"""
        import time
        start_time = time.time()

        try:
            from core.tts_module import get_tts_module

            tts = get_tts_module()
            tts.voice = voice
            tts.set_rate(rate)

            if not output_path:
                import uuid
                from pathlib import Path
                from config import OUTPUT_DIR
                output_dir = OUTPUT_DIR / "temp"
                output_dir.mkdir(parents=True, exist_ok=True)
                output_path = str(output_dir / f"tts_{uuid.uuid4().hex[:8]}.wav")

            success = tts.generate_audio(text, output_path)

            if success:
                duration = tts.get_audio_duration(output_path)
                return ToolResult(
                    tool_name=self.definition.name,
                    success=True,
                    result={"path": output_path, "duration": duration, "voice": voice},
                    execution_time=time.time() - start_time
                )
            else:
                return ToolResult(
                    tool_name=self.definition.name,
                    success=False,
                    error="TTS生成失败，请检查edge-tts是否安装",
                    execution_time=time.time() - start_time
                )

        except Exception as e:
            return ToolResult(
                tool_name=self.definition.name,
                success=False,
                error=str(e),
                execution_time=time.time() - start_time
            )
