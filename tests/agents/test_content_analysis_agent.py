"""
Phase 1 内容分析Agent测试。

测试内容：
1. 规则降级路径（无LLM）- 保证核心可用性
2. LLM主路径（mock客户端）- 验证JSON解析和结构构建
3. 内容结构数据模型
4. 三种分类的内容生成
5. 异常处理
"""

import json
import asyncio

from core.models import (
    Message,
    UserRequest,
    ContentStructure,
    Scene,
    SceneType,
    create_task_message,
)
from core.agents.content_analysis_agent import ContentAnalysisAgent


# ========== Mock LLM客户端 ==========

class MockLLMClient:
    """模拟LLM客户端，用于测试主路径。"""

    def __init__(self, response: str, available: bool = True):
        self._response = response
        self._available = available
        # 模拟local/cloud子客户端
        self.local = self._Sub(available)
        self.cloud = self._Sub(False)

    class _Sub:
        def __init__(self, available):
            self._available = available

        def check_available(self):
            return self._available

    def chat(self, messages, temperature=0.7, **kwargs):
        return self._response

    def extract_json_from_response(self, text):
        match = _find_json(text)
        return match


def _find_json(text):
    import re
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            return None
    return None


def _make_message(user_input, category, style, duration):
    """构建测试用的任务消息。"""
    return create_task_message(
        sender="coordinator",
        receiver="content_analysis",
        task_type="analyze",
        payload={
            "user_input": user_input,
            "category": category,
            "style": style,
            "duration": duration,
        },
    )


# ========== 测试1: 内容结构数据模型 ==========

class TestContentStructure:
    """测试内容结构数据模型。"""

    def test_scene_creation(self):
        """测试场景创建和序列化。"""
        scene = Scene(
            scene_id=1,
            scene_type=SceneType.CONTENT.value,
            text="讲解异步编程",
            duration=8.0,
            keywords=["async", "await"],
        )
        assert scene.scene_id == 1
        assert not scene.is_text_only()

        d = scene.to_dict()
        scene2 = Scene.from_dict(d)
        assert scene2.text == scene.text
        assert scene2.keywords == scene.keywords
        print("✅ 场景数据模型正常")

    def test_title_card_is_text_only(self):
        """测试标题卡识别为纯文字场景。"""
        scene = Scene(
            scene_id=1,
            scene_type=SceneType.TITLE_CARD.value,
            text="标题",
            duration=3.0,
        )
        assert scene.is_text_only()
        print("✅ 纯文字场景识别正常")

    def test_content_validation(self):
        """测试内容结构验证。"""
        content = ContentStructure(
            title="测试视频",
            category="教育讲解",
            style="minimal",
            total_duration=30,
            scenes=[
                Scene(1, SceneType.TITLE_CARD.value, "标题", 3.0),
                Scene(2, SceneType.CONTENT.value, "内容", 24.0, ["kw"]),
                Scene(3, SceneType.CONCLUSION.value, "结尾", 3.0),
            ],
        )
        is_valid, err = content.validate()
        assert is_valid, f"应有效但报错: {err}"
        assert content.scene_count == 3
        assert len(content.get_content_scenes()) == 1
        print("✅ 内容结构验证正常")

    def test_invalid_content(self):
        """测试无效内容检测。"""
        # 空标题
        content = ContentStructure(
            title="",
            category="教育讲解",
            style="minimal",
            total_duration=30,
            scenes=[Scene(1, SceneType.CONTENT.value, "内容", 30.0)],
        )
        is_valid, err = content.validate()
        assert not is_valid
        assert "标题" in err

        # 空场景
        content2 = ContentStructure(
            title="标题",
            category="教育讲解",
            style="minimal",
            total_duration=30,
            scenes=[],
        )
        is_valid, err = content2.validate()
        assert not is_valid
        print("✅ 无效内容正确识别")

    def test_content_roundtrip(self):
        """测试内容结构的序列化往返。"""
        content = ContentStructure(
            title="标题",
            category="短视频",
            style="vibrant",
            total_duration=20,
            scenes=[
                Scene(1, SceneType.TITLE_CARD.value, "标题", 3.0),
                Scene(2, SceneType.CONTENT.value, "内容", 14.0, ["a", "b"]),
                Scene(3, SceneType.CONCLUSION.value, "结尾", 3.0),
            ],
        )
        d = content.to_dict()
        content2 = ContentStructure.from_dict(d)
        assert content2.title == content.title
        assert content2.scene_count == content.scene_count
        assert content2.scenes[1].keywords == ["a", "b"]
        print("✅ 内容结构序列化往返正常")


