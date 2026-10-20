# -*- coding: utf-8 -*-
"""
转场效果系统
为视频添加多种转场效果
"""
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import math


class TransitionType(Enum):
    """转场效果类型"""
    FADE = "fade"              # 淡入淡出
    SLIDE = "slide"            # 滑动
    WIPE = "wipe"              # 擦除
    ZOOM = "zoom"              # 缩放
    BLUR = "blur"              # 模糊
    ROTATE = "rotate"          # 旋转
    FLIP = "flip"              # 翻转
    PIXELATE = "pixelate"      # 像素化
    MOSAIC = "mosaic"          # 马赛克
    WAVE = "wave"              # 波浪


class TransitionDirection(Enum):
    """转场方向"""
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"
    CENTER = "center"


@dataclass
class TransitionConfig:
    """转场配置"""
    type: TransitionType
    duration: float = 1.0          # 持续时间(秒)
    direction: TransitionDirection = TransitionDirection.LEFT
    easing: str = "ease_out"       # 缓动函数
    intensity: float = 1.0         # 强度 0.0-1.0


class TransitionEffect:
    """转场效果生成器"""

    def __init__(self, width: int = 1080, height: int = 1920):
        self.width = width
        self.height = height

    def apply_fade(
        self,
        frame_a,
        frame_b,
        progress: float
    ):
        """
        淡入淡出过渡

        Args:
            frame_a: 起始帧
            frame_b: 目标帧
            progress: 进度 0.0-1.0
        """
        try:
            from PIL import Image

            # 计算混合比例
            alpha = progress

            # 创建淡入淡出效果
            result = Image.blend(frame_a, frame_b, alpha)
            return result
        except Exception as e:
            print(f"[TransitionEffect] Fade failed: {e}")
            return frame_b

    def apply_slide(
        self,
        frame_a,
        frame_b,
        progress: float,
        direction: TransitionDirection = TransitionDirection.LEFT
    ):
        """
        滑动过渡

        Args:
            frame_a: 起始帧
            frame_b: 目标帧
            progress: 进度
            direction: 滑动方向
        """
        try:
            from PIL import Image, ImageDraw

            result = frame_a.copy()

            # 计算滑动距离
            if direction == TransitionDirection.LEFT:
                offset = int(self.width * progress)
                box_a = (-offset, 0, self.width - offset, self.height)
                box_b = (self.width - offset, 0, self.width + (self.width - offset), self.height)
            elif direction == TransitionDirection.RIGHT:
                offset = int(self.width * progress)
                box_a = (offset, 0, self.width + offset, self.height)
                box_b = (-self.width + offset, 0, offset, self.height)
            elif direction == TransitionDirection.UP:
                offset = int(self.height * progress)
                box_a = (0, -offset, self.width, self.height - offset)
                box_b = (0, self.height - offset, self.width, self.height + (self.height - offset))
            elif direction == TransitionDirection.DOWN:
                offset = int(self.height * progress)
                box_a = (0, offset, self.width, self.height + offset)
                box_b = (0, -self.height + offset, self.width, offset)
            else:
                return frame_b

            # 组合帧
            result.paste(frame_b, box_b)
            return result

        except Exception as e:
            print(f"[TransitionEffect] Slide failed: {e}")
            return frame_b

    def apply_zoom(
        self,
        frame_a,
        frame_b,
        progress: float,
        direction: str = "in"
    ):
        """
        缩放过渡

        Args:
            frame_a: 起始帧
            frame_b: 目标帧
            progress: 进度
            direction: 缩放方向 (in/out)
        """
        try:
            from PIL import Image

            if direction == "in":
                # 从小到大
                scale = 0.5 + 0.5 * progress
            else:
                # 从大到小
                scale = 1.5 - 0.5 * progress

            # 缩放frame_b
            new_width = int(self.width * scale)
            new_height = int(self.height * scale)

            frame_b_scaled = frame_b.resize((new_width, new_height), Image.LANCZOS)

            # 居中粘贴
            x_offset = (self.width - new_width) // 2
            y_offset = (self.height - new_height) // 2

            result = frame_a.copy()
            result.paste(frame_b_scaled, (x_offset, y_offset))

            return Image.blend(result, frame_b, progress)

        except Exception as e:
            print(f"[TransitionEffect] Zoom failed: {e}")
            return frame_b

    def apply_blur_transition(
        self,
        frame_a,
        frame_b,
        progress: float
    ):
        """
        模糊过渡

        Args:
            frame_a: 起始帧
            frame_b: 目标帧
            progress: 进度
        """
        try:
            from PIL import Image, ImageFilter

            # 第一半：frame_a变模糊
            if progress < 0.5:
                blur_radius = int(30 * (progress * 2))
                blurred = frame_a.filter(ImageFilter.GaussianBlur(blur_radius))
                return Image.blend(blurred, frame_a, 1 - progress * 2)
            else:
                # 第二半：显示frame_b
                blur_radius = int(30 * ((1 - progress) * 2))
                blurred = frame_b.filter(ImageFilter.GaussianBlur(blur_radius))
                return Image.blend(frame_b, blurred, 1 - (progress - 0.5) * 2)

        except Exception as e:
            print(f"[TransitionEffect] Blur transition failed: {e}")
            return frame_b

    def apply_wipe(
        self,
        frame_a,
        frame_b,
        progress: float,
        direction: TransitionDirection = TransitionDirection.LEFT
    ):
        """
        擦除过渡

        Args:
            frame_a: 起始帧
            frame_b: 目标帧
            progress: 进度
            direction: 擦除方向
        """
        try:
            from PIL import Image, ImageDraw

            result = frame_a.copy()

            # 根据方向计算擦除区域
            if direction == TransitionDirection.LEFT:
                wipe_x = int(self.width * progress)
                result.paste(frame_b, (wipe_x, 0, self.width, self.height))
            elif direction == TransitionDirection.RIGHT:
                wipe_x = int(self.width * (1 - progress))
                result.paste(frame_b, (0, 0, wipe_x, self.height))
            elif direction == TransitionDirection.UP:
                wipe_y = int(self.height * progress)
                result.paste(frame_b, (0, wipe_y, self.width, self.height))
            elif direction == TransitionDirection.DOWN:
                wipe_y = int(self.height * (1 - progress))
                result.paste(frame_b, (0, 0, self.width, wipe_y))

            return result

        except Exception as e:
            print(f"[TransitionEffect] Wipe failed: {e}")
            return frame_b

    def apply_rotate(
        self,
        frame_a,
        frame_b,
        progress: float
    ):
        """
        旋转过渡

        Args:
            frame_a: 起始帧
            frame_b: 目标帧
            progress: 进度
        """
        try:
            from PIL import Image

            # 计算旋转角度
            angle = 360 * progress

            # 旋转frame_a
            rotated = frame_a.rotate(angle, expand=False, center=(self.width//2, self.height//2))

            # 混合
            return Image.blend(rotated, frame_b, progress)

        except Exception as e:
            print(f"[TransitionEffect] Rotate failed: {e}")
            return frame_b


class TransitionLibrary:
    """转场效果库 - 管理和应用转场效果"""

    def __init__(self, width: int = 1080, height: int = 1920):
        self.effect = TransitionEffect(width, height)
        self.transitions: Dict[str, TransitionConfig] = {}

    def add_transition(
        self,
        name: str,
        config: TransitionConfig
    ):
        """添加转场配置"""
        self.transitions[name] = config

    def get_transition(self, name: str) -> Optional[TransitionConfig]:
        """获取转场配置"""
        return self.transitions.get(name)

    def list_transitions(self) -> List[str]:
        """列出所有转场效果"""
        return list(self.transitions.keys())

    def apply(
        self,
        frame_a,
        frame_b,
        transition_name: str,
        progress: float
    ):
        """
        应用转场效果

        Args:
            frame_a: 起始帧
            frame_b: 目标帧
            transition_name: 转场效果名称
            progress: 进度

        Returns:
            过渡后的帧
        """
        config = self.get_transition(transition_name)

        if not config:
            # 默认使用淡入淡出
            return self.effect.apply_fade(frame_a, frame_b, progress)

        # 应用指定的转场效果
        if config.type == TransitionType.FADE:
            return self.effect.apply_fade(frame_a, frame_b, progress)

        elif config.type == TransitionType.SLIDE:
            return self.effect.apply_slide(frame_a, frame_b, progress, config.direction)

        elif config.type == TransitionType.ZOOM:
            return self.effect.apply_zoom(frame_a, frame_b, progress)

        elif config.type == TransitionType.BLUR:
            return self.effect.apply_blur_transition(frame_a, frame_b, progress)

        elif config.type == TransitionType.WIPE:
            return self.effect.apply_wipe(frame_a, frame_b, progress, config.direction)

        elif config.type == TransitionType.ROTATE:
            return self.effect.apply_rotate(frame_a, frame_b, progress)

        else:
            # 不支持的效果，使用淡入淡出
            return self.effect.apply_fade(frame_a, frame_b, progress)


# 预设转场效果
def create_preset_library(width: int = 1080, height: int = 1920) -> TransitionLibrary:
    """创建预设转场库"""
    lib = TransitionLibrary(width, height)

    # 基础转场
    lib.add_transition("fade", TransitionConfig(TransitionType.FADE, duration=0.5))
    lib.add_transition("fade_slow", TransitionConfig(TransitionType.FADE, duration=1.0))
    lib.add_transition("fade_fast", TransitionConfig(TransitionType.FADE, duration=0.2))

    # 滑动转场
    lib.add_transition("slide_left", TransitionConfig(
        TransitionType.SLIDE, duration=0.6, direction=TransitionDirection.LEFT
    ))
    lib.add_transition("slide_right", TransitionConfig(
        TransitionType.SLIDE, duration=0.6, direction=TransitionDirection.RIGHT
    ))
    lib.add_transition("slide_up", TransitionConfig(
        TransitionType.SLIDE, duration=0.6, direction=TransitionDirection.UP
    ))
    lib.add_transition("slide_down", TransitionConfig(
        TransitionType.SLIDE, duration=0.6, direction=TransitionDirection.DOWN
    ))

    # 缩放转场
    lib.add_transition("zoom_in", TransitionConfig(TransitionType.ZOOM, duration=0.5))
    lib.add_transition("zoom_out", TransitionConfig(TransitionType.ZOOM, duration=0.5))

    # 模糊转场
    lib.add_transition("blur", TransitionConfig(TransitionType.BLUR, duration=0.8))

    # 擦除转场
    lib.add_transition("wipe_left", TransitionConfig(
        TransitionType.WIPE, duration=0.7, direction=TransitionDirection.LEFT
    ))
    lib.add_transition("wipe_right", TransitionConfig(
        TransitionType.WIPE, duration=0.7, direction=TransitionDirection.RIGHT
    ))

    # 旋转转场
    lib.add_transition("rotate", TransitionConfig(TransitionType.ROTATE, duration=0.8))

    return lib
