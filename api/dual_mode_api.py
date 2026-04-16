# -*- coding: utf-8 -*-
"""
双模式生成API路由
"""
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

router = APIRouter()


def get_generator():
    """获取双模式生成器"""
    from core.dual_mode_module import get_dual_mode_generator
    return get_dual_mode_generator()


@router.post("/api/generate/mode-a")
async def api_generate_mode_a(request: Request):
    """
    模式A：题材全自动生成

    请求体:
    {
        "topic_keyword": "AI变现" 或 null,
        "category": "知识付费" 或 null,
        "platform": "抖音",
        "duration": 30,
        "voice": "zh-CN-XiaoxiaoNeural",
        "use_whisper_subtitle": true,
        "add_bgm": true,
        "fetch_images": true
    }
    """
    try:
        data = await request.json()

        topic_keyword = data.get("topic_keyword")
        category = data.get("category")
        platform = data.get("platform", "抖音")
        duration = data.get("duration", 30)
        voice = data.get("voice", "zh-CN-XiaoxiaoNeural")
        use_whisper = data.get("use_whisper_subtitle", True)
        add_bgm = data.get("add_bgm", True)
        fetch_images = data.get("fetch_images", True)

        generator = get_generator()

        result = generator.generate_mode_a(
            topic_keyword=topic_keyword,
            category=category,
            platform=platform,
            duration=duration,
            voice=voice,
            use_whisper_subtitle=use_whisper,
            add_bgm=add_bgm,
            fetch_images=fetch_images,
        )

        return JSONResponse(result)

    except Exception as e:
        import traceback
        return JSONResponse({
            "success": False,
            "error": str(e),
            "trace": traceback.format_exc()
        }, status_code=500)


@router.post("/api/generate/mode-b")
async def api_generate_mode_b(request: Request):
    """
    模式B：素材智能剪辑

    请求体:
    {
        "material_paths": ["path/to/img1.jpg", "path/to/img2.png"],
        "platform": "抖音",
        "transition": "fade",
        "add_bgm": true,
        "add_subtitles": true,
        "use_whisper": false,
        "duration_per_image": 4
    }
    """
    try:
        data = await request.json()

        material_paths = data.get("material_paths", [])
        platform = data.get("platform", "抖音")
        transition = data.get("transition", "fade")
        add_bgm = data.get("add_bgm", True)
        add_subtitles = data.get("add_subtitles", True)
        use_whisper = data.get("use_whisper", False)
        duration_per_image = data.get("duration_per_image", 4)

        if not material_paths:
            return JSONResponse({
                "success": False,
                "error": "素材路径不能为空"
            }, status_code=400)

        generator = get_generator()

        result = generator.generate_mode_b(
            material_paths=material_paths,
            platform=platform,
            transition=transition,
            add_bgm=add_bgm,
            add_subtitles=add_subtitles,
            use_whisper=use_whisper,
            duration_per_image=duration_per_image,
        )

        return JSONResponse(result)

    except Exception as e:
        import traceback
        return JSONResponse({
            "success": False,
            "error": str(e),
            "trace": traceback.format_exc()
        }, status_code=500)


@router.get("/api/modes/info")
async def api_modes_info():
    """获取双模式说明"""
    return JSONResponse({
        "modes": {
            "mode_a": {
                "name": "题材全自动生成",
                "description": "输入题材关键词 → 联网选题 → AI脚本 → TTS配音 → 自动配图 → 动画视频 → 字幕 → 多轨道合成",
                "params": [
                    {"name": "topic_keyword", "type": "string", "required": False, "description": "题材关键词"},
                    {"name": "category", "type": "string", "required": False, "description": "赛道分类"},
                    {"name": "platform", "type": "string", "required": False, "default": "抖音"},
                    {"name": "duration", "type": "int", "required": False, "default": 30},
                    {"name": "voice", "type": "string", "required": False, "default": "zh-CN-XiaoxiaoNeural"},
                    {"name": "use_whisper_subtitle", "type": "bool", "required": False, "default": True},
                    {"name": "add_bgm", "type": "bool", "required": False, "default": True},
                    {"name": "fetch_images", "type": "bool", "required": False, "default": True}
                ]
            },
            "mode_b": {
                "name": "素材智能剪辑",
                "description": "上传图片/视频/音频 → 素材拼接 → 转场 → 字幕 → 多轨道合成（不生成新脚本和配音）",
                "params": [
                    {"name": "material_paths", "type": "list", "required": True, "description": "素材路径列表"},
                    {"name": "platform", "type": "string", "required": False, "default": "抖音"},
                    {"name": "transition", "type": "string", "required": False, "default": "fade"},
                    {"name": "add_bgm", "type": "bool", "required": False, "default": True},
                    {"name": "add_subtitles", "type": "bool", "required": False, "default": True},
                    {"name": "use_whisper", "type": "bool", "required": False, "default": False},
                    {"name": "duration_per_image", "type": "int", "required": False, "default": 4}
                ]
            }
        },
        "voices": [
            {"value": "zh-CN-XiaoxiaoNeural", "label": "晓晓（女声-年轻）"},
            {"value": "zh-CN-YunxiNeural", "label": "云希（男声-年轻）"},
            {"value": "zh-CN-YunyangNeural", "label": "云扬（男声-新闻）"},
            {"value": "zh-CN-Xiaoyi", "label": "小艺（女声-温柔）"},
            {"value": "zh-CN-Zhiyu", "label": "志宇（男声-成熟）"},
            {"value": "zh-CN-Xiaomo", "label": "小墨（女声-活力）"},
        ],
        "categories": list(config.CATEGORIES.keys()),
        "platforms": ["抖音", "小红书", "B站"],
        "transitions": ["fade", "dissolve", "wipe", "none"]
    })


@router.post("/api/image-fetch/keywords")
async def api_fetch_by_keywords(request: Request):
    """根据关键词抓取配图"""
    try:
        data = await request.json()
        keywords = data.get("keywords", "")
        count = data.get("count", 5)

        from core.image_fetch_module import get_image_fetch_module
        fetcher = get_image_fetch_module()

        results, paths = fetcher.fetch_and_download(keywords, count)

        return JSONResponse({
            "success": True,
            "count": len(paths),
            "paths": paths,
            "usage": fetcher.get_usage_stats()
        })

    except Exception as e:
        import traceback
        return JSONResponse({
            "success": False,
            "error": str(e),
            "trace": traceback.format_exc()
        }, status_code=500)


@router.post("/api/image-fetch/script")
async def api_fetch_by_script(request: Request):
    """根据脚本抓取配图"""
    try:
        data = await request.json()
        script = data.get("script", "")
        count_per_keyword = data.get("count_per_keyword", 2)

        from core.image_fetch_module import get_image_fetch_module
        fetcher = get_image_fetch_module()

        results, paths = fetcher.fetch_by_script_keywords(script, count_per_keyword)

        return JSONResponse({
            "success": True,
            "count": len(paths),
            "paths": paths,
            "usage": fetcher.get_usage_stats()
        })

    except Exception as e:
        import traceback
        return JSONResponse({
            "success": False,
            "error": str(e),
            "trace": traceback.format_exc()
        }, status_code=500)