# ========== 测试2: 规则降级路径 ==========

class TestRuleFallback:
    """测试规则降级路径（无LLM时的核心保障）。"""

    def test_fallback_basic(self):
        """测试基础降级生成。"""
        async def _test():
            # llm_client=False 强制禁用LLM
            agent = ContentAnalysisAgent(llm_client=False)
            msg = _make_message(
                user_input="Python异步编程的核心是async和await。它可以处理并发任务。异步IO能提升性能。",
                category="教育讲解",
                style="minimal",
                duration=30,
            )
            result = await agent.execute(msg)

            assert result.status == "success", f"应成功: {result.error}"
            content = ContentStructure.from_dict(result.result)

            assert content.source == "fallback"
            assert content.scene_count >= 3

            # 验证首尾场景类型
            assert content.scenes[0].scene_type == SceneType.TITLE_CARD.value
            assert content.scenes[-1].scene_type == SceneType.CONCLUSION.value

            # 验证结构有效
            is_valid, err = content.validate()
            assert is_valid, f"降级内容应有效: {err}"

            print(f"✅ 规则降级路径正常\n{content.get_summary()}")

        asyncio.run(_test())

    def test_fallback_keywords(self):
        """测试降级路径的关键词提取。"""
        async def _test():
            agent = ContentAnalysisAgent(llm_client=False)
            msg = _make_message(
                user_input="Python异步编程async和await。并发任务处理。性能优化技巧。",
                category="教育讲解",
                style="tech",
                duration=30,
            )
            result = await agent.execute(msg)
            content = ContentStructure.from_dict(result.result)

            # 内容场景应有关键词
            content_scenes = content.get_content_scenes()
            assert len(content_scenes) >= 1
            has_keywords = any(len(s.keywords) > 0 for s in content_scenes)
            assert has_keywords, "内容场景应有关键词"
            print(f"✅ 关键词提取正常: {[s.keywords for s in content_scenes]}")

        asyncio.run(_test())

    def test_fallback_duration_distribution(self):
        """测试时长分配合理性。"""
        async def _test():
            agent = ContentAnalysisAgent(llm_client=False)
            msg = _make_message(
                user_input="第一点内容。第二点内容。第三点内容。第四点内容。",
                category="教育讲解",
                style="minimal",
                duration=30,
            )
            result = await agent.execute(msg)
            content = ContentStructure.from_dict(result.result)

            # 总时长应接近目标（允许偏差）
            computed = content.computed_duration
            deviation = abs(computed - 30) / 30
            assert deviation < 0.5, f"时长偏差过大: {computed}s vs 30s"
            print(f"✅ 时长分配合理: {computed:.1f}s (目标30s)")

        asyncio.run(_test())

    def test_fallback_short_input(self):
        """测试极短输入的降级处理。"""
        async def _test():
            agent = ContentAnalysisAgent(llm_client=False)
            msg = _make_message(
                user_input="介绍Python",
                category="教育讲解",
                style="minimal",
                duration=15,
            )
            result = await agent.execute(msg)
            assert result.status == "success"
            content = ContentStructure.from_dict(result.result)
            is_valid, err = content.validate()
            assert is_valid, f"短输入也应生成有效内容: {err}"
            print("✅ 极短输入处理正常")

        asyncio.run(_test())


# ========== 测试3: LLM主路径（mock） ==========

