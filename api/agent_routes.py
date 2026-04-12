# -*- coding: utf-8 -*-
"""
Agent API路由
"""
from flask import Blueprint, request, jsonify, session
import uuid
import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.agent import Agent

agent_bp = Blueprint('agent', __name__, url_prefix='/api/agent')

# 全局Agent实例
_agent = None


def get_agent() -> Agent:
    """获取Agent实例"""
    global _agent
    if _agent is None:
        _agent = Agent()
    return _agent


@agent_bp.route('/chat', methods=['POST'])
def chat():
    """Agent对话接口"""
    data = request.get_json() or {}
    user_message = data.get('message', '')
    session_id = data.get('session_id') or session.get('agent_session_id')

    agent = get_agent()

    # 创建或恢复session
    if session_id:
        agent.restore_session(session_id)
    else:
        session_id = str(uuid.uuid4())
        session['agent_session_id'] = session_id
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
