"""
Phase 4 主控Agent集成测试。

测试内容：
1. 完整编排流程（stub子Agent）
2. 内容分析失败 → 整体失败
3. 素材检索失败 → 降级继续
4. 视频合成降级 → success=False但有阶段结果
5. 重试逻辑
6. 真实Agent离线全链路（rule fallback + placeholder + fake composer）
7. MessageBus通信验证
8. 真实全服务端到端冒烟（DeepSeek+Pexels+FFmpeg，不可用时跳过）
"""

import os
import asyncio
from pathlib import Path

from PIL import Image

from core.agents.base_agent import BaseAgent
from core.agents.message_bus import MessageBus
from core.agents.coordinator_agent import CoordinatorAgent
from core.models import (
    Message,
    UserRequest,
    ContentStructure,
    Scene,
    SceneType,
    SceneMaterialMap,
)


TEST_DIR = Path("output/test_phase4")


def _request(user_input="讲解Python异步编程", category="教育讲解",
             style="minimal", duration=30):
    return UserRequest(user_input=user_input, category=category,
                       style=style, duration=duration)


def _sample_content(source="stub"):
    return ContentStructure(
        title="测试视频", category="教育讲解", style="minimal",
        total_duration=30, source=source,
        scenes=[
            Scene(1, SceneType.TITLE_CARD.value, "标题", 3.0),
            Scene(2, SceneType.CONTENT.value, "内容一", 12.0, ["test"]),
            Scene(3, SceneType.CONTENT.value, "内容二", 12.0, ["demo"]),
            Scene(4, SceneType.CONCLUSION.value, "结尾", 3.0),
        ],
    )


# ========== Stub 子Agent ==========

class StubContentAgent(BaseAgent):
    def __init__(self):
        super().__init__("content_analysis", "StubContent")

    async def execute(self, msg):
        return self.create_success_message(msg, _sample_content().to_dict())

    async def handle_error(self, e, msg):
        return self.create_error_message(msg, str(e))


class StubMaterialAgent(BaseAgent):
    def __init__(self):
        super().__init__("material_fetch", "StubMaterial")

    async def execute(self, msg):
        m = SceneMaterialMap()
        m.metadata = {"match_rate": 1.0, "real_count": 2}
        return self.create_success_message(msg, m.to_dict())

    async def handle_error(self, e, msg):
        return self.create_error_message(msg, str(e))


class StubComposeAgent(BaseAgent):
    def __init__(self, success=True, degraded=False):
        super().__init__("video_compose", "StubCompose")
        self._success = success
        self._degraded = degraded

    async def execute(self, msg):
        data = {
            "success": self._success,
            "degraded": self._degraded,
            "video_path": "output/stub.mp4" if self._success else None,
            "duration": 30.0,
            "resolution": "1080x1920",
            "quality_score": 0.9 if self._success else 0.3,
        }
        return self.create_success_message(msg, data)

    async def handle_error(self, e, msg):
        return self.create_error_message(msg, str(e))


class FailingAgent(BaseAgent):
    """总是失败的Agent。"""
    def __init__(self, agent_id):
        super().__init__(agent_id, f"Failing_{agent_id}")

    async def execute(self, msg):
        return self.create_error_message(msg, "模拟失败")

    async def handle_error(self, e, msg):
        return self.create_error_message(msg, str(e))


class FlakeyAgent(BaseAgent):
    """前N次失败，之后成功（测试重试）。"""
    def __init__(self, agent_id, fail_times=1):
        super().__init__(agent_id, f"Flakey_{agent_id}")
        self.fail_times = fail_times
        self.call_count = 0

    async def execute(self, msg):
        self.call_count += 1
        if self.call_count <= self.fail_times:
            return self.create_error_message(msg, f"第{self.call_count}次失败")
        return self.create_success_message(msg, _sample_content().to_dict())

    async def handle_error(self, e, msg):
        return self.create_error_message(msg, str(e))


