# -*- coding: utf-8 -*-
"""
ReAct推理循环 - Thought → Action → Observation → Decision
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum
import time
import re
import json

from agent.llm.ollama_client import OllamaClient
from agent.tools.tool_base import BaseTool
from agent.core.tool_executor import ToolExecutor


class ReActStep(Enum):
    """ReAct步骤类型"""
    THOUGHT = "thought"
    ACTION = "action"
    OBSERVATION = "observation"
    DECISION = "decision"
    FINAL = "final"


@dataclass
class ReactStepRecord:
    """ReAct步骤记录"""
    step_type: ReActStep
    content: str
    tool_name: Optional[str] = None
    tool_params: Optional[Dict] = None
    result: Optional[Any] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class ReactContext:
    """ReAct执行上下文"""
    task: str
    conversation_history: List[Dict[str, str]]
    steps: List[ReactStepRecord] = field(default_factory=list)
    max_iterations: int = 10
    current_iteration: int = 0


class ReActLoop:
    """ReAct推理循环"""

    SYSTEM_PROMPT = """你是一个专业的短视频AI生产助手。你的任务是帮助用户完成短视频制作的完整流程。

## 可用工具
{tools_description}

## 执行规则
1. 仔细分析用户需求
2. 选择合适的工具执行
3. 根据执行结果判断是否需要继续
4. 完成后给出最终结果和操作建议

## 输出格式（严格按此格式输出）
当需要使用工具时：
```
Thought: [你的思考过程]
Action: [工具名称]
Action_Params: {JSON格式的参数}
```

当收到工具执行结果后：
```
Observation: [观察结果]
Decision: [基于观察的判断，决定下一步]
```

当任务完成时：
```
Final: [最终结果和建议]
```
"""

    def __init__(self, llm_client: OllamaClient, tools: List[BaseTool], tool_executor: ToolExecutor):
        self.llm_client = llm_client
        self.tools = {t.definition.name: t for t in tools}
        self.tool_executor = tool_executor
        self._build_tools_description()

    def _build_tools_description(self):
        """构建工具描述"""
        self.tools_description = "\n\n".join(
            t.definition.to_markdown() for t in self.tools.values()
        )

    def parse_llm_response(self, response: str) -> Dict:
        """解析LLM响应，提取Thought/Action/Observation等"""
        # 提取Thought
        thought_match = re.search(r'Thought:\s*(.+?)(?=Action:|Final:|$)', response, re.DOTALL)
        thought = thought_match.group(1).strip() if thought_match else ""

        # 提取Action
        action_match = re.search(r'Action:\s*(\w+)', response)
        action = action_match.group(1).strip() if action_match else None

        # 提取Action_Params
        params_match = re.search(r'Action_Params:\s*(\{.*?\})', response, re.DOTALL)
        params = {}
        if params_match:
            try:
                params = json.loads(params_match.group(1))
            except:
                pass

        # 提取Final
        final_match = re.search(r'Final:\s*(.+?)$', response, re.DOTALL)
        final = final_match.group(1).strip() if final_match else None

        # 提取Decision
        decision_match = re.search(r'Decision:\s*(.+?)(?=Action:|Final:|$)', response, re.DOTALL)
        decision = decision_match.group(1).strip() if decision_match else None

        # 提取Observation
        obs_match = re.search(r'Observation:\s*(.+?)(?=Decision:|Thought:|Action:|Final:|$)', response, re.DOTALL)
        observation = obs_match.group(1).strip() if obs_match else None

        return {
            "thought": thought,
            "action": action,
            "action_params": params,
            "final": final,
            "decision": decision,
            "observation": observation
        }

    def build_messages(self, context: ReactContext, last_result: Any = None, last_observation: str = None) -> List[Dict]:
        """构建消息列表"""
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT.format(
                tools_description=self.tools_description
            )}
        ]

        # 添加对话历史
        for msg in context.conversation_history[-5:]:
            messages.append(msg)

        # 添加上一轮观察结果
        if last_observation:
            messages.append({"role": "user", "content": f"Observation: {last_observation}"})

        return messages

    def run(self, task: str, conversation_history: List[Dict] = None) -> Dict:
        """运行ReAct循环"""
        context = ReactContext(
            task=task,
            conversation_history=conversation_history or []
        )

        last_observation = None
        all_steps = []

        while context.current_iteration < context.max_iterations:
            context.current_iteration += 1

            messages = self.build_messages(context, last_result=None, last_observation=last_observation)

            # 调用LLM
            response = self.llm_client.chat(messages)

            # 解析响应
            parsed = self.parse_llm_response(response)

            # 记录思考步骤
            if parsed.get("thought"):
                all_steps.append({
                    "step_type": "thought",
                    "content": parsed["thought"]
                })

            # 检查是否完成
            if parsed.get("final"):
                return {
                    "success": True,
                    "final": parsed["final"],
                    "steps": all_steps,
                    "iterations": context.current_iteration
                }

            # 执行工具
            if parsed.get("action"):
                tool_name = parsed["action"]
                tool_params = parsed.get("action_params", {})

                all_steps.append({
                    "step_type": "action",
                    "content": f"调用工具: {tool_name}",
                    "tool_name": tool_name,
                    "tool_params": tool_params
                })

                # 执行工具
                result = self.tool_executor.execute_tool(tool_name, tool_params)
                last_observation = result.to_observation()

                all_steps.append({
                    "step_type": "observation",
                    "content": last_observation,
                    "result": result.result if result.success else None
                })

                # 如果工具执行失败，返回错误
                if not result.success:
                    return {
                        "success": False,
                        "error": f"工具执行失败: {result.error}",
                        "steps": all_steps,
                        "iterations": context.current_iteration
                    }

        return {
            "success": False,
            "error": f"超过最大迭代次数 {context.max_iterations}",
            "steps": all_steps,
            "iterations": context.current_iteration
        }
