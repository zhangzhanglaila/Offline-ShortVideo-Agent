"""
Phase D4 丰富转场测试。

测试内容：
1. 转场选择逻辑(柔和/动感/轮换)
2. build_transitions序列生成
3. 多转场混用合成(真实FFmpeg)
4. Agent传递转场
5. 向后兼容(无transitions默认fade)
"""

from pathlib import Path

from PIL import Image

from core.compose.motion.transitions import (
    select_transition, build_transitions, _GENTLE, _DYNAMIC,
)
from core.compose.ffmpeg_composer import FFmpegComposer


TEST_DIR = Path("output/test_d4u")


def _setup():
    TEST_DIR.mkdir(parents=True, exist_ok=True)


# ========== 测试1: 转场选择 ==========

class TestSelect:
    def test_card_boundary_gentle(self):
        """涉及文字卡的边界用柔和转场。"""
        t = select_transition("title_card", "content", 0)
        assert t in _GENTLE
        t2 = select_transition("content", "conclusion", 0)
        assert t2 in _GENTLE
        print("✅ 卡片边界柔和")

    def test_content_boundary_dynamic(self):
        """内容间用动感转场。"""
        t = select_transition("content", "content", 0)
        assert t in _DYNAMIC
        print("✅ 内容间动感")

    def test_variety(self):
        """相邻内容边界转场不同(轮换)。"""
        t1 = select_transition("content", "content", 1)
        t2 = select_transition("content", "content", 2)
        assert t1 != t2
        print(f"✅ 转场轮换: {t1} != {t2}")


# ========== 测试2: 序列生成 ==========

class TestBuild:
    def test_build_length(self):
        """转场数 = 场景数 - 1。"""
        types = ["title_card", "content", "content", "conclusion"]
        trans = build_transitions(types)
        assert len(trans) == 3
        print(f"✅ 转场序列: {trans}")

    def test_build_single_scene(self):
        """单场景无转场。"""
        assert build_transitions(["title_card"]) == []
        print("✅ 单场景无转场")

    def test_build_content_structure(self):
        """典型结构: 首尾柔和,中间动感。"""
        types = ["title_card", "content", "content", "content", "conclusion"]
        trans = build_transitions(types)
        assert trans[0] in _GENTLE  # title→content
        assert trans[1] in _DYNAMIC  # content→content
        assert trans[-1] in _GENTLE  # content→conclusion
        print(f"✅ 结构化转场: {trans}")


# ========== 测试3: 多转场合成(真实FFmpeg) ==========

class TestMultiTransition:
    def test_varied_transitions_compose(self):
        """多种转场混用合成成功。"""
        composer = FFmpegComposer(size=(360, 640), fps=20)
        if not composer.available:
            print("⏭️  跳过: FFmpeg不可用")
            return

        _setup()
        for i, c in enumerate([(200, 60, 60), (60, 200, 60), (60, 60, 200)]):
            Image.new("RGB", (360, 640), c).save(TEST_DIR / f"c{i}.png")

        scenes = [(str(TEST_DIR / f"c{i}.png"), 1.5) for i in range(3)]
        out = str(TEST_DIR / "multi.mp4")
        ok = composer.compose(scenes, out, transition_duration=0.4,
                              transitions=["wipeleft", "circleopen"])
        assert ok
        # 时长 = 3*1.5 - 2*0.4 = 3.7
        dur = composer.probe_duration(out)
        if dur:
            assert 3.4 < dur < 4.0, f"时长异常: {dur}"
        print(f"✅ 多转场合成: {dur}")

    def test_fallback_no_transitions(self):
        """不传transitions默认fade。"""
        composer = FFmpegComposer(size=(360, 640), fps=20)
        if not composer.available:
            print("⏭️  跳过: FFmpeg不可用")
            return

        _setup()
        for i, c in enumerate([(200, 60, 60), (60, 60, 200)]):
            Image.new("RGB", (360, 640), c).save(TEST_DIR / f"f{i}.png")
        scenes = [(str(TEST_DIR / f"f{i}.png"), 1.5) for i in range(2)]
        out = str(TEST_DIR / "default.mp4")
        # 不传transitions
        ok = composer.compose(scenes, out, transition_duration=0.4)
        assert ok
        print("✅ 默认fade向后兼容")


# ========== 测试4: Agent集成 ==========

class TestAgentTransitions:
    def test_agent_passes_transitions(self):
        """Agent应根据场景类型传递转场。"""
        import asyncio
        from core.agents.video_compose_agent import VideoComposeAgent
        from core.models import (ContentStructure, Scene, SceneType,
                                  SceneMaterialMap, create_task_message)

        # 捕获传入的transitions
        captured = {}

        class CaptureComposer:
            available = True
            def compose(self, scenes, output_path, transition_duration=0.0,
                        audio_path=None, transitions=None, bgm_path=None, bgm_volume=0.3):
                captured["transitions"] = transitions
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                Path(output_path).write_bytes(b"x")
                return True

        async def _test():
            _setup()
            agent = VideoComposeAgent(size=(360, 640), composer=CaptureComposer(),
                                      output_dir=str(TEST_DIR))
            content = ContentStructure(
                title="t", category="教育讲解", style="minimal",
                total_duration=20,
                scenes=[
                    Scene(1, SceneType.TITLE_CARD.value, "标题", 3.0),
                    Scene(2, SceneType.CONTENT.value, "内容1", 6.0, ["a"]),
                    Scene(3, SceneType.CONTENT.value, "内容2", 6.0, ["b"]),
                    Scene(4, SceneType.CONCLUSION.value, "结尾", 3.0),
                ],
            )
            msg = create_task_message(
                sender="c", receiver="video_compose", task_type="compose_video",
                payload={"content": content.to_dict(),
                         "output_path": str(TEST_DIR / "agent.mp4")},
            )
            await agent.execute(msg)
            trans = captured["transitions"]
            assert trans is not None
            assert len(trans) == 3  # 4场景3边界
            print(f"✅ Agent传递转场: {trans}")

        asyncio.run(_test())


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
