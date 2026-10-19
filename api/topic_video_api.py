# -*- coding: utf-8 -*-
"""
通用视频生成 API 路由 - 主题驱动的分步可控管线入口。

与电商 /api/ecom/generate 不同：本入口不依赖商品，用户直接输入主题/描述。
下游脚本编辑 / TTS / 渲染完全复用现有 /api/ecom/videos/{id}/* 端点
（它们只认 video_id，与商品无关）。
"""
import sys
import os
import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.topic_adapter import topic_to_dict, PLATFORM_MAP, CATEGORIES
from core.pipeline_helpers import (
    normalize_script_result as _normalize_script_result,
    normalize_storyboard as _normalize_storyboard,
    ensure_storyboard_placeholders as _ensure_storyboard_placeholders,
)

router = APIRouter()


@router.post("/api/topic/generate")
async def api_generate(data: dict):
    """
    主题驱动生成脚本（Step 1，不启动渲染）。

    入参: {topic, category, style, platform, duration, orientation, visual_style}
    返回: {success, video_id, script}

    后续步骤复用:
      PUT  /api/ecom/videos/{id}/script
      POST /api/ecom/videos/{id}/tts
      POST /api/ecom/videos/{id}/render
    """
    from core.db_init import get_db_path
    import sqlite3

    topic = (data.get('topic') or data.get('topic_keyword') or '').strip()
    if not topic:
        return JSONResponse({'error': '请输入视频主题'}, status_code=400)

    category = data.get('category', '短视频')
    if category not in CATEGORIES:
        category = '短视频'
    style = data.get('style', 'soft_sell')
    script_style = data.get('script_style', '爆款')
    platform = data.get('platform', '抖音')
    duration = data.get('duration', 30)
    orientation = data.get('orientation', 'portrait')
    visual_style = data.get('visual_style', 'manga')
    animation_style = data.get('animation_style', 'comic_explain')
    video_width, video_height = config.get_output_dimensions(orientation)

    topic_dict = topic_to_dict(topic, category=category, style=script_style)

    try:
        from core.script_module import generate_script
        script_result = generate_script(
            topic_dict, PLATFORM_MAP.get(platform, '抖音'), duration,
        )
    except Exception as e:
        return JSONResponse({'error': f'脚本生成失败: {str(e)}'}, status_code=500)

    if 'error' in script_result:
        return JSONResponse({'error': f'LLM 生成失败: {script_result["error"]}'}, status_code=500)

    script_result = _normalize_script_result(script_result)

    if not script_result.get('full_script'):
        return JSONResponse({'error': 'LLM 返回空内容，请检查 API Key 是否有效或稍后重试'}, status_code=500)

    normalized_storyboard = _normalize_storyboard(script_result, int(duration))

    conn = sqlite3.connect(get_db_path())
    try:
        cursor = conn.cursor()
        cursor.execute("BEGIN")
        cursor.execute("""
            INSERT INTO ecom_videos
                (product_id, topic, category, platform, style, script_content, storyboard,
                 status, pipeline_step, prompt_snapshot, llm_model, duration,
                 animation_style, orientation, video_width, video_height, visual_style)
            VALUES (NULL, ?, ?, ?, ?, ?, ?, 'script_ready', 'script_ready', ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            topic, category, platform, style,
            script_result.get('full_script', ''),
            json.dumps(normalized_storyboard, ensure_ascii=False),
            topic,  # prompt_snapshot: 主题即快照
            config.OPENAI_MODEL,
            duration,
            animation_style if animation_style in ('contain', 'side', 'comic_explain') else 'comic_explain',
            orientation,
            video_width,
            video_height,
            visual_style if visual_style in config.VISUAL_STYLES else 'manga',
        ))
        video_id = cursor.lastrowid
        normalized_storyboard = _ensure_storyboard_placeholders(
            video_id, normalized_storyboard, script_result.get('full_script', ''),
        )
        cursor.execute(
            "UPDATE ecom_videos SET storyboard=? WHERE id=?",
            (json.dumps(normalized_storyboard, ensure_ascii=False), video_id),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        return JSONResponse({'error': f'数据库写入失败: {str(e)}'}, status_code=500)
    finally:
        conn.close()

    return JSONResponse({
        'success': True,
        'video_id': video_id,
        'script': {**script_result, 'storyboard': normalized_storyboard},
    })


@router.get("/api/topic/generate/meta")
async def api_generate_meta():
    """返回通用生成的元数据（分类列表、平台、视觉风格）。"""
    name_cn = {
        'minimal': '极简', 'vibrant': '撞色', 'cinematic': '电影感',
        'tech': '科技', 'manga': '日式漫画',
    }
    return JSONResponse({
        'categories': CATEGORIES,
        'platforms': list(PLATFORM_MAP.keys()),
        'visual_styles': {
            k: {
                "name_cn": v.get("name_cn", name_cn.get(k, k)),
                "paper_color": v.get("paper_color", "#FFFFFF"),
                "accent_red": v.get("accent_red", "#E04040"),
                "text_c": v.get("text_c", "#1A1A2E"),
            }
            for k, v in config.VISUAL_STYLES.items()
        },
    })
