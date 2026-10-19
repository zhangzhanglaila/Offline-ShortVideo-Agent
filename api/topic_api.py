# -*- coding: utf-8 -*-
"""
选题API路由
"""
import sys
import os
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

router = APIRouter()

CATEGORY_ICONS = {
    "知识付费": "💡",
    "美食探店": "🍜",
    "生活方式": "🌿",
    "情感心理": "💝",
    "科技数码": "💻",
    "娱乐搞笑": "🎮",
}


def get_topics_module():
    """获取选题模块单例"""
    from core.topics_module import TopicsModule
    return TopicsModule(
        enable_cache=config.CACHE_CONFIG.get("enabled", True),
        preload_count=config.CACHE_CONFIG.get("preload_count", 500)
    )


@router.get("/api/categories")
async def api_categories():
    """获取赛道分类"""
    categories = config.CATEGORIES
    return JSONResponse([
        {'name': name, 'icon': CATEGORY_ICONS.get(name, '📁')}
        for name in categories.keys()
    ])


@router.get("/api/topics")
async def api_topics(
    limit: int = 20,
    offset: int = 0,
    category: str = '',
    keyword: str = ''
):
    """获取选题列表"""
    try:
        topics = get_topics_module()

        if keyword:
            topic_list = topics.search_topics(keyword, limit + offset)
            topic_list = topic_list[offset:offset + limit]
        elif category and category != 'all':
            topic_list = topics.get_topics_by_category(category, limit + offset)
            topic_list = topic_list[offset:offset + limit]
        else:
            topic_list = topics.get_all_topics(limit + offset)
            topic_list = topic_list[offset:offset + limit]

        return JSONResponse({'topics': topic_list})
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@router.get("/api/topics/recommend")
async def api_recommend(category: str = '', count: int = 5):
    """智能推荐选题"""
    try:
        topics = get_topics_module()
        result = topics.recommend_topics(
            category=category if category and category != 'all' else None,
            count=count
        )
        return JSONResponse({'topics': result})
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


class ExpandLibraryRequest(BaseModel):
    """扩充选题库请求"""
    target: int = 1500


@router.post("/api/library/expand")
def api_library_expand(req: ExpandLibraryRequest = None):
    """扩充选题库（联网爬取 + 规则扩充）。

    注意：这里必须是同步 def —— 内部 expand_library() 使用 asyncio.run()，
    在 async 端点里调用会抛 "asyncio.run() cannot be called from a running event loop"。
    FastAPI 会把同步端点放进线程池，不会阻塞事件循环。
    """
    try:
        target_count = max(1, int((req.target if req else 1500)))
        topics = get_topics_module()
        result = topics.expand_library(target_count)

        return JSONResponse({
            'success': True,
            'before': result.get('before', 0),
            'after': result.get('after', 0),
            'generated': result.get('generated', 0)
        })
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)
