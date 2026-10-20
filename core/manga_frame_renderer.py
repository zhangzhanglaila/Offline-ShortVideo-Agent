# -*- coding: utf-8 -*-
"""
漫画帧渲染引擎 — 真正的日式漫画风格竖屏帧生成器
用 Pillow + numpy 生成网点纸、气泡框、速度线、分镜格等漫画元素。
输出 1080×1920 竖屏 PNG，文字讲解为主，图片为辅。
"""
import math
import random
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from config import get_visual_style_config, MANGA_STYLE_CONFIG


# ── 漫画风格默认值 ──────────────────────────────────────────
MANGA = MANGA_STYLE_CONFIG

def _hex_to_rgba(hex_str: str, alpha: int = 255) -> tuple:
    """Convert '#RRGGBB' or '#RRGGBBAA' hex string to (R, G, B, A) tuple."""
    h = hex_str.lstrip('#')
    if len(h) == 8:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16))
    if len(h) == 6:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)
    return (0, 0, 0, alpha)

W = 1080   # 竖屏宽度
H = 1920   # 竖屏高度
_PANEL_GAP = MANGA["panel_gap"]
BORDER_W = MANGA["border_width"]
PAPER = MANGA["paper_color"]
TEXT_C = MANGA["text_color_primary"]
RED = MANGA["accent_red"]
BLUE = MANGA["accent_blue"]


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _load_fonts():
    """加载漫画用字体：标题粗体、正文、SFX大字。"""
    fonts = {}
    font_roots = [
        "C:/Windows/Fonts",
        "/System/Library/Fonts",
        "/usr/share/fonts",
        "/usr/local/share/fonts",
    ]
    candidates = {
        "title": ["msyhbd.ttc", "simhei.ttf", "NotoSansCJK-Bold.ttc", "Arial Bold.ttf"],
        "body":  ["msyh.ttc", "simsun.ttc", "NotoSansCJK-Regular.ttc", "Arial.ttf"],
        "sfx":   ["simhei.ttf", "msyhbd.ttc", "Impact.ttf", "Arial.ttf"],
    }

    for key, names in candidates.items():
        for name in names:
            for root in font_roots:
                fp = Path(root) / name
                if fp.exists():
                    try:
                        fonts[key] = str(fp)
                        break
                    except Exception:
                        continue
            if key in fonts:
                break
        if key not in fonts:
            fonts[key] = None  # 回退到 default
    return fonts


_FONTS = None

def _get_font(size: int, style: str = "body") -> ImageFont.FreeTypeFont:
    global _FONTS
    if _FONTS is None:
        _FONTS = _load_fonts()
    fp = _FONTS.get(style)
    if fp:
        try:
            return ImageFont.truetype(fp, size)
        except Exception:
            pass
    try:
        return ImageFont.truetype(_FONTS.get("body") or "", size)
    except Exception:
        return ImageFont.load_default()


# ═══════════════════════════════════════════════════════════════
# 网点纸 / 砂目 (Halftone / Screentone)
# ═══════════════════════════════════════════════════════════════

def _halftone_pattern(width: int, height: int,
                      dot_size: int = 3, spacing: int = 6,
                      angle_deg: float = 45) -> Image.Image:
    """生成圆点网点纸图案 (灰度图，用于叠加/遮罩)。"""
    if not HAS_NUMPY:
        return None
    period = dot_size + spacing
    if period < 2:
        period = 2
    angle = math.radians(angle_deg)
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    xs = np.arange(width, dtype=np.float64)
    ys = np.arange(height, dtype=np.float64)
    xv, yv = np.meshgrid(xs, ys)
    # 旋转坐标系
    xx = xv * cos_a - yv * sin_a
    yy = xv * sin_a + yv * cos_a
    # 到最近网点中心的距离
    cx = np.round(xx / period) * period
    cy = np.round(yy / period) * period
    dx = xx - cx
    dy = yy - cy
    dist = np.sqrt(dx * dx + dy * dy)

    mask = (dist <= dot_size / 2.0).astype(np.uint8) * 255
    return Image.fromarray(mask, mode="L")


def apply_halftone(canvas: Image.Image, region: Tuple[int,int,int,int],
                   dot_size: int = None, spacing: int = None,
                   angle: float = 45, opacity: float = 0.35):
    """在 canvas 的指定区域上叠加网点纸效果。"""
    if not HAS_NUMPY:
        # 降级：用纯色覆盖表示网点
        overlay = Image.new("RGBA", (region[2]-region[0], region[3]-region[1]),
                            (0, 0, 0, int(opacity * 255)))
        canvas.paste(overlay, (region[0], region[1]), overlay)
        return

    ds = dot_size if dot_size is not None else MANGA["halftone_dot_size"]
    sp = spacing if spacing is not None else MANGA["halftone_spacing"]
    rw = region[2] - region[0]
    rh = region[3] - region[1]
    if rw <= 0 or rh <= 0:
        return

    pattern = _halftone_pattern(rw, rh, ds, sp, angle)
    if pattern is None:
        return

    # 转为 RGBA 叠加
    alpha = pattern.point(lambda p: int(p * opacity) if p > 0 else 0)
    overlay = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
    overlay.putalpha(alpha)
    canvas.paste(overlay, (region[0], region[1]), overlay)


# ═══════════════════════════════════════════════════════════════
# 速度线 / 集中线 (Speed Lines)
# ═══════════════════════════════════════════════════════════════

def draw_speed_lines(draw: ImageDraw.Draw,
                     cx: int, cy: int,
                     inner_r: int, outer_r: int,
                     count: int = 28, opacity: int = 60):
    """以 (cx,cy) 为中心绘制辐射状速度线（集中线）。"""
    color = (0, 0, 0, opacity) if isinstance(draw, object) else (0, 0, 0)
    for i in range(count):
        angle = (2 * math.pi * i / count) + random.uniform(-0.08, 0.08)
        # 线条长度微变化
        r1 = inner_r + random.randint(0, 15)
        r2 = outer_r - random.randint(0, 30)
        x1 = cx + int(r1 * math.cos(angle))
        y1 = cy + int(r1 * math.sin(angle))
        x2 = cx + int(r2 * math.cos(angle))
        y2 = cy + int(r2 * math.sin(angle))
        w = random.randint(1, 3)
        draw.line([(x1, y1), (x2, y2)], fill=(0, 0, 0, opacity), width=w)


