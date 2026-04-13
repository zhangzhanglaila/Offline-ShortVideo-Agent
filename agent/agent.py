# -*- coding: utf-8 -*-
"""
企业级AI Agent主类
"""
import time
import uuid
import re
from typing import Dict, List, Optional, Any, Generator
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.llm.ollama_client import OllamaClient, get_llm_client
from agent.llm.streaming import get_streaming_client
from agent.core.memory import AgentMemory, ShortTermMemory, WorkingMemory, LongTermMemory
from agent.core.tool_executor import ToolExecutor
from agent.core.react_loop import ReActLoop
from agent.core.task_planner import TaskPlanner
from agent.core.task_queue import get_task_queue
from agent.core.retry_handler import get_retry_handler
from agent.core.event_emitter import push_agent_log
from agent.tools import get_all_tools
from config import AGENT_CONFIG


class Agent:
    """企业级AI Agent"""

    def __init__(self, db_path: str = "data/agent_memory.db"):
        # 初始化LLM客户端
        self.llm = get_llm_client()
        self.streaming_llm = get_streaming_client()

        # 初始化工具
        self.tools = get_all_tools()

        # 初始化记忆
        self.memory = AgentMemory(db_path)

        # 初始化执行器（带重试）
        self.executor = ToolExecutor(self.tools, self.llm)
        self.retry = get_retry_handler()

        # 初始化ReAct循环
        self.react = ReActLoop(self.llm, self.tools, self.executor)

        # 初始化任务规划器
        self.planner = TaskPlanner(self.llm)

        # 初始化任务队列
        self.task_queue = get_task_queue()

        # Session管理
        self._sessions: Dict[str, Dict] = {}

        # Agent ID
        self.agent_id = str(uuid.uuid4())[:8]

        # Ollama可用状态
        self._ollama_available = None

    def check_ollama(self) -> bool:
        """检查Ollama是否可用（快速检测）"""
        import urllib.request
        import urllib.error
        try:
            req = urllib.request.Request(
                f"{self.llm.base_url}/api/tags",
                method='GET'
            )
            with urllib.request.urlopen(req, timeout=1) as response:
                self._ollama_available = response.status == 200
        except Exception:
            self._ollama_available = False

        return self._ollama_available

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
        # 从持久化加载对话历史
        try:
            self.memory.load_conversation(session_id)
        except Exception:
            pass

    def end_session(self, session_id: str):
        """结束会话"""
        if session_id in self._sessions:
            # 保存对话历史到持久化
            try:
                self.memory.save_conversation(session_id)
            except Exception:
                pass
            del self._sessions[session_id]

    def list_sessions(self, limit: int = 50) -> List[Dict]:
        """列出所有历史会话"""
        sessions = self.memory.long_term.list_conversation_sessions(limit)
        # 格式化时间戳
        for s in sessions:
            if s.get('created_at'):
                from datetime import datetime
                s['updated_at_display'] = datetime.fromtimestamp(s['updated_at']).strftime('%m-%d %H:%M') if s['updated_at'] else ''
        return sessions

    def chat(self, message: str) -> Dict:
        """处理用户消息"""
        task_id = str(uuid.uuid4())

        # 前置检查：判断模型可用性，不可用则立即返回错误不重试
        _, _, immediate_error = self.llm._get_error_info()
        if immediate_error:
            return {"success": False, "response": immediate_error}

        try:
            # 推送开始日志
            if AGENT_CONFIG.get('log_to_ui'):
                try:
                    push_agent_log(task_id, f"收到消息: {message[:50]}...", 'info', self.agent_id)
                except Exception:
                    pass

            # 添加用户消息到记忆
            self.memory.short_term.add_message("user", message)

            # 更新会话计数
            for session in self._sessions.values():
                session["message_count"] = session.get("message_count", 0) + 1

            # 意图分类（带重试）
            intent = self.retry.with_retry(
                self.executor.classify_intent
            )(message)

            if AGENT_CONFIG.get('log_to_ui'):
                try:
                    push_agent_log(task_id, f"识别意图: {intent}", 'info', self.agent_id)
                except Exception:
                    pass

            # 根据意图执行
            handler_map = {
                "full_workflow": self._handle_full_workflow,
                "topic_request": self._handle_topic_request,
                "script_request": self._handle_script_request,
                "video_request": self._handle_video_request,
                "subtitle_request": self._handle_subtitle_request,
                "platform_request": self._handle_platform_request,
            }

            handler = handler_map.get(intent, self._handle_general)
            result = handler(message, task_id=task_id)

            # 添加助手消息到记忆
            self.memory.short_term.add_message("assistant", result["response"])

            if AGENT_CONFIG.get('log_to_ui'):
                try:
                    push_agent_log(task_id, "处理完成", 'success', self.agent_id)
                except Exception:
                    pass

            return result

        except ConnectionError as e:
            error_msg = str(e)
            try:
                if AGENT_CONFIG.get('log_to_ui'):
                    push_agent_log(task_id, "模型服务异常", 'error', self.agent_id)
            except Exception:
                pass
            return {"success": False, "response": error_msg}
        except Exception as e:
            error_msg = "抱歉，发生了错误：" + str(e)
            try:
                if AGENT_CONFIG.get('log_to_ui'):
                    push_agent_log(task_id, error_msg, 'error', self.agent_id)
            except Exception:
                pass
            return {"success": False, "response": error_msg}

    def chat_stream(self, message: str) -> Generator[str, None, None]:
        """流式处理用户消息"""
        task_id = str(uuid.uuid4())

        # 前置检查：判断模型可用性，不可用则立即返回错误不重试
        _, _, immediate_error = self.llm._get_error_info()
        if immediate_error:
            yield immediate_error
            return

        try:
            # 推送开始日志
            try:
                if AGENT_CONFIG.get('log_to_ui'):
                    push_agent_log(task_id, f"收到消息: {message[:50]}...", 'info', self.agent_id)
            except Exception:
                pass

            # 添加用户消息到记忆
            self.memory.short_term.add_message("user", message)

            # 更新会话计数
            for session in self._sessions.values():
                session["message_count"] = session.get("message_count", 0) + 1

            # 意图分类
            try:
                intent = self.retry.with_retry(
                    self.executor.classify_intent
                )(message)
            except Exception as e:
                error_msg = f"意图识别失败: {str(e)}"
                try:
                    if AGENT_CONFIG.get('log_to_ui'):
                        push_agent_log(task_id, error_msg, 'error', self.agent_id)
                except Exception:
                    pass
                yield error_msg
                return

            try:
                if AGENT_CONFIG.get('log_to_ui'):
                    push_agent_log(task_id, f"识别意图: {intent}", 'info', self.agent_id)
            except Exception:
                pass

            # 根据意图执行
            handler_map = {
                "full_workflow": self._handle_full_workflow,
                "topic_request": self._handle_topic_request,
                "script_request": self._handle_script_request,
                "video_request": self._handle_video_request,
                "subtitle_request": self._handle_subtitle_request,
                "platform_request": self._handle_platform_request,
            }

            handler = handler_map.get(intent, self._handle_general)

            # 流式执行handler
            try:
                for chunk in handler(message, task_id=task_id, stream=True):
                    yield chunk
            except Exception as e:
                error_msg = f"执行失败: {str(e)}"
                try:
                    if AGENT_CONFIG.get('log_to_ui'):
                        push_agent_log(task_id, error_msg, 'error', self.agent_id)
                except Exception:
                    pass
                yield error_msg
                return

            try:
                if AGENT_CONFIG.get('log_to_ui'):
                    push_agent_log(task_id, "处理完成", 'success', self.agent_id)
            except Exception:
                pass

        except ConnectionError as e:
            error_msg = str(e)
            try:
                if AGENT_CONFIG.get('log_to_ui'):
                    push_agent_log(task_id, "模型服务异常", 'error', self.agent_id)
            except Exception:
                pass
            yield error_msg
        except Exception as e:
            error_msg = "抱歉，发生了错误：" + str(e)
            try:
                if AGENT_CONFIG.get('log_to_ui'):
                    push_agent_log(task_id, error_msg, 'error', self.agent_id)
            except Exception:
                pass
            yield error_msg

    def submit_task(self, task_type: str, params: Dict) -> str:
        """提交异步任务"""
        task_id = str(uuid.uuid4())

        try:
            if AGENT_CONFIG.get('log_to_ui'):
                push_agent_log(task_id, f"提交任务: {task_type}", 'info', self.agent_id)
        except Exception:
            pass

        def task_fn(_progress_callback=None):
            if task_type == 'video':
                return self._execute_video_task(params, task_id, _progress_callback)
            elif task_type == 'full_workflow':
                return self._execute_full_workflow_async(params, task_id, _progress_callback)
            else:
                raise ValueError(f"Unknown task type: {task_type}")

        return self.task_queue.submit(task_fn)

    def _execute_video_task(self, params: Dict, task_id: str, progress_callback=None):
        """执行视频任务"""
        try:
            if progress_callback:
                progress_callback(0.1, "读取素材...")

            images = params.get('images', [])
            if not images:
                result = self.executor.execute_tool("get_local_materials", {
                    "material_type": "image",
                    "limit": 5
                })
                if result.success:
                    images = [m["path"] for m in result.result.get("materials", [])]

            if progress_callback:
                progress_callback(0.3, "生成视频...")

            result = self.executor.execute_tool("render_video", {
                "image_paths": images,
                "duration_per_image": params.get('duration_per_image', 5),
                "transition": params.get('transition', 'fade')
            })

            if progress_callback:
                progress_callback(1.0, "完成")

            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _execute_full_workflow_async(self, params: Dict, task_id: str, progress_callback=None):
        """异步执行完整工作流"""
        try:
            if progress_callback:
                progress_callback(0.05, "推荐选题...")

            result = self.executor.execute_full_workflow_with_progress(
                self.memory.current_task.collected_data if self.memory.current_task else {},
                progress_callback=progress_callback
            )
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _handle_topic_request(self, message: str, task_id: str = None, stream: bool = False) -> Any:
        """处理选题请求"""
        category = None
        for cat in ["知识付费", "美食探店", "生活方式", "情感心理", "科技数码", "娱乐搞笑"]:
            if cat in message:
                category = cat
                break

        count_match = re.search(r"(\d+)[个条]", message)
        count = int(count_match.group(1)) if count_match else 3

        try:
            if AGENT_CONFIG.get('log_to_ui'):
                push_agent_log(task_id, f"推荐{count}个选题 (分类:{category or '全部'})", 'info', self.agent_id)
        except Exception:
            pass

        try:
            result = self.retry.with_retry(
                self.executor.execute_tool
            )("get_hot_topics", {"category": category, "count": count})
        except Exception as e:
            return self._stream_or_return(f"选题推荐失败: {str(e)}", stream)

        if result.success:
            topics = result.result.get("topics", [])
            response = f"为您推荐 {len(topics)} 个选题：\n\n"
            for i, t in enumerate(topics, 1):
                response += f"{i}. **{t.get('title', '无标题')}**\n"
                response += f"   赛道: {t.get('category', '通用')} | 热度: {t.get('heat_score', 0)}\n"
                response += f"   钩子: {t.get('hook', '暂无')}\n\n"
        else:
            response = f"选题推荐失败: {result.error}"

        return self._stream_or_return(response, stream)

    def _handle_script_request(self, message: str, task_id: str = None, stream: bool = False) -> Any:
        """处理脚本生成请求"""
        platform = "抖音"
        for p in ["抖音", "小红书", "B站"]:
            if p in message:
                platform = p
                break

        duration_match = re.search(r"(\d+)[秒]", message)
        duration = int(duration_match.group(1)) if duration_match else 30

        try:
            if AGENT_CONFIG.get('log_to_ui'):
                push_agent_log(task_id, f"生成{platform}脚本 ({duration}秒)", 'info', self.agent_id)
        except Exception:
            pass

        # 先获取选题
        try:
            topic_result = self.retry.with_retry(
                self.executor.execute_tool
            )("get_hot_topics", {"count": 1})
        except Exception as e:
            return self._stream_or_return(f"选题获取失败: {str(e)}", stream)

        if not topic_result.success or not topic_result.result.get("topics"):
            return self._stream_or_return("选题推荐失败，请先添加选题", stream)

        topic = topic_result.result["topics"][0]

        # 生成脚本
        try:
            result = self.retry.with_retry(
                self.executor.execute_tool
            )("generate_script", {
                "topic": topic,
                "platform": platform,
                "duration": duration
            })
        except Exception as e:
            return self._stream_or_return(f"脚本生成失败: {str(e)}", stream)

        if result.success:
            script = result.result
            response = f"脚本生成成功！\n\n"
            response += f"**【黄金3秒钩子】**\n{script.get('hook', '')}\n\n"
            response += f"**【主体内容】**\n{script.get('body', '')}\n\n"
            response += f"**【行动号召】**\n{script.get('cta', '')}\n\n"
            response += f"**【完整脚本】**\n{script.get('full_script', '')}"
        else:
            response = f"脚本生成失败: {result.error}"

        return self._stream_or_return(response, stream)

    def _handle_video_request(self, message: str, task_id: str = None, stream: bool = False) -> Any:
        """处理视频生成请求"""
        try:
            if AGENT_CONFIG.get('log_to_ui'):
                push_agent_log(task_id, "读取素材...", 'info', self.agent_id)
        except Exception:
            pass

        try:
            materials_result = self.retry.with_retry(
                self.executor.execute_tool
            )("get_local_materials", {"material_type": "image", "limit": 10})
        except Exception as e:
            return self._stream_or_return(f"素材读取失败: {str(e)}", stream)

        if not materials_result.success or not materials_result.result.get("materials"):
            return self._stream_or_return("素材池为空，请先上传素材", stream)

        images = [m["path"] for m in materials_result.result["materials"][:5]]

        try:
            if AGENT_CONFIG.get('log_to_ui'):
                push_agent_log(task_id, f"生成视频 ({len(images)}张图片)", 'info', self.agent_id)
        except Exception:
            pass

        try:
            result = self.retry.with_retry(
                self.executor.execute_tool
            )("render_video", {
                "image_paths": images,
                "duration_per_image": 5,
                "transition": "fade"
            })
        except Exception as e:
            return self._stream_or_return(f"视频生成失败: {str(e)}", stream)

        if result.success:
            response = f"视频生成成功！\n\n输出路径: {result.result.get('output_path')}\n\n可以使用字幕生成工具为视频添加字幕。"
        else:
            response = f"视频生成失败: {result.error}"

        return self._stream_or_return(response, stream)

    def _handle_subtitle_request(self, message: str, task_id: str = None, stream: bool = False) -> Any:
        """处理字幕生成请求"""
        video_path_match = re.search(r'视频[：:]\s*([^\s]+)', message)
        if not video_path_match:
            return self._stream_or_return("请提供视频路径", stream)

        video_path = video_path_match.group(1)

        script = ""
        if self.memory.current_task:
            script = self.memory.current_task.collected_data.get("script", {}).get("full_script", "")

        if not script:
            script = "这是一个测试字幕"

        try:
            if AGENT_CONFIG.get('log_to_ui'):
                push_agent_log(task_id, f"生成字幕: {video_path}", 'info', self.agent_id)
        except Exception:
            pass

        try:
            result = self.retry.with_retry(
                self.executor.execute_tool
            )("generate_subtitle", {
                "video_path": video_path,
                "script": script,
                "output_path": video_path.replace(".mp4", "_subtitled.mp4")
            })
        except Exception as e:
            return self._stream_or_return(f"字幕生成失败: {str(e)}", stream)

        if result.success:
            response = f"字幕生成成功！\n\n输出路径: {result.result.get('video_path')}"
        else:
            response = f"字幕生成失败: {result.error}"

        return self._stream_or_return(response, stream)

    def _handle_platform_request(self, message: str, task_id: str = None, stream: bool = False) -> Any:
        """处理平台适配请求"""
        platform = "抖音"
        for p in ["抖音", "小红书", "B站"]:
            if p in message:
                platform = p
                break

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
            return self._stream_or_return("请提供视频路径", stream)

        if not script_result:
            return self._stream_or_return("请先生成脚本", stream)

        try:
            if AGENT_CONFIG.get('log_to_ui'):
                push_agent_log(task_id, f"适配{platform}平台", 'info', self.agent_id)
        except Exception:
            pass

        try:
            result = self.retry.with_retry(
                self.executor.execute_tool
            )("adapt_platform_content", {
                "video_path": video_path,
                "script_result": script_result,
                "platform": platform
            })
        except Exception as e:
            return self._stream_or_return(f"平台适配失败: {str(e)}", stream)

        if result.success:
            adapted = result.result.get("adapted_content", {})
            response = f"平台适配成功！\n\n"
            response += f"**平台**: {platform}\n"
            response += f"**标题**: {adapted.get('title', '')}\n"
            response += f"**描述**: {adapted.get('description', '')}\n"
            response += f"**标签**: {', '.join(adapted.get('hashtags', []))}"
        else:
            response = f"平台适配失败: {result.error}"

        return self._stream_or_return(response, stream)

    def _handle_full_workflow(self, message: str, task_id: str = None, stream: bool = False) -> Any:
        """处理完整工作流"""
        try:
            if AGENT_CONFIG.get('log_to_ui'):
                push_agent_log(task_id, "开始完整生产流程", 'info', self.agent_id)
        except Exception:
            pass

        tid = task_id or str(uuid.uuid4())
        self.memory.start_task(tid, message)

        try:
            result = self.executor.execute_full_workflow(self.memory.current_task.collected_data)
        except Exception as e:
            return self._stream_or_return(f"生产流程失败: {str(e)}", stream)

        if result.get("success"):
            ctx = result.get("context", {})
            response = "✅ 视频生产完成！\n\n"
            response += f"**选题**: {ctx.get('topic', {}).get('title', '未知')}\n"
            response += f"**最终视频**: {ctx.get('final_video', '未知')}\n\n"
            response += "视频已生成并添加字幕，可以直接发布到平台。"
        else:
            response = f"生产失败: {result.get('error', '未知错误')}"

        return self._stream_or_return(response, stream)

    def _handle_general(self, message: str, task_id: str = None, stream: bool = False) -> Any:
        """处理通用请求"""
        try:
            if AGENT_CONFIG.get('log_to_ui'):
                push_agent_log(task_id, "通用推理中...", 'info', self.agent_id)
        except Exception:
            pass

        try:
            react_result = self.react.run(
                message,
                self.memory.short_term.get_conversation_format()
            )
        except Exception as e:
            return self._stream_or_return(f"推理失败: {str(e)}", stream)

        if react_result.get("success"):
            response = react_result.get("final", "")
            steps = react_result.get("steps", [])
            return {"success": True, "response": response, "steps": steps}
        else:
            response = f"处理失败: {react_result.get('error', '未知错误')}"
            return {"success": False, "response": response}

    def _stream_or_return(self, response: str, stream: bool):
        """根据stream参数返回或生成"""
        if stream:
            # 流式返回
            yield response
        else:
            return {"success": True, "response": response}

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
