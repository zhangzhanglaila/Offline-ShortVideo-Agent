"""
文字入场动画 - 生成FFmpeg滤镜片段。

将透明元素层叠加到背景上，并施加入场动画(淡入/上滑/下滑)。
支持多个元素错开时间出现，制造"逐个入场"的编排感。

技术：
- fade(alpha)滤镜实现透明度淡入
- overlay位置表达式实现滑动(带ease-out缓动)
- 每层用独立的start时间 → 元素先后出现
"""

from typing import Tuple

from core.compose.motion.animation_spec import (
    AnimationSpec,
    ANIM_NONE, ANIM_FADE_IN, ANIM_SLIDE_UP, ANIM_SLIDE_DOWN,
    ANIM_SLIDE_LEFT, ANIM_SLIDE_RIGHT,
)


# 默认滑动距离(占画面高度/宽度比例)
_DEFAULT_SLIDE_RATIO = 0.045
_DEFAULT_HSLIDE_RATIO = 0.06


def build_overlay_filter(
    input_idx: int,
    prev_label: str,
    out_label: str,
    anim: AnimationSpec,
    size: Tuple[int, int],
) -> str:
    """构建单个覆盖层的FFmpeg滤镜片段。

    Args:
        input_idx: 该层在ffmpeg输入中的索引(如1,2,...)
        prev_label: 前一级视频流标签(如"bg"或"t0")
        out_label: 本级输出标签
        anim: 入场动画规格
        size: 画面尺寸 (宽, 高)

    Returns:
        滤镜片段字符串(以;分隔的一或两段)
    """
    w, h = size
    ov = f"ovsrc{input_idx}"

    # 静态层：直接overlay，无动画
    if anim.is_static:
        return (
            f"[{input_idx}:v]format=rgba[{ov}];"
            f"[{prev_label}][{ov}]overlay=0:0:eof_action=pass[{out_label}]"
        )

    start = anim.start
    dur = max(0.05, anim.duration)
    p = f"clip((t-{start:.3f})/{dur:.3f}\\,0\\,1)"

    # 垂直滑动：淡入 + Y位置动画
    if anim.anim_type in (ANIM_SLIDE_UP, ANIM_SLIDE_DOWN):
        slide = int(anim.params.get("slide_px", h * _DEFAULT_SLIDE_RATIO))
        if anim.anim_type == ANIM_SLIDE_DOWN:
            slide = -slide  # 从上方滑入
        # ease-out: offset = slide*(1-p)^2
        y_expr = f"{slide}*(1-{p})*(1-{p})"
        return (
            f"[{input_idx}:v]format=rgba,"
            f"fade=t=in:st={start:.3f}:d={dur:.3f}:alpha=1[{ov}];"
            f"[{prev_label}][{ov}]overlay=x=0:y='{y_expr}':"
            f"eof_action=pass[{out_label}]"
        )

    # 水平滑动：淡入 + X位置动画
    if anim.anim_type in (ANIM_SLIDE_LEFT, ANIM_SLIDE_RIGHT):
        slide = int(anim.params.get("slide_px", w * _DEFAULT_HSLIDE_RATIO))
        if anim.anim_type == ANIM_SLIDE_LEFT:
            slide = -slide  # 从左侧滑入(起点在左，x为负偏移)
        x_expr = f"{slide}*(1-{p})*(1-{p})"
        return (
            f"[{input_idx}:v]format=rgba,"
            f"fade=t=in:st={start:.3f}:d={dur:.3f}:alpha=1[{ov}];"
            f"[{prev_label}][{ov}]overlay=x='{x_expr}':y=0:"
            f"eof_action=pass[{out_label}]"
        )

    # 淡入 / 缩放(暂等同淡入) / 其他：纯alpha淡入
    return (
        f"[{input_idx}:v]format=rgba,"
        f"fade=t=in:st={start:.3f}:d={dur:.3f}:alpha=1[{ov}];"
        f"[{prev_label}][{ov}]overlay=0:0:eof_action=pass[{out_label}]"
    )