class TestLLMPath:
    """测试LLM主路径（使用mock客户端）。"""

    def test_llm_success(self):
        """测试LLM成功返回并解析。"""
        async def _test():
            llm_response = json.dumps({
                "title": "Python异步编程指南",
                "scenes": [
                    {"scene_id": 1, "scene_type": "title_card",
                     "text": "Python异步编程", "duration": 3, "keywords": []},
                    {"scene_id": 2, "scene_type": "content",
                     "text": "async/await是什么", "duration": 12,
                     "keywords": ["async", "await"]},
                    {"scene_id": 3, "scene_type": "content",
                     "text": "如何处理并发", "duration": 12,
                     "keywords": ["并发", "concurrency"]},
                    {"scene_id": 4, "scene_type": "conclusion",
                     "text": "感谢观看", "duration": 3, "keywords": []},
                ],
            }, ensure_ascii=False)

            mock = MockLLMClient(llm_response, available=True)
            agent = ContentAnalysisAgent(llm_client=mock)
            msg = _make_message(
                user_input="讲解Python异步编程",
                category="教育讲解",
                style="minimal",
                duration=30,
            )
            result = await agent.execute(msg)

            assert result.status == "success"
            content = ContentStructure.from_dict(result.result)
            assert content.source == "llm"
            assert content.title == "Python异步编程指南"
            assert content.scene_count == 4
            assert content.scenes[1].keywords == ["async", "await"]
            print(f"✅ LLM主路径正常\n{content.get_summary()}")

        asyncio.run(_test())

    def test_llm_response_with_markdown(self):
        """测试LLM返回带markdown代码块的响应。"""
        async def _test():
            inner = json.dumps({
                "title": "测试标题",
                "scenes": [
                    {"scene_id": 1, "scene_type": "title_card",
                     "text": "标题", "duration": 3, "keywords": []},
                    {"scene_id": 2, "scene_type": "content",
                     "text": "内容", "duration": 24, "keywords": ["test"]},
                    {"scene_id": 3, "scene_type": "conclusion",
                     "text": "结尾", "duration": 3, "keywords": []},
                ],
            }, ensure_ascii=False)
            # 模拟LLM有时会包裹markdown
            llm_response = f"```json\n{inner}\n```"

            mock = MockLLMClient(llm_response, available=True)
            agent = ContentAnalysisAgent(llm_client=mock)
            msg = _make_message("测试", "教育讲解", "minimal", 30)
            result = await agent.execute(msg)

            assert result.status == "success"
            content = ContentStructure.from_dict(result.result)
            assert content.title == "测试标题"
            print("✅ Markdown包裹的JSON解析正常")

        asyncio.run(_test())

    def test_llm_invalid_json_fallback(self):
        """测试LLM返回无效JSON时降级到规则路径。"""
        async def _test():
            mock = MockLLMClient("抱歉我无法理解这个请求", available=True)
            agent = ContentAnalysisAgent(llm_client=mock)
            msg = _make_message(
                user_input="讲解内容。第二句话。第三句话。",
                category="教育讲解",
                style="minimal",
                duration=30,
            )
            result = await agent.execute(msg)

            # 应降级成功而非失败
            assert result.status == "success"
            content = ContentStructure.from_dict(result.result)
            assert content.source == "fallback"
            print("✅ LLM无效响应降级正常")

        asyncio.run(_test())

    def test_llm_unavailable_fallback(self):
        """测试LLM不可用时降级。"""
        async def _test():
            # available=False 模拟服务未就绪
            mock = MockLLMClient("{}", available=False)
            agent = ContentAnalysisAgent(llm_client=mock)
            msg = _make_message(
                user_input="讲解内容。第二句话。",
                category="短视频",
                style="vibrant",
                duration=20,
            )
            result = await agent.execute(msg)

            assert result.status == "success"
            content = ContentStructure.from_dict(result.result)
            assert content.source == "fallback"
            print("✅ LLM不可用降级正常")

        asyncio.run(_test())


# ========== 测试4: 多分类支持 ==========

class TestCategories:
    """测试不同分类的内容生成。"""

    def test_all_categories(self):
        """测试四种分类都能生成有效内容。"""
        async def _test():
            agent = ContentAnalysisAgent(llm_client=False)
            categories = ["教育讲解", "短视频", "纪录片", "商业宣传"]

            for category in categories:
                msg = _make_message(
                    user_input="这是第一段内容。这是第二段内容。这是第三段内容。",
                    category=category,
                    style="minimal",
                    duration=30,
                )
                result = await agent.execute(msg)
                assert result.status == "success", f"{category}应成功"
                content = ContentStructure.from_dict(result.result)
                is_valid, err = content.validate()
                assert is_valid, f"{category}内容应有效: {err}"
                # 结尾文字应按分类定制
                conclusion = content.scenes[-1].text
                assert conclusion, f"{category}应有结尾文字"

            print(f"✅ 四种分类均正常: {categories}")

        asyncio.run(_test())


# ========== 测试5: 异常处理 ==========

class TestErrorHandling:
    """测试异常处理。"""

    def test_invalid_request(self):
        """测试无效请求处理。"""
        async def _test():
            agent = ContentAnalysisAgent(llm_client=False)
            # 无效分类
            msg = _make_message(
                user_input="内容",
                category="不存在的分类",
                style="minimal",
                duration=30,
            )
            result = await agent.execute(msg)
            assert result.status == "failed"
            assert result.error is not None
            print("✅ 无效请求正确拒绝")

        asyncio.run(_test())

    def test_empty_input(self):
        """测试空输入处理。"""
        async def _test():
            agent = ContentAnalysisAgent(llm_client=False)
            msg = _make_message(
                user_input="",
                category="教育讲解",
                style="minimal",
                duration=30,
            )
            result = await agent.execute(msg)
            assert result.status == "failed"
            print("✅ 空输入正确拒绝")

        asyncio.run(_test())


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
