# -*- coding: utf-8 -*-
"""
素材读取工具
"""
import sys
import os
from pathlib import Path
from typing import List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.tools.tool_base import BaseTool, ToolDefinition, ToolParameter, ToolCategory, ToolResult


class MaterialReadingTool(BaseTool):
    """素材读取工具 - get_local_materials()"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_local_materials",
            category=ToolCategory.MATERIAL,
            description="读取素材池中的图片、视频、音频文件列表，支持按类型筛选",
            parameters=[
                ToolParameter(
                    name="material_type",
                    type="str",
                    description="素材类型",
                    required=False,
                    default="all",
                    enum_values=["all", "image", "video", "audio"]
                ),
                ToolParameter(
                    name="limit",
                    type="int",
                    description="返回数量限制",
                    required=False,
                    default=20
                )
            ]
        )

    def execute(self, material_type: str = "all", limit: int = 20) -> ToolResult:
        """获取本地素材列表"""
        import time
        start_time = time.time()

        try:
            # 延迟导入避免循环依赖
            from core.video_module import VideoModule

            video_mod = VideoModule()
            items = []

            if material_type == "image" or material_type == "all":
                images = video_mod.get_material_images()
                for p in images[:limit]:
                    items.append({
                        "type": "image",
                        "path": p,
                        "name": Path(p).name,
                        "size": os.path.getsize(p) if os.path.exists(p) else 0
                    })

            if material_type == "video" or material_type == "all":
                videos = video_mod.get_material_videos()
                for p in videos[:limit]:
                    items.append({
                        "type": "video",
                        "path": p,
                        "name": Path(p).name,
                        "size": os.path.getsize(p) if os.path.exists(p) else 0
                    })

            if material_type == "audio" or material_type == "all":
                audios = video_mod.get_available_bgm()
                for p in audios[:limit]:
                    items.append({
                        "type": "audio",
                        "path": p,
                        "name": Path(p).name,
                        "size": os.path.getsize(p) if os.path.exists(p) else 0
                    })

            return ToolResult(
                tool_name=self.definition.name,
                success=True,
                result={"materials": items[:limit], "count": len(items)},
                execution_time=time.time() - start_time,
                metadata={"type": material_type}
            )

        except Exception as e:
            return ToolResult(
                tool_name=self.definition.name,
                success=False,
                error=str(e),
                execution_time=time.time() - start_time
            )
