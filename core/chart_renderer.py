# -*- coding: utf-8 -*-
"""
图表渲染引擎 — 纯 Pillow 柱状图/饼图/折线图/流程图生成器
输出 PNG 图片，注入 manga_frame_renderer 的 materials 管道。
零新依赖（Pillow 已有），配色自适应 5 种视觉风格。
"""
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

# ═══════════════════════════════════════════════════════════════
# 字体加载（复用 manga_frame_renderer 方案）
# ═══════════════════════════════════════════════════════════════

def _find_font_path() -> Optional[str]:
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/local/share/fonts/NotoSansCJK-Regular.ttc",
    ]
    for fp in candidates:
        if Path(fp).exists():
            return fp
    return None

_FONT_PATH = None

def _get_font(size: int) -> ImageFont.FreeTypeFont:
    global _FONT_PATH
    if _FONT_PATH is None:
        _FONT_PATH = _find_font_path()
    if _FONT_PATH:
        return ImageFont.truetype(_FONT_PATH, size)
    return ImageFont.load_default()


# ═══════════════════════════════════════════════════════════════
# 配色表 — 每种 visual_style 一组专属调色板
# ═══════════════════════════════════════════════════════════════

CHART_COLORS = {
    "manga": {
        "bg": "#FFFBF5",
        "text": "#1A1A2E",
        "grid": "#E5DDD5",
        "accent": "#E04040",
        "palette": ["#E04040", "#3060C0", "#4CAF50", "#FF9800", "#9C27B0", "#00BCD4"],
    },
    "minimal": {
        "bg": "#FFFFFF",
        "text": "#1A1A2E",
        "grid": "#E8ECF0",
        "accent": "#4A90D9",
        "palette": ["#4A90D9", "#5C6BC0", "#26A69A", "#FFA726", "#AB47BC", "#29B6F6"],
    },
    "neon": {
        "bg": "#111128",
        "text": "#E0E0F0",
        "grid": "#1E1E3A",
        "accent": "#00FFC8",
        "palette": ["#00FFC8", "#FF6EC7", "#FFD700", "#00BFFF", "#7B68EE", "#FF4500"],
    },
    "magazine": {
        "bg": "#F9F6F0",
        "text": "#2C2C2C",
        "grid": "#E8E0D0",
        "accent": "#B8860B",
        "palette": ["#B8860B", "#8B4513", "#556B2F", "#CD853F", "#6B8E23", "#D2691E"],
    },
    "vibrant": {
        "bg": "#FFFFFF",
        "text": "#1A1A2E",
        "grid": "#FFE0E0",
        "accent": "#FF4757",
        "palette": ["#FF4757", "#3742FA", "#2ED573", "#FFA502", "#FF6348", "#1E90FF"],
    },
}


def _get_colors(visual_style: str) -> dict:
    return CHART_COLORS.get(visual_style, CHART_COLORS["manga"])


def _hex_to_rgba(hex_str: str, alpha: int = 255) -> tuple:
    h = hex_str.lstrip('#')
    if len(h) == 8:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16))
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def _make_gradient(w: int, h: int, top_hex: str, bottom_hex: str, direction: str = "vertical") -> Image.Image:
    """生成线性渐变 Image，vertical/horizontal/diagonal。"""
    from PIL import Image as PILImage
    grad = PILImage.new("RGBA", (w, h))
    pixels = grad.load()
    t = _hex_to_rgba(top_hex)
    b = _hex_to_rgba(bottom_hex)
    for y in range(h):
        for x in range(w):
            if direction == "vertical":
                frac = y / max(h - 1, 1)
            elif direction == "horizontal":
                frac = x / max(w - 1, 1)
            else:  # diagonal
                frac = (x / max(w - 1, 1) + y / max(h - 1, 1)) / 2
            r = int(t[0] + (b[0] - t[0]) * frac)
            g = int(t[1] + (b[1] - t[1]) * frac)
            bl = int(t[2] + (b[2] - t[2]) * frac)
            a = int(t[3] + (b[3] - t[3]) * frac) if len(t) > 3 and len(b) > 3 else 255
            pixels[x, y] = (r, g, bl, a)
    return grad


