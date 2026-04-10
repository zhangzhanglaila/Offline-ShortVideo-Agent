# -*- coding: utf-8 -*-
"""
数据复盘&迭代模块
本地记录播放/完播/点赞数据，自动分析爆款规律，迭代生成下一批高概率选题
"""
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from config import TOPICS_DB, DB_SCRIPTS_TABLE, DB_ANALYTICS_TABLE

class AnalyticsModule:
    """数据复盘模块"""

    def __init__(self):
        """初始化数据复盘模块"""
        self.db_path = TOPICS_DB

    def _get_connection(self):
        """获取数据库连接"""
        return sqlite3.connect(str(self.db_path))

    def record_metrics(self, script_id: int, metrics: Dict) -> int:
        """
        记录视频数据指标

        参数:
            script_id: 脚本ID
            metrics: 数据指标字典，包含 views, likes, comments, shares, completion_rate, avg_watch_time

        返回:
            记录ID
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO analytics (script_id, platform, views, likes, comments, shares,
                                  completion_rate, avg_watch_time, record_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            script_id,
            metrics.get("platform", ""),
            metrics.get("views", 0),
            metrics.get("likes", 0),
            metrics.get("comments", 0),
            metrics.get("shares", 0),
            metrics.get("completion_rate", 0.0),
            metrics.get("avg_watch_time", 0.0),
            datetime.now().date().isoformat()
        ))

        record_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return record_id

    def update_metrics(self, record_id: int, metrics: Dict) -> bool:
        """更新数据指标"""
        conn = self._get_connection()
        cursor = conn.cursor()

        set_clauses = []
        params = []

        for key in ["views", "likes", "comments", "shares", "completion_rate", "avg_watch_time", "notes"]:
            if key in metrics:
                set_clauses.append(f"{key} = ?")
                params.append(metrics[key])

        if not set_clauses:
            return False

        params.append(record_id)
        cursor.execute(f"""
            UPDATE analytics SET {', '.join(set_clauses)}
            WHERE id = ?
        """, params)

        affected = cursor.rowcount
        conn.commit()
        conn.close()

        return affected > 0

    def get_script_analytics(self, script_id: int) -> List[Dict]:
        """获取脚本的所有数据记录"""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, script_id, platform, views, likes, comments, shares,
                   completion_rate, avg_watch_time, notes, record_date, created_at
            FROM analytics WHERE script_id = ?
            ORDER BY record_date DESC
        """, (script_id,))

        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_dict(row) for row in rows]

    def get_latest_metrics(self, script_id: int) -> Optional[Dict]:
        """获取脚本最新一次的数据"""
        records = self.get_script_analytics(script_id)
        return records[0] if records else None

    def get_platform_summary(self, platform: str, days: int = 30) -> Dict:
        """获取平台数据汇总"""
        conn = self._get_connection()
        cursor = conn.cursor()

        start_date = (datetime.now() - timedelta(days=days)).date().isoformat()

        cursor.execute("""
            SELECT
                COUNT(*) as video_count,
                SUM(views) as total_views,
                SUM(likes) as total_likes,
                SUM(comments) as total_comments,
                SUM(shares) as total_shares,
                AVG(completion_rate) as avg_completion_rate,
                AVG(avg_watch_time) as avg_watch_time
            FROM analytics
            WHERE platform = ? AND record_date >= ?
        """, (platform, start_date))

        row = cursor.fetchone()
        conn.close()

        if not row or row[0] == 0:
            return {
                "platform": platform,
                "video_count": 0,
                "total_views": 0,
                "total_likes": 0,
                "avg_completion_rate": 0,
                "avg_engagement_rate": 0,
            }

        total_views = row[1] or 0
        total_likes = row[2] or 0
        video_count = row[0] or 0

        return {
            "platform": platform,
            "video_count": video_count,
            "total_views": total_views,
            "total_likes": total_likes,
            "total_comments": row[3] or 0,
            "total_shares": row[4] or 0,
            "avg_completion_rate": round((row[5] or 0) * 100, 1),
            "avg_watch_time": round(row[6] or 0, 1),
            "avg_views_per_video": round(total_views / video_count, 0) if video_count > 0 else 0,
            "avg_engagement_rate": round((total_likes / total_views * 100), 2) if total_views > 0 else 0,
        }

    def analyze_top_performing(self, platform: str = None, limit: int = 10) -> List[Dict]:
        """分析表现最好的视频"""
        conn = self._get_connection()
        cursor = conn.cursor()

        query = """
            SELECT
                a.script_id,
                a.platform,
                a.views,
                a.likes,
                a.comments,
                a.shares,
                a.completion_rate,
                s.script_content,
                s.title
            FROM analytics a
            JOIN scripts s ON a.script_id = s.id
        """

        if platform:
            query += " WHERE a.platform = ?"
            query += " ORDER BY (a.likes + a.comments * 2 + a.shares * 3) DESC, a.views DESC"
            query += f" LIMIT {limit}"
            cursor.execute(query, (platform,))
        else:
            query += " ORDER BY (a.likes + a.comments * 2 + a.shares * 3) DESC, a.views DESC"
            query += f" LIMIT {limit}"
            cursor.execute(query)

        rows = cursor.fetchall()
        conn.close()

        results = []
        for row in rows:
            results.append({
                "script_id": row[0],
                "platform": row[1],
                "views": row[2],
                "likes": row[3],
                "comments": row[4],
                "shares": row[5],
                "completion_rate": round(row[6] * 100, 1) if row[6] else 0,
                "engagement_score": row[3] + row[4] * 2 + row[5] * 3,
                "title": row[8],
                "script_preview": (row[7][:100] + "...") if row[7] and len(row[7]) > 100 else row[7],
            })

        return results

    def identify_trending_patterns(self) -> Dict:
        """识别爆款规律"""
        conn = self._get_connection()
        cursor = conn.cursor()

        # 分析高完播率视频的特点
        cursor.execute("""
            SELECT
                s.category,
                s.platform,
                t.heat_score,
                t.transform_rate,
                AVG(a.completion_rate) as avg_completion
            FROM analytics a
            JOIN scripts s ON a.script_id = s.id
            JOIN topics t ON s.topic_id = t.id
            WHERE a.views >= 100
            GROUP BY s.category, s.platform
            ORDER BY avg_completion DESC
        """)

        category_performance = {}
        for row in cursor.fetchall():
            key = f"{row[0]}_{row[1]}"
            category_performance[key] = {
                "category": row[0],
                "platform": row[1],
                "avg_topic_heat": row[2],
                "avg_transform": round(row[3] * 100, 1),
                "avg_completion_rate": round(row[4] * 100, 1),
            }

        # 分析最佳时长
        cursor.execute("""
            SELECT
                CASE
                    WHEN a.avg_watch_time < 10 THEN '0-10秒'
                    WHEN a.avg_watch_time < 20 THEN '10-20秒'
                    WHEN a.avg_watch_time < 30 THEN '20-30秒'
                    WHEN a.avg_watch_time < 45 THEN '30-45秒'
                    ELSE '45秒以上'
                END as duration_range,
                AVG(a.completion_rate) as avg_completion,
                COUNT(*) as count
            FROM analytics a
            WHERE a.views >= 50
            GROUP BY duration_range
            ORDER BY avg_completion DESC
        """)

        duration_analysis = []
        for row in cursor.fetchall():
            duration_analysis.append({
                "range": row[0],
                "avg_completion": round(row[1] * 100, 1),
                "video_count": row[2],
            })

        # 高互动视频的共同特点
        cursor.execute("""
            SELECT
                t.category,
                t.sub_category,
                COUNT(*) as count,
                AVG(a.completion_rate) as avg_completion,
                SUM(a.likes) as total_likes
            FROM analytics a
            JOIN scripts s ON a.script_id = s.id
            JOIN topics t ON s.topic_id = t.id
            WHERE a.views >= 50 AND a.likes > 0
            GROUP BY t.category, t.sub_category
            HAVING count >= 2
            ORDER BY (SUM(a.likes) * 1.0 / COUNT(*)) DESC
            LIMIT 10
        """)

        high_engagement_topics = []
        for row in cursor.fetchall():
            high_engagement_topics.append({
                "category": row[0],
                "sub_category": row[1],
                "video_count": row[2],
                "avg_completion": round(row[3] * 100, 1),
                "avg_likes": round(row[4] / row[2], 0),
            })

        conn.close()

        return {
            "category_performance": category_performance,
            "duration_analysis": duration_analysis,
            "high_engagement_topics": high_engagement_topics,
            "generated_at": datetime.now().isoformat(),
        }

    def generate_recommended_topics(self, count: int = 10) -> List[Dict]:
        """基于数据分析生成推荐选题"""
        conn = self._get_connection()
        cursor = conn.cursor()

        # 找出表现最好的赛道和子类别
        cursor.execute("""
            SELECT
                t.category,
                t.sub_category,
                AVG(a.completion_rate) as avg_completion,
                AVG(a.likes * 1.0 / NULLIF(a.views, 0)) as avg_like_rate,
                COUNT(*) as video_count
            FROM analytics a
            JOIN scripts s ON a.script_id = s.id
            JOIN topics t ON s.topic_id = t.id
            WHERE a.views >= 30
            GROUP BY t.category, t.sub_category
            HAVING video_count >= 1
            ORDER BY (avg_completion * 0.6 + avg_like_rate * 0.4) DESC
            LIMIT 3
        """)

        top_categories = cursor.fetchall()

        recommended = []
        seen_categories = set()

        for row in top_categories:
            category, sub_category = row[0], row[1]
            seen_categories.add(f"{category}_{sub_category}")

            # 在该子类别中寻找未制作过的选题
            cursor.execute("""
                SELECT id, category, sub_category, title, hook, tags, duration, heat_score
                FROM topics
                WHERE category = ? AND sub_category = ?
                AND id NOT IN (SELECT DISTINCT topic_id FROM scripts WHERE topic_id IS NOT NULL)
                ORDER BY heat_score DESC
                LIMIT ?
            """, (category, sub_category, max(1, count // 3)))

            for topic_row in cursor.fetchall():
                recommended.append({
                    "id": topic_row[0],
                    "category": topic_row[1],
                    "sub_category": topic_row[2],
                    "title": topic_row[3],
                    "hook": topic_row[4],
                    "tags": topic_row[5].split(",") if topic_row[5] else [],
                    "duration": topic_row[6],
                    "heat_score": topic_row[7],
                    "recommendation_reason": "基于历史表现优秀的同类别视频",
                })

        # 如果推荐不够，补充高热度选题
        if len(recommended) < count:
            cursor.execute("""
                SELECT id, category, sub_category, title, hook, tags, duration, heat_score
                FROM topics
                WHERE heat_score >= 80
                AND id NOT IN (SELECT DISTINCT topic_id FROM scripts WHERE topic_id IS NOT NULL)
                ORDER BY heat_score DESC
                LIMIT ?
            """, (count - len(recommended),))

            for topic_row in cursor.fetchall():
                key = f"{topic_row[1]}_{topic_row[2]}"
                if key not in seen_categories:
                    recommended.append({
                        "id": topic_row[0],
                        "category": topic_row[1],
                        "sub_category": topic_row[2],
                        "title": topic_row[3],
                        "hook": topic_row[4],
                        "tags": topic_row[5].split(",") if topic_row[5] else [],
                        "duration": topic_row[6],
                        "heat_score": topic_row[7],
                        "recommendation_reason": "高热度爆款选题",
                    })
                    seen_categories.add(key)

        conn.close()

        return recommended[:count]

    def get_weekly_report(self) -> Dict:
        """生成周报"""
        conn = self._get_connection()
        cursor = conn.cursor()

        week_ago = (datetime.now() - timedelta(days=7)).date().isoformat()

        # 本周数据
        cursor.execute("""
            SELECT
                COUNT(*) as video_count,
                SUM(views) as total_views,
                SUM(likes) as total_likes,
                SUM(comments) as total_comments,
                SUM(shares) as total_shares,
                AVG(completion_rate) as avg_completion
            FROM analytics
            WHERE record_date >= ?
        """, (week_ago,))

        week_row = cursor.fetchone()

        # 上周数据
        two_weeks_ago = (datetime.now() - timedelta(days=14)).date().isoformat()
        cursor.execute("""
            SELECT
                COUNT(*) as video_count,
                SUM(views) as total_views,
                SUM(likes) as total_likes
            FROM analytics
            WHERE record_date >= ? AND record_date < ?
        """, (two_weeks_ago, week_ago))

        prev_week_row = cursor.fetchone()

        # 平台分布
        cursor.execute("""
            SELECT platform, COUNT(*), SUM(views)
            FROM analytics
            WHERE record_date >= ?
            GROUP BY platform
        """, (week_ago,))

        platform_dist = {}
        for row in cursor.fetchall():
            platform_dist[row[0]] = {"video_count": row[1], "views": row[2] or 0}

        conn.close()

        # 计算变化
        week_views = week_row[1] or 0
        prev_views = prev_week_row[1] or 0 if prev_week_row else 0
        views_change = ((week_views - prev_views) / prev_views * 100) if prev_views > 0 else 0

        week_likes = week_row[2] or 0
        prev_likes = prev_week_row[2] or 0 if prev_week_row else 0
        likes_change = ((week_likes - prev_likes) / prev_likes * 100) if prev_likes > 0 else 0

        return {
            "period": f"{(datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')} 至 {datetime.now().strftime('%Y-%m-%d')}",
            "summary": {
                "video_count": week_row[0] or 0,
                "total_views": week_views,
                "total_likes": week_likes,
                "total_comments": week_row[3] or 0,
                "total_shares": week_row[4] or 0,
                "avg_completion_rate": round((week_row[5] or 0) * 100, 1),
            },
            "changes": {
                "views_change": round(views_change, 1),
                "likes_change": round(likes_change, 1),
            },
            "platform_distribution": platform_dist,
            "top_videos": self.analyze_top_performing(limit=5),
            "trending_patterns": self.identify_trending_patterns(),
            "recommended_topics": self.generate_recommended_topics(count=5),
        }

    def export_report(self, report: Dict, output_path: str) -> bool:
        """导出报告为JSON"""
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"报告导出失败: {str(e)}")
            return False

    def _row_to_dict(self, row: tuple) -> Dict:
        """行数据转字典"""
        return {
            "id": row[0],
            "script_id": row[1],
            "platform": row[2],
            "views": row[3],
            "likes": row[4],
            "comments": row[5],
            "shares": row[6],
            "completion_rate": round(row[7] * 100, 1) if row[7] else 0,
            "avg_watch_time": round(row[8], 1) if row[8] else 0,
            "notes": row[9],
            "record_date": row[10],
            "created_at": row[11],
        }


# ==================== 便捷函数 ====================
_module_instance = None

def get_analytics_module() -> AnalyticsModule:
    """获取数据复盘模块单例"""
    global _module_instance
    if _module_instance is None:
        _module_instance = AnalyticsModule()
    return _module_instance

def record_video_metrics(script_id: int, metrics: Dict) -> int:
    """快速记录视频数据"""
    return get_analytics_module().record_metrics(script_id, metrics)

def get_recommendations(count: int = 10) -> List[Dict]:
    """获取推荐选题"""
    return get_analytics_module().generate_recommended_topics(count)
