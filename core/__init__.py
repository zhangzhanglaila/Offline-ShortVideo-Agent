# -*- coding: utf-8 -*-
"""
核心模块初始化
"""
from .topics_module import TopicsModule
from .script_module import ScriptModule
from .video_module import VideoModule
from .subtitle_module import SubtitleModule
from .platform_module import PlatformModule
from .analytics_module import AnalyticsModule

__all__ = [
    "TopicsModule",
    "ScriptModule",
    "VideoModule",
    "SubtitleModule",
    "PlatformModule",
    "AnalyticsModule",
]