def _lighten_hex(hex_str: str, amount: float = 0.3) -> str:
    """将 hex 颜色变亮 amount (0-1)，返回 hex 字符串。"""
    r, g, b, a = _hex_to_rgba(hex_str)
    r = min(255, int(r + (255 - r) * amount))
    g = min(255, int(g + (255 - g) * amount))
    b = min(255, int(b + (255 - b) * amount))
    return f"#{r:02X}{g:02X}{b:02X}"


# ═══════════════════════════════════════════════════════════════
# 主分发
# ═══════════════════════════════════════════════════════════════

def render_chart(
    chart_spec: dict,
    output_path: str,
    visual_style: str = "manga",
    width: int = 340,
    height: int = 700,
    progress: float = 1.0,
    _override_layout: list = None,
) -> str:
    """
    主分发函数。根据 chart_type 渲染对应图表 PNG。

    参数:
        chart_spec: {"chart_type":"bar","title":"...","labels":[...],"values":[...],...}
        output_path: PNG 输出路径
        visual_style: manga/minimal/neon/magazine/vibrant
        width/height: 画布尺寸
        progress: 0.0→1.0 动画进度，1.0=完整图表
        _override_layout: 内部用，传入已解析的 flowchart layout

    返回 output_path，失败抛异常。
    """
    chart_type = chart_spec.get("chart_type", "bar")
    colors = _get_colors(visual_style)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if chart_type in ("bar", "bar_chart", "column"):
        return _render_bar_chart(chart_spec, output_path, colors, width, height, progress)
    elif chart_type in ("pie", "pie_chart"):
        return _render_pie_chart(chart_spec, output_path, colors, width, height, progress)
    elif chart_type in ("line", "line_chart"):
        return _render_line_chart(chart_spec, output_path, colors, width, height, progress)
    elif chart_type in ("flowchart", "diagram", "architecture"):
        return _render_static_flowchart(chart_spec, output_path, colors, width, height, _override_layout, progress)
    else:
        raise ValueError(f"Unknown chart_type: {chart_type}")


def render_chart_frames(
    chart_spec: dict,
    output_dir: str,
    visual_style: str = "manga",
    width: int = 340,
    height: int = 700,
    num_frames: int = 20,
) -> List[str]:
    """生成图表动画帧序列，返回 PNG 路径列表。"""
    import os
    os.makedirs(output_dir, exist_ok=True)
    paths = []
    for fi in range(num_frames):
        p = (fi + 1) / num_frames
        out = os.path.join(output_dir, f"frame_{fi:03d}.png")
        render_chart(chart_spec, out, visual_style, width, height, progress=p)
        paths.append(out)
    return paths


# ═══════════════════════════════════════════════════════════════
# 柱状图
# ═══════════════════════════════════════════════════════════════

