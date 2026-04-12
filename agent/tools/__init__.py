# -*- coding: utf-8 -*-
"""
Agent工具集
"""
from .tool_base import BaseTool, ToolDefinition, ToolParameter, ToolResult, ToolCategory
from .material_tool import MaterialReadingTool
from .topic_tool import TopicRecommendTool
from .script_tool import ScriptGenerateTool
from .video_tool import VideoEditTool
from .subtitle_tool import SubtitleGenerateTool
from .platform_tool import PlatformAdaptTool

__all__ = [
    'BaseTool', 'ToolDefinition', 'ToolParameter', 'ToolResult', 'ToolCategory',
    'MaterialReadingTool', 'TopicRecommendTool', 'ScriptGenerateTool',
    'VideoEditTool', 'SubtitleGenerateTool', 'PlatformAdaptTool'
]

# 导出所有工具实例化函数
def get_all_tools():
    """获取所有可用工具实例"""
    return [
        MaterialReadingTool(),
        TopicRecommendTool(),
        ScriptGenerateTool(),
        VideoEditTool(),
        SubtitleGenerateTool(),
        PlatformAdaptTool(),
    ]
