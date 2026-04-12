# -*- coding: utf-8 -*-
"""
脚本生成工具
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.tools.tool_base import BaseTool, ToolDefinition, ToolParameter, ToolCategory, ToolResult


class ScriptGenerateTool(BaseTool):
    """脚本生成工具 - generate_script()"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="generate_script",
            category=ToolCategory.SCRIPT,
            description="为指定选题生成爆款口播脚本，包含黄金3秒钩子、主体内容、行动号召和分镜表",
            parameters=[
                ToolParameter(
                    name="topic",
                    type="dict",
                    description="选题信息字典，包含 title, hook, category, tags 等字段",
                    required=True
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
                    name="style",
                    type="str",
                    description="风格类型",
                    required=False,
                    default="爆款",
                    enum_values=["爆款", "温和", "专业"]
                )
            ]
        )

    def execute(self, topic: dict, platform: str = "抖音", duration: int = 30, style: str = "爆款") -> ToolResult:
        """生成口播脚本"""
        import time
        start_time = time.time()

        try:
            from core.script_module import ScriptModule

            script_mod = ScriptModule()

            # 调用脚本生成
            script_result = script_mod.generate_script(
                topic=topic,
                platform=platform,
                video_duration=duration,
                style=style
            )

            return ToolResult(
                tool_name=self.definition.name,
                success=True,
                result=script_result,
                execution_time=time.time() - start_time,
                metadata={"platform": platform, "duration": duration}
            )

        except Exception as e:
            return ToolResult(
                tool_name=self.definition.name,
                success=False,
                error=str(e),
                execution_time=time.time() - start_time
            )
