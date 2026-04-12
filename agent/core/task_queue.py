# -*- coding: utf-8 -*-
"""
异步任务队列 - 支持视频处理等耗时任务的异步执行
"""
import threading
import queue
import time
import uuid
from typing import Dict, Any, Callable, Optional
from dataclasses import dataclass, field
from enum import Enum
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config import AGENT_CONFIG


class TaskStatus(Enum):
    """任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskInfo:
    """任务信息"""
    task_id: str
    task_fn: Callable
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    result: Any = None
    error: str = None
    progress: float = 0.0
    progress_msg: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float = None
    completed_at: float = None


class TaskQueue:
    """异步任务队列"""

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
        self._tasks: Dict[str, TaskInfo] = {}
        self._result_queues: Dict[str, queue.Queue] = {}
        self._tasks_lock = threading.Lock()
        self._initialized = True

    def submit(self, task_fn: Callable, *args, task_id: str = None, **kwargs) -> str:
        """提交任务，返回task_id"""
        if task_id is None:
            task_id = str(uuid.uuid4())

        task_info = TaskInfo(
            task_id=task_id,
            task_fn=task_fn,
            args=args,
            kwargs=kwargs
        )

        with self._tasks_lock:
            self._tasks[task_id] = task_info
            self._result_queues[task_id] = queue.Queue()

        # 后台线程执行
        thread = threading.Thread(
            target=self._run_task,
            args=(task_id,),
            daemon=True
        )
        thread.start()

        return task_id

    def submit_with_progress(self, task_fn: Callable, *args,
                            progress_callback: Callable = None, **kwargs) -> str:
        """提交带进度回调的任务"""
        task_id = str(uuid.uuid4())

        def wrapped_fn():
            def update_progress(progress: float, msg: str = ""):
                with self._tasks_lock:
                    if task_id in self._tasks:
                        self._tasks[task_id].progress = progress
                        self._tasks[task_id].progress_msg = msg
                if progress_callback:
                    progress_callback(progress, msg)

            # 将进度回调注入kwargs
            kwargs['_progress_callback'] = update_progress
            return task_fn(*args, **kwargs)

        return self.submit(wrapped_fn, task_id=task_id)

    def _run_task(self, task_id: str):
        """执行任务"""
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()

        try:
            result = task.task_fn(*task.args, **task.kwargs)

            with self._tasks_lock:
                task.result = result
                task.status = TaskStatus.COMPLETED
                task.completed_at = time.time()
                task.progress = 1.0

            # 通知等待者
            if task_id in self._result_queues:
                self._result_queues[task_id].put_nowait({
                    'status': 'completed',
                    'result': result
                })

        except Exception as e:
            with self._tasks_lock:
                task.error = str(e)
                task.status = TaskStatus.FAILED
                task.completed_at = time.time()

            if task_id in self._result_queues:
                self._result_queues[task_id].put_nowait({
                    'status': 'failed',
                    'error': str(e)
                })

    def get_status(self, task_id: str) -> Optional[Dict]:
        """获取任务状态"""
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if not task:
                return None

            return {
                'task_id': task_id,
                'status': task.status.value,
                'progress': task.progress,
                'progress_msg': task.progress_msg,
                'result': task.result,
                'error': task.error,
                'created_at': task.created_at,
                'started_at': task.started_at,
                'completed_at': task.completed_at
            }

    def get_result(self, task_id: str, timeout: float = None) -> Dict:
        """获取任务结果（阻塞等待）"""
        if task_id not in self._result_queues:
            status = self.get_status(task_id)
            if not status:
                return {'status': 'not_found'}
            if status['status'] == 'completed':
                return {'status': 'completed', 'result': status['result']}
            if status['status'] == 'failed':
                return {'status': 'failed', 'error': status['error']}

        result_queue = self._result_queues[task_id]
        try:
            return result_queue.get(timeout=timeout)
        except queue.Empty:
            return {'status': 'timeout'}

    def cancel(self, task_id: str) -> bool:
        """取消任务"""
        with self._tasks_lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if task.status == TaskStatus.RUNNING:
                return False  # 正在运行的任务无法取消
            task.status = TaskStatus.CANCELLED
            return True

    def list_tasks(self, status: TaskStatus = None) -> list:
        """列出任务"""
        with self._tasks_lock:
            tasks = list(self._tasks.values())
            if status:
                tasks = [t for t in tasks if t.status == status]
            return [{
                'task_id': t.task_id,
                'status': t.status.value,
                'progress': t.progress,
                'progress_msg': t.progress_msg,
                'created_at': t.created_at
            } for t in tasks]

    def clear_completed(self):
        """清理已完成任务"""
        with self._tasks_lock:
            completed_ids = [
                tid for tid, t in self._tasks.items()
                if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
            ]
            for tid in completed_ids:
                del self._tasks[tid]
                if tid in self._result_queues:
                    del self._result_queues[tid]


# 全局单例
_task_queue = None


def get_task_queue() -> TaskQueue:
    """获取任务队列单例"""
    global _task_queue
    if _task_queue is None:
        _task_queue = TaskQueue()
    return _task_queue
