# -*- coding: utf-8 -*-
"""
爆款选题模块 - 本地SQLite爆款选题库
支持赛道筛选、热度排序、标签匹配、智能推荐
"""
import sqlite3
import json
import random
from typing import List, Dict, Optional
from config import TOPICS_DB, CATEGORIES, TRENDING_TAGS

class TopicsModule:
    """爆款选题管理模块"""

    def __init__(self):
        """初始化选题模块"""
        self.db_path = TOPICS_DB
        self._ensure_db()

    def _ensure_db(self):
        """确保数据库存在"""
        if not self.db_path.exists():
            from core.db_init import init_topics_db, insert_sample_topics
            conn = init_topics_db()
            insert_sample_topics(conn)
            conn.close()

    def _get_connection(self):
        """获取数据库连接"""
        return sqlite3.connect(str(self.db_path))

    def get_all_topics(self, limit: int = 100) -> List[Dict]:
        """获取所有选题"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, category, sub_category, title, hook, tags, duration, heat_score, transform_rate
            FROM topics ORDER BY heat_score DESC LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_dict(row) for row in rows]

    def get_topics_by_category(self, category: str, limit: int = 50) -> List[Dict]:
        """按赛道筛选选题"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, category, sub_category, title, hook, tags, duration, heat_score, transform_rate
            FROM topics WHERE category = ? ORDER BY heat_score DESC LIMIT ?
        """, (category, limit))
        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_dict(row) for row in rows]

    def get_topics_by_tags(self, tags: List[str], limit: int = 30) -> List[Dict]:
        """按标签筛选选题"""
        conn = self._get_connection()
        cursor = conn.cursor()
        # 模糊匹配标签
        pattern = "%" + "%".join(tags) + "%"
        cursor.execute("""
            SELECT id, category, sub_category, title, hook, tags, duration, heat_score, transform_rate
            FROM topics WHERE tags LIKE ? ORDER BY heat_score DESC LIMIT ?
        """, (pattern, limit))
        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_dict(row) for row in rows]

    def get_high_heat_topics(self, min_heat: int = 80, limit: int = 30) -> List[Dict]:
        """获取高热度选题"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, category, sub_category, title, hook, tags, duration, heat_score, transform_rate
            FROM topics WHERE heat_score >= ? ORDER BY heat_score DESC LIMIT ?
        """, (min_heat, limit))
        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_dict(row) for row in rows]

    def get_high_transform_topics(self, min_rate: float = 0.75, limit: int = 30) -> List[Dict]:
        """获取高转化率选题"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, category, sub_category, title, hook, tags, duration, heat_score, transform_rate
            FROM topics WHERE transform_rate >= ? ORDER BY transform_rate DESC LIMIT ?
        """, (min_rate, limit))
        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_dict(row) for row in rows]

    def recommend_topics(self, category: Optional[str] = None, duration: Optional[str] = None,
                         tags: Optional[List[str]] = None, count: int = 5) -> List[Dict]:
        """智能推荐选题 - 综合热度、转化率、匹配度"""
        conn = self._get_connection()
        cursor = conn.cursor()

        query = """
            SELECT id, category, sub_category, title, hook, tags, duration, heat_score, transform_rate,
                   (heat_score * 0.4 + transform_rate * 100 * 0.6) as score
            FROM topics WHERE 1=1
        """
        params = []

        if category:
            query += " AND category = ?"
            params.append(category)

        if duration:
            # 匹配时长
            if "15" in duration:
                query += " AND (duration LIKE '%15%' OR duration LIKE '%20%' OR duration LIKE '%30%')"
            elif "45" in duration or "60" in duration:
                query += " AND (duration LIKE '%45%' OR duration LIKE '%60%' OR duration LIKE '%60秒以上%')"

        if tags:
            tag_pattern = "%" + "%".join(tags) + "%"
            query += " AND tags LIKE ?"
            params.append(tag_pattern)

        query += " ORDER BY score DESC LIMIT ?"
        params.append(count)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_dict(row) for row in rows]

    def search_topics(self, keyword: str, limit: int = 30) -> List[Dict]:
        """关键词搜索选题"""
        conn = self._get_connection()
        cursor = conn.cursor()
        pattern = f"%{keyword}%"
        cursor.execute("""
            SELECT id, category, sub_category, title, hook, tags, duration, heat_score, transform_rate
            FROM topics
            WHERE title LIKE ? OR hook LIKE ? OR tags LIKE ?
            ORDER BY heat_score DESC LIMIT ?
        """, (pattern, pattern, pattern, limit))
        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_dict(row) for row in rows]

    def get_topic_by_id(self, topic_id: int) -> Optional[Dict]:
        """根据ID获取选题"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, category, sub_category, title, hook, tags, duration, heat_score, transform_rate
            FROM topics WHERE id = ?
        """, (topic_id,))
        row = cursor.fetchone()
        conn.close()

        return self._row_to_dict(row) if row else None

    def get_random_topic(self, category: Optional[str] = None) -> Optional[Dict]:
        """随机获取一个选题"""
        conn = self._get_connection()
        cursor = conn.cursor()

        if category:
            cursor.execute("""
                SELECT id, category, sub_category, title, hook, tags, duration, heat_score, transform_rate
                FROM topics WHERE category = ? ORDER BY RANDOM() LIMIT 1
            """, (category,))
        else:
            cursor.execute("""
                SELECT id, category, sub_category, title, hook, tags, duration, heat_score, transform_rate
                FROM topics ORDER BY RANDOM() LIMIT 1
            """)

        row = cursor.fetchone()
        conn.close()

        return self._row_to_dict(row) if row else None

    def add_bookmark(self, topic_id: int) -> bool:
        """收藏选题"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE topics SET is_bookmarked = 1 WHERE id = ?", (topic_id,))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0

    def remove_bookmark(self, topic_id: int) -> bool:
        """取消收藏"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE topics SET is_bookmarked = 0 WHERE id = ?", (topic_id,))
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return affected > 0

    def get_bookmarked_topics(self) -> List[Dict]:
        """获取已收藏选题"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, category, sub_category, title, hook, tags, duration, heat_score, transform_rate
            FROM topics WHERE is_bookmarked = 1 ORDER BY created_at DESC
        """)
        rows = cursor.fetchall()
        conn.close()

        return [self._row_to_dict(row) for row in rows]

    def get_categories(self) -> List[str]:
        """获取所有赛道类别"""
        return list(CATEGORIES.keys())

    def get_subcategories(self, category: str) -> List[str]:
        """获取赛道子类别"""
        return CATEGORIES.get(category, [])

    def get_statistics(self) -> Dict:
        """获取选题库统计信息"""
        conn = self._get_connection()
        cursor = conn.cursor()

        # 总数
        cursor.execute("SELECT COUNT(*) FROM topics")
        total = cursor.fetchone()[0]

        # 各赛道数量
        cursor.execute("""
            SELECT category, COUNT(*) as count
            FROM topics GROUP BY category ORDER BY count DESC
        """)
        by_category = dict(cursor.fetchall())

        # 平均热度
        cursor.execute("SELECT AVG(heat_score) FROM topics")
        avg_heat = cursor.fetchone()[0] or 0

        # 平均转化率
        cursor.execute("SELECT AVG(transform_rate) FROM topics")
        avg_transform = cursor.fetchone()[0] or 0

        conn.close()

        return {
            "total": total,
            "by_category": by_category,
            "avg_heat_score": round(avg_heat, 1),
            "avg_transform_rate": round(avg_transform * 100, 1),
        }

    def _row_to_dict(self, row: tuple) -> Dict:
        """行数据转字典"""
        return {
            "id": row[0],
            "category": row[1],
            "sub_category": row[2],
            "title": row[3],
            "hook": row[4],
            "tags": row[5].split(",") if row[5] else [],
            "duration": row[6],
            "heat_score": row[7],
            "transform_rate": row[8],
        }


# ==================== 便捷函数 ====================
_module_instance = None

def get_topics_module() -> TopicsModule:
    """获取选题模块单例"""
    global _module_instance
    if _module_instance is None:
        _module_instance = TopicsModule()
    return _module_instance

def quick_recommend(category: Optional[str] = None, count: int = 5) -> List[Dict]:
    """快速推荐选题"""
    return get_topics_module().recommend_topics(category=category, count=count)

def search_topics(keyword: str) -> List[Dict]:
    """快速搜索选题"""
    return get_topics_module().search_topics(keyword)
