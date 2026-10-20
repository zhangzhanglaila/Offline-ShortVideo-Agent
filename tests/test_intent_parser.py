"""Tests for Intent Parser — natural language instruction understanding."""

import pytest
from compiler.thinking.intent_parser import IntentParser, EditIntent
from compiler.thinking.state import VideoProjectState, ModuleState, ScriptSentence


def make_state():
    s = VideoProjectState(topic="Redis为什么快")
    mod = ModuleState(title="Introduction", index=0)
    mod.script.append(ScriptSentence(text="Redis是一个高性能的内存数据库", index=0))
    mod.script.append(ScriptSentence(text="它使用单线程模型", index=1))
    s.modules.append(mod)
    s.current_module_index = 0
    return s


@pytest.fixture
def parser():
    return IntentParser(llm_client=None)


@pytest.fixture
def state():
    return make_state()


class TestEditIntent:
    def test_default_values(self):
        intent = EditIntent()
        assert intent.action == ""
        assert intent.confidence == 0.0

    def test_with_values(self):
        intent = EditIntent(action="rewrite", target="sentence:1", confidence=0.9)
        assert intent.action == "rewrite"


class TestIntentParser:
    def test_parse_rewrite(self, parser, state):
        intents = parser.parse("修改第1句为Redis非常快", state)
        assert len(intents) >= 1
        assert intents[0].action == "rewrite"
        assert intents[0].target == "sentence:1"

    def test_parse_delete(self, parser, state):
        intents = parser.parse("删除第2句", state)
        assert intents[0].action == "remove"
        assert intents[0].target == "sentence:2"

    def test_parse_add(self, parser, state):
        intents = parser.parse("添加：Redis支持多种数据结构", state)
        assert intents[0].action == "add"

    def test_parse_regenerate(self, parser, state):
        intents = parser.parse("重新生成", state)
        assert intents[0].action == "regenerate"

    def test_parse_approve(self, parser, state):
        for kw in ["确认", "OK", "继续"]:
            intents = parser.parse(kw, state)
            assert intents[0].action == "approve"

    def test_parse_style_engaging(self, parser, state):
        intents = parser.parse("前面太枯燥了", state)
        assert intents[0].action == "style"
        assert intents[0].params.get("style") == "engaging"

    def test_parse_shorten(self, parser, state):
        intents = parser.parse("太长了，精简一下", state)
        assert intents[0].action == "shorten"

    def test_parse_extend(self, parser, state):
        intents = parser.parse("请详细展开一下", state)
        assert intents[0].action == "extend"

    def test_parse_pacing(self, parser, state):
        intents = parser.parse("节奏快一点", state)
        assert intents[0].action == "adjust_pacing"

    def test_parse_example(self, parser, state):
        intents = parser.parse("举个例子", state)
        assert intents[0].action == "add"
        assert intents[0].params.get("type") == "example"

    def test_parse_unknown_falls_back_to_feedback(self, parser, state):
        intents = parser.parse("xyzzy", state)
        assert intents[0].action == "feedback"

    def test_confidence_range(self, parser, state):
        for instruction in ["修改第1句为test", "前面太枯燥", "xyzzy"]:
            intents = parser.parse(instruction, state)
            for intent in intents:
                assert 0 <= intent.confidence <= 1


class TestLLMParser:
    def test_uses_llm_when_available(self):
        class MockLLM:
            def generate(self, prompt):
                return '{"intents": [{"action": "rewrite", "target": "sentence:1", "params": {"text": "new"}, "confidence": 0.95, "reasoning": "test"}]}'

        parser = IntentParser(llm_client=MockLLM())
        state = make_state()
        intents = parser.parse("change sentence 1", state)
        assert len(intents) == 1
        assert intents[0].action == "rewrite"
        assert intents[0].confidence == 0.95

    def test_falls_back_on_llm_error(self):
        class BadLLM:
            def generate(self, prompt):
                raise RuntimeError("API error")

        parser = IntentParser(llm_client=BadLLM())
        state = make_state()
        intents = parser.parse("删除第1句", state)
        assert intents[0].action == "remove"
