# -*- coding: utf-8 -*-
"""
电商分析模块 - 聚合指标、趋势分析、AI 洞察
"""
import json
import sqlite3
from typing import Optional


def _get_conn():
    from core.db_init import get_db_path
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def get_aggregated(filters: dict = None) -> dict:
    """聚合指标。"""
    filters = filters or {}
    conn = _get_conn()
    cursor = conn.cursor()

    conditions = []
    params = []
    if filters.get('product_id'):
        conditions.append("v.product_id = ?")
        params.append(filters['product_id'])
    if filters.get('platform'):
        conditions.append("a.platform = ?")
        params.append(filters['platform'])

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ''

    cursor.execute(f"""
        SELECT
            COUNT(*) as video_count,
            COALESCE(SUM(a.impressions), 0) as impressions,
            COALESCE(SUM(a.clicks), 0) as clicks,
            COALESCE(SUM(a.conversions), 0) as conversions,
            COALESCE(SUM(a.revenue), 0) as revenue,
            CASE WHEN SUM(a.impressions) > 0 THEN CAST(SUM(a.clicks) AS REAL) / SUM(a.impressions) ELSE 0 END as ctr,
            COALESCE(AVG(a.completion_rate), 0) as completion_rate,
            COALESCE(AVG(a.avg_watch_time), 0) as avg_watch_time
        FROM ecom_analytics a
        LEFT JOIN ecom_videos v ON a.video_id = v.id
        {where}
    """, params)

    row = cursor.fetchone()
    conn.close()

    return dict(row) if row else {}


def get_trends(video_id: int, days: int = 30) -> list:
    """获取视频的趋势数据。"""
    conn = _get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT recorded_at, impressions, clicks, ctr, conversions, revenue, completion_rate
        FROM ecom_analytics
        WHERE video_id = ?
        ORDER BY recorded_at DESC
        LIMIT ?
    """, (video_id, days))

    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def compare_videos(video_ids: list) -> dict:
    """对比多个视频的表现。"""
    if not video_ids:
        return {}

    conn = _get_conn()
    cursor = conn.cursor()
    placeholders = ','.join('?' * len(video_ids))
    cursor.execute(f"""
        SELECT
            video_id,
            MAX(impressions) as impressions,
            MAX(clicks) as clicks,
            MAX(conversions) as conversions,
            MAX(revenue) as revenue,
            CASE WHEN MAX(impressions) > 0 THEN CAST(MAX(clicks) AS REAL) / MAX(impressions) ELSE 0 END as ctr,
            MAX(completion_rate) as completion_rate
        FROM ecom_analytics
        WHERE video_id IN ({placeholders})
        GROUP BY video_id
    """, video_ids)

    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {row['video_id']: row for row in rows}


def generate_insights(product_id: int = None) -> str:
    """生成 AI 洞察。优先 LLM，降级规则。"""
    conn = _get_conn()
    cursor = conn.cursor()

    if product_id:
        cursor.execute("""
            SELECT a.* FROM ecom_analytics a
            JOIN ecom_videos v ON a.video_id = v.id
            WHERE v.product_id = ?
            ORDER BY a.recorded_at DESC LIMIT 20
        """, (product_id,))
    else:
        cursor.execute("SELECT * FROM ecom_analytics ORDER BY recorded_at DESC LIMIT 20")

    items = [dict(r) for r in cursor.fetchall()]

    product_name = ''
    if product_id:
        cursor.execute("SELECT name FROM products WHERE id = ?", (product_id,))
        row = cursor.fetchone()
        if row:
            product_name = row[0]

    conn.close()

    if not items:
        return '暂无分析数据，请先录入视频表现数据。'

    # 尝试 LLM
    try:
        from config import get_cloud_llm_config
        cfg = get_cloud_llm_config()
        if cfg.get('api_key'):
            from core.ai.ecom_prompts import build_insight_prompt
            import requests as req
            prompt = build_insight_prompt(items, product_name)
            resp = req.post(
                f'{cfg["api_base"]}/chat/completions',
                headers={'Authorization': f'Bearer {cfg["api_key"]}', 'Content-Type': 'application/json'},
                json={'model': cfg['model'], 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 1024, 'temperature': 0.7},
                timeout=30,
                proxies={'http': None, 'https': None},
            )
            result = resp.json()
            return result['choices'][0]['message']['content']
    except Exception:
        pass

    # 降级规则
    return _rule_based_insights(items)


def _rule_based_insights(items: list) -> str:
    """规则洞察。"""
    avg_ctr = sum(i.get('ctr', 0) for i in items) / len(items) if items else 0
    avg_completion = sum(i.get('completion_rate', 0) for i in items) / len(items) if items else 0

    tips = []
    if avg_ctr < 0.03:
        tips.append("【问题】CTR 偏低\n【优化方案】优化视频封面和开头3秒hook，使用更有冲击力的文案")
    if avg_completion < 0.4:
        tips.append("【问题】完播率偏低\n【优化方案】缩短视频时长，加快节奏，在前5秒抛出核心卖点")
    if not tips:
        tips.append("【表现良好】各项指标正常，建议持续产出并A/B测试不同风格")

    return '\n\n'.join(tips)
