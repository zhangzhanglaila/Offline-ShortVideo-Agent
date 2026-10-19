# -*- coding: utf-8 -*-
"""AI 动效：让文本 LLM 生成 HTML/CSS/SVG，再逐帧渲染成视频。

适用场景：没有生图/生视频预算，只有 DeepSeek 这类文本 API。
成本结构 = 一次文本补全 + 本地浏览器截图 + 本地 ffmpeg 编码。
"""
from .generator import (
    DEMO_HTML,
    HtmlGenerationError,
    extract_html,
    generate_html,
    sanitize_html,
)
from .prompts import DEFAULT_STYLE, STYLES, build_html_prompt
from .renderer import HtmlRenderError, render_html_to_video

__all__ = [
    'DEMO_HTML',
    'HtmlGenerationError',
    'HtmlRenderError',
    'extract_html',
    'generate_html',
    'sanitize_html',
    'DEFAULT_STYLE',
    'STYLES',
    'build_html_prompt',
    'render_html_to_video',
]
