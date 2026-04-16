# -*- coding: utf-8 -*-
"""
动画生成工具
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.tools.tool_base import BaseTool, ToolDefinition, ToolParameter, ToolCategory, ToolResult


class AnimationGenerateTool(BaseTool):
    """动画生成工具 - generate_animation()"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="generate_animation",
            category=ToolCategory.VIDEO,
            description="生成带Ken Burns缩放效果的动画视频，支持文字动画叠加和时间轴同步",
            parameters=[
                ToolParameter(
                    name="image_paths",
                    type="list",
                    description="图片路径列表",
                    required=True
                ),
                ToolParameter(
                    name="timeline",
                    type="list",
                    description="时间轴列表，每项包含 start, end, text, image_index",
                    required=True
                ),
                ToolParameter(
                    name="output_path",
                    type="str",
                    description="输出视频路径",
                    required=False
                ),
                ToolParameter(
                    name="animation_style",
                    type="str",
                    description="动画风格",
                    required=False,
                    default="ken_burns",
                    enum_values=["ken_burns", "pan_zoom", "static"]
                ),
                ToolParameter(
                    name="transition",
                    type="str",
                    description="转场效果",
                    required=False,
                    default="fade",
                    enum_values=["fade", "dissolve", "wipe", "none"]
                )
            ]
        )

    def execute(self, image_paths: list, timeline: list,
                output_path: str = None, animation_style: str = "ken_burns",
                transition: str = "fade") -> ToolResult:
        """生成动画视频"""
        import time
        start_time = time.time()

        try:
            from core.animation_module import get_animation_module

            anim = get_animation_module()

            if not output_path:
                import uuid
                from pathlib import Path
                from config import OUTPUT_DIR
                output_dir = OUTPUT_DIR / "temp"
                output_dir.mkdir(parents=True, exist_ok=True)
                output_path = str(output_dir / f"anim_{uuid.uuid4().hex[:8]}.mp4")

            success = anim.create_animated_video_from_segments(
                images=image_paths,
                segments=timeline,
                output_path=output_path,
                animation_style=animation_style,
                transition=transition
            )

            if success:
                return ToolResult(
                    tool_name=self.definition.name,
                    success=True,
                    result={"path": output_path, "image_count": len(image_paths), "segments": len(timeline)},
                    execution_time=time.time() - start_time
                )
            else:
                return ToolResult(
                    tool_name=self.definition.name,
                    success=False,
                    error="动画视频生成失败",
                    execution_time=time.time() - start_time
                )

        except Exception as e:
            return ToolResult(
                tool_name=self.definition.name,
                success=False,
                error=str(e),
                execution_time=time.time() - start_time
            )
