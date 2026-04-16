# -*- coding: utf-8 -*-
"""
图库抓取工具
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.tools.tool_base import BaseTool, ToolDefinition, ToolParameter, ToolCategory, ToolResult


class ImageFetchTool(BaseTool):
    """图库抓取工具 - fetch_images()"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="fetch_images",
            category=ToolCategory.MATERIAL,
            description="联网从Pexels/Unsplash图库抓取配图，支持根据脚本关键词自动抓取",
            parameters=[
                ToolParameter(
                    name="keywords",
                    type="str",
                    description="搜索关键词",
                    required=False
                ),
                ToolParameter(
                    name="script_text",
                    type="str",
                    description="脚本文本（会自动提取关键词抓取配图）",
                    required=False
                ),
                ToolParameter(
                    name="count",
                    type="int",
                    description="每个关键词抓取数量",
                    required=False,
                    default=5
                )
            ]
        )

    def execute(self, keywords: str = None, script_text: str = None,
                count: int = 5) -> ToolResult:
        """抓取配图"""
        import time
        start_time = time.time()

        try:
            from core.image_fetch_module import get_image_fetch_module

            fetcher = get_image_fetch_module()

            if script_text:
                results, paths = fetcher.fetch_by_script_keywords(script_text, count_per_keyword=count)
            elif keywords:
                results, paths = fetcher.fetch_and_download(keywords, count)
            else:
                return ToolResult(
                    tool_name=self.definition.name,
                    success=False,
                    error="必须提供 keywords 或 script_text",
                    execution_time=time.time() - start_time
                )

            usage = fetcher.get_usage_stats()

            return ToolResult(
                tool_name=self.definition.name,
                success=True,
                result={
                    "paths": paths,
                    "count": len(paths),
                    "usage": usage
                },
                execution_time=time.time() - start_time
            )

        except Exception as e:
            return ToolResult(
                tool_name=self.definition.name,
                success=False,
                error=str(e),
                execution_time=time.time() - start_time
            )
