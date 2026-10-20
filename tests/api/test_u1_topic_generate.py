# -*- coding: utf-8 -*-
"""
U1 测试：主题驱动的生成入口。

- topic_to_dict 纯函数结构校验
- /api/generate 端点校验与 DB 写入（generate_script 被 mock，保持离线）
"""
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.topic_adapter import topic_to_dict, CATEGORIES, PLATFORM_MAP


# ==================== topic_to_dict ====================

def test_topic_to_dict_basic():
    """基本结构：包含 generate_script 需要的字段。"""
    d = topic_to_dict("讲解什么是区块链", category="教育讲解", style="爆款")
    assert d["title"] == "讲解什么是区块链"
    assert d["category"] == "教育讲解"
    assert d["hook"] == ""          # 留空让 LLM 自己生成
    assert d["id"] == "topic_user"
    assert d["tags"] == []
    assert d["_style"] == "爆款"


def test_topic_to_dict_strips_whitespace():
    """主题首尾空白被清理。"""
    d = topic_to_dict("  海边日落  ")
    assert d["title"] == "海边日落"


def test_topic_to_dict_invalid_category_falls_back():
    """非法分类降级为 短视频。"""
    d = topic_to_dict("测试", category="不存在的分类")
    assert d["category"] == "短视频"


def test_topic_to_dict_valid_categories():
    """CLI 分类全部有效。"""
    for cat in CATEGORIES:
        assert topic_to_dict("x", category=cat)["category"] == cat


def test_topic_to_dict_compatible_with_product_to_topic_keys():
    """与 product_to_topic 输出的关键字段兼容（下游只读这几个）。"""
    d = topic_to_dict("主题")
    for key in ("id", "title", "hook", "category", "tags"):
        assert key in d


# ==================== /api/generate 端点 ====================

# ecom_videos 表最小 schema（含 U1 新增的 topic/category 列）
_CREATE_ECOM_VIDEOS = """
CREATE TABLE ecom_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    topic TEXT,
    category TEXT,
    session_id TEXT,
    platform TEXT,
    style TEXT,
    script_content TEXT,
    storyboard TEXT,
    video_path TEXT,
    thumbnail_path TEXT,
    duration REAL,
    status TEXT DEFAULT 'draft',
    pipeline_step TEXT DEFAULT 'init',
    prompt_snapshot TEXT,
    llm_model TEXT,
    tts_audio_path TEXT,
    materials_json TEXT,
    animation_style TEXT DEFAULT 'contain',
    video_width INTEGER DEFAULT 1080,
    video_height INTEGER DEFAULT 1920,
    orientation TEXT DEFAULT 'portrait',
    visual_style TEXT DEFAULT 'manga',
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def test_generate_endpoint_empty_topic():
    """空主题返回 400。"""
    import asyncio
    from api.topic_video_api import api_generate

    resp = asyncio.run(api_generate({"topic": "   "}))
    assert resp.status_code == 400


def test_generate_endpoint_writes_record():
    """正常主题：脚本 mock 后写入视频记录，product_id 为 NULL。"""
    import asyncio

    tmp = tempfile.mkdtemp()
    db_path = str(Path(tmp) / "test.db")

    # 建最小表
    conn = sqlite3.connect(db_path)
    conn.execute(_CREATE_ECOM_VIDEOS)
    conn.commit()
    conn.close()

    fake_script = {
        "full_script": "这是一段测试脚本。第二句。第三句。",
        "hook": "开头",
        "storyboard": [],
    }

    with patch("core.db_init.get_db_path", return_value=db_path), \
         patch("core.script_module.generate_script", return_value=fake_script), \
         patch("api.topic_video_api._ensure_storyboard_placeholders",
               side_effect=lambda vid, sb, script: sb):
        from api.topic_video_api import api_generate
        resp = asyncio.run(api_generate({
            "topic": "讲解机器学习",
            "category": "教育讲解",
            "duration": 20,
        }))

    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["success"] is True
    video_id = body["video_id"]

    # 校验 DB 记录
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT product_id, topic, category, pipeline_step FROM ecom_videos WHERE id=?",
        (video_id,),
    ).fetchone()
    conn.close()

    assert row["product_id"] is None
    assert row["topic"] == "讲解机器学习"
    assert row["category"] == "教育讲解"
    assert row["pipeline_step"] == "script_ready"
