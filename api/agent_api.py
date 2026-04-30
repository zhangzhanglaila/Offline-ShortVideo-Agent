# -*- coding: utf-8 -*-
"""
Agent API路由 - 对话、任务、会话、工具、MCP、认证
"""
import sys
import os
import json
import uuid
import asyncio
import queue
from typing import Optional
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse, JSONResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

router = APIRouter()

# 全局Agent实例
_agent = None
_mcp_handler = None


def get_agent():
    """获取Agent实例"""
    global _agent
    if _agent is None:
        from agent.agent import Agent
        _agent = Agent()
    return _agent


def get_mcp_handler():
    """获取MCP处理器"""
    global _mcp_handler
    if _mcp_handler is None:
        from agent.core.mcp_protocol import create_mcp_handler
        _mcp_handler = create_mcp_handler(get_agent())
    return _mcp_handler


@router.post("/api/agent/chat")
async def chat(request: Request):
    """非流式对话"""
    try:
        data = await request.json()
        user_message = data.get('message', '')
        session_id = data.get('session_id')

        agent = get_agent()

        if session_id:
            agent.restore_session(session_id)
        else:
            session_id = str(uuid.uuid4())
            agent.start_session(session_id)

        result = agent.chat(user_message)

        return JSONResponse({
            'session_id': session_id,
            'response': result['response'],
            'success': result['success'],
            'steps': result.get('steps', []),
            'context': result.get('context', {})
        })
    except ConnectionError as e:
        return JSONResponse({
            'success': False,
            'response': str(e),
            'error': str(e)
        })
    except Exception as e:
        return JSONResponse({
            'success': False,
            'response': f"服务器错误: {str(e)}",
            'error': str(e)
        }, status_code=500)


@router.post("/api/agent/chat/stream")
async def chat_stream(request: Request):
    """SSE流式对话"""
    # 在async函数中先获取JSON数据
    try:
        json_data = await request.json()
    except Exception:
        json_data = {}

    user_message = json_data.get('message', '')
    session_id = json_data.get('session_id')

    def generate():
        """同步生成器 - 内部访问外层捕获的变量"""
        try:
            agent = get_agent()

            # 前置检查：判断模型可用性
            _, _, immediate_error = agent.llm._get_error_info()

            if immediate_error:
                sid = session_id or str(uuid.uuid4())
                try:
                    yield f"data: {json.dumps({'type': 'session', 'session_id': sid}, ensure_ascii=False)}\n\n"
                except Exception:
                    pass
                try:
                    yield f"data: {json.dumps({'type': 'error', 'error': immediate_error}, ensure_ascii=False)}\n\n"
                except Exception:
                    pass
                try:
                    yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                except Exception:
                    pass
                return

            if session_id:
                try:
                    agent.restore_session(session_id)
                except Exception:
                    sid = str(uuid.uuid4())
                    agent.start_session(sid)
            else:
                sid = str(uuid.uuid4())
                agent.start_session(sid)

            # 发送session_id
            try:
                yield f"data: {json.dumps({'type': 'session', 'session_id': sid}, ensure_ascii=False)}\n\n"
            except Exception:
                pass

            # 流式执行
            try:
                for chunk in agent.chat_stream(user_message):
                    try:
                        if chunk and (
                            chunk.startswith("本地模型") or
                            chunk.startswith("未检测到") or
                            chunk.startswith("未配置云端") or
                            chunk.startswith("抱歉") or
                            chunk.startswith("API密钥") or
                            chunk.startswith("API请求失败") or
                            chunk.startswith("无法连接到")
                        ):
                            yield f"data: {json.dumps({'type': 'error', 'error': chunk}, ensure_ascii=False)}\n\n"
                        else:
                            yield f"data: {json.dumps({'type': 'content', 'content': chunk}, ensure_ascii=False)}\n\n"
                    except Exception:
                        break
            except Exception as e:
                error_msg = str(e)
                yield f"data: {json.dumps({'type': 'error', 'error': error_msg}, ensure_ascii=False)}\n\n"
                return

            # 发送完成
            try:
                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            except Exception:
                pass

        except Exception as e:
            try:
                yield f"data: {json.dumps({'type': 'error', 'error': f'服务器错误: {str(e)}'}, ensure_ascii=False)}\n\n"
            except Exception:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.post("/api/agent/task/submit")
