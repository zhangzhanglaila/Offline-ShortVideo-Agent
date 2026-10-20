"""
Phase D5 视频素材背景测试。

测试内容：
1. 视频素材检索(注入fake视频模块)
2. 无视频降级图片
3. 视频资产→视频背景SceneClipSpec
4. 视频背景合成(真实FFmpeg)
5. 真实网络视频检索冒烟
"""

import os
import asyncio
import subprocess
from pathlib import Path

from PIL import Image

from core.models import (
    Scene, SceneType, ContentStructure, MaterialAsset, SceneMaterialMap,
    create_task_message,
)
from core.agents.material_fetch_agent import MaterialFetchAgent
from core.compose.motion.clip_spec import SceneClipSpec
from core.compose.ffmpeg_composer import FFmpegComposer


TEST_DIR = Path("output/test_d5u")


def _setup():
    TEST_DIR.mkdir(parents=True, exist_ok=True)


# ========== Fake 视频模块 ==========

class FakeVideoMaterial:
    def __init__(self, url, duration=5.0, provider="pexels", w=1080, h=1920):
        self.url = url
        self.duration = duration
        self.provider = provider
        self.width = w
        self.height = h
        self.keywords = ""


class FakeVideoModule:
    """模拟StockVideoModule。"""
    def __init__(self, has_results=True, download_ok=True, video_path=None):
        self.has_results = has_results
        self.download_ok = download_ok
        self.video_path = video_path
        self.searches = []

    def search_pexels(self, query, min_duration=3):
        self.searches.append(query)
        if self.has_results:
            return [FakeVideoMaterial(f"http://x/{query}.mp4")]
        return []

    def search_pixabay(self, query, min_duration=3):
        return []

    def download_video(self, url):
        return self.video_path if self.download_ok else ""


def _content_msg(scenes=None):
    if scenes is None:
        scenes = [
            Scene(1, SceneType.TITLE_CARD.value, "标题", 3.0),
            Scene(2, SceneType.CONTENT.value, "内容", 8.0, ["nature"]),
            Scene(3, SceneType.CONCLUSION.value, "结尾", 3.0),
        ]
    content = ContentStructure("测试", "教育讲解", "minimal", 14, scenes)
    return create_task_message(
        sender="c", receiver="material_fetch", task_type="fetch_material",
        payload=content.to_dict(),
    )


# ========== 测试1: 视频素材检索 ==========

class TestVideoFetch:
    def test_video_preferred(self):
        """有视频时优先返回视频素材。"""
        async def _test():
            _setup()
            # 造一个真实测试视频文件
            vid = str(TEST_DIR / "fake.mp4")
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                            "testsrc=size=320x240:rate=10:duration=2", vid],
                           capture_output=True)

            fake_vm = FakeVideoModule(has_results=True, download_ok=True,
                                      video_path=vid)
            agent = MaterialFetchAgent(api_manager=False, video_module=fake_vm,
                                       prefer_video=True)
            result = await agent.execute(_content_msg())
            m = SceneMaterialMap.from_dict(result.result)

            # 内容场景应得到视频素材
            assets = m.get(2)
            assert len(assets) == 1
            assert assets[0].media_type == "video"
            assert assets[0].local_path == vid
            print("✅ 视频素材优先")

        asyncio.run(_test())

    def test_no_video_fallback_placeholder(self):
        """无视频且无图API时降级占位符。"""
        async def _test():
            fake_vm = FakeVideoModule(has_results=False)
            agent = MaterialFetchAgent(api_manager=False, video_module=fake_vm,
                                       prefer_video=True)
            result = await agent.execute(_content_msg())
            m = SceneMaterialMap.from_dict(result.result)
            assets = m.get(2)
            assert assets[0].is_placeholder
            print("✅ 无视频降级占位符")

        asyncio.run(_test())

    def test_video_download_fail_fallback(self):
        """视频下载失败时降级(无图API→占位符)。"""
        async def _test():
            fake_vm = FakeVideoModule(has_results=True, download_ok=False)
            agent = MaterialFetchAgent(api_manager=False, video_module=fake_vm,
                                       prefer_video=True)
            result = await agent.execute(_content_msg())
            m = SceneMaterialMap.from_dict(result.result)
            assert m.get(2)[0].is_placeholder
            print("✅ 视频下载失败降级")

        asyncio.run(_test())

    def test_prefer_video_disabled(self):
        """关闭视频偏好时不搜视频。"""
        async def _test():
            fake_vm = FakeVideoModule(has_results=True, download_ok=True,
                                      video_path="x.mp4")
            agent = MaterialFetchAgent(api_manager=False, video_module=fake_vm,
                                       prefer_video=False)
            result = await agent.execute(_content_msg())
            # 未搜索视频
            assert len(fake_vm.searches) == 0
            print("✅ 关闭视频偏好")

        asyncio.run(_test())

    def test_video_disabled_module(self):
        """video_module=False禁用视频。"""
        async def _test():
            agent = MaterialFetchAgent(api_manager=False, video_module=False,
                                       prefer_video=True)
            result = await agent.execute(_content_msg())
            m = SceneMaterialMap.from_dict(result.result)
            # 降级占位符(无图API)
            assert m.get(2)[0].is_placeholder
            print("✅ 视频模块禁用")

        asyncio.run(_test())


