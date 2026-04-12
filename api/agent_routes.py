# -*- coding: utf-8 -*-
"""
Agent API路由
"""
from flask import Blueprint, request, jsonify, Response
import uuid
import sys
import os
import json

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.agent import Agent
from agent.core.event_emitter import push_agent_log, get_event_emitter
from agent.core.mcp_protocol import MCPProtocol, create_mcp_handler
from agent.multi_user import get_user_manager
from config import AGENT_CONFIG

agent_bp = Blueprint('agent', __name__, url_prefix='/api/agent')

# 全局Agent实例
_agent = None

# MCP处理器
_mcp_handler = None


def get_agent() -> Agent:
    """获取Agent实例"""
    global _agent
    if _agent is None:
        try:
            _agent = Agent()
        except Exception as e:
            raise RuntimeError(f"Agent初始化失败: {str(e)}。请确保Ollama服务已启动 (ollama serve)")
    return _agent


def get_mcp_handler():
    """获取MCP处理器"""
    global _mcp_handler
    if _mcp_handler is None:
        _mcp_handler = create_mcp_handler(get_agent())
    return _mcp_handler


def require_auth(f):
    """认证装饰器（可选启用）"""
    def wrapper(*args, **kwargs):
        if not AGENT_CONFIG.get('enable_multi_user', False):
            return f(*args, **kwargs)

        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        user_manager = get_user_manager()
        user_id = user_manager.verify_token(token)

        if not user_id:
            return jsonify({'error': 'Unauthorized'}), 401

        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper
    return _agent


@agent_bp.route('/chat', methods=['POST'])
def chat():
    """Agent对话接口"""
    try:
        data = request.get_json() or {}
        user_message = data.get('message', '')
        session_id = data.get('session_id')

        agent = get_agent()

        # 创建或恢复session
        if session_id:
            agent.restore_session(session_id)
        else:
            session_id = str(uuid.uuid4())
            agent.start_session(session_id)

        # 执行对话
        result = agent.chat(user_message)

        return jsonify({
            'session_id': session_id,
            'response': result['response'],
            'success': result['success'],
            'steps': result.get('steps', []),
            'context': result.get('context', {})
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'response': f'服务器错误: {str(e)}',
            'error': str(e)
        }), 500


@agent_bp.route('/chat/stream', methods=['POST'])
def chat_stream():
    """Agent流式对话接口 (SSE)"""
    def generate():
        try:
            data = request.get_json or (lambda: {})()
            user_message = data.get('message', '') if callable(data.get) else ''
            session_id = data.get('session_id')

            agent = get_agent()

            # 创建或恢复session
            if session_id:
                agent.restore_session(session_id)
            else:
                session_id = str(uuid.uuid4())
                agent.start_session(session_id)

            # 发送session_id
            yield f"data: {json.dumps({'type': 'session', 'session_id': session_id}, ensure_ascii=False)}\n\n"

            # 流式执行
            for chunk in agent.chat_stream(user_message):
                yield f"data: {json.dumps({'type': 'content', 'content': chunk}, ensure_ascii=False)}\n\n"

            # 发送完成
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'
    })


@agent_bp.route('/task/submit', methods=['POST'])
def submit_task():
    """提交异步任务"""
    try:
        data = request.get_json() or {}
        task_type = data.get('task_type')  # 'video', 'subtitle', etc.
        params = data.get('params', {})

        agent = get_agent()
        task_id = agent.submit_task(task_type, params)

        return jsonify({
            'success': True,
            'task_id': task_id
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@agent_bp.route('/task/<task_id>/status', methods=['GET'])
def get_task_status(task_id):
    """获取任务状态"""
    from agent.core.task_queue import get_task_queue

    queue = get_task_queue()
    status = queue.get_status(task_id)

    if status:
        return jsonify(status)
    return jsonify({'error': 'Task not found'}), 404


@agent_bp.route('/session/<session_id>', methods=['GET'])
def get_session(session_id):
    """获取会话状态"""
    agent = get_agent()
    session_data = agent.get_session_info(session_id)

    if session_data:
        return jsonify(session_data)
    return jsonify({'error': 'Session not found'}), 404


@agent_bp.route('/session/<session_id>', methods=['DELETE'])
def delete_session(session_id):
    """删除会话"""
    agent = get_agent()
    agent.end_session(session_id)
    return jsonify({'success': True})


@agent_bp.route('/tools', methods=['GET'])
def list_tools():
    """获取可用工具列表"""
    agent = get_agent()
    tools = [t.definition.to_openai_format() for t in agent.tools]
    return jsonify({'tools': tools})


# ========== MCP协议端点 ==========

@agent_bp.route('/mcp', methods=['POST'])
def mcp_request():
    """
    MCP协议请求接口

    支持标准MCP方法：
    - tools/list: 列出工具
    - tools/call: 调用工具
    - resources/list: 列出资源
    - resources/read: 读取资源
    """
    try:
        data = request.get_json()
        method = data.get('method')
        params = data.get('params', {})

        # 添加id用于响应匹配
        params['_id'] = data.get('id', 1)

        handler = get_mcp_handler()
        result = handler.handle_request(method, params)

        return jsonify(result)

    except Exception as e:
        return jsonify({
            "jsonrpc": "2.0",
            "id": 1,
            "error": {
                "code": -32603,
                "message": str(e)
            }
        }), 500


@agent_bp.route('/mcp/notification', methods=['POST'])
def mcp_notification():
    """MCP通知接口（无响应）"""
    try:
        data = request.get_json()
        method = data.get('method')
        params = data.get('params', {})

        handler = get_mcp_handler()
        handler.handle_notification(method, params)

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ========== 用户认证端点 ==========

@agent_bp.route('/auth/register', methods=['POST'])
def register():
    """用户注册"""
    if not AGENT_CONFIG.get('enable_multi_user', False):
        return jsonify({'success': False, 'error': '多用户功能未启用'}), 403

    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({'success': False, 'error': '用户名和密码不能为空'}), 400

    user_manager = get_user_manager()
    result = user_manager.register(username, password)

    if result.get('success'):
        return jsonify(result)
    return jsonify(result), 400


@agent_bp.route('/auth/login', methods=['POST'])
def login():
    """用户登录"""
    if not AGENT_CONFIG.get('enable_multi_user', False):
        return jsonify({'success': False, 'error': '多用户功能未启用'}), 403

    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({'success': False, 'error': '用户名和密码不能为空'}), 400

    user_manager = get_user_manager()
    token = user_manager.authenticate(username, password)

    if token:
        return jsonify({'success': True, 'token': token})
    return jsonify({'success': False, 'error': '用户名或密码错误'}), 401


@agent_bp.route('/logs/stream')
def logs_stream():
    """Agent专用SSE日志流"""
    def generate():
        emitter = get_event_emitter()
        q = emitter.get_queue('agent_logs')

        # 发送初始连接消息
        yield f"data: {json.dumps({'type': 'connected'}, ensure_ascii=False)}\n\n"

        try:
            while True:
                try:
                    event = q.get(timeout=30)
                    log_data = {
                        'type': 'agent_log',
                        'task_id': event.task_id,
                        'time': event.timestamp,
                        'msg': event.message,
                        'level': event.level
                    }
                    yield f"data: {json.dumps(log_data, ensure_ascii=False)}\n\n"
                except:
                    # 超时发送ping保持连接
                    yield f"data: {json.dumps({'type': 'ping'}, ensure_ascii=False)}\n\n"
        except GeneratorExit:
            pass

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'
    })
