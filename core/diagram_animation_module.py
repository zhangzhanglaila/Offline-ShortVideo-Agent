# -*- coding: utf-8 -*-
"""
2D流程图/架构图动画生成模块
- 纯2D平面效果，元素逐个显现、箭头生长、边框绘制、高亮
- 背景色#1E1E1E，支持中文，极简专业风格
"""
import math
import random
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from config import OUTPUT_WIDTH, OUTPUT_HEIGHT, OUTPUT_FPS, OUTPUT_CRF


# ==================== 绘图原语（基于Pillow） ====================

def _load_font(size: int, font_path: str = None) -> "ImageFont":
    """加载字体，优先用系统中文字体"""
    from PIL import ImageFont
    # Windows 常用中文字体
    font_candidates = [
        "C:/Windows/Fonts/msyh.ttc",   # 微软雅黑
        "C:/Windows/Fonts/simhei.ttf", # 黑体
        "C:/Windows/Fonts/simsun.ttc", # 宋体
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
    # 降级：默认字体
    return ImageFont.load_default()


def _draw_rounded_rect(draw, rect: "Rect", radius: int = 8, outline="white", width: int = 2):
    """绘制圆角矩形（无填充）"""
    x0, y0, x1, y1 = rect.x0, rect.y0, rect.x1, rect.y1
    r = radius
    # 四条边 + 四个角圆弧
    draw.arc([x0, y0, x0 + 2*r, y0 + 2*r], 180, 270, fill=outline, width=width)
    draw.arc([x1 - 2*r, y0, x1, y0 + 2*r], 270, 360, fill=outline, width=width)
    draw.arc([x0, y1 - 2*r, x0 + 2*r, y1], 90, 180, fill=outline, width=width)
    draw.arc([x1 - 2*r, y1 - 2*r, x1, y1], 0, 90, fill=outline, width=width)
    draw.line([(x0 + r, y0), (x1 - r, y0)], fill=outline, width=width)
    draw.line([(x1, y0 + r), (x1, y1 - r)], fill=outline, width=width)
    draw.line([(x0 + r, y1), (x1 - r, y1)], fill=outline, width=width)
    draw.line([(x0, y0 + r), (x0, y1 - r)], fill=outline, width=width)


def _clip_rect_by_ratio(rect: "Rect", ratio: float) -> Tuple[int, int, int, int]:
    """按比例裁剪矩形，返回(x0, y0, x1, y1)"""
    x0, y0, x1, y1 = rect.x0, rect.y0, rect.x1, rect.y1
    w = x1 - x0
    h = y1 - y0
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    new_w = w * ratio
    new_h = h * ratio
    return (int(cx - new_w/2), int(cy - new_h/2),
            int(cx + new_w/2), int(cy + new_h/2))


# ==================== 动画元素定义 ====================

@dataclass
class Rect:
    """矩形模块"""
    x0: int
    y0: int
    x1: int
    y1: int
    label: str = ""
    sub_label: str = ""
    color: str = "#4EC9B0"       # 边框色
    fill_color: str = "#1E1E1E"  # 填充色（与背景同色）
    font_size: int = 20
    sub_font_size: int = 14
    visible: bool = False        # 是否已显现
    border_drawn: float = 0.0    # 边框绘制进度 0.0~1.0
    highlighted: bool = False    # 是否高亮
    highlight_color: str = "#CE9178"

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
    """箭头/连线"""
    x0: int
    y0: int
    x1: int
    y1: int
    label: str = ""
    color: str = "#808080"
    width: int = 2
    drawn: float = 0.0     # 绘制进度 0.0~1.0
    arrow_head_size: int = 10
    curved: bool = False   # 是否曲线（贝塞尔）
    bidirection: bool = False  # 是否双向箭头

    def path_points(self, t: float) -> Tuple[Tuple[int, int], Tuple[int, int]]:
        """
        返回t时刻箭头的(起点,终点)，用于动画生长效果
        t=0.0: 起点=终点=(x0,y0)
        t=1.0: 起点=(x0,y0), 终点=(x1,y1)
        """
        cx = self.x0 + (self.x1 - self.x0) * t
        cy = self.y0 + (self.y1 - self.y0) * t
        return (self.x0, self.y0), (int(cx), int(cy))


@dataclass
class Box:
    """带文字的方框容器（比Rect更高级）"""
    rect: Rect
    border_color: str = "#4EC9B0"
    fill_color: str = "#252526"
    text_color: str = "#D4D4D4"
    sub_text_color: str = "#808080"
    border_width: int = 2


# ==================== 动画渲染器 ====================

class DiagramRenderer:
    """2D图表动画帧渲染器"""

    BG_COLOR = (30, 30, 30)  # #1E1E1E
    GRID_COLOR = (45, 45, 45)
    TEXT_COLOR = (212, 212, 212)  # #D4D4D4
    ACCENT_COLOR = (78, 201, 176)  # #4EC9B0
    ARROW_COLOR = (128, 128, 128)  # #808080
    HIGHLIGHT_COLOR = (206, 145, 120)  # #CE9178
    FILL_COLOR = (37, 37, 38)  # #252526

    def __init__(self, width: int, height: int):
        from PIL import Image, ImageDraw  # noqa: F401
        self.width = width
        self.height = height
        self.img = Image.new("RGB", (width, height), self.BG_COLOR)
        self.draw = ImageDraw.Draw(self.img)
        self.font_cache = {}

    def _get_font(self, size: int) -> "ImageFont":
        if size not in self.font_cache:
            self.font_cache[size] = _load_font(size)
        return self.font_cache[size]

    def render_frame(self, elements: Dict, progress: float) -> "Image":
        """
        渲染一帧
        elements: {"rects": [...], "arrows": [...], "active_idx": int}
        progress: 全局动画进度 0.0~1.0
        """
        from PIL import ImageDraw
        draw = ImageDraw.Draw(self.img)
        # 重置背景
        self.img.paste(self.BG_COLOR, (0, 0, self.width, self.height))

        rects = elements.get("rects", [])
        arrows = elements.get("arrows", [])
        active_idx = elements.get("active_idx", -1)

        # 1. 先画所有箭头（底层）
        for i, arrow in enumerate(arrows):
            self._render_arrow(draw, arrow, progress, i, active_idx)

        # 2. 再画矩形（顶层）
        for i, rect in enumerate(rects):
            self._render_rect(draw, rect, progress, i, active_idx)

        return self.img

    def _render_rect(self, draw: "ImageDraw.Draw", rect: Rect,
                     progress: float, idx: int, active_idx: int):
        """渲染单个矩形"""
        if idx > active_idx:
            return  # 未到该元素的动画时间

        # 该元素自己的进度（0.0~1.0）
        # active_idx之前的元素完全显示
        if idx < active_idx:
            draw_ratio = 1.0
        else:
            # 当前激活元素：从0到1
            draw_ratio = min(1.0, progress * 2)  # 前半段画边框

        if draw_ratio <= 0:
            return

        x0, y0, x1, y1 = rect.x0, rect.y0, rect.x1, rect.y1

        # 填充背景
        fill = self._hex_to_rgb(rect.fill_color)
        draw.rectangle([x0, y0, x1, y1], fill=fill)

        # 高亮效果
        if rect.highlighted:
            hl = self._hex_to_rgb(rect.highlight_color)
            # 画高亮边框
            for offset in range(3, 0, -1):
                alpha_fill = tuple(max(0, min(255, c + 30 * (4 - offset))) for c in hl)
                draw.rectangle([x0 - offset, y0 - offset, x1 + offset, y1 + offset],
                              fill=None, outline=alpha_fill)
            draw.rectangle([x0 - 4, y0 - 4, x1 + 4, y1 + 4],
                          fill=None, outline=hl, width=2)

        # 边框绘制动画：按比例裁剪矩形
        if draw_ratio < 1.0:
            x0a, y0a, x1a, y1a = _clip_rect_by_ratio(rect, draw_ratio)
        else:
            x0a, y0a, x1a, y1a = x0, y0, x1, y1

        border_color = self._hex_to_rgb(rect.color)
        # 画边框（用矩形模拟圆角矩形简化版）
        draw.rectangle([x0a, y0a, x1a, y1a], fill=None, outline=border_color, width=2)

        # 文字显现动画（后半段）
        if draw_ratio > 0.5 or idx < active_idx:
            text_ratio = 0.0
            if idx < active_idx:
                text_ratio = 1.0
            elif draw_ratio > 0.5:
                text_ratio = min(1.0, (draw_ratio - 0.5) * 2)

            if text_ratio > 0 and rect.label:
                self._render_label_centered(
                    draw, rect.label, rect, text_ratio,
                    rect.font_size, rect.text_color if hasattr(rect, 'text_color') else "#D4D4D4"
                )

    def _render_label_centered(self, draw: "ImageDraw.Draw", text: str,
                                rect: Rect, ratio: float,
                                font_size: int, color: str):
        """在矩形中心渲染文字，按ratio截断显示"""
        from PIL import ImageFont
        font = self._get_font(font_size)
        # 计算文字宽度
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

        cx = (rect.x0 + rect.x1) // 2
        cy = (rect.y0 + rect.y1) // 2

        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text((cx - tw // 2, cy - th // 2), text, font=font, fill=self._hex_to_rgb(color))
        except Exception:
            draw.text((cx - text_w // 2, cy - text_h // 2), text, font=font, fill=self._hex_to_rgb(color))

    def _render_arrow(self, draw: "ImageDraw.Draw", arrow: Arrow,
                      progress: float, idx: int, active_idx: int):
        """渲染箭头/连线"""
        if idx > active_idx:
            return

        if idx < active_idx:
            draw_ratio = 1.0
        else:
            draw_ratio = min(1.0, max(0.0, (progress - 0.5) * 2))  # 后半段画箭头

        if draw_ratio <= 0:
            return

        arrow_color = self._hex_to_rgb(arrow.color)

        # 计算箭头终点（生长效果）
        x0, y0 = arrow.x0, arrow.y0
        x1 = arrow.x0 + int((arrow.x1 - arrow.x0) * draw_ratio)
        y1 = arrow.y0 + int((arrow.y1 - arrow.y0) * draw_ratio)

        # 画线
        if arrow.curved:
            # 简化为折线
            mid_x = (x0 + x1) // 2
            mid_y = (y0 + y1) // 2
            draw.line([(x0, y0), (mid_x, y0)], fill=arrow_color, width=arrow.width)
            draw.line([(mid_x, y0), (mid_x, y1)], fill=arrow_color, width=arrow.width)
            draw.line([(mid_x, y1), (x1, y1)], fill=arrow_color, width=arrow.width)
        else:
            draw.line([(x0, y0), (x1, y1)], fill=arrow_color, width=arrow.width)

        # 画箭头
        if draw_ratio > 0.3:
            arrow_ratio = (draw_ratio - 0.3) / 0.7
            self._draw_arrowhead(draw, x0, y0, x1, y1, arrow_color, arrow.arrow_head_size, arrow_ratio)

        # 标签
        if arrow.label and draw_ratio > 0.5:
            mid_x = (x0 + x1) // 2
            mid_y = (y0 + y1) // 2
            self._render_simple_text(draw, arrow.label, mid_x, mid_y - 15, 14, "#808080")

    def _draw_arrowhead(self, draw, x0, y0, x1, y1, color, size: int, ratio: float = 1.0):
        """绘制箭头头部"""
        import math
        angle = math.atan2(y1 - y0, x1 - x0)
        size = int(size * ratio)
        ax1 = int(x1 - size * math.cos(angle - math.pi / 6))
        ay1 = int(y1 - size * math.sin(angle - math.pi / 6))
        ax2 = int(x1 - size * math.cos(angle + math.pi / 6))
        ay2 = int(y1 - size * math.sin(angle + math.pi / 6))
        draw.line([(x1, y1), (ax1, ay1)], fill=color, width=2)
        draw.line([(x1, y1), (ax2, ay2)], fill=color, width=2)

    def _render_simple_text(self, draw, text: str, x: int, y: int,
                            size: int, color: str):
        try:
            font = self._get_font(size)
            draw.text((x, y), text, font=font, fill=self._hex_to_rgb(color))
        except Exception:
            pass

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
        h = hex_color.lstrip('#')
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


# ==================== 图表动画模块 ====================

class DiagramAnimationModule:
    """
    2D流程图/架构图动画生成器

    使用方式:
        module = DiagramAnimationModule()
        module.add_rect("API网关", 200, 100, 400, 180, color="#4EC9B0")
        module.add_arrow("API网关", "服务A", label="HTTP")
        module.add_arrow("服务A", "数据库", label="SQL")
        success = module.generate(output_path="output/diagram.mp4")
    """

    BG_COLOR = "#1E1E1E"

    # 预定义配色方案（专业极简）
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

    def __init__(self, width: int = None, height: int = None):
        from config import OUTPUT_WIDTH, OUTPUT_HEIGHT
        self.canvas_w = width or OUTPUT_WIDTH
        self.canvas_h = height or OUTPUT_HEIGHT
        self.renderer = DiagramRenderer(self.canvas_w, self.canvas_h)

        self.rects: List[Rect] = []
        self.arrows: List[Arrow] = []
        self.scheme = "teal"

    def _scheme(self) -> Dict:
        return self.COLOR_SCHEMES.get(self.scheme, self.COLOR_SCHEMES["teal"])

    def set_scheme(self, name: str = "teal"):
        """设置配色方案: teal / blue / orange / purple"""
        self.scheme = name

    def add_rect(
        self,
        label: str,
        x: int, y: int,
        w: int = 180, h: int = 80,
        sub_label: str = "",
        color: str = None,
        scheme: str = None,
    ) -> int:
        """
        添加矩形模块
        返回模块索引，用于箭头连接
        """
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
        self,
        from_idx: int,
        to_idx: int,
        label: str = "",
        color: str = None,
        curved: bool = False,
        bidirection: bool = False,
    ) -> int:
        """添加箭头（从 from_idx 矩形中心 → to_idx 矩形中心）"""
        if from_idx >= len(self.rects) or to_idx >= len(self.rects):
            print(f"[Diagram] 无效的模块索引: from={from_idx}, to={to_idx}")
            return -1

        r1 = self.rects[from_idx]
        r2 = self.rects[to_idx]

        x0, y0 = r1.center
        x1, y1 = r2.center

        arrow = Arrow(
            x0=x0, y0=y0, x1=x1, y1=y1,
            label=label,
            color=color or self._scheme()["arrow"],
            curved=curved,
            bidirection=bidirection,
        )
        self.arrows.append(arrow)
        return len(self.arrows) - 1

    def add_arrow_raw(
        self,
        x0: int, y0: int,
        x1: int, y1: int,
        label: str = "",
        color: str = None,
        curved: bool = False,
    ) -> int:
        """添加原始坐标箭头"""
        arrow = Arrow(
            x0=x0, y0=y0, x1=x1, y1=y1,
            label=label,
            color=color or self._scheme()["arrow"],
            curved=curved,
        )
        self.arrows.append(arrow)
        return len(self.arrows) - 1

    def highlight_rect(self, idx: int, color: str = None):
        """高亮指定模块"""
        if idx < len(self.rects):
            self.rects[idx].highlighted = True
            if color:
                self.rects[idx].highlight_color = color

    def generate(
        self,
        output_path: str,
        fps: int = None,
        frame_count: int = None,
        show_grid: bool = False,
    ) -> bool:
        """
        生成动画视频

        参数:
            output_path: 输出路径
            fps: 帧率（默认30）
            frame_count: 总帧数（默认自动计算）
            show_grid: 是否显示网格背景

        返回:
            是否成功
        """
        import subprocess
        from PIL import Image

        if not self.rects:
            print("[Diagram] 没有添加任何模块")
            return False

        fps = fps or OUTPUT_FPS
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        # 自动计算帧数：每个元素给2秒动画
        total_elements = len(self.rects) + len(self.arrows)
        frame_count = frame_count or (total_elements * fps * 2)

        temp_frames_dir = Path(output_path).parent / "temp_diagram_frames"
        temp_frames_dir.mkdir(parents=True, exist_ok=True)

        print(f"[Diagram] 生成 {frame_count} 帧 ({frame_count/fps:.1f}秒) ...")

        for frame_idx in range(frame_count):
            # 计算当前进度（0.0~1.0）
            progress = frame_idx / frame_count

            # 计算当前激活的元素索引
            # 每个元素分配 1/(total_elements) 的时间
            per_element = 1.0 / total_elements
            active_idx = int(progress / per_element)
            if active_idx >= total_elements:
                active_idx = total_elements - 1

            # 当前元素在自己的小节内的进度（0.0~1.0）
            element_start = active_idx * per_element
            element_progress = (progress - element_start) / per_element
            element_progress = max(0.0, min(1.0, element_progress))

            # 构建elements字典
            elements = {
                "rects": self.rects,
                "arrows": self.arrows,
                "active_idx": active_idx if active_idx < len(self.rects) else len(self.rects) - 1,
            }

            # 渲染帧
            frame = self.renderer.render_frame(elements, element_progress)

            # 保存帧
            frame_path = temp_frames_dir / f"frame_{frame_idx:05d}.png"
            frame.save(frame_path, "PNG")

            if frame_idx % 30 == 0:
                print(f"  渲染帧 {frame_idx}/{frame_count} ({frame_idx*100//frame_count}%)")

        # 用FFmpeg合成视频
        print(f"[Diagram] 合成视频 ...")
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

        # 清理临时帧
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
        从布局描述生成图表动画

        layout示例:
        [
            {"type": "rect", "label": "API网关", "x": 400, "y": 100, "w": 200, "h": 80, "scheme": "teal"},
            {"type": "rect", "label": "服务A", "x": 200, "y": 300, "w": 160, "h": 80, "scheme": "blue"},
            {"type": "rect", "label": "服务B", "x": 600, "y": 300, "w": 160, "h": 80, "scheme": "blue"},
            {"type": "arrow", "from": 0, "to": 1, "label": "HTTP"},
            {"type": "arrow", "from": 0, "to": 2, "label": "HTTP"},
            {"type": "arrow", "from": 1, "to": 2, "label": "调用"},
        ]

        自动计算背景颜色：#1E1E1E
        """
        # 解析布局
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

        # 自动计算帧数
        total = len(self.rects) + len(self.arrows)
        frame_count = total * fps * 2 if auto_duration else total * fps

        return self.generate(output_path, fps=fps, frame_count=frame_count)

    @staticmethod
    def _run_ffmpeg(cmd: List[str]) -> bool:
        """执行FFmpeg命令"""
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                encoding='utf-8',
                errors='replace',
                timeout=600
            )
            if result.returncode != 0:
                stderr_lines = result.stderr.strip().split('\n')
                errors = [l.strip() for l in stderr_lines
                         if l.strip() and 'ffmpeg' not in l.lower()
                         and 'libav' not in l.lower()]
                if errors:
                    print(f"[FFmpeg错误] {errors[0]}")
                else:
                    print(f"[FFmpeg错误] 返回码 {result.returncode}")
                return False
            return True
        except subprocess.TimeoutExpired:
            print("[FFmpeg] 执行超时")
            return False
        except Exception as e:
            print(f"[FFmpeg] 执行失败: {e}")
            return False


# ==================== 便捷函数 ====================
_module_instance = None


def get_diagram_module() -> DiagramAnimationModule:
    """获取模块单例"""
    global _module_instance
    if _module_instance is None:
        _module_instance = DiagramAnimationModule()
    return _module_instance


def create_simple_flowchart(
    nodes: List[Dict],
    output_path: str,
    title: str = None,
) -> bool:
    """
    快速创建简单流程图动画

    nodes示例:
    [
        {"label": "用户请求", "color": "#4EC9B0"},
        {"label": "负载均衡", "color": "#569CD6"},
        {"label": "服务集群", "color": "#569CD6"},
        {"label": "缓存", "color": "#CE9178"},
        {"label": "数据库", "color": "#CE9178"},
    ]
    """
    module = DiagramAnimationModule()

    if title:
        module.add_rect(title, x=300, y=20, w=400, h=60, color="#808080", scheme="teal")

    node_w, node_h = 180, 80
    cols = 3
    padding = 30
    start_x = (module.canvas_w - (cols * node_w + (cols - 1) * padding)) // 2
    start_y = 150

    node_indices = []
    for i, node in enumerate(nodes):
        col = i % cols
        row = i // cols
        x = start_x + col * (node_w + padding)
        y = start_y + row * (node_h + padding * 2)

        idx = module.add_rect(
            label=node.get("label", f"节点{i}"),
            x=x, y=y, w=node_w, h=node_h,
            color=node.get("color"),
            scheme="teal"
        )
        node_indices.append(idx)

    # 添加箭头
    for i in range(len(node_indices) - 1):
        module.add_arrow(node_indices[i], node_indices[i + 1])

    return module.generate(output_path)
