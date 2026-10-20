"""
Phase 5 CLI测试。

测试内容：
1. 参数解析
2. 输入验证
3. CLI端到端（离线Agent）
"""

import sys
import asyncio
from pathlib import Path

import generate_video as cli


TEST_DIR = Path("output/test_phase5")


# ========== 测试1: 参数与常量 ==========

class TestCLIConstants:
    """测试CLI常量定义。"""

    def test_categories_valid(self):
        """分类与UserRequest验证一致。"""
        from core.models import UserRequest
        for cat in cli.CATEGORIES:
            req = UserRequest("测试内容", cat, "minimal", 30)
            assert req.validate(), f"分类{cat}应有效"
        print("✅ CLI分类与验证一致")

    def test_styles_valid(self):
        """风格与UserRequest验证一致。"""
        from core.models import UserRequest
        for st in cli.STYLES:
            req = UserRequest("测试内容", "教育讲解", st, 30)
            assert req.validate(), f"风格{st}应有效"
        print("✅ CLI风格与验证一致")

    def test_style_names_complete(self):
        """每个风格都有中文名。"""
        for st in cli.STYLES:
            assert st in cli.STYLE_NAMES
        print("✅ 风格中文名完整")


# ========== 测试2: 参数解析 ==========

class TestArgParsing:
    """测试命令行参数解析。"""

    def test_parse_full_args(self):
        """测试完整参数解析。"""
        sys.argv = [
            "generate_video.py",
            "--input", "测试需求",
            "--category", "短视频",
            "--style", "vibrant",
            "--duration", "20",
        ]
        args = cli.parse_args()
        assert args.input == "测试需求"
        assert args.category == "短视频"
        assert args.style == "vibrant"
        assert args.duration == 20
        print("✅ 完整参数解析正常")

    def test_parse_defaults(self):
        """测试默认值。"""
        sys.argv = ["generate_video.py", "-i", "测试"]
        args = cli.parse_args()
        assert args.category == "教育讲解"
        assert args.style == "minimal"
        assert args.duration == 30
        print("✅ 默认参数正常")


# ========== 测试3: CLI端到端（离线） ==========

class TestCLIEndToEnd:
    """测试CLI完整流程（离线Agent）。"""

    def test_run_offline(self):
        """测试run()离线执行。"""
        async def _test():
            TEST_DIR.mkdir(parents=True, exist_ok=True)
            from core.agents.coordinator_agent import CoordinatorAgent
            from core.agents.content_analysis_agent import ContentAnalysisAgent
            from core.agents.material_fetch_agent import MaterialFetchAgent
            from core.agents.video_compose_agent import VideoComposeAgent
            from core.agents.message_bus import MessageBus

            # Fake composer
            class FakeComposer:
                available = True
                def compose(self, scenes, output_path, transition_duration=0.0,
                            audio_path=None, transitions=None, bgm_path=None, bgm_volume=0.3):
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    Path(output_path).write_bytes(b"fake")
                    return True

            out = str(TEST_DIR / "cli_offline.mp4")
            coordinator = CoordinatorAgent(
                bus=MessageBus(),
                content_agent=ContentAnalysisAgent(llm_client=False),
                material_agent=MaterialFetchAgent(api_manager=False, video_module=False),
                compose_agent=VideoComposeAgent(
                    size=(540, 960), composer=FakeComposer(),
                    output_dir=str(TEST_DIR),
                ),
                output_path=out,
            )
            from core.models import UserRequest
            req = UserRequest("第一段。第二段。第三段。", "教育讲解", "minimal", 20)
            result = await coordinator.process_request(req)

            assert result.success is True
            assert Path(out).exists()
            print(f"✅ CLI离线端到端成功: {out}")

        asyncio.run(_test())

    def test_invalid_input_rejected(self):
        """测试无效输入被拒绝。"""
        async def _test():
            code = await cli.run({
                "user_input": "",  # 空输入
                "category": "教育讲解",
                "style": "minimal",
                "duration": 30,
            })
            assert code == 1
            print("✅ CLI无效输入正确拒绝")

        asyncio.run(_test())


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