def draw_crosshatch(draw: ImageDraw.Draw, x0: int, y0: int, x1: int, y1: int,
                    spacing: int = 10, opacity: int = 15, angle: float = 45):
    """纯PIL交叉排线 — 漫画阴影纹理，不依赖numpy。"""
    import math
    color = (0, 0, 0, opacity)
    rad = math.radians(angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)

    # 计算覆盖范围
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    half_diag = math.sqrt((x1 - x0) ** 2 + (y1 - y0) ** 2) / 2 + 20

    # 画平行线
    n_lines = int(half_diag * 2 / spacing)
    for i in range(-n_lines, n_lines):
        offset = i * spacing
        # 线通过中心点，垂直于角度方向
        sx = cx + offset * cos_a - half_diag * (-sin_a)
        sy = cy + offset * sin_a - half_diag * cos_a
        ex = cx + offset * cos_a + half_diag * (-sin_a)
        ey = cy + offset * sin_a + half_diag * cos_a
        draw.line([(int(sx), int(sy)), (int(ex), int(ey))], fill=color, width=1)

    # 第二组交叉线（90度）
    rad2 = rad + math.pi / 2
    cos_a2, sin_a2 = math.cos(rad2), math.sin(rad2)
    for i in range(-n_lines, n_lines):
        offset = i * spacing
        sx = cx + offset * cos_a2 - half_diag * (-sin_a2)
        sy = cy + offset * sin_a2 - half_diag * cos_a2
        ex = cx + offset * cos_a2 + half_diag * (-sin_a2)
        ey = cy + offset * sin_a2 + half_diag * cos_a2
        draw.line([(int(sx), int(sy)), (int(ex), int(ey))], fill=color, width=1)


def draw_parallel_speed_lines(draw: ImageDraw.Draw,
                              x0: int, y0: int, x1: int, y1: int,
                              count: int = 16, opacity: int = 50):
    """绘制平行速度线（水平或垂直方向的动态线）。"""
    for i in range(count):
        if abs(x1 - x0) > abs(y1 - y0):
            # 水平方向
            frac = i / max(count - 1, 1)
            y = y0 + int((y1 - y0) * frac)
            lx = x0 + random.randint(-20, 20)
            rx = x1 + random.randint(-20, 20)
            segs = [(lx, y)]
            cx_pos = lx
            while cx_pos < rx:
                seg_len = random.randint(30, 120)
                cx_pos += seg_len
                cy_jitter = y + random.randint(-4, 4)
                segs.append((min(cx_pos, rx), cy_jitter))
            for s in range(len(segs) - 1):
                w = random.randint(1, 3)
                draw.line([segs[s], segs[s+1]], fill=(0, 0, 0, opacity), width=w)
        else:
            frac = i / max(count - 1, 1)
            x = x0 + int((x1 - x0) * frac)
            ty = y0 + random.randint(-20, 20)
            by = y1 + random.randint(-20, 20)
            segs = [(x, ty)]
            cy_pos = ty
            while cy_pos < by:
                seg_len = random.randint(30, 120)
                cy_pos += seg_len
                cx_jitter = x + random.randint(-4, 4)
                segs.append((cx_jitter, min(cy_pos, by)))
            for s in range(len(segs) - 1):
                w = random.randint(1, 3)
                draw.line([segs[s], segs[s+1]], fill=(0, 0, 0, opacity), width=w)




# ═══════════════════════════════════════════════════════════════
# 漫画帧渲染器主类
# ═══════════════════════════════════════════════════════════════

