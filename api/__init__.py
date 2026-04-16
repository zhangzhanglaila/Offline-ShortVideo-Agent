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

__all__ = [
    'agent_api',
    'generate_api',
    'material_api',
    'system_api',
    'topic_api',
    'work_api',
    'tts_api',
    'dual_mode_api',
]
