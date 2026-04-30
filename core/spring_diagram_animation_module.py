# -*- coding: utf-8 -*-
"""
Spring-Powered 2D Flowchart Animation Module
Replaces linear animations with Remotion-style spring physics for professional motion graphics

Key improvements over diagram_animation_module:
- Spring physics for border drawing (natural bounce/settling)
- Easing for text reveals (back/elastic instead of linear)
- Scale animations on rect appearance
- Opacity fades with proper easing
- Staggered animations with proper timing
"""
import math
import random
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

from config import OUTPUT_WIDTH, OUTPUT_HEIGHT, OUTPUT_FPS, OUTPUT_CRF
from core.spring_easing import (
    spring, measure_spring, spring_calculation,
    Easing, interpolate, SpringConfig
)
from core.utils.ffmpeg_runner import run_ffmpeg_safe


# ==================== 字体加载 ====================

def _load_font(size: int, font_path: str = None) -> "ImageFont":
    """加载字体，优先用系统中文字体"""
    from PIL import ImageFont
    font_candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/System/Library/Fonts/PingFang.ttc",
    ]
    if font_path:
        font_candidates.insert(0, font_path)
    for fp in font_candidates:
        try:
            return ImageFont.truetype(fp, size)
        except Exception:
            continue
    return ImageFont.load_default()


# ==================== Spring动画配置 ====================

@dataclass
class SpringDamping:
    """预定义的弹簧阻尼配置"""
    # 快速弹性 - 用于边框绘制
    QUICK_BOUNCE = SpringConfig(damping=12, mass=0.8, stiffness=180)
    # 柔和进入 - 用于文字淡入
    SOFT_ENTRY = SpringConfig(damping=15, mass=1.0, stiffness=100)
    # 强力回弹 - 用于元素出现
    STRONG_BOUNCE = SpringConfig(damping=8, mass=0.5, stiffness=200)
    # 稳定到达 - 无超调
    STABLE = SpringConfig(damping=20, mass=1.0, stiffness=100, overshootClamping=True)


# ==================== 动画元素定义 ====================

