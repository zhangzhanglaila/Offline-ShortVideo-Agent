# -*- coding: utf-8 -*-
"""
全局事件发射器 - 用于Agent日志推送到前端
"""
import threading
import queue
import time
from typing import Dict, List, Callable
from dataclasses import dataclass


@dataclass
class AgentLogEvent:
    """Agent日志事件"""
    task_id: str
    agent_id: str
    level: str  # info, success, error, warning
    message: str
    timestamp: str = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.strftime('%H:%M:%S')


class EventEmitter:
    """全局事件发射器（单例）"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._subscribers: Dict[str, List[Callable]] = {}  # event_type -> [callbacks]
        self._queues: Dict[str, queue.Queue] = {}  # 用于SSE推送
        self._lock = threading.Lock()
        self._initialized = True

    def subscribe(self, event_type: str, callback: Callable) -> None:
        """订阅事件"""
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            if callback not in self._subscribers[event_type]:
                self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callable) -> None:
        """取消订阅"""
        with self._lock:
            if event_type in self._subscribers:
                if callback in self._subscribers[event_type]:
                    self._subscribers[event_type].remove(callback)

    def emit(self, event_type: str, data: any) -> None:
        """发射事件"""
        with self._lock:
            if event_type in self._subscribers:
                for callback in self._subscribers[event_type]:
                    try:
                        callback(data)
                    except Exception as e:
                        print(f"Event callback error: {e}")

    def get_queue(self, queue_name: str) -> queue.Queue:
        """获取事件队列（用于SSE）"""
        with self._lock:
            if queue_name not in self._queues:
                self._queues[queue_name] = queue.Queue()
            return self._queues[queue_name]

    def put_event(self, queue_name: str, event: AgentLogEvent) -> None:
        """放入事件队列"""
        q = self.get_queue(queue_name)
        try:
            q.put_nowait(event)
        except queue.Full:
            pass  # 队列满时丢弃旧事件


# 全局事件发射器实例
_event_emitter = None


def get_event_emitter() -> EventEmitter:
    """获取全局事件发射器"""
    global _event_emitter
    if _event_emitter is None:
        _event_emitter = EventEmitter()
    return _event_emitter


# 全局日志推送函数（可在任何模块调用）
def push_agent_log(task_id: str, message: str, level: str = 'info', agent_id: str = 'default'):
    """
    全局推送Agent日志

    用法:
        from agent.core.event_emitter import push_agent_log
        push_agent_log('task-123', '开始处理视频', 'info')
        push_agent_log('task-123', '视频生成成功', 'success')
        push_agent_log('task-123', '连接失败', 'error')
    """
    emitter = get_event_emitter()
    event = AgentLogEvent(
        task_id=task_id,
        agent_id=agent_id,
        level=level,
        message=message
    )

    # 放入队列供SSE消费
    emitter.put_event('agent_logs', event)

    # 触发回调
    emitter.emit('agent_log', event)
