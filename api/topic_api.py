# -*- coding: utf-8 -*-
"""
选题API路由
"""
import sys
import os
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

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


@router.post("/api/library/expand")
async def api_library_expand(request: Request):
    """扩充选题库"""
    try:
        data = await request.json()
        target_count = data.get('target', 1000)

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