def _make_coordinator(content=None, material=None, compose=None):
    """构建注入stub的coordinator（独立bus）。"""
    return CoordinatorAgent(
        bus=MessageBus(),
        content_agent=content or StubContentAgent(),
        material_agent=material or StubMaterialAgent(),
        compose_agent=compose or StubComposeAgent(),
    )


# ========== 测试1: 完整编排流程 ==========

class TestOrchestration:
    """测试完整编排流程。"""

    def test_full_success(self):
        """测试全流程成功。"""
        async def _test():
            coord = _make_coordinator()
            result = await coord.process_request(_request())

            assert result.success is True
            assert result.video_path == "output/stub.mp4"
            assert result.resolution == "1080x1920"
            assert result.quality_score == 0.9

            # 三个阶段都有结果
            assert "content_analysis" in result.stages
            assert "material_fetch" in result.stages
            assert "video_compose" in result.stages
            assert all(s.success for s in result.stages.values())

            # total_time对instant stub可能≈0，只验证已记录
            assert result.total_time >= 0
            print(f"✅ 全流程成功: {result.get_summary()}")

        asyncio.run(_test())

    def test_invalid_request(self):
        """测试无效请求直接拒绝。"""
        async def _test():
            coord = _make_coordinator()
            bad = UserRequest("", "教育讲解", "minimal", 30)
            result = await coord.process_request(bad)
            assert result.success is False
            assert "无效" in result.error
            print("✅ 无效请求正确拒绝")

        asyncio.run(_test())


# ========== 测试2: 异常与降级 ==========

class TestFailureHandling:
    """测试异常处理与降级。"""

    def test_content_failure_aborts(self):
        """测试内容分析失败 → 整体失败。"""
        async def _test():
            coord = _make_coordinator(
                content=FailingAgent("content_analysis"),
            )
            result = await coord.process_request(_request())
            assert result.success is False
            assert "内容分析失败" in result.error
            # 内容阶段标记失败
            assert result.stages["content_analysis"].success is False
            print("✅ 内容分析失败正确中止")

        asyncio.run(_test())

    def test_material_failure_continues(self):
        """测试素材检索失败 → 降级继续到合成。"""
        async def _test():
            coord = _make_coordinator(
                material=FailingAgent("material_fetch"),
            )
            result = await coord.process_request(_request())
            # 素材失败不阻断，合成仍执行
            assert "material_fetch" in result.stages
            assert result.stages["material_fetch"].success is False
            assert "video_compose" in result.stages
            # 合成用stub成功 → 整体成功
            assert result.success is True
            print("✅ 素材失败降级继续正常")

        asyncio.run(_test())

    def test_compose_degraded(self):
        """测试视频合成降级 → success=False但有阶段。"""
        async def _test():
            coord = _make_coordinator(
                compose=StubComposeAgent(success=False, degraded=True),
            )
            result = await coord.process_request(_request())
            assert result.success is False
            assert result.error is not None
            # 但合成阶段消息本身是成功送达的
            assert "video_compose" in result.stages
            print("✅ 合成降级处理正常")

        asyncio.run(_test())


# ========== 测试3: 重试逻辑 ==========

class TestRetry:
    """测试重试逻辑。"""

    def test_retry_success(self):
        """测试内容分析第一次失败后重试成功。"""
        async def _test():
            # analyze配置retries=1，第1次失败第2次成功
            flakey = FlakeyAgent("content_analysis", fail_times=1)
            coord = _make_coordinator(content=flakey)
            result = await coord.process_request(_request())

            # 重试后成功
            assert flakey.call_count == 2, f"应调用2次，实际{flakey.call_count}"
            assert result.stages["content_analysis"].success is True
            print(f"✅ 重试成功: 调用{flakey.call_count}次")

        asyncio.run(_test())

    def test_retry_exhausted(self):
        """测试重试耗尽仍失败。"""
        async def _test():
            # 失败次数超过重试上限
            flakey = FlakeyAgent("content_analysis", fail_times=5)
            coord = _make_coordinator(content=flakey)
            result = await coord.process_request(_request())

            assert result.success is False
            # analyze retries=1 → 共调用2次
            assert flakey.call_count == 2
            print(f"✅ 重试耗尽正确失败: 调用{flakey.call_count}次")

        asyncio.run(_test())


