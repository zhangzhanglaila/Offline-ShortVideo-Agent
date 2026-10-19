# -*- coding: utf-8 -*-
"""提示词模板：约束 LLM 只产出可直接逐帧渲染的 HTML。"""
from __future__ import annotations

STYLES = {
    "minimal": {"name": "极简", "hint": "大留白、细线条、黑白灰为主，只用一个强调色"},
    "tech": {"name": "科技", "hint": "深色背景、霓虹蓝紫、网格或数据流、发光效果"},
    "guofeng": {"name": "国风", "hint": "宣纸米黄底、墨色、朱红点缀、竖排或留白构图"},
    "vivid": {"name": "活泼", "hint": "高饱和撞色、圆角几何、弹跳式节奏"},
    "cinematic": {"name": "电影感", "hint": "暗调、渐变光晕、慢速推进、轻微颗粒感"},
}

DEFAULT_STYLE = "minimal"


def build_html_prompt(
    topic: str,
    style: str = DEFAULT_STYLE,
    duration: float = 5,
    width: int = 1080,
    height: int = 1920,
) -> str:
    """构造生成 HTML 动效的提示词。

    约束越硬，后续逐帧渲染越稳：单文档、纯 CSS 动画、无脚本、无外部资源。
    """
    style_meta = STYLES.get(style, STYLES[DEFAULT_STYLE])
    return f"""你是一位动效设计师兼前端工程师。请为下面的主题写一个**竖屏动效页面**，
最终会被逐帧截图合成为视频，所以必须严格满足以下全部约束。

# 主题
{topic}

# 视觉风格
{style_meta['name']}：{style_meta['hint']}

# 硬性约束（违反任何一条都会导致渲染失败）
1. 只输出一个完整的 HTML 文档，从 `<!DOCTYPE html>` 开始、`</html>` 结束。
   不要输出 Markdown 代码围栏，不要任何解释文字。
2. 画布固定 {width}×{height}：`html,body{{margin:0;padding:0;width:{width}px;height:{height}px;overflow:hidden;}}`
3. 只能使用内联 `<style>` 与内联 `<svg>`。禁止 `<script>`、`<img>`、`<video>`、
   `<link>`、`@import` 和任何 http(s) 外部资源（字体也只能用系统字体）。
4. 所有动画必须用 CSS `@keyframes` + `animation`，并设置 `animation-fill-mode: both`。
5. 整段动画总时长 {duration} 秒：所有 animation-duration 与 animation-delay
   加起来不超过 {duration}s，第 0 秒就开始有内容，最后一帧仍在画面内。
6. 不要使用无限循环动画（`infinite`），否则逐帧截图会抖动。
7. 字体栈：`"Microsoft YaHei","PingFang SC","Noto Sans SC",sans-serif`；
   正文不小于 48px，标题不小于 90px，保证手机竖屏可读。
8. 画面分 2~3 个节拍：先出现主标题，再出现要点/图解，最后收尾。
   元素之间用淡入、上移、缩放、描边生长等简单动作即可，不要炫技。

# 输出
直接给出 HTML 源码本身。"""