# ========== 测试2: 视频背景合成 ==========

class TestVideoBackground:
    def test_compose_video_bg(self):
        """视频背景合成(loop+cover-fit)。"""
        composer = FFmpegComposer(size=(360, 640), fps=15)
        if not composer.available:
            print("⏭️  跳过: FFmpeg不可用")
            return

        _setup()
        vid = str(TEST_DIR / "bg.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=480x360:rate=15:duration=2", vid],
                       capture_output=True)

        spec = SceneClipSpec(vid, 4.0, background_is_video=True)  # 4s>2s需循环
        out = str(TEST_DIR / "vidbg.mp4")
        assert composer._render_scene_clip(spec, out)
        # 时长应为4s(循环填充)
        dur = composer.probe_duration(out)
        if dur:
            assert 3.8 < dur < 4.2, f"时长异常: {dur}"
        print(f"✅ 视频背景合成(循环): {dur}")

    def test_video_bg_no_kenburns(self):
        """视频背景不应有Ken Burns。"""
        _setup()
        from core.agents.video_compose_agent import VideoComposeAgent
        vid = str(TEST_DIR / "asset.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=320x240:rate=10:duration=2", vid],
                       capture_output=True)

        agent = VideoComposeAgent(size=(360, 640), enable_motion=True)
        agent._content_counter = 0
        from styles import get_style
        from core.compose.scene_image_renderer import SceneImageRenderer
        renderer = SceneImageRenderer(style=get_style("minimal"), size=(360, 640))

        m = SceneMaterialMap()
        m.add(2, MaterialAsset("v2", 2, "pexels", media_type="video",
                               local_path=vid, quality_score=0.9))
        scene = Scene(2, SceneType.CONTENT.value, "内容", 5.0, ["kw"])
        spec = agent._build_scene_spec(scene, 1, m, renderer, TEST_DIR)

        assert spec.background_is_video is True
        assert spec.ken_burns is None  # 视频背景无运镜
        assert spec.background_path == vid
        print("✅ 视频背景无Ken Burns")


# ========== 测试3: 真实网络冒烟 ==========

class TestRealVideoNetwork:
    def test_real_video_search(self):
        """真实Pexels视频检索(需PEXELS_API_KEY)。"""
        try:
            from dotenv import load_dotenv
            load_dotenv(override=True)
        except Exception:
            pass
        if not os.environ.get("PEXELS_API_KEY"):
            print("⏭️  跳过: 无PEXELS_API_KEY")
            return

        try:
            from core.stock_video_module import StockVideoModule
            vm = StockVideoModule(orientation="portrait")
            results = vm.search_pexels("nature", min_duration=3)
            if results:
                print(f"✅ 真实视频检索: {len(results)}个结果")
            else:
                print("⚠️  未找到视频(网络受限?)")
        except Exception as e:
            print(f"⏭️  跳过真实视频检索: {e}")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