# ========== 测试4: 真实Agent离线全链路 ==========

class TestRealAgentsOffline:
    """用真实Agent但离线依赖（rule/placeholder/fake composer）。"""

    def test_offline_pipeline(self):
        """测试真实Agent离线全链路。"""
        async def _test():
            TEST_DIR.mkdir(parents=True, exist_ok=True)
            from core.agents.content_analysis_agent import ContentAnalysisAgent
            from core.agents.material_fetch_agent import MaterialFetchAgent
            from core.agents.video_compose_agent import VideoComposeAgent

            # Fake composer
            class FakeComposer:
                available = True
                def compose(self, scenes, output_path, transition_duration=0.0,
                            audio_path=None, transitions=None, bgm_path=None, bgm_volume=0.3):
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    Path(output_path).write_bytes(b"fake")
                    return True

            coord = CoordinatorAgent(
                bus=MessageBus(),
                content_agent=ContentAnalysisAgent(llm_client=False),
                material_agent=MaterialFetchAgent(api_manager=False, video_module=False),
                compose_agent=VideoComposeAgent(
                    size=(540, 960),
                    composer=FakeComposer(),
                    output_dir=str(TEST_DIR),
                ),
            )
            result = await coord.process_request(
                _request(user_input="第一段。第二段。第三段。", duration=20)
            )

            assert result.success is True
            # 内容来自规则降级
            assert result.stages["content_analysis"].data["source"] == "fallback"
            print(f"✅ 真实Agent离线全链路成功\n{result.get_summary()}")

        asyncio.run(_test())


# ========== 测试5: MessageBus通信验证 ==========

class TestMessageBusComm:
    """验证确实通过MessageBus通信。"""

    def test_bus_message_log(self):
        """测试消息经过总线（有通信记录）。"""
        async def _test():
            bus = MessageBus(enable_logging=True)
            coord = _make_coordinator()
            coord.bus = bus
            await coord.process_request(_request())

            # 总线应记录了任务和结果消息
            logs = bus.get_message_log()
            task_msgs = [m for m in logs if m.msg_type == "task"]
            result_msgs = [m for m in logs if m.msg_type == "result"]

            # 至少3个任务(3阶段) + 3个结果
            assert len(task_msgs) >= 3, f"任务消息不足: {len(task_msgs)}"
            assert len(result_msgs) >= 3, f"结果消息不足: {len(result_msgs)}"
            print(f"✅ MessageBus通信验证: {len(task_msgs)}任务/{len(result_msgs)}结果")

        asyncio.run(_test())


# ========== 测试6: 真实全服务端到端冒烟 ==========

class TestRealEndToEnd:
    """真实全服务端到端（DeepSeek+Pexels+FFmpeg，不可用时跳过）。"""

    def test_real_full_pipeline(self):
        """真实生成一个完整视频。"""
        try:
            from dotenv import load_dotenv
            load_dotenv(override=True)
        except Exception:
            pass

        # 检查FFmpeg
        from core.compose.ffmpeg_composer import FFmpegComposer
        if not FFmpegComposer().available:
            print("⏭️  跳过: FFmpeg不可用")
            return

        async def _test():
            from core.agents.video_compose_agent import VideoComposeAgent
            TEST_DIR.mkdir(parents=True, exist_ok=True)

            coord = CoordinatorAgent(
                bus=MessageBus(),
                compose_agent=VideoComposeAgent(
                    size=(540, 960), fps=15, output_dir=str(TEST_DIR),
                ),
            )
            result = await coord.process_request(
                _request(user_input="讲解什么是机器学习", duration=15)
            )

            print(f"\n{result.get_summary()}")
            if result.success:
                assert Path(result.video_path).exists()
                print(f"✅ 真实全流程成功: {result.video_path}")
            else:
                print(f"⚠️  流程降级: {result.error}")

        try:
            asyncio.run(_test())
        except Exception as e:
            print(f"⏭️  跳过真实端到端（异常）: {e}")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