@dataclass
class Rect:
    """矩形模块 - 带Spring动画状态"""
    x0: int
    y0: int
    x1: int
    y1: int
    label: str = ""
    sub_label: str = ""
    color: str = "#4EC9B0"
    fill_color: str = "#1E1E1E"
    font_size: int = 20
    sub_font_size: int = 14
    visible: bool = False
    border_drawn: float = 0.0
    highlighted: bool = False
    highlight_color: str = "#CE9178"
    # Spring动画专用
    scale: float = 1.0        # 缩放 (0.0=隐藏, 1.0=正常)
    opacity: float = 1.0       # 透明度
    text_progress: float = 0.0 # 文字显现进度

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def center(self) -> Tuple[int, int]:
        return ((self.x0 + self.x1) // 2, (self.y0 + self.y1) // 2)


@dataclass
class Arrow:
    """箭头/连线 - 带Spring动画"""
    x0: int
    y0: int
    x1: int
    y1: int
    label: str = ""
    color: str = "#808080"
    width: int = 2
    drawn: float = 0.0
    arrow_head_size: int = 10
    curved: bool = False
    bidirection: bool = False
    # Spring动画专用
    opacity: float = 1.0
    line_progress: float = 0.0  # 线条绘制进度

    def path_points(self, t: float) -> Tuple[Tuple[int, int], Tuple[int, int]]:
        cx = self.x0 + (self.x1 - self.x0) * t
        cy = self.y0 + (self.y1 - self.y0) * t
        return (self.x0, self.y0), (int(cx), int(cy))


# ==================== Spring动画渲染器 ====================

class SpringDiagramRenderer:
    """
    Spring物理动画的图表渲染器

    相比线性动画的优势:
    1. 边框绘制用spring，有自然的回弹感
    2. 元素出现有scale+bounce效果
    3. 文字淡入用back/elastic easing，不再是线性和渐出
    4. 箭头生长有弹性效果
    """

    BG_COLOR = (30, 30, 30)
    GRID_COLOR = (45, 45, 45)
    TEXT_COLOR = (212, 212, 212)
    ACCENT_COLOR = (78, 201, 176)
    ARROW_COLOR = (128, 128, 128)
    HIGHLIGHT_COLOR = (206, 145, 120)
    FILL_COLOR = (37, 37, 38)

    def __init__(self, width: int, height: int, fps: int = OUTPUT_FPS):
        from PIL import Image, ImageDraw
        self.width = width
        self.height = height
        self.fps = fps
        self.img = Image.new("RGB", (width, height), self.BG_COLOR)
        self.draw = ImageDraw.Draw(self.img)
        self.font_cache = {}

    def _get_font(self, size: int) -> "ImageFont":
        if size not in self.font_cache:
            self.font_cache[size] = _load_font(size)
        return self.font_cache[size]

    def render_frame(self, elements: Dict, global_progress: float, fps: int = None) -> "Image":
        """
        渲染一帧 - 使用Spring动画

        elements: {"rects": [...], "arrows": [...], "active_idx": int}
        global_progress: 全局动画进度 0.0~1.0
        """
        from PIL import ImageDraw
        draw = ImageDraw.Draw(self.img)
        self.img.paste(self.BG_COLOR, (0, 0, self.width, self.height))

        rects = elements.get("rects", [])
        arrows = elements.get("arrows", [])
        active_idx = elements.get("active_idx", -1)
        fps = fps or self.fps

        # 1. 先画箭头（底层）
        for i, arrow in enumerate(arrows):
            self._render_arrow_spring(draw, arrow, global_progress, i, active_idx, fps)

        # 2. 再画矩形（顶层）
        for i, rect in enumerate(rects):
            self._render_rect_spring(draw, rect, global_progress, i, active_idx, fps)

        return self.img

    def _render_rect_spring(
        self,
        draw: "ImageDraw.Draw",
        rect: Rect,
        global_progress: float,
        idx: int,
        active_idx: int,
        fps: int,
    ):
        """用Spring动画渲染单个矩形"""
        if idx > active_idx:
            return

        # 该元素自己的进度（0.0~1.0）
        if idx < active_idx:
            draw_ratio = 1.0
            rect.scale = 1.0
            rect.opacity = 1.0
            rect.text_progress = 1.0
        else:
            # 当前激活元素：使用spring动画
            # 边框绘制：用spring + back easing (先行后跳)
            border_spring = spring(
                global_progress * 2,  # 0~0.5映射到0~1
                fps,
                config=SpringDamping.STRONG_BOUNCE,
                from_val=0,
                to_val=1,
            )
            draw_ratio = min(1.0, border_spring)

            # 元素整体scale：0.8→1.0的spring回弹
            rect.scale = interpolate(
                global_progress,
                [0, 0.3, 1.0],
                [0.85, 1.08, 1.0],
                Easing.out(Easing.back(1.2))
            )

            # 文字显现：后50%进行，用back easing
            if global_progress > 0.5:
                text_p = (global_progress - 0.5) * 2
                rect.text_progress = spring(
                    text_p, fps,
                    config=SpringDamping.SOFT_ENTRY,
                    from_val=0, to_val=1,
                )
            else:
                rect.text_progress = 0.0

        if draw_ratio <= 0 and rect.scale < 0.1:
            return

        x0, y0, x1, y1 = rect.x0, rect.y0, rect.x1, rect.y1
        w, h = rect.width, rect.height

        # 应用scale（以中心为原点）
        cx, cy = rect.center
        scale = rect.scale
        new_w = int(w * scale)
        new_h = int(h * scale)
        x0a = cx - new_w // 2
        y0a = cy - new_h // 2
        x1a = cx + new_w // 2
        y1a = cy + new_h // 2

        # 填充背景
        fill = self._hex_to_rgb(rect.fill_color)
        draw.rectangle([x0a, y0a, x1a, y1a], fill=fill)

        # 高亮效果
        if rect.highlighted:
            hl = self._hex_to_rgb(rect.highlight_color)
            for offset in range(3, 0, -1):
                alpha_fill = tuple(max(0, min(255, c + 30 * (4 - offset))) for c in hl)
                draw.rectangle(
                    [x0a - offset, y0a - offset, x1a + offset, y1a + offset],
                    fill=None, outline=alpha_fill
                )
            draw.rectangle(
                [x0a - 4, y0a - 4, x1a + 4, y1a + 4],
                fill=None, outline=hl, width=2
            )

        # 边框绘制动画
        if draw_ratio < 1.0:
            x0b, y0b, x1b, y1b = self._clip_rect_by_ratio_scaled(
                x0a, y0a, x1a, y1a, draw_ratio
            )
        else:
            x0b, y0b, x1b, y1b = x0a, y0a, x1a, y1a

        border_color = self._hex_to_rgb(rect.color)
        draw.rectangle([x0b, y0b, x1b, y1b], fill=None, outline=border_color, width=2)

        # 文字显现
        if rect.text_progress > 0 and rect.label:
            # 文字也有scale效果
            text_scale = interpolate(rect.text_progress, [0, 0.5, 1.0], [0.5, 1.0, 1.0])
            self._render_label_centered(
                draw, rect.label, x0a, y0a, x1a, y1a,
                rect.text_progress, rect.font_size,
                rect.text_color if hasattr(rect, 'text_color') else "#D4D4D4"
            )

    def _clip_rect_by_ratio_scaled(
        self, x0: int, y0: int, x1: int, y1: int, ratio: float
    ) -> Tuple[int, int, int, int]:
        """按比例裁剪矩形（中心缩放）"""
        w = x1 - x0
        h = y1 - y0
        cx = (x0 + x1) // 2
        cy = (y0 + y1) // 2
        new_w = w * ratio
        new_h = h * ratio
        return (
            int(cx - new_w / 2), int(cy - new_h / 2),
            int(cx + new_w / 2), int(cy + new_h / 2)
        )

    def _render_label_centered(
        self, draw: "ImageDraw.Draw", text: str,
        x0: int, y0: int, x1: int, y1: int,
        ratio: float, font_size: int, color: str
    ):
        """在矩形区域中心渲染文字，带scale效果"""
        font = self._get_font(font_size)
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
        except Exception:
            text_w = font_size * len(text) * 0.6
            text_h = font_size

        # 根据ratio决定显示多少字符
        if ratio < 1.0:
            char_count = max(1, int(len(text) * ratio))
            text = text[:char_count]

        cx = (x0 + x1) // 2
        cy = (y0 + y1) // 2

        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text((cx - tw // 2, cy - th // 2), text, font=font, fill=self._hex_to_rgb(color))
        except Exception:
            draw.text((cx - text_w // 2, cy - text_h // 2), text, font=font, fill=self._hex_to_rgb(color))

    def _render_arrow_spring(
        self,
        draw: "ImageDraw.Draw",
        arrow: Arrow,
        global_progress: float,
        idx: int,
        active_idx: int,
        fps: int,
    ):
        """用Spring动画渲染箭头"""
        if idx > active_idx:
            return

        if idx < active_idx:
            draw_ratio = 1.0
            arrow.opacity = 1.0
            arrow.line_progress = 1.0
        else:
            # 箭头在 50%~100% 这个阶段
            arrow_p = max(0.0, min(1.0, (global_progress - 0.5) * 2))
            # 用spring做线条生长
            arrow.line_progress = spring(
                arrow_p, fps,
                config=SpringDamping.QUICK_BOUNCE,
                from_val=0, to_val=1,
            )
            draw_ratio = min(1.0, arrow.line_progress)

        if draw_ratio <= 0:
            return

        arrow_color = self._hex_to_rgb(arrow.color)
        x0, y0 = arrow.x0, arrow.y0
        x1 = arrow.x0 + int((arrow.x1 - arrow.x0) * draw_ratio)
        y1 = arrow.y0 + int((arrow.y1 - arrow.y0) * draw_ratio)

        if arrow.curved:
            mid_x = (x0 + x1) // 2
            mid_y = (y0 + y1) // 2
            draw.line([(x0, y0), (mid_x, y0)], fill=arrow_color, width=arrow.width)
            draw.line([(mid_x, y0), (mid_x, y1)], fill=arrow_color, width=arrow.width)
            draw.line([(mid_x, y1), (x1, y1)], fill=arrow_color, width=arrow.width)
        else:
            draw.line([(x0, y0), (x1, y1)], fill=arrow_color, width=arrow.width)

        # 箭头
        if draw_ratio > 0.3:
            arrow_ratio = (draw_ratio - 0.3) / 0.7
            arrow_ratio = spring(
                arrow_ratio, fps,
                config=SpringDamping.QUICK_BOUNCE,
                from_val=0, to_val=1,
            )
            self._draw_arrowhead(draw, x0, y0, x1, y1, arrow_color, arrow.arrow_head_size, arrow_ratio)

        # 标签
        if arrow.label and draw_ratio > 0.5:
            mid_x = (x0 + x1) // 2
            mid_y = (y0 + y1) // 2
            self._render_simple_text(draw, arrow.label, mid_x, mid_y - 15, 14, "#808080")

    def _draw_arrowhead(
        self, draw, x0: int, y0: int, x1: int, y1: int,
        color: Tuple[int, int, int], size: int, ratio: float = 1.0
    ):
        """绘制箭头头部"""
        angle = math.atan2(y1 - y0, x1 - x0)
        size = int(size * ratio)
        ax1 = int(x1 - size * math.cos(angle - math.pi / 6))
        ay1 = int(y1 - size * math.sin(angle - math.pi / 6))
        ax2 = int(x1 - size * math.cos(angle + math.pi / 6))
        ay2 = int(y1 - size * math.sin(angle + math.pi / 6))
        draw.line([(x1, y1), (ax1, ay1)], fill=color, width=2)
        draw.line([(x1, y1), (ax2, ay2)], fill=color, width=2)

    def _render_simple_text(
        self, draw, text: str, x: int, y: int, size: int, color: str
    ):
        try:
            font = self._get_font(size)
            draw.text((x, y), text, font=font, fill=self._hex_to_rgb(color))
        except Exception:
            pass

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
        h = hex_color.lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


# ==================== Spring图表动画模块 ====================

class SpringDiagramAnimationModule:
    """
    Spring物理动画的流程图/架构图生成器

    使用方式（与DiagramAnimationModule兼容）:
        module = SpringDiagramAnimationModule()
        module.add_rect("API网关", 200, 100, 400, 180, color="#4EC9B0")
        module.add_arrow("API网关", "服务A", label="HTTP")
        module.generate(output_path="output/spring_diagram.mp4")

    相比原DiagramAnimationModule:
    - 边框绘制有回弹感（spring bounce）
    - 元素出现有缩放回弹（scale spring）
    - 文字显现有easing（back/elastic）
    - 箭头生长有弹性效果
    """

    BG_COLOR = "#1E1E1E"

    COLOR_SCHEMES = {
        "teal": {
            "rect": "#4EC9B0", "fill": "#252526",
            "text": "#D4D4D4", "sub": "#808080",
            "highlight": "#CE9178", "arrow": "#808080"
        },
        "blue": {
            "rect": "#569CD6", "fill": "#252526",
            "text": "#D4D4D4", "sub": "#808080",
            "highlight": "#DCDCAA", "arrow": "#808080"
        },
        "orange": {
            "rect": "#CE9178", "fill": "#252526",
            "text": "#D4D4D4", "sub": "#808080",
            "highlight": "#4EC9B0", "arrow": "#808080"
        },
        "purple": {
            "rect": "#C586C0", "fill": "#252526",
            "text": "#D4D4D4", "sub": "#808080",
            "highlight": "#DCDCAA", "arrow": "#808080"
        },
    }

    def __init__(self, width: int = None, height: int = None, fps: int = OUTPUT_FPS):
        from config import OUTPUT_WIDTH, OUTPUT_HEIGHT
        self.canvas_w = width or OUTPUT_WIDTH
        self.canvas_h = height or OUTPUT_HEIGHT
        self.fps = fps
        self.renderer = SpringDiagramRenderer(self.canvas_w, self.canvas_h, fps)
        self.rects: List[Rect] = []
        self.arrows: List[Arrow] = []
        self.scheme = "teal"

    def _scheme(self) -> Dict:
        return self.COLOR_SCHEMES.get(self.scheme, self.COLOR_SCHEMES["teal"])

    def set_scheme(self, name: str = "teal"):
        self.scheme = name

    def add_rect(
        self, label: str, x: int, y: int,
        w: int = 180, h: int = 80,
        sub_label: str = "",
        color: str = None,
        scheme: str = None,
    ) -> int:
        scheme_name = scheme or self.scheme
        colors = self.COLOR_SCHEMES.get(scheme_name, self.COLOR_SCHEMES["teal"])
        rect = Rect(
            x0=x, y0=y, x1=x + w, y1=y + h,
            label=label, sub_label=sub_label,
            color=color or colors["rect"],
            fill_color=colors["fill"],
            font_size=20, sub_font_size=14,
        )
        rect.text_color = colors["text"]
        rect.sub_text_color = colors["sub"]
        rect.highlight_color = colors["highlight"]
        self.rects.append(rect)
        return len(self.rects) - 1

    def add_arrow(
        self, from_idx: int, to_idx: int,
        label: str = "", color: str = None,
        curved: bool = False, bidirection: bool = False,
    ) -> int:
        if from_idx >= len(self.rects) or to_idx >= len(self.rects):
            print(f"[SpringDiagram] 无效的模块索引: from={from_idx}, to={to_idx}")
            return -1

        r1 = self.rects[from_idx]
        r2 = self.rects[to_idx]
        x0, y0 = r1.center
        x1, y1 = r2.center

        arrow = Arrow(
            x0=x0, y0=y0, x1=x1, y1=y1,
            label=label,
            color=color or self._scheme()["arrow"],
            curved=curved, bidirection=bidirection,
        )
        self.arrows.append(arrow)
        return len(self.arrows) - 1

    def highlight_rect(self, idx: int, color: str = None):
        if idx < len(self.rects):
            self.rects[idx].highlighted = True
            if color:
                self.rects[idx].highlight_color = color

    def generate(
        self, output_path: str,
        fps: int = None,
        frame_count: int = None,
        show_grid: bool = False,
    ) -> bool:
        """生成Spring动画视频"""
        from PIL import Image
        import subprocess

        if not self.rects:
            print("[SpringDiagram] 没有添加任何模块")
            return False

        fps = fps or self.fps
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        total_elements = len(self.rects) + len(self.arrows)
        frame_count = frame_count or (total_elements * fps * 2)

        temp_frames_dir = Path(output_path).parent / "temp_spring_diagram_frames"
        temp_frames_dir.mkdir(parents=True, exist_ok=True)

        print(f"[SpringDiagram] 生成 {frame_count} 帧 ({frame_count/fps:.1f}秒) - Spring动画模式")

        for frame_idx in range(frame_count):
            progress = frame_idx / frame_count
            per_element = 1.0 / total_elements
            active_idx = int(progress / per_element)
            if active_idx >= total_elements:
                active_idx = total_elements - 1

            element_start = active_idx * per_element
            element_progress = (progress - element_start) / per_element
            element_progress = max(0.0, min(1.0, element_progress))

            elements = {
                "rects": self.rects,
                "arrows": self.arrows,
                "active_idx": active_idx if active_idx < len(self.rects) else len(self.rects) - 1,
            }

            frame = self.renderer.render_frame(elements, element_progress, fps)
            frame_path = temp_frames_dir / f"frame_{frame_idx:05d}.png"
            frame.save(frame_path, "PNG")

            if frame_idx % 30 == 0:
                print(f"  渲染帧 {frame_idx}/{frame_count} ({frame_idx*100//frame_count}%)")

        print(f"[SpringDiagram] 合成视频...")
        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(fps),
            "-i", str(temp_frames_dir / "frame_%05d.png"),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", str(OUTPUT_CRF),
            "-pix_fmt", "yuv420p",
            "-frames:v", str(frame_count),
            output_path
        ]

        success = self._run_ffmpeg(cmd)

        try:
            import shutil
            shutil.rmtree(temp_frames_dir)
        except Exception:
            pass

        return success

    def generate_from_layout(
        self,
        layout: List[Dict],
        output_path: str,
        fps: int = 30,
        auto_duration: bool = True,
    ) -> bool:
        """
        从布局描述生成Spring动画图表

        layout示例:
        [
            {"type": "rect", "label": "API网关", "x": 400, "y": 100, "w": 200, "h": 80, "scheme": "teal"},
            {"type": "rect", "label": "服务A", "x": 200, "y": 300, "w": 160, "h": 80, "scheme": "blue"},
            {"type": "arrow", "from": 0, "to": 1, "label": "HTTP"},
        ]
        """
        rect_index_map = {}
        for i, item in enumerate(layout):
            t = item.get("type")
            if t == "rect":
                idx = self.add_rect(
                    label=item.get("label", ""),
                    x=item.get("x", 0), y=item.get("y", 0),
                    w=item.get("w", 180), h=item.get("h", 80),
                    sub_label=item.get("sub", ""),
                    color=item.get("color"),
                    scheme=item.get("scheme"),
                )
                rect_index_map[item.get("id", i)] = idx
            elif t == "arrow":
                from_id = item.get("from")
                to_id = item.get("to")
                if from_id in rect_index_map and to_id in rect_index_map:
                    self.add_arrow(
                        rect_index_map[from_id],
                        rect_index_map[to_id],
                        label=item.get("label", ""),
                        curved=item.get("curved", False),
                    )

        total = len(self.rects) + len(self.arrows)
        frame_count = total * fps * 2 if auto_duration else total * fps

        return self.generate(output_path, fps=fps, frame_count=frame_count)

    @staticmethod
    @staticmethod
    def _run_ffmpeg(cmd: List[str]) -> bool:
        return run_ffmpeg_safe(cmd)
# ==================== 单例便捷函数 ====================

_module_instance = None


def get_spring_diagram_module() -> SpringDiagramAnimationModule:
    global _module_instance
    if _module_instance is None:
        _module_instance = SpringDiagramAnimationModule()
    return _module_instance
