"""
Phase D2 文字入场动画测试。

测试内容：
1. AnimationSpec / OverlayLayer
2. build_overlay_filter 滤镜生成
3. render_title_overlay / render_solid_bg (含vibrant渐变bug修复)
4. 多覆盖层合成 + 淡入生效(真实FFmpeg)
5. Agent构建带动画的覆盖层
"""

import asyncio
import subprocess
from pathlib import Path

from PIL import Image
import numpy as np

from core.compose.motion.animation_spec import (
    AnimationSpec, OverlayLayer,
    ANIM_NONE, ANIM_FADE_IN, ANIM_SLIDE_UP,
)
from core.compose.motion.text_animations import build_overlay_filter
from core.compose.motion.clip_spec import SceneClipSpec
from core.compose.scene_image_renderer import SceneImageRenderer
from core.compose.ffmpeg_composer import FFmpegComposer


TEST_DIR = Path("output/test_d2u")


def _setup():
    TEST_DIR.mkdir(parents=True, exist_ok=True)


# ========== 测试1: 动画规格 ==========

class TestAnimationSpec:
    def test_static_spec(self):
        a = AnimationSpec(anim_type=ANIM_NONE)
        assert a.is_static
        print("✅ 静态规格")

    def test_fade_spec(self):
        a = AnimationSpec(anim_type=ANIM_FADE_IN, start=0.3, duration=0.5)
        assert not a.is_static
        assert a.start == 0.3
        print("✅ 淡入规格")

    def test_overlay_layer(self):
        lyr = OverlayLayer("a.png", AnimationSpec(ANIM_SLIDE_UP))
        assert lyr.image_path == "a.png"
        assert lyr.animation.anim_type == ANIM_SLIDE_UP
        print("✅ 覆盖层")


# ========== 测试2: 滤镜生成 ==========

class TestFilterGen:
    def test_static_filter(self):
        f = build_overlay_filter(1, "0:v", "out", AnimationSpec(ANIM_NONE), (540, 960))
        assert "overlay=0:0" in f
        assert "fade" not in f
        print("✅ 静态滤镜无fade")

    def test_fade_filter(self):
        f = build_overlay_filter(1, "bg", "out",
                                 AnimationSpec(ANIM_FADE_IN, 0.2, 0.5), (540, 960))
        assert "fade=t=in:st=0.200:d=0.500:alpha=1" in f
        assert "[out]" in f
        print("✅ 淡入滤镜")

    def test_slide_filter(self):
        f = build_overlay_filter(1, "bg", "out",
                                 AnimationSpec(ANIM_SLIDE_UP, 0.3, 0.6), (540, 960))
        assert "fade=t=in" in f
        assert "overlay=x=0:y=" in f  # 位置动画
        print("✅ 滑动滤镜")


# ========== 测试3: 渲染(含vibrant修复) ==========

class TestRendering:
    def test_title_overlay_transparent(self):
        _setup()
        r = SceneImageRenderer(size=(540, 960))
        out = str(TEST_DIR / "title.png")
        assert r.render_title_overlay("标题文字", out, "title_card")
        img = Image.open(out)
        assert img.mode == "RGBA"
        # 边角应透明
        arr = np.array(img)
        assert arr[0, 0, 3] == 0
        print("✅ 标题透明层")

    def test_solid_bg_solid_style(self):
        _setup()
        from styles import get_style
        r = SceneImageRenderer(style=get_style("minimal"), size=(540, 960))
        out = str(TEST_DIR / "bg_solid.png")
        assert r.render_solid_bg(out)
        print("✅ 纯色风格背景")

    def test_solid_bg_gradient_style(self):
        """vibrant渐变背景不应崩溃(修复潜伏bug)。"""
        _setup()
        from styles import get_style
        r = SceneImageRenderer(style=get_style("vibrant"), size=(540, 960))
        out = str(TEST_DIR / "bg_grad.png")
        # 修复前此处返回False
        assert r.render_solid_bg(out), "vibrant渐变背景应成功渲染"
        img = np.array(Image.open(out).convert("RGB"))
        # 应为粉色渐变，非黑
        assert img.mean() > 100, "渐变背景不应为黑"
        print(f"✅ vibrant渐变背景修复: 均色{tuple(img.mean(axis=(0,1)).astype(int))}")


# ========== 测试4: 多覆盖层合成(真实FFmpeg) ==========

