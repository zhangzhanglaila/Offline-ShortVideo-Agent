"""Intent Parser — LLM-based natural language instruction understanding.

Replaces regex-based pattern matching with actual LLM comprehension.
Handles both precise instructions ("修改第3句为XXX") and vague ones
("前面太枯燥", "节奏快一点", "加个例子").

The parser outputs structured EditIntents that the agent converts to Patches.

Usage:
    parser = IntentParser(llm_client)
    intents = parser.parse("前面太枯燥", state)
    # → [EditIntent(type="rewrite", target="first_sentences", params={"style": "engaging"})]
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from compiler.thinking.state import VideoProjectState


@dataclass
class EditIntent:
    """A parsed user intent — the intermediate representation between
    natural language and concrete patches."""
    action: str = ""        # rewrite, shorten, extend, add, remove, reorder, regenerate, approve, style
    target: str = ""        # "sentence:3", "module:mod_00", "first_sentences", "all", etc.
    params: dict = field(default_factory=dict)  # action-specific parameters
    confidence: float = 0.0  # parser confidence 0-1
    reasoning: str = ""     # why the parser chose this intent


class IntentParser:
    """Parse natural language instructions into EditIntents using LLM.

    Falls back to regex patterns when LLM is unavailable.
    """

    def __init__(self, llm_client=None):
        self.llm = llm_client

    def parse(self, instruction: str, state: VideoProjectState) -> list[EditIntent]:
        """Parse a user instruction into one or more EditIntents.

        Args:
            instruction: The user's natural language instruction
            state: Current video project state (for context)

        Returns:
            List of EditIntents to execute
        """
        if self.llm:
            try:
                return self._llm_parse(instruction, state)
            except Exception:
                pass

        # Fallback to regex patterns
        return self._regex_parse(instruction, state)

    def _llm_parse(self, instruction: str, state: VideoProjectState) -> list[EditIntent]:
        """Use LLM to understand the user's intent."""
        # Build context about current state
        current = state.get_current_module()
        module_info = ""
        if current:
            sentences = [f"  [{i+1}] {s.text}" for i, s in enumerate(current.script[:10])]
            module_info = f"""
当前模块: {current.title} (id: {current.id})
当前文案 (前10句):
{chr(10).join(sentences)}
{"  ... 还有 " + str(len(current.script) - 10) + " 句" if len(current.script) > 10 else ""}
"""
        else:
            module_info = "当前没有选中的模块"

        modules_overview = "\n".join(
            f"  - {m.id}: {m.title} ({len(m.script)}句, status={m.status})"
            for m in state.modules
        )

        prompt = f"""你是一个视频编辑助手的意图解析器。用户在编辑一个教学视频，给出了自然语言指令。

用户的指令: "{instruction}"

当前项目状态:
主题: {state.topic}
模块列表:
{modules_overview}
{module_info}

请分析用户的意图，输出JSON格式的编辑意图列表:
{{
  "intents": [
    {{
      "action": "rewrite|shorten|extend|add|remove|reorder|regenerate|approve|style|adjust_pacing",
      "target": "具体目标，如 sentence:3, first_sentences, module:mod_00, all 等",
      "params": {{
        "text": "新的文本（如果是rewrite/add）",
        "style": "风格要求（如 engaging, concise, detailed）",
        "count": "数量（如缩短到多少句）",
        "reason": "原因说明"
      }},
      "confidence": 0.9,
      "reasoning": "为什么理解为这个意图"
    }}
  ],
  "interpretation": "对用户指令的整体理解"
}}

action类型说明:
- rewrite: 重写某些句子
- shorten: 缩短/精简
- extend: 扩展/加长
- add: 添加新内容
- remove: 删除内容
- reorder: 重新排列
- regenerate: 重新生成
- approve: 确认/通过
- style: 调整风格
- adjust_pacing: 调整节奏

target格式:
- sentence:N → 第N句
- sentences:N-M → 第N到M句
- first_sentences → 开头几句
- last_sentences → 结尾几句
- module:ID → 整个模块
- all → 所有内容"""

        response = self.llm.generate(prompt)
        data = json.loads(response)

        intents = []
        for item in data.get("intents", []):
            intents.append(EditIntent(
                action=item.get("action", ""),
                target=item.get("target", ""),
                params=item.get("params", {}),
                confidence=item.get("confidence", 0.5),
                reasoning=item.get("reasoning", ""),
            ))

        return intents

    def _regex_parse(self, instruction: str, state: VideoProjectState) -> list[EditIntent]:
        """Fallback regex-based parsing when LLM is unavailable."""
        import re
        intents = []
        instruction_lower = instruction.lower().strip()

        # Pattern: "修改第X句为..."
        match = re.search(
            r'(?:修改|改|edit)\s*(?:第)?(\d+)(?:句|个)?\s*(?:为|成|到)?[:：]?\s*(.+)',
            instruction
        )
        if match:
            idx = int(match.group(1))
            new_text = match.group(2).strip()
            intents.append(EditIntent(
                action="rewrite",
                target=f"sentence:{idx}",
                params={"text": new_text},
                confidence=0.95,
                reasoning=f"明确指定了修改第{idx}句",
            ))
            return intents

        # Pattern: "删除第X句"
        match = re.search(r'(?:删除|去掉|移除)\s*(?:第)?(\d+)(?:句|个)?', instruction)
        if match:
            idx = int(match.group(1))
            intents.append(EditIntent(
                action="remove",
                target=f"sentence:{idx}",
                confidence=0.95,
                reasoning=f"明确指定了删除第{idx}句",
            ))
            return intents

        # Pattern: "加一句/添加..."
        match = re.search(r'(?:加|添加|增加)\s*(?:一句|一个句子)?[:：]?\s*(.+)', instruction)
        if match:
            text = match.group(1).strip()
            intents.append(EditIntent(
                action="add",
                target="end",
                params={"text": text},
                confidence=0.9,
                reasoning="明确指定了添加句子",
            ))
            return intents

        # Pattern: "重新生成"
        if '重新生成' in instruction or '重来' in instruction:
            intents.append(EditIntent(
                action="regenerate",
                target="current_module",
                confidence=0.9,
                reasoning="用户要求重新生成",
            ))
            return intents

        # Pattern: "确认/OK/继续"
        if any(kw in instruction_lower for kw in ['确认', 'ok', '继续', 'approve', '没问题']):
            intents.append(EditIntent(
                action="approve",
                target="current_module",
                confidence=0.9,
                reasoning="用户确认当前内容",
            ))
            return intents

        # Vague patterns (no LLM → simple heuristics)
        if any(kw in instruction for kw in ['枯燥', '无聊', '没意思', '吸引']):
            intents.append(EditIntent(
                action="style",
                target="first_sentences",
                params={"style": "engaging", "reason": "用户觉得开头不够吸引人"},
                confidence=0.6,
                reasoning="检测到用户觉得内容枯燥",
            ))

        if any(kw in instruction for kw in ['短', '精简', '压缩']):
            intents.append(EditIntent(
                action="shorten",
                target="all",
                params={"reason": "用户要求精简"},
                confidence=0.6,
                reasoning="检测到用户要求缩短",
            ))

        if any(kw in instruction for kw in ['长', '详细', '展开', '补充']):
            intents.append(EditIntent(
                action="extend",
                target="all",
                params={"reason": "用户要求扩展"},
                confidence=0.6,
                reasoning="检测到用户要求扩展",
            ))

        if any(kw in instruction for kw in ['快', '节奏', '紧凑']):
            intents.append(EditIntent(
                action="adjust_pacing",
                target="all",
                params={"style": "fast", "reason": "用户要求加快节奏"},
                confidence=0.6,
                reasoning="检测到用户要求调整节奏",
            ))

        if any(kw in instruction for kw in ['例子', '举例', '案例']):
            intents.append(EditIntent(
                action="add",
                target="middle",
                params={"type": "example", "reason": "用户要求加例子"},
                confidence=0.6,
                reasoning="检测到用户要求添加例子",
            ))

        # If nothing matched, record as generic feedback
        if not intents:
            intents.append(EditIntent(
                action="feedback",
                target="all",
                params={"instruction": instruction},
                confidence=0.3,
                reasoning="无法精确解析，记录为反馈",
            ))

        return intents
