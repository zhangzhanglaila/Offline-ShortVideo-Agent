"""AI 生视频服务层"""
from .base import (
    AIVideoGenerationService,
    VideoGenerationRequest,
    VideoGenerationProgress,
    VideoGenerationResult,
    VideoProvider,
    VideoSize,
    VideoResolution,
    VideoTaskStatus,
)
from .dashscope_generator import DashScopeVideoGenerator
from .comfyui_generator import ComfyUIVideoGenerator  # E5
from .skills import (
    VideoSkill,
    SkillError,
    SkillNotFound,
    SkillParamError,
    get_skill,
    load_skills,
    render_prompt,
)

__all__ = [
    'AIVideoGenerationService',
    'VideoGenerationRequest',
    'VideoGenerationProgress',
    'VideoGenerationResult',
    'VideoProvider',
    'VideoSize',
    'VideoResolution',
    'VideoTaskStatus',
    'DashScopeVideoGenerator',
    'ComfyUIVideoGenerator',
    'VideoSkill',
    'SkillError',
    'SkillNotFound',
    'SkillParamError',
    'get_skill',
    'load_skills',
    'render_prompt',
]
