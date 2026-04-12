# -*- coding: utf-8 -*-
"""
多平台适配工具
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.tools.tool_base import BaseTool, ToolDefinition, ToolParameter, ToolCategory, ToolResult


class PlatformAdaptTool(BaseTool):
    """多平台适配工具 - adapt_platform_content()"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="adapt_platform_content",
            category=ToolCategory.PLATFORM,
            description="为视频适配多平台内容，生成标题、描述、话题标签和发布包",
            parameters=[
                ToolParameter(
                    name="video_path",
                    type="str",
                    description="视频路径",
                    required=True
                ),
                ToolParameter(
                    name="script_result",
                    type="dict",
                    description="脚本生成结果（包含hook、body、cta等）",
                    required=True
                ),
                ToolParameter(
                    name="platform",
                    type="str",
                    description="目标平台",
                    required=True,
                    enum_values=["抖音", "小红书", "B站"]
                )
            ]
        )

    def execute(self, video_path: str, script_result: dict, platform: str) -> ToolResult:
        """适配多平台内容"""
        import time
        start_time = time.time()

        try:
            from core.platform_module import PlatformModule

            platform_mod = PlatformModule()

            # 适配平台内容
            adapted_content = platform_mod.adapt_content(script_result, platform)

            # 导出发布包
            export_result = platform_mod.export_package(video_path, adapted_content)

            return ToolResult(
                tool_name=self.definition.name,
                success=export_result.get("success", False),
                result={
                    "adapted_content": adapted_content,
                    "export_result": export_result
                },
                execution_time=time.time() - start_time,
                metadata={"platform": platform}
            )

        except Exception as e:
            return ToolResult(
                tool_name=self.definition.name,
                success=False,
                error=str(e),
                execution_time=time.time() - start_time
            )
