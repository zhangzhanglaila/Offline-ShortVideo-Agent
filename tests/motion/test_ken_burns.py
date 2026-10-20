"""
Phase D1 运动镜头测试。

测试内容：
1. Ken Burns参数生成
2. SceneClipSpec
3. 字幕覆盖层/渐变背景渲染
4. 合成器运镜路径(真实FFmpeg，无则跳过)
5. 向后兼容(元组场景仍可用)
"""

import asyncio
from pathlib import Path

from PIL import Image
import numpy as np

from core.compose.motion.ken_burns import make_ken_burns, KenBurnsSpec, _VARIANTS
from core.compose.motion.clip_spec import SceneClipSpec
from core.compose.scene_image_renderer import SceneImageRenderer
from core.compose.ffmpeg_composer import FFmpegComposer


TEST_DIR = Path("output/test_d1")


def _setup():
    TEST_DIR.mkdir(parents=True, exist_ok=True)


# ========== 测试1: Ken Burns参数 ==========

class TestKenBurns:
    """测试Ken Burns参数生成。"""

    def test_make_spec(self):
        """测试生成运镜规格。"""
        spec = make_ken_burns(0, (1080, 1920), 30, 3.0)
        assert isinstance(spec, KenBurnsSpec)
        assert spec.size == (1080, 1920)
        assert spec.total_frames == 90
        print(f"✅ 运镜规格生成: {spec.name}, {spec.total_frames}帧")

    def test_variants_cycle(self):
        """测试变体轮换(不同场景不雷同)。"""
        names = [make_ken_burns(i, (540, 960), 15, 2.0).name
                 for i in range(len(_VARIANTS) + 2)]
        # 前N个应各不相同
        assert len(set(names[:len(_VARIANTS)])) == len(_VARIANTS)
        # 循环回绕
        assert names[len(_VARIANTS)] == names[0]
        print(f"✅ 变体轮换: {len(_VARIANTS)}种")

    def test_build_filter(self):
        """测试滤镜字符串生成。"""
        spec = make_ken_burns(2, (540, 960), 15, 2.0)
        f = spec.build_filter()
        # 应包含关键滤镜
        assert "scale=1080:1920" in f  # 2倍画布
        assert "zoompan" in f
        assert "s=540x960" in f  # 输出尺寸
        assert "d=30" in f  # 2s×15fps
        # 不应含format(留给overlay组合)
        assert "format=yuv420p" not in f
        print("✅ 滤镜字符串正确")

    def test_zoom_only_variant(self):
        """测试纯缩放变体(无漂移)。"""
        spec = make_ken_burns(0, (540, 960), 15, 2.0)  # zoom_in_center
        assert spec.drift_x == 0.0
        assert spec.z_end > spec.z_start  # 放大
        print("✅ 纯缩放变体正确")


# ========== 测试2: SceneClipSpec ==========

class TestSceneClipSpec:
    """测试片段规格。"""

    def test_static_spec(self):
        """测试静态规格。"""
        spec = SceneClipSpec(background_path="a.png", duration=3.0)
        assert not spec.has_motion
        assert not spec.has_overlay
        print("✅ 静态规格正常")

    def test_motion_spec(self):
        """测试运镜规格。"""
        kb = make_ken_burns(0, (540, 960), 15, 2.0)
        spec = SceneClipSpec(
            background_path="a.png", duration=2.0,
            ken_burns=kb, overlay_path="sub.png",
        )
        assert spec.has_motion
        assert spec.has_overlay
        print("✅ 运镜规格正常")


# ========== 测试3: 分层渲染 ==========

class TestLayerRendering:
    """测试字幕覆盖层和渐变背景渲染。"""

    def test_subtitle_overlay_transparent(self):
        """测试字幕覆盖层是透明PNG且轻量。"""
        _setup()
        renderer = SceneImageRenderer(size=(540, 960))
        out = str(TEST_DIR / "sub.png")
        ok = renderer.render_subtitle_overlay("这是字幕文字", out)
        assert ok
        img = Image.open(out)
        assert img.mode == "RGBA"

        arr = np.array(img)
        # 顶部应完全透明(素材可露出)
        top_alpha = arr[:int(960 * 0.5), :, 3].mean()
        assert top_alpha < 5, f"上半部应透明: {top_alpha}"
        # 底部有字幕(部分不透明)
        bottom_alpha = arr[int(960 * 0.85):, :, 3].mean()
        assert bottom_alpha > 20, f"底部应有字幕: {bottom_alpha}"
        print(f"✅ 字幕层轻量透明: 上{top_alpha:.0f}/下{bottom_alpha:.0f}")

    def test_gradient_bg(self):
        """测试渐变背景渲染。"""
        _setup()
        renderer = SceneImageRenderer(size=(540, 960))
        out = str(TEST_DIR / "grad.png")
        ok = renderer.render_gradient_bg(out)
        assert ok
        assert Image.open(out).size == (540, 960)
        print("✅ 渐变背景渲染正常")