async def submit_task(request: Request):
    """提交异步任务"""
    try:
        data = await request.json()
        task_type = data.get('task_type')
        params = data.get('params', {})

        agent = get_agent()
        task_id = agent.submit_task(task_type, params)

        return JSONResponse({
            'success': True,
            'task_id': task_id
        })
    except Exception as e:
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.get("/api/agent/task/{task_id}/status")
async def get_task_status(task_id: str):
    """获取任务状态"""
    try:
        from agent.core.task_queue import get_task_queue
        queue = get_task_queue()
        status = queue.get_status(task_id)

        if status:
            return JSONResponse(status)
        return JSONResponse({'error': 'Task not found'}, status_code=404)
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@router.get("/api/agent/sessions")
async def list_sessions():
    """列出所有历史会话"""
    try:
        agent = get_agent()
        sessions = agent.list_sessions()
        return JSONResponse({'success': True, 'data': {'sessions': sessions}})
    except Exception as e:
        return JSONResponse({'success': False, 'error': str(e)}, status_code=500)


@router.get("/api/agent/session/{session_id}")
async def get_session(session_id: str):
    """获取会话信息"""
    try:
        agent = get_agent()
        session_data = agent.get_session_info(session_id)

        if session_data:
            # 同时加载对话历史
            try:
                agent.restore_session(session_id)
                messages = agent.memory.short_term.get_conversation_format()
                session_data['messages'] = messages
            except Exception:
                pass
            return JSONResponse({'success': True, 'data': session_data})
        return JSONResponse({'success': False, 'error': 'Session not found'}, status_code=404)
    except Exception as e:
        return JSONResponse({'success': False, 'error': str(e)}, status_code=500)


@router.delete("/api/agent/session/{session_id}")
async def delete_session(session_id: str):
    """删除会话"""
    try:
        agent = get_agent()
        agent.end_session(session_id)
        return JSONResponse({'success': True})
    except Exception as e:
        return JSONResponse({'success': False, 'error': str(e)}, status_code=500)


@router.get("/api/agent/tools")
async def list_tools():
    """获取可用工具列表"""
    try:
        agent = get_agent()
        tools = [t.definition.to_openai_format() for t in agent.tools]
        return JSONResponse({'tools': tools})
    except Exception as e:
        return JSONResponse({'tools': [], 'error': str(e)}, status_code=500)