def _render_bar_chart(spec: dict, output_path: str, colors: dict, w: int, h: int, progress: float = 1.0) -> str:
    labels = spec.get("labels", [])
    values = spec.get("values", [])
    title = spec.get("title", "")
    value_suffix = spec.get("value_suffix", "")
    bar_color_hex = spec.get("color") or colors["palette"][0]

    n = min(len(labels), len(values), 8)
    labels, values = labels[:n], values[:n]

    img = Image.new("RGBA", (w, h), _hex_to_rgba(colors["bg"]))
    draw = ImageDraw.Draw(img)

    margin_l, margin_r, margin_t, margin_b = 48, 24, 60, 64
    chart_w = w - margin_l - margin_r
    chart_h = h - margin_t - margin_b
    cx, cy = margin_l, margin_t

    # 标题
    title_font = _get_font(18)
    tw = draw.textlength(title, font=title_font)
    draw.text(((w - tw) / 2, 14), title, fill=_hex_to_rgba(colors["text"]), font=title_font)

    # Y 轴网格 + 刻度
    max_val = max(values) if values else 1
    y_top = _nice_ceil(max_val)
    grid_steps = 4
    step_v = y_top / grid_steps

    label_font = _get_font(13)
    for i in range(grid_steps + 1):
        gy = cy + chart_h - i * (chart_h / grid_steps)
        if i > 0:
            draw.line([(cx, gy), (cx + chart_w, gy)], fill=_hex_to_rgba(colors["grid"]), width=1)
        tick_text = f"{i * step_v:.0f}{value_suffix}"
        tl = draw.textlength(tick_text, font=label_font)
        draw.text((cx - tl - 8, gy - 7), tick_text, fill=_hex_to_rgba(colors["text"]), font=label_font)

    # 柱子 — 带交错生长动画
    bar_gap = max(8, chart_w // (n * 3))
    bar_w = max(14, (chart_w - bar_gap * (n + 1)) // n)
    val_font = _get_font(14)
    x_font = _get_font(12)

    # 生成浅色渐变顶色
    bar_top_hex = _lighten_hex(bar_color_hex, 0.35)

    for i in range(n):
        # 每根柱子独立的渐进阶段：第i根从 i/n 开始生长
        bar_start = i / max(n, 1) * 0.6
        bar_end = bar_start + 0.4
        bar_progress = max(0.0, min(1.0, (progress - bar_start) / max(bar_end - bar_start, 0.01))) if progress < 1.0 else 1.0

        bx = cx + bar_gap + i * (bar_w + bar_gap)
        target_h = int(values[i] / y_top * chart_h)
        val_h = int(target_h * bar_progress)
        by = cy + chart_h - val_h
        bw, bh = bar_w, max(val_h, 0)

        if bh > 0:
            radius = min(6, bw // 3, bh // 2)
            # 渐变填充
            grad = _make_gradient(bw, bh, bar_top_hex, bar_color_hex, "vertical")
            # 裁剪圆角
            mask = Image.new("L", (bw, bh), 0)
            mask_draw = ImageDraw.Draw(mask)
            try:
                mask_draw.rounded_rectangle([0, 0, bw, bh], radius=radius, fill=255)
                img.paste(grad, (bx, by), mask)
            except ValueError:
                img.paste(grad, (bx, by))

        # 数值标注（仅在柱子成形后显示）
        if bar_progress > 0.5:
            val_text = f"{values[i]}{value_suffix}"
            vw = draw.textlength(val_text, font=val_font)
            draw.text((bx + (bw - vw) / 2, by - 20), val_text, fill=_hex_to_rgba(colors["text"]), font=val_font)

        # X 轴标签
        lw = draw.textlength(labels[i], font=x_font)
        draw.text((bx + (bw - lw) / 2, cy + chart_h + 8), labels[i], fill=_hex_to_rgba(colors["text"]), font=x_font)

    img.save(output_path, "PNG")
    return output_path


# ═══════════════════════════════════════════════════════════════
# 饼图
# ═══════════════════════════════════════════════════════════════

def _render_pie_chart(spec: dict, output_path: str, colors: dict, w: int, h: int, progress: float = 1.0) -> str:
    labels = spec.get("labels", [])
    values = spec.get("values", [])
    title = spec.get("title", "")
    value_suffix = spec.get("value_suffix", "%")

    n = min(len(labels), len(values), 8)
    labels, values = labels[:n], values[:n]

    img = Image.new("RGBA", (w, h), _hex_to_rgba(colors["bg"]))
    draw = ImageDraw.Draw(img)

    # 标题
    title_font = _get_font(18)
    tw = draw.textlength(title, font=title_font)
    draw.text(((w - tw) / 2, 14), title, fill=_hex_to_rgba(colors["text"]), font=title_font)

    cx, cy = w / 2, h * 0.42
    radius = min(w, h) / 2 - 50
    total = sum(values) or 1

    palette = colors["palette"]
    start_angle = -90  # 从 12 点钟方向开始
    total_target = 360.0 * progress

    label_font = _get_font(13)
    legend_y = cy + radius + 30

    for i in range(n):
        angle = values[i] / total * 360
        target_end = start_angle + angle

        if progress < 1.0:
            if start_angle >= total_target:
                break
            actual_end = min(target_end, total_target)
        else:
            actual_end = target_end

        if actual_end > start_angle:
            color_rgba = _hex_to_rgba(palette[i % len(palette)])
            bbox = [cx - radius, cy - radius, cx + radius, cy + radius]
            draw.pieslice(bbox, start=start_angle, end=actual_end, fill=color_rgba, outline=_hex_to_rgba(colors["bg"]), width=2)

            # 百分比标注（仅该扇区画完后显示）
            if actual_end >= target_end - 1:
                mid = math.radians(start_angle + angle / 2)
                lx = cx + (radius * 0.7) * math.cos(mid)
                ly = cy + (radius * 0.7) * math.sin(mid)
                pct = values[i] / total * 100
                pct_text = f"{pct:.0f}{value_suffix}"
                pw = draw.textlength(pct_text, font=label_font)
                draw.text((lx - pw / 2, ly - 8), pct_text, fill=_hex_to_rgba(colors["text"]), font=label_font)

        # 图例
        lx0 = 24 + (i % 3) * 100
        ly0 = legend_y + (i // 3) * 26
        legend_color = _hex_to_rgba(palette[i % len(palette)])
        draw.rectangle([lx0, ly0, lx0 + 10, ly0 + 10], fill=legend_color)
        legend_label = labels[i][:6] if len(labels[i]) > 6 else labels[i]
        draw.text((lx0 + 14, ly0 - 1), legend_label, fill=_hex_to_rgba(colors["text"]), font=_get_font(12))

        start_angle = target_end

    img.save(output_path, "PNG")
    return output_path


# ═══════════════════════════════════════════════════════════════
# 折线图
# ═══════════════════════════════════════════════════════════════

def _render_line_chart(spec: dict, output_path: str, colors: dict, w: int, h: int, progress: float = 1.0) -> str:
    labels = spec.get("labels", [])
    values = spec.get("values", [])
    title = spec.get("title", "")
    value_suffix = spec.get("value_suffix", "")
    fill_area = spec.get("fill_area", True)
    line_color_hex = spec.get("color") or colors["palette"][0]

    n = min(len(labels), len(values), 12)
    labels, values = labels[:n], values[:n]

    img = Image.new("RGBA", (w, h), _hex_to_rgba(colors["bg"]))
    draw = ImageDraw.Draw(img)

    margin_l, margin_r, margin_t, margin_b = 48, 24, 60, 64
    chart_w = w - margin_l - margin_r
    chart_h = h - margin_t - margin_b
    cx, cy = margin_l, margin_t

    # 标题
    title_font = _get_font(18)
    tw = draw.textlength(title, font=title_font)
    draw.text(((w - tw) / 2, 14), title, fill=_hex_to_rgba(colors["text"]), font=title_font)

    # Y 轴网格
    max_val = max(values) if values else 1
    min_val = min(values) if values else 0
    y_range = max_val - min_val or 1
    y_bottom = min_val - y_range * 0.1
    y_top = max_val + y_range * 0.1
    y_span = y_top - y_bottom or 1

    label_font = _get_font(12)
    grid_steps = 4
    step_v = y_span / grid_steps

    for i in range(grid_steps + 1):
        gy = cy + chart_h - i * (chart_h / grid_steps)
        if i > 0 and i < grid_steps:
            draw.line([(cx, gy), (cx + chart_w, gy)], fill=_hex_to_rgba(colors["grid"]), width=1)
        tick_text = f"{y_bottom + i * step_v:.0f}{value_suffix}"
        tl = draw.textlength(tick_text, font=label_font)
        draw.text((cx - tl - 8, gy - 7), tick_text, fill=_hex_to_rgba(colors["text"]), font=label_font)

    # 数据点坐标
    points = []
    for i in range(n):
        px = cx + chart_w * i / max(n - 1, 1)
        py = cy + chart_h - chart_h * (values[i] - y_bottom) / y_span
        points.append((px, py))

    line_color = _hex_to_rgba(line_color_hex)
    fill_color = _hex_to_rgba(line_color_hex, alpha=50)

    # 动画：按进度截取已揭示的数据点
    visible_count = max(1, int(n * progress + 0.5)) if progress < 1.0 else n
    visible_points = points[:visible_count]

    # 如果进度在两个点之间，插值最后一个点
    if progress < 1.0 and visible_count < n and n >= 2:
        seg_frac = (progress * (n - 1)) - (visible_count - 1)
        if 0 <= seg_frac < 1 and visible_count - 1 >= 0:
            p0 = points[visible_count - 1]
            p1 = points[visible_count]
            ix = p0[0] + (p1[0] - p0[0]) * seg_frac
            iy = p0[1] + (p1[1] - p0[1]) * seg_frac
            visible_points.append((ix, iy))
        else:
            visible_points.append(points[visible_count])
    elif progress < 1.0:
        visible_points = points[:max(1, visible_count)]

    # 填充区域
    if fill_area and len(visible_points) > 1:
        fill_pts = [(visible_points[0][0], cy + chart_h)] + visible_points + [(visible_points[-1][0], cy + chart_h)]
        draw.polygon(fill_pts, fill=fill_color)

    # 折线
    if len(visible_points) > 1:
        draw.line(visible_points, fill=line_color, width=3)

    # 数据点 + 标注
    point_r = 5
    val_font = _get_font(13)
    x_font = _get_font(12)

    for i, (px, py) in enumerate(points):
        if i < visible_count - (1 if progress < 1.0 else 0):
            draw.ellipse([px - point_r, py - point_r, px + point_r, py + point_r], fill=line_color, outline=_hex_to_rgba(colors["bg"]), width=2)
            val_text = f"{values[i]}{value_suffix}"
            vw = draw.textlength(val_text, font=val_font)
            draw.text((px - vw / 2, py - 22), val_text, fill=_hex_to_rgba(colors["text"]), font=val_font)

        lw = draw.textlength(labels[i], font=x_font)
        draw.text((px - lw / 2, cy + chart_h + 8), labels[i], fill=_hex_to_rgba(colors["text"]), font=x_font)

    img.save(output_path, "PNG")
    return output_path


# ═══════════════════════════════════════════════════════════════
# 流程图（复用 SpringDiagramRenderer 渲染静态帧）
# ═══════════════════════════════════════════════════════════════

def _render_static_flowchart(
    spec: dict, output_path: str, colors: dict,
    w: int, h: int, override_layout: list = None,
    progress: float = 1.0,
) -> str:
    from core.spring_diagram_animation_module import SpringDiagramAnimationModule

    if override_layout:
        layout = override_layout
    elif "nodes" in spec and "edges" in spec:
        layout = _nodes_edges_to_layout(spec)
    else:
        layout = []

    if not layout:
        img = Image.new("RGBA", (w, h), _hex_to_rgba(colors["bg"]))
        draw = ImageDraw.Draw(img)
        title = spec.get("title", "")
        title_font = _get_font(18)
        tw = draw.textlength(title, font=title_font)
        draw.text(((w - tw) / 2, h / 2 - 12), title, fill=_hex_to_rgba(colors["text"]), font=title_font)
        img.save(output_path, "PNG")
        return output_path

    mod = SpringDiagramAnimationModule(width=w, height=h, fps=30)
    id_to_idx = {}
    for item in layout:
        t = item.get("type")
        if t == "rect":
            idx = mod.add_rect(
                label=item.get("label", ""),
                x=item.get("x", 0), y=item.get("y", 0),
                w=item.get("w", 120), h=item.get("h", 50),
                scheme=item.get("scheme", "teal"),
            )
            nid = item.get("id")
            if nid is not None:
                id_to_idx[nid] = idx
                id_to_idx[item.get("label")] = idx
        elif t == "arrow":
            fid = item.get("from")
            tid = item.get("to")
            fi = fid if isinstance(fid, int) else id_to_idx.get(fid, 0)
            ti = tid if isinstance(tid, int) else id_to_idx.get(tid, 0)
            if fi < len(mod.rects) and ti < len(mod.rects):
                mod.add_arrow(
                    from_idx=fi, to_idx=ti,
                    label=item.get("label", ""),
                    bidirection=item.get("bidirection", False),
                )

    total_elements = len(mod.rects) + len(mod.arrows)
    active_idx = min(int(progress * total_elements), total_elements - 1) if total_elements > 0 else 0
    elements = {
        "rects": mod.rects,
        "arrows": mod.arrows,
        "active_idx": active_idx,
    }
    frame = mod.renderer.render_frame(elements, global_progress=progress, fps=30)
    frame.save(output_path, "PNG")
    return output_path


def _nodes_edges_to_layout(spec: dict) -> list:
    """将 nodes/edges 格式转为 layout list 格式"""
    nodes = spec.get("nodes", [])
    edges = spec.get("edges", [])
    layout = []
    scheme_colors = ["teal", "blue", "blue", "orange", "purple", "teal"]

    for i, node in enumerate(nodes):
        layout.append({
            "type": "rect",
            "id": node.get("id", f"n{i}"),
            "label": node.get("label", ""),
            "x": node.get("x", 100 + i * 180),
            "y": node.get("y", 40 + i * 90),
            "w": node.get("w", 120),
            "h": node.get("h", 50),
            "scheme": node.get("scheme", scheme_colors[i % len(scheme_colors)]),
        })

    for edge in edges:
        layout.append({
            "type": "arrow",
            "from": edge.get("from"),
            "to": edge.get("to"),
            "label": edge.get("label", ""),
            "bidirection": edge.get("bidirection", False),
        })

    return layout


# ═══════════════════════════════════════════════════════════════
# DSL 解析器 — 解析旧 diagram_layout 格式
# ═══════════════════════════════════════════════════════════════

def parse_diagram_dsl(dsl_text: str, canvas_w: int = 1080, canvas_h: int = 1920,
                      target_w: int = 340, target_h: int = 600) -> list:
    """
    解析 LLM 输出的 diagram_layout DSL 文本，转换为 layout list。

    DSL 格式:
        [id] 标签 (x, y, w, h)
        [id] -> [id] "标注"
        [id] <-> [id]

    返回: [{"type":"rect",...}, {"type":"arrow",...}]
    """
    if not dsl_text or not dsl_text.strip():
        return []

    # 清理 ```diagram 包裹
    dsl_text = re.sub(r'```diagram\s*', '', dsl_text)
    dsl_text = re.sub(r'```\s*$', '', dsl_text)

    scale_x = target_w / canvas_w
    scale_y = target_h / canvas_h
    scale = min(scale_x, scale_y)

    layout = []
    id_index = {}
    scheme_colors = ["teal", "blue", "blue", "orange", "purple", "teal"]

    lines = [l.strip() for l in dsl_text.split('\n') if l.strip()]

    # First pass: nodes
    for line in lines:
        if '->' in line or '<->' in line:
            continue
        m = re.match(r'\[(\w+)\]\s+(.+?)\s*\((\d+),\s*(\d+),?\s*(\d+)?,?\s*(\d+)?\)', line)
        if m:
            nid, label, x, y, nw, nh = m.groups()
            idx = len(layout)
            id_index[nid] = idx
            layout.append({
                "type": "rect",
                "id": nid,
                "label": label.strip(),
                "x": max(10, int(float(x) * scale)),
                "y": max(10, int(float(y) * scale)),
                "w": int(float(nw or 120) * scale),
                "h": int(float(nh or 50) * scale),
                "scheme": scheme_colors[idx % len(scheme_colors)],
            })

    # Second pass: edges
    for line in lines:
        bidir = False
        if '<->' in line:
            arrow = '<->'
            bidir = True
        elif '->' in line:
            arrow = '->'
        else:
            continue

        m = re.match(r'\[(\w+)\]\s*' + re.escape(arrow) + r'\s*\[(\w+)\]\s*"?([^"]*)"?', line)
        if m:
            fid, tid, label = m.groups()
            layout.append({
                "type": "arrow",
                "from": id_index.get(fid, 0),
                "to": id_index.get(tid, 0),
                "label": (label or "").strip(),
                "bidirection": bidir,
            })

    return layout


# ═══════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════

def _nice_ceil(val: float) -> float:
    """将值取整到美观的刻度上限。"""
    if val <= 0:
        return 10
    magnitude = 10 ** int(math.log10(val))
    normalized = val / magnitude
    for nice in [1, 2, 2.5, 5, 10]:
        if normalized <= nice:
            return nice * magnitude
    return 10 * magnitude