# ========== 测试4: 合成器运镜路径 ==========

class TestComposerMotion:
    """测试合成器的运镜路径(真实FFmpeg)。"""

    def test_motion_clip_has_movement(self):
        """测试运镜片段首尾帧不同(真实运动)。"""
        composer = FFmpegComposer(size=(540, 960), fps=15)
        if not composer.available:
            print("⏭️  跳过: FFmpeg不可用")
            return

        _setup()
        # 造一张有特征的背景图
        bg = Image.new("RGB", (1200, 800), (40, 80, 140))
        from PIL import ImageDraw
        d = ImageDraw.Draw(bg)
        d.ellipse([200, 150, 600, 550], fill=(240, 200, 60))
        bg_path = str(TEST_DIR / "motion_bg.png")
        bg.save(bg_path)

        kb = make_ken_burns(2, (540, 960), 15, 2.0)  # zoom+pan
        spec = SceneClipSpec(background_path=bg_path, duration=2.0, ken_burns=kb)
        out = str(TEST_DIR / "motion_clip.mp4")

        ok = composer._render_scene_clip(spec, out)
        assert ok
        assert Path(out).exists()

        # 抽首尾帧对比
        import subprocess
        subprocess.run(["ffmpeg", "-y", "-i", out, "-vf", "select=eq(n\\,0)",
                        "-frames:v", "1", str(TEST_DIR / "mf.png")],
                       capture_output=True)
        subprocess.run(["ffmpeg", "-y", "-sseof", "-0.15", "-i", out,
                        "-frames:v", "1", str(TEST_DIR / "ml.png")],
                       capture_output=True)
        f = np.array(Image.open(TEST_DIR / "mf.png").convert("RGB").resize((135, 240)))
        l = np.array(Image.open(TEST_DIR / "ml.png").convert("RGB").resize((135, 240)))
        diff = np.abs(f.astype(int) - l.astype(int)).mean()
        assert diff > 3, f"应有运动: {diff}"
        print(f"✅ 运镜片段有运动: 首尾差异{diff:.1f}")

    def test_motion_with_overlay(self):
        """测试运镜+字幕覆盖层合成。"""
        composer = FFmpegComposer(size=(540, 960), fps=15)
        if not composer.available:
            print("⏭️  跳过: FFmpeg不可用")
            return

        _setup()
        bg = Image.new("RGB", (1200, 800), (60, 100, 160))
        bg_path = str(TEST_DIR / "ov_bg.png")
        bg.save(bg_path)

        renderer = SceneImageRenderer(size=(540, 960))
        sub_path = str(TEST_DIR / "ov_sub.png")
        renderer.render_subtitle_overlay("测试字幕", sub_path)

        kb = make_ken_burns(0, (540, 960), 15, 1.5)
        spec = SceneClipSpec(background_path=bg_path, duration=1.5,
                             ken_burns=kb, overlay_path=sub_path)
        out = str(TEST_DIR / "ov_clip.mp4")
        ok = composer._render_scene_clip(spec, out)
        assert ok
        assert Path(out).exists()
        print("✅ 运镜+字幕覆盖合成正常")


# ========== 测试5: 向后兼容 ==========

class TestBackwardCompat:
    """测试元组场景仍可用。"""

    def test_tuple_scenes_normalize(self):
        """测试元组归一化为SceneClipSpec。"""
        composer = FFmpegComposer(size=(540, 960), fps=15)
        spec = composer._normalize_spec(("a.png", 3.0))
        assert isinstance(spec, SceneClipSpec)
        assert spec.background_path == "a.png"
        assert spec.duration == 3.0
        assert not spec.has_motion
        print("✅ 元组向后兼容")

    def test_spec_passthrough(self):
        """测试SceneClipSpec直接透传。"""
        composer = FFmpegComposer(size=(540, 960), fps=15)
        original = SceneClipSpec(background_path="b.png", duration=2.0)
        spec = composer._normalize_spec(original)
        assert spec is original
        print("✅ 规格透传正常")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