class TestMultiOverlay:
    def test_fade_in_appears(self):
        """淡入动画: 动画前无内容，动画后出现。"""
        composer = FFmpegComposer(size=(540, 960), fps=20)
        if not composer.available:
            print("⏭️  跳过: FFmpeg不可用")
            return

        _setup()
        # 纯色背景 + 白色块文字层
        Image.new("RGB", (540, 960), (30, 40, 70)).save(TEST_DIR / "mo_bg.png")
        ov = Image.new("RGBA", (540, 960), (0, 0, 0, 0))
        from PIL import ImageDraw
        ImageDraw.Draw(ov).rectangle([100, 400, 440, 500], fill=(255, 255, 255, 255))
        ov.save(TEST_DIR / "mo_ov.png")

        spec = SceneClipSpec(
            background_path=str(TEST_DIR / "mo_bg.png"), duration=2.0,
            overlays=[OverlayLayer(str(TEST_DIR / "mo_ov.png"),
                                   AnimationSpec(ANIM_FADE_IN, 0.4, 0.5))],
        )
        out = str(TEST_DIR / "mo.mp4")
        assert composer._render_scene_clip(spec, out)

        subprocess.run(["ffmpeg", "-y", "-ss", "0.1", "-i", out, "-frames:v", "1",
                        str(TEST_DIR / "mo_e.png")], capture_output=True)
        subprocess.run(["ffmpeg", "-y", "-ss", "1.5", "-i", out, "-frames:v", "1",
                        str(TEST_DIR / "mo_l.png")], capture_output=True)
        e = np.array(Image.open(TEST_DIR / "mo_e.png").convert("RGB"))
        l = np.array(Image.open(TEST_DIR / "mo_l.png").convert("RGB"))
        we = int((e[400:500] > 200).all(axis=2).sum())
        wl = int((l[400:500] > 200).all(axis=2).sum())
        assert wl > we + 1000, f"淡入应出现文字: {we}->{wl}"
        print(f"✅ 淡入生效: 白像素 {we}->{wl}")

    def test_two_staggered_overlays(self):
        """两个错开时间的覆盖层链式合成。"""
        composer = FFmpegComposer(size=(540, 960), fps=20)
        if not composer.available:
            print("⏭️  跳过: FFmpeg不可用")
            return

        _setup()
        Image.new("RGB", (540, 960), (30, 40, 70)).save(TEST_DIR / "st_bg.png")
        for i, y in enumerate([(200, 300), (600, 700)]):
            ov = Image.new("RGBA", (540, 960), (0, 0, 0, 0))
            from PIL import ImageDraw
            ImageDraw.Draw(ov).rectangle([100, y[0], 440, y[1]],
                                         fill=(255, 255, 255, 255))
            ov.save(TEST_DIR / f"st_ov{i}.png")

        spec = SceneClipSpec(
            background_path=str(TEST_DIR / "st_bg.png"), duration=2.5,
            overlays=[
                OverlayLayer(str(TEST_DIR / "st_ov0.png"),
                             AnimationSpec(ANIM_FADE_IN, 0.1, 0.4)),
                OverlayLayer(str(TEST_DIR / "st_ov1.png"),
                             AnimationSpec(ANIM_SLIDE_UP, 0.8, 0.5)),
            ],
        )
        out = str(TEST_DIR / "st.mp4")
        assert composer._render_scene_clip(spec, out)
        assert Path(out).exists()
        print("✅ 双错开覆盖层合成")


# ========== 测试5: Agent构建动画覆盖层 ==========

class TestAgentOverlays:
    def test_content_scene_has_slide_subtitle(self):
        """内容场景应生成运镜背景+上滑字幕(D3后含多元素，字幕为最后一层)。"""
        _setup()
        from core.agents.video_compose_agent import VideoComposeAgent
        from core.models import Scene, SceneType, SceneMaterialMap
        from styles import get_style

        agent = VideoComposeAgent(size=(540, 960), enable_motion=True)
        agent._content_counter = 0
        renderer = SceneImageRenderer(style=get_style("minimal"), size=(540, 960))
        scene = Scene(2, SceneType.CONTENT.value, "讲解文字", 5.0, ["kw"])

        spec = agent._build_scene_spec(
            scene, 1, SceneMaterialMap(), renderer, TEST_DIR
        )
        assert spec is not None
        # 无素材→渐变背景 + 运镜 + 多元素覆盖层
        assert spec.has_motion
        assert len(spec.overlays) >= 1
        # 字幕为最后一层，上滑
        assert spec.overlays[-1].animation.anim_type == ANIM_SLIDE_UP
        print(f"✅ 内容场景{len(spec.overlays)}元素，字幕上滑")

    def test_card_has_fade_title(self):
        """标题卡应生成淡入标题覆盖层。"""
        _setup()
        from core.agents.video_compose_agent import VideoComposeAgent
        from core.models import Scene, SceneType, SceneMaterialMap
        from styles import get_style

        agent = VideoComposeAgent(size=(540, 960), enable_motion=True)
        renderer = SceneImageRenderer(style=get_style("minimal"), size=(540, 960))
        scene = Scene(1, SceneType.TITLE_CARD.value, "标题", 3.0)

        spec = agent._build_scene_spec(
            scene, 0, SceneMaterialMap(), renderer, TEST_DIR
        )
        assert spec is not None
        assert len(spec.overlays) == 1
        assert spec.overlays[0].animation.anim_type == ANIM_FADE_IN
        print("✅ 标题卡淡入标题")

    def test_motion_disabled_static(self):
        """关闭动画时回退静态。"""
        _setup()
        from core.agents.video_compose_agent import VideoComposeAgent
        from core.models import Scene, SceneType, SceneMaterialMap
        from styles import get_style

        agent = VideoComposeAgent(size=(540, 960), enable_motion=False)
        renderer = SceneImageRenderer(style=get_style("minimal"), size=(540, 960))
        scene = Scene(1, SceneType.TITLE_CARD.value, "标题", 3.0)
        spec = agent._build_scene_spec(scene, 0, SceneMaterialMap(), renderer, TEST_DIR)
        assert spec is not None
        assert not spec.has_motion
        assert len(spec.overlays) == 0
        print("✅ 关闭动画回退静态")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