class MangaFrameRenderer:
    """漫画帧渲染器 — 生成真正的漫画风格讲解帧。"""

    def __init__(self, width: int = None, height: int = None,
                 dark_mode: bool = False, visual_style: str = "manga"):
        self.w = width or W
        self.h = height or H
        self.orientation = "landscape" if self.w > self.h else "portrait"
        self.visual_style = visual_style
        self.style = get_visual_style_config(visual_style)
        self.panel_gap = self.style["panel_gap"]
        self.border_rgba = _hex_to_rgba(self.style["border_color"])
        self.panel_bg_rgba = _hex_to_rgba(self.style["panel_bg"])
        self.text_secondary = self.style["text_secondary"]
        self.text_muted = self.style["text_muted"]
        self.progress_inactive = self.style["progress_inactive"]
        self.media_panel_bg = self.style["media_panel_bg"]
        self.dark = dark_mode
        if dark_mode:
            self.paper = "#1E1E2E"
            self.panel_bg = "#2A2A3C"
            self.bubble_bg = "#323248"
            self.text_c = "#E8E8F0"
        else:
            self.paper = self.style["paper_color"]
            self.panel_bg = self.style["panel_bg"]
            self.bubble_bg = self.style["bubble_bg"]
            self.text_c = self.style["text_c"]

    # ── 单帧渲染 ──────────────────────────────────────────

    def render_frame(self,
                     title: str,
                     bullets: List[str],
                     output_path: str,
                     subtitle: str = "",
                     media_path: str = None,
                     scene_index: int = 0,
                     total_scenes: int = 1,
                     sfx_text: str = "",
                     accent_color: str = None,
                     visual_element: str = "",
                     visual_data: dict = None,
                     visible_bullets: int = 0) -> str:
        """渲染单帧漫画讲解图 — 网点纸+气泡框+速度线+分镜格全开。

        布局 (1080×1920)：
        ┌────────────────────────┐
        │  标题面板 + 网点背景   │  ~260px
        │  ▸ SFX + 副标题       │
        ├────────────┬──────────┤
        │  气泡框1   │          │
        │  ────────  │  素材图  │  主内容区 ~1420px
        │  气泡框2   │  (可选)  │
        │  ────────  │          │
        │  气泡框3   │          │
        ├────────────┴──────────┤
        │  要点总结 + 场景编号   │  ~180px
        │  速度线装饰           │
        └────────────────────────┘
        """
        accent = accent_color or (self.style["accent_red"] if not self.dark else "#FF6B6B")

        if self.orientation == "landscape":
            return self._render_frame_landscape(title, bullets, output_path,
                                                subtitle, media_path,
                                                scene_index, total_scenes,
                                                sfx_text, accent)

        # RGBA画布 — 支持网点纸alpha合成 + 速度线透明度
        img = Image.new("RGBA", (self.w, self.h), self.paper + "FF" if len(self.paper) == 7 else self.paper)
        draw = ImageDraw.Draw(img, "RGBA")

        # ── 背景纹理（纸张纤维 + 网点纸）──
        self._draw_bg_texture(draw, img)
        # 全画布微妙网点纸叠加（漫画影印质感）
        if self.style.get("enable_halftone", True):
            apply_halftone(img, (self.panel_gap, self.panel_gap, self.w - self.panel_gap, self.h - self.panel_gap),
                          dot_size=self.style["halftone_dot_size"], spacing=self.style["halftone_spacing"], angle=self.style["halftone_angle"], opacity=self.style["halftone_opacity"])
        # 霓虹网格
        self._draw_grid_overlay(draw)

        # ── 1. 顶部标题区 ──
        title_y1 = self._draw_title_header(draw, title, sfx_text, subtitle, accent)

        # ── 2. 底部信息区 ──
        bottom_y0 = self._draw_bottom_bar(draw, subtitle, scene_index, total_scenes, accent)

        # ── 3. 中间主内容区 ──
        main_y0 = title_y1 + self.panel_gap
        main_y1 = bottom_y0 - self.panel_gap

        vd = visual_data or {}
        # Auto-detect big_number from bullets
        if not visual_element and bullets and not (media_path and Path(media_path).exists()):
            first = str(bullets[0]) if bullets else ""
            import re as _re
            if _re.search(r'[\d.]+[万亿千百]', first) and len(first) < 40:
                visual_element = "big_number"
                vd = {"value": _re.sub(r'[^\d.万亿千百%+]', '', first), "label": subtitle or title, "trend": "up" if any(w in first for w in ["增","涨","升","超","突"]) else ""}

        if visual_element == "big_number":
            self._draw_big_number(draw, img, main_y0, main_y1, vd, accent)
        elif visual_element == "vs_compare":
            self._draw_vs_compare(draw, img, main_y0, main_y1, vd, accent)
        else:
            # 要点逐条弹出：visible_bullets 控制显示前N条
            show_bullets = bullets[:visible_bullets] if visible_bullets > 0 else bullets
            has_media = media_path and Path(media_path).exists()
            if has_media:
                self._draw_content_with_media(draw, img, show_bullets, media_path,
                                              main_y0, main_y1, accent)
            else:
                self._draw_content_text_only(draw, img, show_bullets,
                                             main_y0, main_y1, accent)

        # 转回RGB保存
        rgb = Image.new("RGB", (self.w, self.h), self.paper)
        rgb.paste(img, (0, 0), img)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        rgb.save(output_path, quality=92)
        return output_path

    def _draw_bg_texture(self, draw, img: Image.Image = None):
        """全画布微妙纹理：漫画纸质感 + 随机淡色斑点 + 网点纸。"""
        # 纸张纤维纹理 — 随机微小淡灰斑点
        import random as _random
        for _ in range(self.style.get("speckle_count", 80)):
            x, y = _random.randint(0, self.w - 1), _random.randint(0, self.h - 1)
            s = _random.randint(1, 3)
            shade = _random.randint(0, 15)
            draw.ellipse([x, y, x + s, y + s], fill=(0, 0, 0, shade))

    def _draw_grid_overlay(self, draw):
        """霓虹风格网格背景覆盖层。"""
        if not self.style.get("bg_grid", False):
            return
        color = self.style.get("bg_grid_color")
        if not color:
            return
        spacing = self.style.get("bg_grid_spacing", 40)
        r, g, b, a = color if isinstance(color, tuple) else (0, 255, 200, 12)
        for x in range(0, self.w, spacing):
            draw.line([(x, 0), (x, self.h)], fill=(r, g, b, a), width=1)
        for y in range(0, self.h, spacing):
            draw.line([(0, y), (self.w, y)], fill=(r, g, b, a), width=1)

    # ── 标题头 ──────────────────────────────────────────

    def _draw_title_header(self, draw, title: str, sfx: str, subtitle: str, accent: str) -> int:
        """顶部标题面板 — 网点背景+粗双线框+SFX+标题+副标题+装饰线。"""
        header_h = 220
        y0, y1 = self.panel_gap, self.panel_gap + header_h
        x0, x1 = self.panel_gap, self.w - self.panel_gap

        # 面板背景
        draw.rounded_rectangle([x0, y0, x1, y1], radius=18,
                               fill=self.panel_bg, outline=self.style['border_color'], width=self.style['border_width'])

        # 交叉排线纹理
        if self.style.get("enable_crosshatch", True):
            draw_crosshatch(draw, x0 + 14, y0 + 14, x1 - 14, y0 + 180,
                          spacing=self.style["crosshatch_spacing"], opacity=self.style["crosshatch_opacity"], angle=self.style["crosshatch_angle"])

        # 内边框
        if self.style.get("enable_inner_border", True):
            draw.rounded_rectangle([x0 + self.style['border_width'] + 3, y0 + self.style['border_width'] + 3,
                                    x1 - self.style['border_width'] - 3, y1 - self.style['border_width'] - 3],
                                   radius=14, outline=self.style['border_color'], width=2)

        # SFX 拟声词 — 右上角大字描边
        if sfx:
            sfx_font = _get_font(68, "sfx")
            sfx_w = draw.textlength(sfx, font=sfx_font)
            sfx_x = x1 - 60 - int(sfx_w)
            sfx_y = y0 + 24
            for ox, oy in [(-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2)]:
                draw.text((sfx_x + ox, sfx_y + oy), sfx, fill=(0, 0, 0, 255), font=sfx_font)
            draw.text((sfx_x, sfx_y), sfx, fill=accent, font=sfx_font)

        # 标题 — 大字+描边（允许2行）
        title_font = _get_font(52, "title")
        tx = x0 + 34
        ty = y0 + 28
        title_max_w = x1 - tx - 40
        display_title = title[:48]
        title_lines = self._wrap_text(display_title, title_font, title_max_w)[:4]
        title_line_h = 60
        for li, tline in enumerate(title_lines):
            tty = ty + li * title_line_h
            for ox, oy in [(-3, 0), (3, 0), (0, -3), (0, 3)]:
                draw.text((tx + ox, tty + oy), tline, fill=(0, 0, 0, 255), font=title_font)
            draw.text((tx, tty), tline, fill=accent, font=title_font)
        title_bottom = ty + len(title_lines) * title_line_h

        # 副标题/导读行 — 允许换行（最多2行）
        info_font = _get_font(26, "body")
        if subtitle:
            info_text = subtitle[:120]
        else:
            info_text = self.style.get("default_subtitle", "详细讲解 · 建议收藏反复观看")
        info_max_w = x1 - tx - 40
        info_lines = self._wrap_text(info_text, info_font, info_max_w)[:4]
        info_line_h = 34
        for li, iline in enumerate(info_lines):
            iw = draw.textlength(iline, font=info_font)
            ix = (self.w - int(iw)) // 2
            draw.text((ix, title_bottom + 24 + li * info_line_h), iline, fill=self.text_secondary, font=info_font)
        info_bottom = title_bottom + 24 + len(info_lines) * info_line_h

        # 装饰线 — 标题下方
        if self.style.get("enable_decorative_lines", True):
            deco_y = info_bottom + 12
            draw.line([(tx, deco_y), (tx + 300, deco_y)], fill=(0, 0, 0, 255), width=3)
            draw.line([(tx, deco_y + 8), (tx + 180, deco_y + 8)], fill=accent, width=2)

        # 右下角小标签
        if self.style.get("enable_bottom_tags", True):
            tag_font = _get_font(20, "body")
            tag_text = self.style.get("tag_text", "")
            if tag_text:
                tag_w = draw.textlength(tag_text, font=tag_font)
                draw.text((x1 - int(tag_w) - 30, y1 - 40), tag_text, fill=self.text_muted, font=tag_font)

        return y1

    # ── 底栏 ────────────────────────────────────────────

    def _draw_bottom_bar(self, draw, subtitle: str, idx: int, total: int, accent: str) -> int:
        """底部信息栏 — 速度线+页码+总结+装饰。"""
        bar_h = 140
        y1 = self.h - self.panel_gap
        y0 = y1 - bar_h
        x0, x1 = self.panel_gap, self.w - self.panel_gap

        draw.rounded_rectangle([x0, y0, x1, y1], radius=14,
                               fill=self.panel_bg, outline=self.style['border_color'], width=self.style['border_width'])

        # 速度线装饰（底部微妙的动感）
        if self.style.get("enable_speed_lines", True):
            draw_parallel_speed_lines(draw, x0 + 30, y0 + 140, x1 - 30, y0 + 140, count=12, opacity=20)

        # 场景编号 + 进度条
        num_font = _get_font(22, "body")
        num_text = f"第{idx+1}/{total}话"
        draw.text((x0 + 24, y0 + 14), num_text, fill=self.text_secondary, font=num_font)

        # 进度点
        if self.style.get("enable_progress_dots", True):
            dot_y = y0 + 20
            bar_x0 = x0 + 140
            bar_x1 = x1 - 140
            bar_cx = (bar_x0 + bar_x1) // 2
            seg_w = (bar_x1 - bar_x0) // max(total, 1)
            for i in range(total):
                sx = bar_x0 + i * seg_w + 2
                ex = bar_x0 + (i + 1) * seg_w - 2
                fill_c = accent if i <= idx else self.progress_inactive
                draw.rounded_rectangle([sx, dot_y - 4, ex, dot_y + 4], radius=3, fill=fill_c)

        # 中间总结文字 — 允许2行换行
        if subtitle:
            sub_font = _get_font(28, "body")
            sub = subtitle[:120]
            sub_max_w = x1 - x0 - 160
            sub_lines = self._wrap_text(sub, sub_font, sub_max_w)[:3]
            for li, sline in enumerate(sub_lines):
                sw = draw.textlength(sline, font=sub_font)
                sx = (self.w - int(sw)) // 2
                draw.text((sx, y0 + 38 + li * 34), sline, fill=self.style["text_c"], font=sub_font)

        # 底部标签行
        if self.style.get("enable_bottom_tags", True):
            tag_font = _get_font(22, "body")
            tags = self.style.get("tags_bottom", ["收藏", "点赞", "转发"])
            tag_x = x0 + 24
            for tag in tags:
                tw = draw.textlength(tag, font=tag_font)
                draw.rounded_rectangle([tag_x, y0 + 108, tag_x + int(tw) + 24, y0 + 138],
                                       radius=8, fill=None, outline=(180, 180, 190, 255), width=1)
                draw.text((tag_x + 12, y0 + 112), tag, fill=self.text_muted, font=tag_font)
                tag_x += int(tw) + 36

        # 右侧"▶ NEXT"
        next_font = _get_font(20, "body")
        draw.text((x1 - 100, y0 + 14), "▶ NEXT", fill=accent, font=next_font)

        return y0

    def _draw_content_text_only(self, draw, img, bullets: List[str],
                                y0: int, y1: int, accent: str):
        """文字流式布局 — 大字号连续排版，填满整个内容区。"""
        n = len(bullets)
        if n == 0:
            return

        content_h = y1 - y0
        margin = self.panel_gap + 10

        # 限制每帧最多8个要点
        bullets = bullets[:8]
        n = len(bullets)

        # 每个要点获取均等高度
        gap = 10
        card_h = (content_h - gap * (n - 1)) // n

        for i in range(n):
            cy0 = y0 + i * (card_h + gap)
            cy1 = cy0 + card_h if i < n - 1 else y1
            self._draw_fullwidth_card(draw, bullets[i], margin, cy0,
                                      self.w - margin, cy1, accent, i)
            # 分隔线
            if i < n - 1:
                sep_y = cy1 + gap // 2
                draw.line([(margin + 60, sep_y), (self.w - margin - 60, sep_y)],
                         fill=(200, 200, 210, 255), width=1)

    def _draw_fullwidth_card(self, draw, text: str,
                           x0: int, y0: int, x1: int, y1: int,
                           accent: str, index: int):
        """大字卡片 — 用最大的字号填满卡片，漫画冲击力排版。"""
        panel_w = x1 - x0
        panel_h = y1 - y0
        if panel_w < 100 or panel_h < 60:
            return

        # 极简边框
        draw.rounded_rectangle([x0, y0, x1, y1], radius=self.style["card_radius"],
                               fill=None, outline=self.style["card_border_color"], width=self.style["card_border_width"])

        pad = 24
        text_w = panel_w - pad * 2
        text_h = panel_h - pad * 2

        # ── 拆分冒号引导词 ──
        if '：' in text:
            lead_raw, body_raw = text.split('：', 1)
            lead = lead_raw + '：'
            body = body_raw
        elif ':' in text:
            lead_raw, body_raw = text.split(':', 1)
            lead = lead_raw + ':'
            body = body_raw
        else:
            lead = ''
            body = text

        # ── 找最大能放下的字号（上限40px，确保多行排版）──
        best_size = 16
        for size in [32, 30, 28, 26, 24, 22, 20, 18, 16]:
            body_f = _get_font(size, "body")
            lead_f = _get_font(size + 4, "title")
            line_spacing = max(8, size // 3)  # 大字多留行距
            lh = size + line_spacing

            total = 0
            if lead:
                total += len(self._wrap_text(lead, lead_f, text_w)) * (lh + 6)
            total += len(self._wrap_text(body, body_f, text_w)) * lh
            if total <= text_h:
                best_size = size
                break

        body_font = _get_font(best_size, "body")
        lead_font = _get_font(best_size + 4, "title")
        line_spacing = max(8, best_size // 3)
        lh = best_size + line_spacing

        # ── 按冒号拆分排版 → 引导词（彩色）+ 正文（黑色）──
        all_segments = []
        if lead:
            all_segments.append(('lead', lead))
        all_segments.append(('body', body))

        # 计算总行数用于垂直居中
        total_lines = 0
        for seg_type, seg_text in all_segments:
            f = lead_font if seg_type == 'lead' else body_font
            slh = lh + 6 if seg_type == 'lead' else lh
            total_lines += len(self._wrap_text(seg_text, f, text_w))
        total_text_px = 0
        for seg_type, seg_text in all_segments:
            f = lead_font if seg_type == 'lead' else body_font
            slh = lh + 6 if seg_type == 'lead' else lh
            total_text_px += len(self._wrap_text(seg_text, f, text_w)) * slh

        start_y = y0 + pad + (text_h - total_text_px) // 2
        text_y = max(y0 + pad, start_y)

        for seg_type, seg_text in all_segments:
            f = lead_font if seg_type == 'lead' else body_font
            slh = lh + 6 if seg_type == 'lead' else lh
            lines = self._wrap_text(seg_text, f, text_w)
            for ln in lines:
                if text_y + slh > y1 - pad:
                    break
                color = accent if seg_type == 'lead' else self.text_c
                draw.text((x0 + pad, text_y), ln, fill=color, font=f)
                text_y += slh

        # ── 序号圆圈（左上角）──
        if self.style.get("enable_numbered_circles", True):
            cr = 12
            ccx, ccy = x0 + pad + cr, y0 + pad + cr
            draw.ellipse([ccx - cr, ccy - cr, ccx + cr, ccy + cr], fill=accent)
            nf = _get_font(13, "title")
            ns = str(index + 1)
            nw = draw.textlength(ns, font=nf)
            draw.text((ccx - int(nw)//2, ccy - 8), ns, fill=(255,255,255,255), font=nf)

    # ── 带素材的内容区 ──────────────────────────────────

    def _draw_content_with_media(self, draw, img, bullets: List[str],
                                 media_path: str, y0: int, y1: int, accent: str):
        """文字+素材混合 — 左文字面板(网点+速度线) + 右素材图(漫画框)。"""
        content_h = y1 - y0
        media_w = 380
        gap_w = 16

        # 素材图片区域（右侧）
        mx0 = self.w - self.panel_gap - media_w
        mx1 = self.w - self.panel_gap
        my0 = y0
        my1 = y1

        # 素材面板 — 双线漫画框
        draw.rounded_rectangle([mx0, my0, mx1, my1], radius=14,
                               fill=self.media_panel_bg if not self.dark else (40, 40, 55, 255),
                               outline=self.border_rgba, width=self.style['border_width'])
        # 内框
        draw.rounded_rectangle([mx0 + 6, my0 + 6, mx1 - 6, my1 - 6], radius=10,
                               outline=self.border_rgba, width=2)
        # 图标签
        img_label = _get_font(20, "title")
        draw.text((mx0 + 16, my0 + 12), self.style.get("placeholder_text", "素材参考"), fill=accent, font=img_label)

        try:
            media_img = Image.open(media_path).convert("RGBA")
            mw = media_w - 36
            mh = content_h - 60
            media_img.thumbnail((mw, mh), Image.LANCZOS)
            px = mx0 + (media_w - media_img.width) // 2
            py = my0 + 44
            img.paste(media_img, (px, py), media_img if media_img.mode == "RGBA" else None)
            draw.rounded_rectangle([px - 3, py - 3, px + media_img.width + 3, py + media_img.height + 3],
                                   radius=6, outline=self.border_rgba, width=3)
        except Exception:
            ph_font = _get_font(22, "body")
            no_img_text = "暂无素材"
            nw = draw.textlength(no_img_text, font=ph_font)
            draw.text((mx0 + (media_w - int(nw)) // 2, my0 + content_h // 2 - 14),
                      no_img_text, fill=self.text_muted, font=ph_font)

        # 文字要点区（左侧）— 面板间速度线
        lx0, lx1 = self.panel_gap, mx0 - gap_w
        n = len(bullets)

        if n == 1:
            self._draw_fullwidth_card(draw, bullets[0], lx0, y0, lx1, y1, accent, 0)
        elif n == 2:
            h0 = (content_h - gap_w) // 2
            self._draw_fullwidth_card(draw, bullets[0], lx0, y0, lx1, y0 + h0, accent, 0)
            m_y = y0 + h0 + gap_w // 2
            draw_parallel_speed_lines(draw, lx0 + 40, m_y, lx1 - 40, m_y, count=8 if self.style.get("enable_speed_lines") else 0, opacity=20)
            self._draw_fullwidth_card(draw, bullets[1], lx0, y0 + h0 + gap_w, lx1, y1, accent, 1)
        elif n >= 3:
            show_n = min(n, 6)
            h_each = (content_h - gap_w * (show_n - 1)) // show_n
            for j in range(show_n):
                py0 = y0 + j * (h_each + gap_w)
                py1 = py0 + h_each
                self._draw_fullwidth_card(draw, bullets[j], lx0, py0, lx1, min(py1, y1), accent, j)
                if j < show_n - 1:
                    m_y = py1 + gap_w // 2
                    draw_parallel_speed_lines(draw, lx0 + 40, m_y, lx1 - 40, m_y, count=6 if self.style.get("enable_speed_lines") else 0, opacity=20)

    # ── 大数字炸裂卡片 ────────────────────────────────

    def _draw_big_number(self, draw, img, y0: int, y1: int, data: dict, accent: str):
        """大数字冲击卡片 — 居中巨数+标签+趋势箭头+脉冲光环。"""
        content_h = y1 - y0
        x0 = self.panel_gap
        x1 = self.w - self.panel_gap

        value = str(data.get("value") or data.get("number") or "0")
        label = str(data.get("label") or data.get("title") or "")
        trend = data.get("trend", "")
        sub_text = str(data.get("subtitle") or data.get("description") or "")

        # 面板背景
        draw.rounded_rectangle([x0, y0, x1, y1], radius=18,
                               fill=self.panel_bg, outline=self.style['border_color'], width=self.style['border_width'])

        # 网点纹理
        if self.style.get("enable_crosshatch", True):
            draw_crosshatch(draw, x0 + 14, y0 + 14, x1 - 14, y1 - 14,
                          spacing=self.style["crosshatch_spacing"], opacity=self.style["crosshatch_opacity"], angle=self.style["crosshatch_angle"])

        # 脉冲光环 — 多层同心圆
        cx, cy = self.w // 2, y0 + content_h // 2
        ring_base = min(content_h, self.w - x0 * 2) // 3
        for ri in range(3):
            rr = ring_base + ri * 50
            ring_alpha = max(25, 160 - ri * 50)
            for rw in [3, 2, 1]:
                draw.ellipse([cx - rr - rw, cy - rr - rw, cx + rr + rw, cy + rr + rw],
                             outline=(_hex_to_rgba(accent)[0], _hex_to_rgba(accent)[1], _hex_to_rgba(accent)[2], ring_alpha // (rw * 2)), width=rw)

        # 找到能放下的最大数字字号
        best_size = 36
        for size in [180, 160, 140, 120, 100, 80, 72, 64, 56, 48, 42, 36]:
            nf = _get_font(size, "title")
            nw = draw.textlength(value, font=nf)
            if nw < (x1 - x0) * 0.85:
                best_size = size
                break

        num_font = _get_font(best_size, "title")
        label_font = _get_font(max(28, best_size // 3), "body")
        sub_font = _get_font(26, "body")

        nw = draw.textlength(value, font=num_font)
        nx = (self.w - int(nw)) // 2
        ny = y0 + content_h * 0.25

        # 趋势箭头
        arrow = {"up": "↑", "down": "↓", "flat": "→"}.get(trend, "")
        if arrow:
            arrow_color = {"up": "#E04040", "down": "#3060C0", "flat": "#888888"}.get(trend, accent)
            arrow_font = _get_font(best_size // 2, "sfx")
            aw = draw.textlength(arrow, font=arrow_font)
            draw.text((nx - aw - 20, ny + best_size * 0.15), arrow, fill=arrow_color, font=arrow_font)

        # 主数字 — 粗描边
        for ox, oy in [(-4, 0), (4, 0), (0, -4), (0, 4), (-3, -3), (3, 3)]:
            draw.text((nx + ox, ny + oy), value, fill=(0, 0, 0, 255), font=num_font)
        draw.text((nx, ny), value, fill=accent, font=num_font)

        # 标签
        if label:
            lw = draw.textlength(label, font=label_font)
            lx = (self.w - int(lw)) // 2
            ly = ny + best_size + 16
            draw.text((lx, ly), label, fill=self.text_c, font=label_font)

        # 副标题
        if sub_text:
            sw = draw.textlength(sub_text, font=sub_font)
            sx = (self.w - int(sw)) // 2
            sy = max(ly + 44, y0 + content_h * 0.78)
            draw.text((sx, sy), sub_text[:80], fill=self.text_secondary, font=sub_font)

        # 下划线装饰
        if self.style.get("enable_decorative_lines", True):
            deco_y = y0 + content_h - 24
            draw.line([(self.w // 2 - 120, deco_y), (self.w // 2 + 120, deco_y)], fill=accent, width=3)

    # ── VS对比面板 ──────────────────────────────────────

    def _draw_vs_compare(self, draw, img, y0: int, y1: int, data: dict, accent: str):
        """VS对比双栏面板 — 左右分屏数据PK，适合对比分析场景。"""
        content_h = y1 - y0
        x0 = self.panel_gap
        x1 = self.w - self.panel_gap
        panel_w = x1 - x0
        mid_x = self.w // 2

        # 面板背景
        draw.rounded_rectangle([x0, y0, x1, y1], radius=18,
                               fill=self.panel_bg, outline=self.style['border_color'], width=self.style['border_width'])

        left = data.get("left", {})
        right = data.get("right", {})
        vs_text = data.get("vs_text", "VS")

        # 中央 VS 分隔
        vs_font = _get_font(48, "sfx")
        vs_w = draw.textlength(vs_text, font=vs_font)
        vs_x = mid_x - int(vs_w) // 2
        vs_y = y0 + content_h // 2 - 30
        # VS 描边
        for ox, oy in [(-3, 0), (3, 0), (0, -3), (0, 3)]:
            draw.text((vs_x + ox, vs_y + oy), vs_text, fill=(0, 0, 0, 255), font=vs_font)
        draw.text((vs_x, vs_y), vs_text, fill=accent, font=vs_font)

        # 竖线分隔
        draw.line([(mid_x, y0 + 20), (mid_x, y1 - 20)], fill=(200, 200, 210, 255), width=2)

        # 左右两栏
        for side, side_data, col_x0, col_x1 in [
            ("left", left, x0, mid_x - 30),
            ("right", right, mid_x + 30, x1),
        ]:
            col_w = col_x1 - col_x0
            s_label = str(side_data.get("label") or side_data.get("title") or side)
            s_value = str(side_data.get("value") or side_data.get("number") or "-")
            s_desc = str(side_data.get("description") or side_data.get("subtitle") or "")

            # 数值字号自适应
            best_size = 28
            for size in [72, 60, 52, 44, 36, 32, 28]:
                nf = _get_font(size, "title")
                if draw.textlength(s_value, font=nf) < col_w * 0.8:
                    best_size = size
                    break

            num_font = _get_font(best_size, "title")
            name_font = _get_font(max(24, best_size // 2), "body")
            desc_font = _get_font(20, "body")

            # 标签
            nw = draw.textlength(s_label, font=name_font)
            draw.text((col_x0 + (col_w - int(nw)) // 2, y0 + 28), s_label, fill=self.text_c, font=name_font)

            # 数值
            vw = draw.textlength(s_value, font=num_font)
            vx = col_x0 + (col_w - int(vw)) // 2
            vy = y0 + content_h * 0.32
            draw.text((vx, vy), s_value, fill=accent if side == "left" else self.style.get("accent_blue", "#3060C0"), font=num_font)

            # 描述
            if s_desc:
                desc_lines = self._wrap_text(s_desc[:60], desc_font, col_w - 20)[:3]
                for li, dline in enumerate(desc_lines):
                    dw = draw.textlength(dline, font=desc_font)
                    dx = col_x0 + (col_w - int(dw)) // 2
                    dy = vy + best_size + 20 + li * 26
                    if dy < y1 - 20:
                        draw.text((dx, dy), dline, fill=self.text_secondary, font=desc_font)

    # ── 横屏布局 (1920×1080) ─────────────────────────────

    def _render_frame_landscape(self, title, bullets, output_path, subtitle,
                                 media_path, scene_index, total_scenes, sfx_text, accent):
        """横屏漫画帧渲染 — 左侧标题栏 + 右侧3列分镜格。"""
        img = Image.new("RGBA", (self.w, self.h),
                       self.paper + "FF" if len(self.paper) == 7 else self.paper)
        draw = ImageDraw.Draw(img, "RGBA")

        self._draw_bg_texture(draw, img)
        if self.style.get("enable_halftone", True):
            apply_halftone(img, (self.panel_gap, self.panel_gap, self.w - self.panel_gap, self.h - self.panel_gap),
                          dot_size=self.style["halftone_dot_size"], spacing=self.style["halftone_spacing"], angle=self.style["halftone_angle"], opacity=self.style["halftone_opacity"])
        self._draw_grid_overlay(draw)

        # 左侧标题栏
        sidebar_w = 280
        self._draw_title_sidebar(draw, title, sfx_text, subtitle, accent, sidebar_w)

        # 底部信息条
        bottom_y0 = self._draw_bottom_bar_landscape(draw, subtitle, scene_index, total_scenes, accent)

        # 右侧主内容区
        content_x0 = sidebar_w + self.panel_gap * 2
        content_y0 = self.panel_gap
        content_y1 = bottom_y0 - self.panel_gap

        has_media = media_path and Path(media_path).exists()
        if has_media:
            self._draw_content_landscape(draw, img, bullets, content_x0, content_y0,
                                        self.w - self.panel_gap, content_y1, accent, media_path)
        else:
            self._draw_content_landscape(draw, img, bullets, content_x0, content_y0,
                                        self.w - self.panel_gap, content_y1, accent)

        rgb = Image.new("RGB", (self.w, self.h), self.paper)
        rgb.paste(img, (0, 0), img)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        rgb.save(output_path, quality=92)
        return output_path

    def _draw_title_sidebar(self, draw, title, sfx, subtitle, accent, sidebar_w):
        """横屏左侧标题栏 — 窄竖条，标题竖排/缩排。"""
        x0, y0 = self.panel_gap, self.panel_gap
        x1 = x0 + sidebar_w
        y1 = self.h - self.panel_gap

        # 面板背景
        draw.rounded_rectangle([x0, y0, x1, y1], radius=18,
                               fill=self.panel_bg, outline=self.style['border_color'], width=self.style['border_width'])
        if self.style.get("enable_crosshatch", True):
            draw_crosshatch(draw, x0 + 14, y0 + 14, x1 - 14, y1 - 14,
                          spacing=self.style["crosshatch_spacing"], opacity=self.style["crosshatch_opacity"], angle=self.style["crosshatch_angle"])

        # 内边框
        if self.style.get("enable_inner_border", True):
            draw.rounded_rectangle([x0 + self.style['border_width'] + 3, y0 + self.style['border_width'] + 3,
                                    x1 - self.style['border_width'] - 3, y1 - self.style['border_width'] - 3],
                                   radius=14, outline=self.style['border_color'], width=2)

        # SFX拟声词 — 顶部居中
        if sfx:
            sfx_font = _get_font(48, "sfx")
            sfx_w = draw.textlength(sfx, font=sfx_font)
            sfx_x = (x0 + x1 - int(sfx_w)) // 2
            sfx_y = y0 + 30
            for ox, oy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
                draw.text((sfx_x + ox, sfx_y + oy), sfx, fill=(0, 0, 0, 255), font=sfx_font)
            draw.text((sfx_x, sfx_y), sfx, fill=accent, font=sfx_font)

        # 标题 — 窄栏内大字号折行
        title_font = _get_font(34, "title")
        inner_x0 = x0 + 24
        inner_x1 = x1 - 24
        inner_w = inner_x1 - inner_x0
        title_lines = self._wrap_text(title[:28], title_font, inner_w)
        ty = sfx_y + 70 if sfx else y0 + 40

        for ln in title_lines[:4]:
            draw.text((inner_x0, ty), ln, fill=accent, font=title_font)
            ty += 42

        # 装饰线
        if self.style.get("enable_decorative_lines", True):
            deco_y = ty + 16
            draw.line([(inner_x0, deco_y), (inner_x0 + 160, deco_y)], fill=(0, 0, 0, 255), width=3)
            draw.line([(inner_x0, deco_y + 6), (inner_x0 + 90, deco_y + 6)], fill=accent, width=2)

        # 副标题
        info_font = _get_font(22, "body")
        info_lines = self._wrap_text(subtitle[:80] or "详细讲解", info_font, inner_w)
        iy = deco_y + 30
        for ln in info_lines[:3]:
            draw.text((inner_x0, iy), ln, fill=self.text_secondary, font=info_font)
            iy += 30

        # 底部标签
        tag_font = _get_font(18, "body")
        tag_text = self.style.get("tag_secondary", "")
        if tag_text:
            tw = draw.textlength(tag_text, font=tag_font)
            draw.text(((x0 + x1 - int(tw)) // 2, y1 - 40), tag_text,
                     fill=self.text_muted, font=tag_font)

        return x1

    def _draw_content_landscape(self, draw, img, bullets, x0, y0, x1, y1, accent,
                                media_path=None):
        """横屏主内容区 — 水平多列分镜格布局。"""
        n = len(bullets)
        if n == 0:
            return

        content_w = x1 - x0
        content_h = y1 - y0
        gap = self.panel_gap

        # 有图片时：左侧3列文字 + 右侧图片
        has_media = media_path and Path(media_path).exists()
        if has_media:
            media_w = min(420, content_w // 3)
            text_w = content_w - media_w - gap
            text_x0, text_x1 = x0, x0 + text_w
            media_x0, media_x1 = text_x1 + gap, x1

            # 绘制图片区域
            try:
                photo = Image.open(media_path).convert("RGBA")
                pw, ph = photo.size
                scale = min((media_x1 - media_x0) / pw, (y1 - y0 - 20) / ph)
                nw, nh = int(pw * scale), int(ph * scale)
                photo = photo.resize((nw, nh), Image.LANCZOS)
                px = media_x0 + (media_x1 - media_x0 - nw) // 2
                py = y0 + (y1 - y0 - nh) // 2
                img.paste(photo, (px, py), photo)
            except Exception:
                pass

            # 文字区域
            self._layout_landscape_grid(draw, bullets[:min(n, 4)], text_x0, y0, text_x1, y1, accent)
        else:
            self._layout_landscape_grid(draw, bullets, x0, y0, x1, y1, accent)

    def _layout_landscape_grid(self, draw, bullets, x0, y0, x1, y1, accent):
        """横屏分镜格布局 — 根据子弹数量自适应列/行。"""
        n = len(bullets)
        if n == 0:
            return
        gap = self.panel_gap
        content_w = x1 - x0
        content_h = y1 - y0

        if n == 1:
            # 单面板全宽
            self._draw_fullwidth_card(draw, bullets[0], x0, y0, x1, y1, accent, 0)
        elif n == 2:
            # 左右两栏
            hw = (content_w - gap) // 2
            self._draw_fullwidth_card(draw, bullets[0], x0, y0, x0 + hw, y1, accent, 0)
            self._draw_fullwidth_card(draw, bullets[1], x0 + hw + gap, y0, x1, y1, accent, 1)
        elif n == 3:
            # 三列
            cw = (content_w - gap * 2) // 3
            for i in range(3):
                cx0 = x0 + i * (cw + gap) if i > 0 else x0
                self._draw_fullwidth_card(draw, bullets[i], cx0, y0, cx0 + cw, y1, accent, i)
        elif n == 4:
            # 2x2网格
            cw = (content_w - gap) // 2
            rh = (content_h - gap) // 2
            for i in range(4):
                col, row = i % 2, i // 2
                cx0 = x0 + col * (cw + gap) if col > 0 else x0
                cy0 = y0 + row * (rh + gap) if row > 0 else y0
                self._draw_fullwidth_card(draw, bullets[i], cx0, cy0, cx0 + cw, cy0 + rh, accent, i)
        else:
            # 5-6: 3列×2行
            cw = (content_w - gap * 2) // 3
            rh = (content_h - gap) // 2
            for i in range(n):
                col, row = i % 3, i // 2
                cx0 = x0 + col * (cw + gap) if col > 0 else x0
                cy0 = y0 + row * (rh + gap) if row > 0 else y0
                self._draw_fullwidth_card(draw, bullets[i], cx0, cy0, cx0 + cw, cy0 + rh, accent, i)

    def _draw_bottom_bar_landscape(self, draw, subtitle, idx, total, accent):
        """横屏底部紧凑信息栏。"""
        bar_h = 100
        y1 = self.h - self.panel_gap
        y0 = y1 - bar_h
        x0, x1 = self.panel_gap, self.w - self.panel_gap

        # 速度线背景
        draw_parallel_speed_lines(draw, x0, y0 + bar_h // 2, x1, y0 + bar_h // 2,
                                count=12 if self.style.get("enable_speed_lines") else 0, opacity=12)

        # 半透明底条
        draw.rectangle([x0, y0, x1, y1], fill=(*self.panel_bg_rgba[:3], 220))

        # 分隔线
        draw.line([(x0, y0), (x1, y0)], fill=self.style['border_color'], width=2)

        # 页码 + 总结
        page_font = _get_font(24, "body")
        page_text = f"{idx+1}/{total}"
        draw.text((x0 + 20, y0 + 16), page_text, fill=accent, font=page_font)

        summary = subtitle[:80] if subtitle else self.style.get("default_subtitle", "详细讲解")[:20]
        sum_font = _get_font(22, "body")
        sw = draw.textlength(summary, font=sum_font)
        draw.text(((self.w - int(sw)) // 2, y0 + 18), summary,
                 fill=self.text_secondary, font=sum_font)

        # 右侧进度点
        if self.style.get("enable_progress_dots", True):
            dot_r = 6
            total_dots = min(total, 10)
            start_dx = x1 - 30 - total_dots * 20
            for d in range(total_dots):
                dx = start_dx + d * 20
                fill_c = accent if d == idx else self.progress_inactive
                draw.ellipse([dx, y0 + bar_h // 2 - dot_r, dx + dot_r * 2, y0 + bar_h // 2 + dot_r],
                            fill=fill_c)

        return y0

    # ── 文字换行 ────────────────────────────────────────

    def _wrap_text(self, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
        """按像素宽度自动换行。"""
        lines = []
        current = ""
        for ch in text:
            test = current + ch
            w = font.getlength(test) if hasattr(font, 'getlength') else len(test) * (font.size // 2)
            if w > max_width and current:
                lines.append(current)
                current = ch
            else:
                current = test
        if current:
            lines.append(current)
        return lines if lines else [text]

    # ── 批量生成 ────────────────────────────────────────

    def render_storyboard(self,
                          storyboard: List[dict],
                          script_content: str,
                          work_dir: str,
                          materials: dict = None) -> List[str]:
        """为整个分镜批量生成漫画帧。

        Args:
            storyboard: 分镜列表，每项含 title/subtitle/bullets
            script_content: 完整脚本文本
            work_dir: 输出目录
            materials: {scene_index: file_path} 素材映射

        Returns:
            生成的 PNG 路径列表
        """
        materials = materials or {}
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        outputs = []

        scenes = storyboard if storyboard else [
            {"title": f"场景 {i+1}", "subtitle": s, "bullets": self._extract_bullets(s)}
            for i, s in enumerate(self._split_sentences(script_content))
        ]
        if not scenes:
            scenes = [{"title": "讲解", "subtitle": script_content[:60], "bullets": ["内容概要"]}]

        for i, scene in enumerate(scenes):
            title = str(scene.get("title") or f"场景 {i+1}")[:36]
            subtitle = str(scene.get("subtitle") or "")
            bullets = scene.get("bullets") if isinstance(scene.get("bullets"), list) else self._extract_bullets(subtitle or script_content)
            bullets = [str(x).strip() for x in bullets if str(x).strip()] or ["要点讲解"]
            # 每帧最多8个要点
            bullets = bullets[:8]
            sfx = str(scene.get("sfx") or "")
            mp = materials.get(str(i)) or scene.get("material_url")
            ve = str(scene.get("visual_element") or "")
            vd = scene.get("visual_data") if isinstance(scene.get("visual_data"), dict) else {}

            out = work_dir / f"manga_scene_{i:03d}.png"
            self.render_frame(
                title=title,
                bullets=bullets,
                output_path=str(out),
                subtitle=subtitle[:200],
                media_path=mp,
                scene_index=i,
                total_scenes=len(scenes),
                sfx_text=sfx,
                visual_element=ve,
                visual_data=vd,
            )
            outputs.append(str(out))

        return outputs

    @staticmethod
    def _extract_bullets(text: str, max_items: int = 8) -> list:
        import re
        parts = re.split(r'(?<=[。！？!?])\s*|\n+', text or "")
        candidates = [s.strip() for s in parts if s.strip()]
        if candidates:
            return candidates[:max_items]
        if text and text.strip():
            return [text.strip()]
        return []

    @staticmethod
    def _split_sentences(text: str) -> list:
        import re
        return [s.strip() for s in re.split(r'(?<=[。！？!?])\s*|\n+', text or "") if s.strip()]




