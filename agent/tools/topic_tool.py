# -*- coding: utf-8 -*-
"""
选题推荐工具
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from agent.tools.tool_base import BaseTool, ToolDefinition, ToolParameter, ToolCategory, ToolResult


class TopicRecommendTool(BaseTool):
    """选题推荐工具 - get_hot_topics()"""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_hot_topics",
            category=ToolCategory.TOPIC,
            description="根据条件智能推荐爆款选题，支持按赛道、时长、标签筛选",
            parameters=[
                ToolParameter(
                    name="category",
                    type="str",
                    description="赛道分类",
                    required=False,
                    default=None,
                    enum_values=["知识付费", "美食探店", "生活方式", "情感心理", "科技数码", "娱乐搞笑"]
                ),
                ToolParameter(
                    name="count",
                    type="int",
                    description="推荐数量",
                    required=False,
                    default=5
                ),
                ToolParameter(
                    name="min_heat",
                    type="int",
                    description="最低热度值",
                    required=False,
                    default=0
                )
            ]
        )

    def execute(self, category: str = None, count: int = 5, min_heat: int = 0) -> ToolResult:
        """获取热门选题"""
        import time
        start_time = time.time()

        try:
            from core.topics_module import TopicsModule

            topics_mod = TopicsModule()

            # 根据参数选择查询方法
            if category:
                recommendations = topics_mod.get_topics_by_category(category=category, limit=count)
            elif min_heat > 0:
                recommendations = topics_mod.get_high_heat_topics(min_heat=min_heat, limit=count)
            else:
                recommendations = topics_mod.recommend_topics(category=None, count=count)

            # 转换为标准格式
            topics_list = []
            for t in recommendations:
                if isinstance(t, dict):
                    topics_list.append({
                        "id": t.get("id"),
                        "title": t.get("title"),
                        "category": t.get("category"),
                        "hook": t.get("hook", ""),
                        "heat_score": t.get("heat_score", 0),
                        "transform_rate": t.get("transform_rate", 0),
                        "tags": t.get("tags", []),
                        "duration": t.get("duration", "30-60秒")
                    })

            return ToolResult(
                tool_name=self.definition.name,
                success=True,
                result={"topics": topics_list, "count": len(topics_list)},
                execution_time=time.time() - start_time
            )

        except Exception as e:
            return ToolResult(
                tool_name=self.definition.name,
                success=False,
                error=str(e),
                execution_time=time.time() - start_time
            )
