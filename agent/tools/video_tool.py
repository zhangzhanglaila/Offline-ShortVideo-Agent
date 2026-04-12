# -*- coding: utf-8 -*-
"""
视频剪辑工具
"""
import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.tools.tool_base import BaseTool, ToolDefinition, ToolParameter, ToolCategory, ToolResult


class VideoEditTool(BaseTool):
    """视频剪辑工具 - render_video()"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="render_video",
            category=ToolCategory.VIDEO,
            description="将图片和音频素材合成视频，支持设置每张图片时长、转场效果、BGM",
            parameters=[
                ToolParameter(
                    name="image_paths",
                    type="list",
                    description="图片路径列表",
                    required=True
                ),
                ToolParameter(
                    name="output_path",
                    type="str",
                    description="输出视频路径",
                    required=False
                ),
                ToolParameter(
                    name="duration_per_image",
                    type="int",
                    description="每张图片持续秒数",
                    required=False,
                    default=3
                ),
                ToolParameter(
                    name="transition",
                    type="str",
                    description="转场效果",
                    required=False,
                    default="fade",
                    enum_values=["fade", "wipe", "none"]
                ),
                ToolParameter(
                    name="bgm_path",
                    type="str",
                    description="BGM音乐路径",
                    required=False,
                    default=None
                )
            ]
        )

    def execute(self, image_paths: list, output_path: str = None,
                duration_per_image: int = 3, transition: str = "fade",
                bgm_path: str = None) -> ToolResult:
        """剪辑视频"""
        import time
        start_time = time.time()

        try:
            from core.video_module import VideoModule

            video_mod = VideoModule()

            # 生成默认输出路径
            if not output_path:
                output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "output", "temp")
                os.makedirs(output_dir, exist_ok=True)
                output_path = os.path.join(output_dir, f"video_{uuid.uuid4().hex[:8]}.mp4")

            # 调用视频生成
            success = video_mod.create_video_from_images(
                images=image_paths,
                output=output_path,
                duration=duration_per_image,
                transition=transition,
                bgm=bgm_path
            )

            return ToolResult(
                tool_name=self.definition.name,
                success=success,
                result={"output_path": output_path} if success else None,
                error=None if success else "视频生成失败",
                execution_time=time.time() - start_time,
                metadata={"image_count": len(image_paths)}
            )

        except Exception as e:
            return ToolResult(
                tool_name=self.definition.name,
                success=False,
                error=str(e),
                execution_time=time.time() - start_time
            )
