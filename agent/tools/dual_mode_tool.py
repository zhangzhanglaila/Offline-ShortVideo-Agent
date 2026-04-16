# -*- coding: utf-8 -*-
"""
双模式生成工具
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.tools.tool_base import BaseTool, ToolDefinition, ToolParameter, ToolCategory, ToolResult


class DualModeGenerateTool(BaseTool):
    """双模式生成工具 - generate_dual_mode()"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="generate_dual_mode",
            category=ToolCategory.VIDEO,
            description="双模式视频生成：模式A(题材全自动)或模式B(素材智能剪辑)",
            parameters=[
                ToolParameter(
                    name="mode",
                    type="str",
                    description="生成模式",
                    required=True,
                    enum_values=["mode_a", "mode_b"]
                ),
                ToolParameter(
                    name="topic_keyword",
                    type="str",
                    description="题材关键词（mode_a使用）",
                    required=False
                ),
                ToolParameter(
                    name="category",
                    type="str",
                    description="赛道分类",
                    required=False
                ),
                ToolParameter(
                    name="platform",
                    type="str",
                    description="目标平台",
                    required=False,
                    default="抖音",
                    enum_values=["抖音", "小红书", "B站"]
                ),
                ToolParameter(
                    name="duration",
                    type="int",
                    description="视频时长(秒)",
                    required=False,
                    default=30
                ),
                ToolParameter(
                    name="material_paths",
                    type="list",
                    description="素材路径列表（mode_b使用）",
                    required=False
                ),
                ToolParameter(
                    name="voice",
                    type="str",
                    description="配音人（mode_a使用）",
                    required=False,
                    default="zh-CN-XiaoxiaoNeural"
                ),
                ToolParameter(
                    name="add_bgm",
                    type="bool",
                    description="是否添加BGM",
                    required=False,
                    default=True
                ),
                ToolParameter(
                    name="use_whisper",
                    type="bool",
                    description="是否使用Whisper字幕对齐",
                    required=False,
                    default=True
                )
            ]
        )

    def execute(self, mode: str, topic_keyword: str = None,
                category: str = None, platform: str = "抖音",
                duration: int = 30, material_paths: list = None,
                voice: str = "zh-CN-XiaoxiaoNeural",
                add_bgm: bool = True,
                use_whisper: bool = True) -> ToolResult:
        """双模式生成"""
        import time
        start_time = time.time()

        try:
            from core.dual_mode_module import get_dual_mode_generator

            generator = get_dual_mode_generator()

            if mode == "mode_a":
                result = generator.generate_mode_a(
                    topic_keyword=topic_keyword,
                    category=category,
                    platform=platform,
                    duration=duration,
                    voice=voice,
                    use_whisper_subtitle=use_whisper,
                    add_bgm=add_bgm,
                    fetch_images=True
                )
            elif mode == "mode_b":
                if not material_paths:
                    return ToolResult(
                        tool_name=self.definition.name,
                        success=False,
                        error="mode_b需要提供 material_paths",
                        execution_time=time.time() - start_time
                    )
                result = generator.generate_mode_b(
                    material_paths=material_paths,
                    platform=platform,
                    add_bgm=add_bgm,
                    add_subtitles=True,
                    use_whisper=use_whisper
                )
            else:
                return ToolResult(
                    tool_name=self.definition.name,
                    success=False,
                    error=f"未知模式: {mode}",
                    execution_time=time.time() - start_time
                )

            return ToolResult(
                tool_name=self.definition.name,
                success=result.get("success", False),
                result=result,
                error=result.get("error"),
                execution_time=time.time() - start_time
            )

        except Exception as e:
            return ToolResult(
                tool_name=self.definition.name,
                success=False,
                error=str(e),
                execution_time=time.time() - start_time
            )
