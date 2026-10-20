"""
Phase D3 场景元素编排测试。

测试内容：
1. build_content_overlays 多元素+错开时间
2. 关键词提取 / with_badge开关
3. 徽章/标签元素渲染
4. 水平滑动滤镜生成
5. 元素逐个出现(真实FFmpeg)
6. Agent集成
"""

import subprocess
from pathlib import Path

from PIL import Image
import numpy as np

from core.compose.motion.scene_composer import build_content_overlays, _first_keyword
from core.compose.motion.animation_spec import (
    AnimationSpec, ANIM_SLIDE_LEFT, ANIM_SLIDE_UP, ANIM_SLIDE_RIGHT,
)
from core.compose.motion.text_animations import build_overlay_filter
from core.compose.motion.clip_spec import SceneClipSpec
from core.compose.scene_image_renderer import SceneImageRenderer
from core.compose.ffmpeg_composer import FFmpegComposer
from core.models import Scene, SceneType


TEST_DIR = Path("output/test_d3u")


def _setup():
    TEST_DIR.mkdir(parents=True, exist_ok=True)


def _renderer():
    from styles import get_style
    return SceneImageRenderer(style=get_style("tech"), size=(540, 960))


# ========== 测试1: 元素编排 ==========

class TestSceneComposer:
    def test_three_elements_staggered(self):
        """内容场景生成3个错开时间的元素。"""
        _setup()
        scene = Scene(2, SceneType.CONTENT.value, "讲解文字", 4.0, ["Redis", "db"])
        overlays = build_content_overlays(scene, 1, _renderer(), TEST_DIR)

        assert len(overlays) == 3
        # 起始时间递增(逐个出现)
        starts = [o.animation.start for o in overlays]
        assert starts == sorted(starts)
        assert starts[0] < starts[1] < starts[2]
        # 类型: 徽章左滑, 标签左滑, 字幕上滑
        assert overlays[0].animation.anim_type == ANIM_SLIDE_LEFT
        assert overlays[1].animation.anim_type == ANIM_SLIDE_LEFT
        assert overlays[2].animation.anim_type == ANIM_SLIDE_UP
        print(f"✅ 3元素错开: start={starts}")

    def test_no_keyword_no_chip(self):
        """无关键词时不生成标签(徽章+字幕)。"""
        _setup()
        scene = Scene(2, SceneType.CONTENT.value, "文字", 4.0, [])
        overlays = build_content_overlays(scene, 1, _renderer(), TEST_DIR)
        assert len(overlays) == 2  # badge + subtitle
        print("✅ 无关键词跳过标签")

    def test_with_badge_false(self):
        """关闭徽章时只有字幕。"""
        _setup()
        scene = Scene(2, SceneType.CONTENT.value, "文字", 4.0, ["kw"])
        overlays = build_content_overlays(scene, 1, _renderer(), TEST_DIR,
                                          with_badge=False)
        assert len(overlays) == 1
        assert overlays[0].animation.anim_type == ANIM_SLIDE_UP
        print("✅ 关闭徽章只有字幕")

    def test_first_keyword(self):
        """关键词提取。"""
        assert _first_keyword(Scene(1, "content", "t", 3.0, ["", "  ", "real"])) == "real"
        assert _first_keyword(Scene(1, "content", "t", 3.0, [])) == ""
        print("✅ 关键词提取")


# ========== 测试2: 元素渲染 ==========

class TestElementRender:
    def test_badge_transparent(self):
        _setup()
        r = _renderer()
        out = str(TEST_DIR / "badge.png")
        assert r.render_badge_overlay(3, out)
        img = Image.open(out)
        assert img.mode == "RGBA"
        arr = np.array(img)
        # 应有不透明的徽章像素
        assert (arr[:, :, 3] > 0).sum() > 500
        # 边角透明
        assert arr[-1, -1, 3] == 0
        print("✅ 徽章透明层")

    def test_chip_transparent(self):
        _setup()
        r = _renderer()
        out = str(TEST_DIR / "chip.png")
        assert r.render_keyword_chip_overlay("Redis", out)
        arr = np.array(Image.open(out))
        assert (arr[:, :, 3] > 0).sum() > 500
        print("✅ 标签透明层")


# ========== 测试3: 水平滑动滤镜 ==========

