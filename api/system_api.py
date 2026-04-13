# -*- coding: utf-8 -*-
"""
系统API路由 - SSE日志流、配置、统计
"""
import sys
import os
import json
import time
import queue
import asyncio
import platform
import threading
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import StreamingResponse, JSONResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

router = APIRouter()

# SSE客户端管理 - 使用线程安全的队列
_log_queue = queue.Queue()
_log_clients = []
_clients_lock = threading.Lock()


def push_log(msg: str, level: str = 'info'):
    """推送日志到所有SSE客户端"""
    entry = {'time': time.strftime('%H:%M:%S'), 'msg': msg, 'level': level}
    # 广播到所有客户端
    with _clients_lock:
        for q in _log_clients:
            try:
                q.put_nowait(entry)
            except queue.Full:
                pass


def init_agent_event_listener():
    """监听Agent事件并推送到前端"""
    try:
        from agent.core.event_emitter import get_event_emitter, AgentLogEvent

        def on_agent_log(event: AgentLogEvent):
            entry = {
                'type': 'agent_log',
                'task_id': event.task_id,
                'time': event.timestamp,
                'msg': event.message,
                'level': event.level
            }
            with _clients_lock:
                for client_q in _log_clients:
                    try:
                        client_q.put_nowait(entry)
                    except queue.Full:
                        pass

        emitter = get_event_emitter()
        emitter.subscribe('agent_log', on_agent_log)
    except Exception as e:
        print(f"Agent事件监听器初始化失败: {e}")


# 启动事件监听器
init_agent_event_listener()


@router.get("/api/logs/stream")
async def log_stream():
    """SSE日志流"""
    client_queue = queue.Queue()
    with _clients_lock:
        _log_clients.append(client_queue)

    # 在async函数外部获取事件循环
    loop = asyncio.get_event_loop()

    async def generator():
        try:
            # 立即发送连接确认
            yield f"data: {json.dumps({'type': 'connected'}, ensure_ascii=False)}\n\n"
            while True:
                try:
                    # 使用线程池执行阻塞的队列 get 操作
                    entry = await loop.run_in_executor(
                        None, lambda q=client_queue: q.get(timeout=3)
                    )
                    yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        except GeneratorExit:
            pass
        finally:
            with _clients_lock:
                if client_queue in _log_clients:
                    _log_clients.remove(client_queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@router.get("/api/config")
async def api_config():
    """返回前端配置"""
    system = platform.system()
    material_path = str(config.MATERIAL_DIR).replace('\\', '/')
    if system == 'Windows':
        material_url = 'file:///' + material_path
    else:
        material_url = 'file://' + material_path

    return JSONResponse({
        'material_dir': material_url,
        'material_path': str(config.MATERIAL_DIR)
    })


@router.get("/api/stats")
async def api_stats():
    """获取系统统计"""
    try:
        from core.topics_module import TopicsModule

        topics_module = TopicsModule(
            enable_cache=config.CACHE_CONFIG.get("enabled", True),
            preload_count=config.CACHE_CONFIG.get("preload_count", 500)
        )
        stats = topics_module.get_statistics()

        works_count = 0
        output_dir = Path(config.OUTPUT_DIR)
        if output_dir.exists():
            for platform_dir in output_dir.iterdir():
                if platform_dir.is_dir():
                    works_count += len(list(platform_dir.rglob('*.mp4')))

        cache_stats = stats.get('cache_stats', {})
        hit_rate = cache_stats.get('hit_rate', '0%')

        return JSONResponse({
            'topics_count': stats.get('total', 0),
            'cache_hit_rate': hit_rate,
            'cache_size': cache_stats.get('size', 0),
            'works_count': works_count,
            'category_stats': stats.get('by_category', {}),
            'cache_stats': cache_stats,
        })
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@router.post("/api/cache/clear")
async def api_cache_clear():
    """清空缓存"""
    try:
        from core.topics_module import TopicsModule

        topics_module = TopicsModule(
            enable_cache=config.CACHE_CONFIG.get("enabled", True),
            preload_count=config.CACHE_CONFIG.get("preload_count", 500)
        )
        topics_module.invalidate_cache()
        return JSONResponse({'success': True, 'message': '缓存已清空'})
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@router.get("/api/file/open")
async def api_file_open(path: str = None):
    """打开文件位置"""
    try:
        if path:
            file_path = Path(path)
            if file_path.exists():
                if sys.platform == 'win32':
                    os.startfile(str(file_path.parent))
                else:
                    os.system(f'open "{file_path.parent}"')
        return JSONResponse({'success': True})
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)
