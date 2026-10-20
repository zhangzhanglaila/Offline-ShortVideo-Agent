# -*- coding: utf-8 -*-
"""
Cinematic 风格渲染器
电影质感风格 - 暗调背景、胶片颗粒、青橙色调
"""
from typing import List, Dict, Optional
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageEnhance
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from core.style_renderer import StyleRenderer, register_renderer


class CinematicStyleRenderer(StyleRenderer):
    """电影风格渲染器"""

    def __init__(self, width: int = 1080, height: int = 1920,
                 style_config: dict = None):
        super().__init__(width, height, style_config)

        self.colors = self.style_config.get("colors", {})
        self.typography = self.style_config.get("typography", {})
        self.layout = self.style_config.get("layout", {})

        self.bg_color = self.colors.get("background", "#0A0A0A")
        self.primary_color = self.colors.get("primary", "#E5E5E5")
        self.accent_color = self.colors.get("accent", "#FF9500")
        self.card_bg = self.colors.get("card_bg", "#1A1A1A")
        self.card_border = self.colors.get("card_border", "#333333")

        self.padding = self.layout.get("padding", 60)
        self.card_radius = self.layout.get("card_radius", 4)
        self.card_border_width = self.layout.get("card_border_width", 2)

        # 字体配置（使用衬线字体）
        self.title_size = self.typography.get("title_size", 48)
        self.body_size = self.typography.get("body_size", 24)
        self.line_height = self.typography.get("line_height", 1.6)

        # 电影特效
        effects = self.style_config.get("effects", {})
        self.animation_speed = effects.get("animation_speed", "slow")

        # 特效配置
        self.film_grain = self.style_config.get("film_grain", True)
        self.vignette = self.style_config.get("vignette", True)
        self.color_grading = self.style_config.get("color_grading", {})

        # 加载字体
        self._fonts = {}
        self._load_fonts()

    def _load_fonts(self):
        """加载字体（优先衬线字体）"""
        font_roots = [
            "C:/Windows/Fonts",
            "/System/Library/Fonts",
            "/usr/share/fonts",
        ]
        # 衬线字体优先
        candidates = {
            "title": ["georgia.ttf", "Georgia.ttf", "times.ttf", "msyhbd.ttc", "simhei.ttf"],
            "body": ["georgia.ttf", "Georgia.ttf", "times.ttf", "msyh.ttc", "simsun.ttc"],
        }

        for style, names in candidates.items():
            for name in names:
                for root in font_roots:
                    fp = Path(root) / name
                    if fp.exists():
                        try:
                            size = self.title_size if style == "title" else self.body_size
                            self._fonts[style] = ImageFont.truetype(str(fp), size)
                            break
                        except Exception:
                            continue
                if style in self._fonts:
                    break

        if "title" not in self._fonts:
            self._fonts["title"] = ImageFont.load_default()
        if "body" not in self._fonts:
            self._fonts["body"] = ImageFont.load_default()

    def _get_font(self, style: str = "body", size: int = None):
        """获取字体"""
        if size and style in self._fonts and hasattr(self._fonts[style], 'path'):
            try:
                return ImageFont.truetype(self._fonts[style].path, size)
            except Exception:
                pass
        return self._fonts.get(style, ImageFont.load_default())

    def render_frame(
        self,
        title: str,
        bullets: List[str],
        output_path: str,
        subtitle: str = "",
        media_path: str = None,
        scene_index: int = 0,
        total_scenes: int = 1,
        sfx_text: str = "",
        **kwargs
    ) -> str:
        """渲染电影风格帧"""
        if not HAS_PIL:
            raise RuntimeError("PIL is required for rendering")

        # 创建画布
        img = Image.new("RGB", (self.width, self.height), self.bg_color)
        draw = ImageDraw.Draw(img)

        # 绘制标题区
        title_y = self._draw_title(draw, title, subtitle)

        # 绘制内容区
        content_y = title_y + self.padding // 2
        bottom_y = self.height - self.padding

        if media_path and Path(media_path).exists():
            self._draw_content_with_media(draw, img, bullets, media_path,
                                        content_y, bottom_y)
        else:
            self._draw_content_text_only(draw, bullets, content_y, bottom_y)

        # 绘制底部信息
        self._draw_footer(draw, scene_index, total_scenes, bottom_y)

        # 应用电影特效
        img = self._apply_film_effects(img)

        # 保存
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        img.save(output_path, quality=92)
        return output_path

    def _apply_film_effects(self, img):
        """应用电影特效（调色、颗粒、暗角）"""
        # 青橙色调色
        if self.color_grading and self.color_grading.get("teal_orange"):
            img = self._apply_teal_orange(img)

        # 胶片颗粒
        if self.film_grain:
            img = self._add_film_grain(img)

        # 暗角
        if self.vignette:
            img = self._add_vignette(img)

        return img

    def _apply_teal_orange(self, img):
        """应用青橙色调（简化版）"""
        # 增加对比度
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(self.color_grading.get("contrast", 1.15))

        # 调整饱和度
        enhancer = ImageEnhance.Color(img)
        img = enhancer.enhance(self.color_grading.get("saturation", 0.95))

        return img

    def _add_film_grain(self, img):
        """添加胶片颗粒效果"""
        import random
        pixels = img.load()
        w, h = img.size
        for _ in range(int(w * h * 0.02)):  # 2% 像素
            x = random.randint(0, w - 1)
            y = random.randint(0, h - 1)
            r, g, b = pixels[x, y]
            noise = random.randint(-30, 30)
            pixels[x, y] = (
                max(0, min(255, r + noise)),
                max(0, min(255, g + noise)),
                max(0, min(255, b + noise))
            )
        return img

    def _add_vignette(self, img):
        """添加暗角效果"""
        w, h = img.size
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)

        # 创建径向渐变暗角
        center_x, center_y = w // 2, h // 2
        max_radius = int((w ** 2 + h ** 2) ** 0.5) // 2

        for r in range(max_radius, 0, -10):
            alpha = int((1 - r / max_radius) * 80)  # 边缘最暗
            if alpha > 0:
                draw.ellipse(
                    [center_x - r, center_y - r, center_x + r, center_y + r],
                    outline=(0, 0, 0, alpha)
                )

        return img

    def _draw_title(self, draw, title: str, subtitle: str) -> int:
        """绘制标题区（电影风格）"""
        y = self.padding
        x = self.padding
        max_w = self.width - 2 * self.padding

        # 标题（简洁，最多1行）
        title_font = self._get_font("title", self.title_size)
        lines = self._wrap_text(title, title_font, max_w)
        line_h = int(self.title_size * self.line_height)

        for i, line in enumerate(lines[:1]):
            draw.text((x, y + i * line_h), line, fill=self.primary_color,
                     font=title_font)
        y += line_h

        # 副标题（引言风格）
        if subtitle:
            body_font = self._get_font("body", int(self.body_size * 0.95))
            sub_lines = self._wrap_text(f'"{subtitle[:80]}"', body_font, max_w)
            sub_h = int(self.body_size * 0.95 * self.line_height)
            for i, line in enumerate(sub_lines[:2]):
                draw.text((x, y + 15 + i * sub_h), line,
                         fill=self.colors.get("secondary", "#8A8A8A"),
                         font=body_font)
            y += len(sub_lines) * sub_h + 15

        # 细分隔线
        y += 20
        draw.line([(x, y), (x + 120, y)], fill=self.accent_color, width=2)

        return y + 20

    def _draw_content_text_only(self, draw, bullets, y0, y1):
        """绘制纯文字内容（电影风格）"""
        if not bullets:
            bullets = ["故事内容"]

        content_h = y1 - y0 - 60
        n = min(len(bullets), 5)

        gap = 20
        item_h = (content_h - gap * (n - 1)) // n

        x = self.padding
        w = self.width - 2 * self.padding

        for i, bullet in enumerate(bullets[:n]):
            item_y = y0 + i * (item_h + gap)
            body_font = self._get_font("body", self.body_size)
            lines = self._wrap_text(bullet, body_font, w)
            line_h = int(self.body_size * self.line_height)

            for li, line in enumerate(lines[:3]):
                if item_y + li * line_h > y1 - 60:
                    break
                draw.text((x, item_y + li * line_h), line,
                         fill=self.primary_color, font=body_font)

    def _draw_content_with_media(self, draw, img, bullets, media_path, y0, y1):
        """绘制带素材的内容"""
        # 底部素材（类似电影剧照）
        media_h = min(400, (y1 - y0) // 2)
        mx0 = self.padding
        mx1 = self.width - self.padding
        my0 = y1 - media_h - 20
        my1 = y1 - 20

        try:
            from PIL import Image
            media = Image.open(media_path).convert("RGBA")
            mw, mh = mx1 - mx0, media_h
            media.thumbnail((mw, mh), Image.LANCZOS)
            px = mx0 + (mw - media.width) // 2
            py = my0 + (mh - media.height) // 2
            img.paste(media, (px, py), media if media.mode == "RGBA" else None)
        except Exception:
            pass

        # 上方文字
        text_y = y0
        text_bottom = my0 - 20

        if not bullets:
            bullets = ["故事内容"]

        body_font = self._get_font("body", self.body_size)
        w = self.width - 2 * self.padding

        all_text = " ".join(str(b) for b in bullets[:3])
        lines = self._wrap_text(all_text, body_font, w)
        line_h = int(self.body_size * self.line_height)

        for li, line in enumerate(lines):
            if text_y + li * line_h > text_bottom:
                break
            draw.text((self.padding, text_y + li * line_h), line,
                     fill=self.primary_color, font=body_font)

    def _draw_footer(self, draw, idx: int, total: int, y: int):
        """绘制底部信息（电影风格）"""
        # 场景标记（简洁）
        body_font = self._get_font("body", int(self.body_size * 0.9))
        marker = f"SCENE {idx + 1:02d}"
        draw.text((self.padding, y), marker, fill=self.colors.get("secondary", "#666"),
                 font=body_font)

    def render_storyboard(
        self,
        storyboard: List[dict],
        script_content: str,
        work_dir: str,
        materials: dict = None
    ) -> List[str]:
        """批量渲染分镜"""
        materials = materials or {}
        work_dir = Path(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)

        outputs = []
        for i, scene in enumerate(storyboard):
            title = scene.get("title", f"Scene {i+1}")
            subtitle = scene.get("subtitle", "")
            bullets = scene.get("bullets", [])
            if not isinstance(bullets, list):
                bullets = [bullets]
            bullets = [str(b).strip() for b in bullets if str(b).strip()][:5]

            media = materials.get(str(i)) or scene.get("material_url")
            output = work_dir / f"scene_{i:03d}.png"

            self.render_frame(
                title=title,
                bullets=bullets,
                output_path=str(output),
                subtitle=subtitle,
                media_path=media,
                scene_index=i,
                total_scenes=len(storyboard)
            )
            outputs.append(str(output))

        return outputs

    def _wrap_text(self, text: str, font, max_width: int) -> List[str]:
        """文字换行"""
        lines = []
        current = ""
        for ch in text:
            test = current + ch
            w = font.getlength(test) if hasattr(font, 'getlength') else len(test) * 10
            if w > max_width and current:
                lines.append(current)
                current = ch
            else:
                current = test
        if current:
            lines.append(current)
        return lines if lines else [text]


# 注册渲染器
register_renderer("cinematic", CinematicStyleRenderer)