@router.get("/api/agent/logs/stream")
async def agent_logs_stream():
    """Agent专用SSE日志流"""
    async def generator():
        from agent.core.event_emitter import get_event_emitter

        emitter = get_event_emitter()
        q = emitter.get_queue('agent_logs')

        try:
            yield f"data: {json.dumps({'type': 'connected'}, ensure_ascii=False)}\n\n"
        except Exception:
            pass

        try:
            while True:
                try:
                    loop = asyncio.get_running_loop()
                    event = await loop.run_in_executor(None, q.get, True, 30)
                    log_data = {
                        'type': 'agent_log',
                        'task_id': event.task_id,
                        'time': event.timestamp,
                        'msg': event.message,
                        'level': event.level
                    }
                    yield f"data: {json.dumps(log_data, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield f"data: {json.dumps({'type': 'ping'}, ensure_ascii=False)}\n\n"
        except GeneratorExit:
            pass
        except Exception:
            pass

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.post("/api/agent/config/update")
async def update_config(request: Request):
    """更新配置文件（写入.env）"""
    try:
        data = await request.json()

        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')

        # 读取现有.env内容（如果存在）
        env_vars = {}
        if os.path.exists(env_path):
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        env_vars[k] = v

        # 更新配置
        if 'OPENAI_API_KEY' in data:
            env_vars['OPENAI_API_KEY'] = data['OPENAI_API_KEY']
        if 'OPENAI_API_BASE' in data:
            env_vars['OPENAI_API_BASE'] = data['OPENAI_API_BASE']
        if 'OPENAI_MODEL' in data:
            env_vars['OPENAI_API_MODEL'] = data['OPENAI_MODEL']

        # 写入.env文件
        with open(env_path, 'w', encoding='utf-8') as f:
            f.write('# MiniMax API 配置\n')
            for k, v in env_vars.items():
                f.write(f'{k}={v}\n')

        # 立即更新当前进程环境变量
        if 'OPENAI_API_KEY' in data:
            os.environ['OPENAI_API_KEY'] = data['OPENAI_API_KEY']
        if 'OPENAI_API_BASE' in data:
            os.environ['OPENAI_API_BASE'] = data['OPENAI_API_BASE']
        if 'OPENAI_MODEL' in data:
            os.environ['OPENAI_API_MODEL'] = data['OPENAI_MODEL']

        from agent.llm.ollama_client import reset_llm_client
        reset_llm_client()

        return JSONResponse({'success': True, 'message': '配置已更新'})
    except Exception as e:
        return JSONResponse({'success': False, 'error': str(e)}, status_code=500)


# 前端调用路由 /api/config/update（别名）
@router.post("/api/config/update")
async def update_config_alias(request: Request):
    """更新配置文件（别名路由，供前端调用）"""
    return await update_config(request)


# ========== MCP协议端点 ==========

@router.post("/api/agent/mcp")
async def mcp_request(request: Request):
    """MCP协议请求"""
    try:
        data = await request.json()
        method = data.get('method')
        params = data.get('params', {})
        params['_id'] = data.get('id', 1)

        handler = get_mcp_handler()
        result = handler.handle_request(method, params)

        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32603,
                "message": str(e)
            }
        }, status_code=500)


@router.post("/api/agent/mcp/notification")
async def mcp_notification(request: Request):
    """MCP通知接口"""
    try:
        data = await request.json()
        method = data.get('method')
        params = data.get('params', {})

        handler = get_mcp_handler()
        handler.handle_notification(method, params)

        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


# ========== 用户认证端点 ==========

@router.post("/api/agent/auth/register")
async def register(request: Request):
    """用户注册"""
    if not config.AGENT_CONFIG.get('enable_multi_user', False):
        return JSONResponse({'success': False, 'error': '多用户功能未启用'}, status_code=403)

    try:
        data = await request.json()
        username = data.get('username')
        password = data.get('password')

        if not username or not password:
            return JSONResponse({'success': False, 'error': '用户名和密码不能为空'}, status_code=400)

        from agent.multi_user import get_user_manager
        user_manager = get_user_manager()
        result = user_manager.register(username, password)

        if result.get('success'):
            return JSONResponse(result)
        return JSONResponse(result, status_code=400)
    except Exception as e:
        return JSONResponse({'success': False, 'error': str(e)}, status_code=500)


@router.post("/api/agent/auth/login")
async def login(request: Request):
    """用户登录"""
    if not config.AGENT_CONFIG.get('enable_multi_user', False):
        return JSONResponse({'success': False, 'error': '多用户功能未启用'}, status_code=403)

    try:
        data = await request.json()
        username = data.get('username')
        password = data.get('password')

        if not username or not password:
            return JSONResponse({'success': False, 'error': '用户名和密码不能为空'}, status_code=400)

        from agent.multi_user import get_user_manager
        user_manager = get_user_manager()
        token = user_manager.authenticate(username, password)

        if token:
            return JSONResponse({'success': True, 'token': token})
        return JSONResponse({'success': False, 'error': '用户名或密码错误'}, status_code=401)
    except Exception as e:
        return JSONResponse({'success': False, 'error': str(e)}, status_code=500)
