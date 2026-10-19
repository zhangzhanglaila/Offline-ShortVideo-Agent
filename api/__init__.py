# -*- coding: utf-8 -*-
"""
API路由注册
"""
from . import agent_api
from . import generate_api
from . import material_api
from . import system_api
from . import topic_api
from . import work_api
from . import tts_api
from . import dual_mode_api
from . import thinking_api
from . import timeline_api
from . import ecom_api
from . import template_api
from . import ai_image_api
from . import topic_video_api
from . import topic_pipeline_api

__all__ = [
    'agent_api',
    'generate_api',
    'material_api',
    'system_api',
    'topic_api',
    'work_api',
    'tts_api',
    'dual_mode_api',
    'thinking_api',
    'timeline_api',
    'ecom_api',
    'template_api',
    'ai_image_api',
    'topic_video_api',
    'topic_pipeline_api',
]
