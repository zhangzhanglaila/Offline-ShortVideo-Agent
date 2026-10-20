"""
Phase D6 背景音乐测试。

测试内容：
1. BGM选择器
2. BGM混音(真实FFmpeg)
3. Agent的BGM选择/开关
4. 无BGM降级无声
"""

import subprocess
from pathlib import Path

from core.compose.motion.bgm import find_bgm, list_bgm
from core.compose.ffmpeg_composer import FFmpegComposer


TEST_DIR = Path("output/test_d6u")


def _setup():
    TEST_DIR.mkdir(parents=True, exist_ok=True)


def _has_audio(path):
    """检查视频是否有音频流。"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="ignore",
    )
    return "audio" in (r.stdout or "")


# ========== 测试1: BGM选择器 ==========

class TestBgmSelector:
    def test_list_bgm(self):
        """列出BGM(项目有1个)。"""
        bgms = list_bgm()
        assert isinstance(bgms, list)
        # 项目assets/bgm有文件
        assert len(bgms) >= 1
        print(f"✅ BGM列表: {len(bgms)}个")

    def test_find_bgm(self):
        """找到一个BGM。"""
        bgm = find_bgm("教育讲解")
        assert bgm is not None
        assert Path(bgm).exists()
        print(f"✅ 选到BGM: {Path(bgm).name}")


# ========== 测试2: BGM混音 ==========

class TestBgmMix:
    def test_add_bgm_has_audio(self):
        """混入BGM后视频有音频流。"""
        composer = FFmpegComposer(size=(360, 640), fps=15)
        if not composer.available:
            print("⏭️  跳过: FFmpeg不可用")
            return

        _setup()
        silent = str(TEST_DIR / "silent.mp4")
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=360x640:rate=15:duration=3", silent],
                       capture_output=True)
        assert not _has_audio(silent)

        bgm = find_bgm()
        if not bgm:
            print("⏭️  跳过: 无BGM")
            return

        out = str(TEST_DIR / "withbgm.mp4")
        ok = composer._add_bgm(silent, bgm, out, 0.3)
        assert ok
        assert _has_audio(out)
        # 时长应为视频3s(非BGM时长)
        dur = composer.probe_duration(out)
        assert 2.8 < dur < 3.3, f"时长异常: {dur}"
        print(f"✅ BGM混音有音频, 时长{dur:.1f}s")

    def test_compose_with_bgm(self):
        """compose传bgm_path产出有声视频。"""
        composer = FFmpegComposer(size=(360, 640), fps=15)
        if not composer.available:
            print("⏭️  跳过: FFmpeg不可用")
            return

        _setup()
        from PIL import Image
        for i, c in enumerate([(200, 60, 60), (60, 60, 200)]):
            Image.new("RGB", (360, 640), c).save(TEST_DIR / f"s{i}.png")
        scenes = [(str(TEST_DIR / f"s{i}.png"), 1.5) for i in range(2)]
        bgm = find_bgm()
        out = str(TEST_DIR / "composed_bgm.mp4")
        ok = composer.compose(scenes, out, transition_duration=0.3, bgm_path=bgm)
        assert ok
        assert _has_audio(out)
        print("✅ compose带BGM产出有声视频")

    def test_no_bgm_silent(self):
        """不传BGM则无音频。"""
        composer = FFmpegComposer(size=(360, 640), fps=15)
        if not composer.available:
            print("⏭️  跳过: FFmpeg不可用")
            return

        _setup()
        from PIL import Image
        Image.new("RGB", (360, 640), (100, 100, 100)).save(TEST_DIR / "n0.png")
        Image.new("RGB", (360, 640), (50, 50, 50)).save(TEST_DIR / "n1.png")
        scenes = [(str(TEST_DIR / f"n{i}.png"), 1.2) for i in range(2)]
        out = str(TEST_DIR / "silent_out.mp4")
        ok = composer.compose(scenes, out, transition_duration=0.3)
        assert ok
        assert not _has_audio(out)
        print("✅ 无BGM则无声")


# ========== 测试3: Agent集成 ==========

class TestAgentBgm:
    def test_select_bgm_enabled(self):
        """启用BGM时选到音乐。"""
        from core.agents.video_compose_agent import VideoComposeAgent
        agent = VideoComposeAgent(size=(360, 640), enable_bgm=True)
        bgm = agent._select_bgm("教育讲解")
        assert bgm is not None
        print(f"✅ Agent选到BGM: {Path(bgm).name}")

    def test_select_bgm_disabled(self):
        """关闭BGM时返回None。"""
        from core.agents.video_compose_agent import VideoComposeAgent
        agent = VideoComposeAgent(size=(360, 640), enable_bgm=False)
        assert agent._select_bgm("教育讲解") is None
        print("✅ 关闭BGM返回None")

    def test_custom_bgm_path(self):
        """指定bgm_path优先。"""
        from core.agents.video_compose_agent import VideoComposeAgent
        bgm = find_bgm()
        agent = VideoComposeAgent(size=(360, 640), enable_bgm=True, bgm_path=bgm)
        assert agent._select_bgm("教育讲解") == bgm
        print("✅ 自定义BGM路径优先")

    def test_agent_passes_bgm(self):
        """Agent合成时传递BGM给composer。"""
        import asyncio
        from core.agents.video_compose_agent import VideoComposeAgent
        from core.models import (ContentStructure, Scene, SceneType,
                                  create_task_message)

        captured = {}

        class CaptureComposer:
            available = True
            def compose(self, scenes, output_path, transition_duration=0.0,
                        audio_path=None, transitions=None, bgm_path=None,
                        bgm_volume=0.3):
                captured["bgm"] = bgm_path
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_bytes(b"x")
                return True

        async def _test():
            _setup()
            agent = VideoComposeAgent(size=(360, 640), composer=CaptureComposer(),
                                      enable_bgm=True, output_dir=str(TEST_DIR))
            content = ContentStructure(
                title="t", category="教育讲解", style="minimal", total_duration=10,
                scenes=[Scene(1, SceneType.TITLE_CARD.value, "标题", 3.0),
                        Scene(2, SceneType.CONTENT.value, "内容", 4.0, ["a"]),
                        Scene(3, SceneType.CONCLUSION.value, "结尾", 3.0)],
            )
            msg = create_task_message(
                sender="c", receiver="video_compose", task_type="compose_video",
                payload={"content": content.to_dict(),
                         "output_path": str(TEST_DIR / "a.mp4")},
            )
            await agent.execute(msg)
            assert captured["bgm"] is not None
            print(f"✅ Agent传递BGM: {Path(captured['bgm']).name}")

        asyncio.run(_test())


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
