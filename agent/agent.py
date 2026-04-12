# -*- coding: utf-8 -*-
"""
企业级AI Agent主类
"""
import time
import uuid
import re
from typing import Dict, List, Optional, Any

from agent.llm.ollama_client import OllamaClient, get_llm_client
from agent.core.memory import AgentMemory, ShortTermMemory, WorkingMemory, LongTermMemory
from agent.core.tool_executor import ToolExecutor
from agent.core.react_loop import ReActLoop
from agent.core.task_planner import TaskPlanner
from agent.tools import get_all_tools


class Agent:
    """企业级AI Agent"""

    def __init__(self, db_path: str = "data/agent_memory.db"):
        # 初始化LLM客户端
        self.llm = get_llm_client()

        # 初始化工具
        self.tools = get_all_tools()

        # 初始化记忆
        self.memory = AgentMemory(db_path)

        # 初始化执行器
        self.executor = ToolExecutor(self.tools, self.llm)

        # 初始化ReAct循环
        self.react = ReActLoop(self.llm, self.tools, self.executor)

        # 初始化任务规划器
        self.planner = TaskPlanner(self.llm)

        # Session管理
        self._sessions: Dict[str, Dict] = {}

        # 快捷指令映射
        self._quick_commands = {
            "选题推荐": self._handle_topic_request,
            "生成脚本": self._handle_script_request,
            "生成视频": self._handle_video_request,
            "一键生产": self._handle_full_workflow,
        }

    def start_session(self, session_id: str = None) -> str:
        """开始新会话"""
        if session_id is None:
            session_id = str(uuid.uuid4())

        self.memory.short_term.clear()
        self._sessions[session_id] = {
            "started_at": time.time(),
            "message_count": 0
        }
        return session_id

    def restore_session(self, session_id: str):
        """恢复会话"""
        if session_id not in self._sessions:
            self.start_session(session_id)

    def end_session(self, session_id: str):
        """结束会话"""
        if session_id in self._sessions:
            del self._sessions[session_id]

    def chat(self, message: str) -> Dict:
        """处理用户消息"""
        # 添加用户消息到记忆
        self.memory.short_term.add_message("user", message)

        # 更新会话计数
        for session in self._sessions.values():
            session["message_count"] = session.get("message_count", 0) + 1

        # 意图分类
        intent = self.executor.classify_intent(message)

        # 根据意图执行
        if intent == "full_workflow":
            result = self._handle_full_workflow(message)
        elif intent == "topic_request":
            result = self._handle_topic_request(message)
        elif intent == "script_request":
            result = self._handle_script_request(message)
        elif intent == "video_request":
            result = self._handle_video_request(message)
        elif intent == "subtitle_request":
            result = self._handle_subtitle_request(message)
        elif intent == "platform_request":
            result = self._handle_platform_request(message)
        else:
            result = self._handle_general(message)

        # 添加助手消息到记忆
        self.memory.short_term.add_message("assistant", result["response"])

        return result

    def _handle_topic_request(self, message: str) -> Dict:
        """处理选题请求"""
        import re

        # 提取参数
        category = None
        for cat in ["知识付费", "美食探店", "生活方式", "情感心理", "科技数码", "娱乐搞笑"]:
            if cat in message:
                category = cat
                break

        count_match = re.search(r"(\d+)[个条]", message)
        count = int(count_match.group(1)) if count_match else 3

        result = self.executor.execute_tool("get_hot_topics", {
            "category": category,
            "count": count
        })

        if result.success:
            topics = result.result.get("topics", [])
            response = f"为您推荐 {len(topics)} 个选题：\n\n"
            for i, t in enumerate(topics, 1):
                response += f"{i}. **{t.get('title', '无标题')}**\n"
                response += f"   赛道: {t.get('category', '通用')} | 热度: {t.get('heat_score', 0)}\n"
                response += f"   钩子: {t.get('hook', '暂无')}\n\n"
        else:
            response = f"选题推荐失败: {result.error}"

        return {"success": result.success, "response": response}

    def _handle_script_request(self, message: str) -> Dict:
        """处理脚本生成请求"""
        import re

        # 提取参数
        platform = "抖音"
        for p in ["抖音", "小红书", "B站"]:
            if p in message:
                platform = p
                break

        duration_match = re.search(r"(\d+)[秒]", message)
        duration = int(duration_match.group(1)) if duration_match else 30

        # 先获取一个选题
        topic_result = self.executor.execute_tool("get_hot_topics", {"count": 1})
        if not topic_result.success or not topic_result.result.get("topics"):
            return {"success": False, "response": "选题推荐失败，请先添加选题"}

        topic = topic_result.result["topics"][0]

        # 生成脚本
        result = self.executor.execute_tool("generate_script", {
            "topic": topic,
            "platform": platform,
            "duration": duration
        })

        if result.success:
            script = result.result
            response = f"脚本生成成功！\n\n"
            response += f"**【黄金3秒钩子】**\n{script.get('hook', '')}\n\n"
            response += f"**【主体内容】**\n{script.get('body', '')}\n\n"
            response += f"**【行动号召】**\n{script.get('cta', '')}\n\n"
            response += f"**【完整脚本】**\n{script.get('full_script', '')}"
        else:
            response = f"脚本生成失败: {result.error}"

        return {"success": result.success, "response": response}

    def _handle_video_request(self, message: str) -> Dict:
        """处理视频生成请求"""
        # 先读取素材
        materials_result = self.executor.execute_tool("get_local_materials", {
            "material_type": "image",
            "limit": 10
        })

        if not materials_result.success or not materials_result.result.get("materials"):
            return {"success": False, "response": "素材池为空，请先上传素材"}

        images = [m["path"] for m in materials_result.result["materials"][:5]]

        result = self.executor.execute_tool("render_video", {
            "image_paths": images,
            "duration_per_image": 5,
            "transition": "fade"
        })

        if result.success:
            return {
                "success": True,
                "response": f"视频生成成功！\n\n输出路径: {result.result.get('output_path')}\n\n可以使用字幕生成工具为视频添加字幕。"
            }
        else:
            return {"success": False, "response": f"视频生成失败: {result.error}"}

    def _handle_subtitle_request(self, message: str) -> Dict:
        """处理字幕生成请求"""
        import re

        # 从消息中提取视频路径
        video_path_match = re.search(r'视频[：:]\s*([^\s]+)', message)
        if not video_path_match:
            return {"success": False, "response": "请提供视频路径"}

        video_path = video_path_match.group(1)

        # 从上下文中获取脚本
        script = ""
        if self.memory.current_task:
            script = self.memory.current_task.collected_data.get("script", {}).get("full_script", "")

        if not script:
            script = "这是一个测试字幕"

        result = self.executor.execute_tool("generate_subtitle", {
            "video_path": video_path,
            "script": script,
            "output_path": video_path.replace(".mp4", "_subtitled.mp4")
        })

        if result.success:
            return {
                "success": True,
                "response": f"字幕生成成功！\n\n输出路径: {result.result.get('video_path')}"
            }
        else:
            return {"success": False, "response": f"字幕生成失败: {result.error}"}

    def _handle_platform_request(self, message: str) -> Dict:
        """处理平台适配请求"""
        import re

        platform = "抖音"
        for p in ["抖音", "小红书", "B站"]:
            if p in message:
                platform = p
                break

        # 从上下文中获取视频路径和脚本
        video_path = None
        script_result = {}

        if self.memory.current_task:
            video_path = self.memory.current_task.collected_data.get("final_video")
            script_result = self.memory.current_task.collected_data.get("script", {})

        if not video_path:
            video_path_match = re.search(r'视频[：:]\s*([^\s]+)', message)
            if video_path_match:
                video_path = video_path_match.group(1)

        if not video_path:
            return {"success": False, "response": "请提供视频路径"}

        if not script_result:
            return {"success": False, "response": "请先生成脚本"}

        result = self.executor.execute_tool("adapt_platform_content", {
            "video_path": video_path,
            "script_result": script_result,
            "platform": platform
        })

        if result.success:
            adapted = result.result.get("adapted_content", {})
            response = f"平台适配成功！\n\n"
            response += f"**平台**: {platform}\n"
            response += f"**标题**: {adapted.get('title', '')}\n"
            response += f"**描述**: {adapted.get('description', '')}\n"
            response += f"**标签**: {', '.join(adapted.get('hashtags', []))}"
        else:
            response = f"平台适配失败: {result.error}"

        return {"success": result.success, "response": response}

    def _handle_full_workflow(self, message: str) -> Dict:
        """处理完整工作流"""
        # 启动任务
        task_id = str(uuid.uuid4())
        self.memory.start_task(task_id, message)

        # 执行完整工作流
        result = self.executor.execute_full_workflow(self.memory.current_task.collected_data)

        if result.get("success"):
            ctx = result.get("context", {})
            response = "✅ 视频生产完成！\n\n"
            response += f"**选题**: {ctx.get('topic', {}).get('title', '未知')}\n"
            response += f"**最终视频**: {ctx.get('final_video', '未知')}\n\n"
            response += "视频已生成并添加字幕，可以直接发布到平台。"
        else:
            response = f"生产失败: {result.get('error', '未知错误')}"

        return {
            "success": result.get("success", False),
            "response": response,
            "context": result.get("context", {})
        }

    def _handle_general(self, message: str) -> Dict:
        """处理通用请求 - 使用ReAct循环"""
        react_result = self.react.run(
            message,
            self.memory.short_term.get_conversation_format()
        )

        if react_result.get("success"):
            return {
                "success": True,
                "response": react_result.get("final", ""),
                "steps": react_result.get("steps", [])
            }
        else:
            return {
                "success": False,
                "response": f"处理失败: {react_result.get('error', '未知错误')}"
            }

    def get_session_info(self, session_id: str) -> Optional[Dict]:
        """获取会话信息"""
        if session_id not in self._sessions:
            return None

        return {
            "session_id": session_id,
            "started_at": self._sessions[session_id]["started_at"],
            "message_count": self._sessions[session_id].get("message_count", 0),
            "memory_size": len(self.memory.short_term)
        }
