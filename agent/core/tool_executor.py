# -*- coding: utf-8 -*-
"""
工具执行器 - 负责执行工具并解析LLM响应
"""
import re
import json
from typing import List, Dict, Optional, Any
from agent.tools.tool_base import BaseTool, ToolResult
from agent.llm.ollama_client import OllamaClient


class ToolExecutor:
    """工具执行器 + 意图识别"""

    # 意图到工具的映射
    INTENT_ROUTES = {
        "topic_request": ["get_hot_topics"],
        "script_request": ["get_hot_topics", "generate_script"],
        "video_request": ["get_local_materials", "render_video"],
        "subtitle_request": ["generate_subtitle"],
        "platform_request": ["adapt_platform_content"],
        "full_workflow": ["get_hot_topics", "generate_script", "get_local_materials", "render_video", "generate_subtitle", "adapt_platform_content"]
    }

    # 意图分类Prompt
    INTENT_CLASSIFICATION_PROMPT = """分析用户意图，分类如下：
1. topic_request - 需要选题推荐
2. script_request - 需要脚本生成
3. video_request - 需要视频剪辑
4. subtitle_request - 需要字幕生成
5. platform_request - 需要平台适配
6. full_workflow - 完整视频生产
7. info_query - 信息查询
8. other - 其他

用户: {user_message}
意图:"""

    def __init__(self, tools: List[BaseTool], llm_client: OllamaClient):
        self.tools = {t.definition.name: t for t in tools}
        self.llm_client = llm_client

    def classify_intent(self, user_message: str) -> str:
        """意图分类"""
        prompt = self.INTENT_CLASSIFICATION_PROMPT.format(user_message=user_message)
        response = self.llm_client.generate(prompt, temperature=0.3)

        for intent in self.INTENT_ROUTES.keys():
            if intent.replace("_request", "") in response.lower():
                return intent

        return "other"

    def execute_tool(self, tool_name: str, params: Dict) -> ToolResult:
        """执行单个工具"""
        if tool_name not in self.tools:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"未知工具: {tool_name}"
            )

        tool = self.tools[tool_name]

        # 参数验证
        valid, error = tool.validate_params(params)
        if not valid:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=error
            )

        try:
            return tool.execute(**params)
        except Exception as e:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=str(e)
            )

    def parse_tool_call_from_response(self, text: str) -> Optional[Dict]:
        """从LLM响应中解析工具调用"""
        # 匹配格式: {"tool": "xxx", "params": {...}}
        patterns = [
            r'\{\s*"tool"\s*:\s*"(\w+)"\s*,\s*"params"\s*:\s*(\{[^}]+\})',
            r'"tool"\s*:\s*"(\w+)"\s*,\s*"params"\s*:\s*(\{[^}]+\})',
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    tool_name = match.group(1)
                    params_str = match.group(2)
                    params = json.loads(params_str)
                    return {"tool": tool_name, "params": params}
                except:
                    continue

        # 尝试直接解析整个JSON块
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            try:
                obj = json.loads(json_match.group())
                if "tool" in obj and "params" in obj:
                    return obj
            except:
                pass

        return None

    def execute_full_workflow(self, context: Dict = None) -> Dict:
        """执行完整工作流"""
        context = context or {}

        # 1. 推荐选题
        topics_result = self.execute_tool("get_hot_topics", {"count": 1})
        if not topics_result.success:
            return {"success": False, "error": f"选题推荐失败: {topics_result.error}"}

        topic = topics_result.result.get("topics", [{}])[0]
        if not topic:
            return {"success": False, "error": "未找到可用选题"}
        context["topic"] = topic

        # 2. 生成脚本
        script_result = self.execute_tool("generate_script", {
            "topic": topic,
            "platform": "抖音",
            "duration": 30
        })
        if not script_result.success:
            return {"success": False, "error": f"脚本生成失败: {script_result.error}"}

        script_data = script_result.result
        context["script"] = script_data

        # 3. 读取素材
        materials_result = self.execute_tool("get_local_materials", {
            "material_type": "image",
            "limit": 5
        })

        image_paths = []
        if materials_result.success:
            items = materials_result.result.get("materials", [])
            image_paths = [m["path"] for m in items if m.get("type") == "image"]

        if not image_paths:
            return {"success": False, "error": "素材池为空，请先上传素材"}

        context["materials"] = image_paths

        # 4. 生成视频
        video_result = self.execute_tool("render_video", {
            "image_paths": image_paths,
            "duration_per_image": 5,
            "transition": "fade"
        })
        if not video_result.success:
            return {"success": False, "error": f"视频生成失败: {video_result.error}"}

        video_path = video_result.result.get("output_path")
        context["video_path"] = video_path

        # 5. 生成字幕
        subtitle_result = self.execute_tool("generate_subtitle", {
            "video_path": video_path,
            "script": script_data.get("full_script", ""),
            "output_path": video_path.replace(".mp4", "_subtitled.mp4")
        })

        final_video = video_path
        if subtitle_result.success:
            final_video = subtitle_result.result.get("video_path", final_video)

        context["final_video"] = final_video

        # 6. 平台适配
        platform_result = self.execute_tool("adapt_platform_content", {
            "video_path": final_video,
            "script_result": script_data,
            "platform": "抖音"
        })

        return {
            "success": True,
            "context": context,
            "result": {
                "topic": topic.get("title"),
                "script": script_data.get("full_script", "")[:200] + "..." if script_data.get("full_script") else "",
                "video": final_video,
                "export": platform_result.result if platform_result.success else None
            }
        }

    def execute_full_workflow_with_progress(self, context: Dict = None,
                                          progress_callback: callable = None) -> Dict:
        """执行完整工作流（带进度回调）"""
        context = context or {}

        def update_progress(progress: float, msg: str):
            if progress_callback:
                progress_callback(progress, msg)

        # 1. 推荐选题 (0-15%)
        update_progress(0.05, "推荐选题...")
        topics_result = self.execute_tool("get_hot_topics", {"count": 1})
        if not topics_result.success:
            return {"success": False, "error": f"选题推荐失败: {topics_result.error}"}

        topic = topics_result.result.get("topics", [{}])[0]
        if not topic:
            return {"success": False, "error": "未找到可用选题"}
        context["topic"] = topic
        update_progress(0.15, "选题推荐完成")

        # 2. 生成脚本 (15-30%)
        update_progress(0.20, "生成脚本...")
        script_result = self.execute_tool("generate_script", {
            "topic": topic,
            "platform": "抖音",
            "duration": 30
        })
        if not script_result.success:
            return {"success": False, "error": f"脚本生成失败: {script_result.error}"}

        script_data = script_result.result
        context["script"] = script_data
        update_progress(0.30, "脚本生成完成")

        # 3. 读取素材 (30-40%)
        update_progress(0.35, "读取素材...")
        materials_result = self.execute_tool("get_local_materials", {
            "material_type": "image",
            "limit": 5
        })

        image_paths = []
        if materials_result.success:
            items = materials_result.result.get("materials", [])
            image_paths = [m["path"] for m in items if m.get("type") == "image"]

        if not image_paths:
            return {"success": False, "error": "素材池为空，请先上传素材"}

        context["materials"] = image_paths
        update_progress(0.40, f"已加载{len(image_paths)}个素材")

        # 4. 生成视频 (40-70%)
        update_progress(0.45, "开始生成视频...")
        video_result = self.execute_tool("render_video", {
            "image_paths": image_paths,
            "duration_per_image": 5,
            "transition": "fade"
        })
        if not video_result.success:
            return {"success": False, "error": f"视频生成失败: {video_result.error}"}

        video_path = video_result.result.get("output_path")
        context["video_path"] = video_path
        update_progress(0.70, "视频生成完成")

        # 5. 生成字幕 (70-85%)
        update_progress(0.75, "生成字幕...")
        subtitle_result = self.execute_tool("generate_subtitle", {
            "video_path": video_path,
            "script": script_data.get("full_script", ""),
            "output_path": video_path.replace(".mp4", "_subtitled.mp4")
        })

        final_video = video_path
        if subtitle_result.success:
            final_video = subtitle_result.result.get("video_path", final_video)
            update_progress(0.85, "字幕生成完成")
        else:
            update_progress(0.85, "字幕生成跳过（可能无字幕）")

        context["final_video"] = final_video

        # 6. 平台适配 (85-100%)
        update_progress(0.90, "适配平台...")
        platform_result = self.execute_tool("adapt_platform_content", {
            "video_path": final_video,
            "script_result": script_data,
            "platform": "抖音"
        })
        update_progress(1.0, "全部完成")

        return {
            "success": True,
            "context": context,
            "result": {
                "topic": topic.get("title"),
                "script": script_data.get("full_script", "")[:200] + "..." if script_data.get("full_script") else "",
                "video": final_video,
                "export": platform_result.result if platform_result.success else None
            }
        }
