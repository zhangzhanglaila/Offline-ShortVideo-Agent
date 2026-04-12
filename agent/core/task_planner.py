# -*- coding: utf-8 -*-
"""
任务规划器 - 分析任务并规划执行步骤
"""
import re
from typing import List, Dict, Optional, Any
from agent.llm.ollama_client import OllamaClient


class TaskPlanner:
    """任务规划器 - 将复杂任务分解为步骤"""

    PLANNER_PROMPT = """你是一个任务规划专家。分析用户需求，将其分解为具体的执行步骤。

## 可用工具
- get_hot_topics: 获取热门选题
- get_local_materials: 读取本地素材
- generate_script: 生成脚本
- render_video: 渲染视频
- generate_subtitle: 生成字幕
- adapt_platform_content: 平台适配

## 任务: {task}

## 输出格式
将任务分解为步骤列表，格式如下：
1. [步骤类型] 步骤描述
2. [步骤类型] 步骤描述
...

步骤类型可以是：选题、脚本、素材、视频、字幕、平台、完成

请分解任务：
"""

    def __init__(self, llm_client: OllamaClient):
        self.llm_client = llm_client

    def plan(self, task: str) -> Dict[str, Any]:
        """规划任务执行"""
        prompt = self.PLANNER_PROMPT.format(task=task)
        response = self.llm_client.generate(prompt, temperature=0.3)

        # 解析步骤
        steps = self._parse_steps(response)

        # 确定需要的工具
        required_tools = self._determine_tools(steps)

        return {
            "task": task,
            "steps": steps,
            "required_tools": required_tools,
            "raw_plan": response
        }

    def _parse_steps(self, response: str) -> List[Dict[str, str]]:
        """解析步骤列表"""
        steps = []
        lines = response.split('\n')

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 匹配 "1. [类型] 描述" 格式
            match = re.match(r'\d+\)\?\s*\[(\w+)\]\s*(.+)', line)
            if match:
                step_type = match.group(1)
                description = match.group(2)
                steps.append({
                    "type": step_type,
                    "description": description
                })
                continue

            # 匹配 "1. 描述" 格式（无类型）
            match = re.match(r'\d+[.、]\s*(.+)', line)
            if match:
                description = match.group(1)
                # 尝试推断类型
                step_type = self._infer_step_type(description)
                steps.append({
                    "type": step_type,
                    "description": description
                })

        return steps

    def _infer_step_type(self, description: str) -> str:
        """推断步骤类型"""
        desc_lower = description.lower()

        if any(kw in desc_lower for kw in ['选题', '推荐', '找']):
            return "选题"
        if any(kw in desc_lower for kw in ['脚本', '文案', '口播']):
            return "脚本"
        if any(kw in desc_lower for kw in ['素材', '图片', '视频']):
            return "素材"
        if any(kw in desc_lower for kw in ['剪辑', '生成视频', '渲染']):
            return "视频"
        if any(kw in desc_lower for kw in ['字幕']):
            return "字幕"
        if any(kw in desc_lower for kw in ['适配', '平台', '发布']):
            return "平台"

        return "完成"

    def _determine_tools(self, steps: List[Dict[str, str]]) -> List[str]:
        """确定需要的工具"""
        type_to_tool = {
            "选题": "get_hot_topics",
            "脚本": "generate_script",
            "素材": "get_local_materials",
            "视频": "render_video",
            "字幕": "generate_subtitle",
            "平台": "adapt_platform_content"
        }

        tools = []
        for step in steps:
            tool = type_to_tool.get(step["type"])
            if tool and tool not in tools:
                tools.append(tool)

        return tools