class TestHSlide:
    def test_slide_left_filter(self):
        f = build_overlay_filter(1, "bg", "out",
                                 AnimationSpec(ANIM_SLIDE_LEFT, 0.2, 0.4), (540, 960))
        assert "fade=t=in" in f
        assert "overlay=x=" in f  # X位置动画
        print("✅ 左滑滤镜")

    def test_slide_right_filter(self):
        f = build_overlay_filter(1, "bg", "out",
                                 AnimationSpec(ANIM_SLIDE_RIGHT, 0.2, 0.4), (540, 960))
        assert "overlay=x=" in f
        print("✅ 右滑滤镜")


# ========== 测试4: 元素逐个出现(真实FFmpeg) ==========

class TestSequentialAppear:
    def test_elements_appear_in_order(self):
        """真实合成验证元素按顺序出现。"""
        composer = FFmpegComposer(size=(540, 960), fps=20)
        if not composer.available:
            print("⏭️  跳过: FFmpeg不可用")
            return

        _setup()
        Image.new("RGB", (540, 960), (40, 70, 110)).save(TEST_DIR / "sq_bg.png")
        scene = Scene(2, SceneType.CONTENT.value, "讲解内容文字", 3.5, ["关键词"])
        overlays = build_content_overlays(scene, 1, _renderer(), TEST_DIR)
        spec = SceneClipSpec(str(TEST_DIR / "sq_bg.png"), 3.5, overlays=overlays)
        out = str(TEST_DIR / "sq.mp4")
        assert composer._render_scene_clip(spec, out)

        def nonbg(t, region):
            p = str(TEST_DIR / f"sq_{t}.png")
            subprocess.run(["ffmpeg", "-y", "-ss", str(t), "-i", out,
                            "-frames:v", "1", p], capture_output=True)
            img = np.array(Image.open(p).convert("RGB"))
            r = img[region[0]:region[1], region[2]:region[3]].astype(int)
            bg = np.array([40, 70, 110])
            return int((np.abs(r - bg).sum(axis=2) > 80).sum())

        badge_region = (60, 170, 20, 250)
        sub_region = (780, 940, 0, 540)

        # 早期徽章区无内容，后期有；字幕最后才出现
        badge_early = nonbg(0.1, badge_region)
        badge_mid = nonbg(0.7, badge_region)
        sub_early = nonbg(0.7, sub_region)
        sub_late = nonbg(1.8, sub_region)

        assert badge_mid > badge_early, f"徽章应出现: {badge_early}->{badge_mid}"
        assert sub_late > sub_early + 3000, f"字幕应最后出现: {sub_early}->{sub_late}"
        print(f"✅ 元素逐个出现: 徽章{badge_early}->{badge_mid}, "
              f"字幕{sub_early}->{sub_late}")


# ========== 测试5: Agent集成 ==========

class TestAgentIntegration:
    def test_content_scene_multi_element(self):
        """内容场景使用多元素编排。"""
        _setup()
        from core.agents.video_compose_agent import VideoComposeAgent
        from core.models import SceneMaterialMap

        agent = VideoComposeAgent(size=(540, 960), enable_motion=True,
                                  enable_elements=True)
        agent._content_counter = 0
        r = _renderer()
        scene = Scene(2, SceneType.CONTENT.value, "讲解文字", 5.0, ["kw"])
        spec = agent._build_scene_spec(scene, 1, SceneMaterialMap(), r, TEST_DIR)
        assert spec is not None
        # 应有多个元素(徽章+标签+字幕)
        assert len(spec.overlays) == 3
        print(f"✅ Agent内容场景{len(spec.overlays)}元素编排")

    def test_elements_disabled(self):
        """关闭元素时只有字幕。"""
        _setup()
        from core.agents.video_compose_agent import VideoComposeAgent
        from core.models import SceneMaterialMap

        agent = VideoComposeAgent(size=(540, 960), enable_motion=True,
                                  enable_elements=False)
        agent._content_counter = 0
        r = _renderer()
        scene = Scene(2, SceneType.CONTENT.value, "文字", 5.0, ["kw"])
        spec = agent._build_scene_spec(scene, 1, SceneMaterialMap(), r, TEST_DIR)
        # with_badge=False → 仅字幕
        assert len(spec.overlays) == 1
        print("✅ 关闭元素仅字幕")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
