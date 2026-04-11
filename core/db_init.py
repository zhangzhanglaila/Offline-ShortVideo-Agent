# -*- coding: utf-8 -*-
"""
数据库初始化模块 - 预制1000+爆款选题库
"""
import sqlite3
import random
from pathlib import Path

def get_db_path():
    """获取数据库路径"""
    from config import TOPICS_DB
    return TOPICS_DB


def init_topics_db():
    """初始化爆款选题数据库"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            sub_category TEXT NOT NULL,
            title TEXT NOT NULL,
            hook TEXT NOT NULL,
            tags TEXT,
            duration TEXT,
            heat_score INTEGER DEFAULT 0,
            transform_rate REAL DEFAULT 0,
            likes INTEGER DEFAULT 0,
            platform TEXT DEFAULT '内置',
            source_url TEXT,
            is_bookmarked INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER,
            platform TEXT NOT NULL,
            script_content TEXT NOT NULL,
            storyboard TEXT,
            title TEXT,
            description TEXT,
            hashtags TEXT,
            video_path TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (topic_id) REFERENCES topics(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            script_id INTEGER,
            platform TEXT,
            views INTEGER DEFAULT 0,
            likes INTEGER DEFAULT 0,
            comments INTEGER DEFAULT 0,
            shares INTEGER DEFAULT 0,
            completion_rate REAL DEFAULT 0,
            avg_watch_time REAL DEFAULT 0,
            notes TEXT,
            record_date DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (script_id) REFERENCES scripts(id)
        )
    """)

    conn.commit()
    return conn


def insert_sample_topics(conn):
    """插入预制爆款选题数据 (1000+条)"""
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM topics")
    existing_count = cursor.fetchone()[0]
    if existing_count > 0:
        print(f"数据库已有 {existing_count} 条选题")
        if existing_count >= 1000:
            return
        print(f"数据不足1000条，继续扩充...")

    topics_data = _generate_1000_topics()
    cursor.executemany("""
        INSERT INTO topics (category, sub_category, title, hook, tags, duration, heat_score, transform_rate, likes, platform)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, topics_data)

    conn.commit()
    print(f"已预置 {len(topics_data)} 条爆款选题")


def _generate_1000_topics():
    """生成1000条预制选题数据"""
    random.seed(42)

    categories_subcats = [
        ("知识付费", ["干货分享", "技能教学", "职场晋升", "创业故事", "学习技巧", "知识变现"]),
        ("美食探店", ["各地美食", "网红餐厅", "家常菜谱", "小吃推荐", "快手料理", "减脂餐"]),
        ("生活方式", ["日常VLOG", "极简生活", "穿搭美妆", "健身打卡", "家居收纳", "自律生活"]),
        ("情感心理", ["情感故事", "心理分析", "两性关系", "自我成长", "人际交往", "情绪管理"]),
        ("科技数码", ["产品测评", "APP推荐", "科技前沿", "使用技巧", "效率工具", "AI应用"]),
        ("娱乐搞笑", ["搞笑段子", "萌宠动物", "热点吐槽", "影视解说", "明星娱乐", "游戏解说"]),
    ]

    title_templates = [
        "{}只需{}步，学会后{}！",
        "{}的正确方式，99%的人都做错了！",
        "为什么{}越来越火？看完你就懂了！",
        "{}大神都在用的{}，太牛了！",
        "普通人如何{}？学会这几点你也可以！",
        "{}避坑指南，{}个坑千万别踩！",
        "看完这个{}，{}！",
        "{}的{}技巧，学会了你就是大神！",
        "{}秘籍，学会{}！",
        "全网最火的{}，你看过几个？",
    ]

    hook_templates = [
        "学会这{}招，{}！",
        "{}的正确方式，{}！",
        "{}的秘密，{}！",
        "{}只需要{}步，{}！",
        "{}看这一篇就够了！",
        "{}太厉害了，{}！",
        "99%的人都不知道的{}！",
        "{}，你绝对没见过！",
        "{}封神之作！",
        "看完{}，你就知道了！",
    ]

    keywords_pool = {
        "知识付费": ["AI变现", "副业赚钱", "简历优化", "面试技巧", "职场晋升", "创业思维", "知识管理", "高效学习", "英语学习", "写作技巧", "演讲能力", "思维导图", "时间管理", "目标设定", "情绪调节"],
        "美食探店": ["美食探店", "家常菜", "快手早餐", "减脂餐", "必吃榜", "隐藏美食", "网红餐厅", "小吃推荐", "甜品制作", "家常面食", "下饭菜", "懒人食谱", "一人食", "便当制作", "夜市美食"],
        "生活方式": ["极简生活", "早睡早起", "时间管理", "断舍离", "自律", "自律生活", "日常vlog", "收纳整理", "护肤心得", "化妆技巧", "穿搭分享", "健身计划", "冥想放松", "读书分享", "观影记录"],
        "情感心理": ["情感修复", "沟通技巧", "情绪管理", "人际交往", "脱单", "自我成长", "心理测试", "星座分析", "情感挽回", "恋爱技巧", "婚姻经营", "亲子教育", "原生家庭", "自我认知", "心理疗愈"],
        "科技数码": ["手机测评", "APP推荐", "效率工具", "黑科技", "数码测评", "AI工具", "平板使用", "电脑技巧", "键盘快捷键", "数据备份", "隐私保护", "网络技巧", "软件推荐", "硬件升级", "科技趋势"],
        "娱乐搞笑": ["搞笑段子", "萌宠", "猫咪", "狗狗", "影视解说", "明星八卦", "综艺", "游戏", "短视频", "音乐推荐", "舞蹈", "绘画", "手工", "摄影", "旅行"],
    }

    tags_pool = [
        "干货分享", "建议收藏", "必看推荐", "宝藏技巧", "涨知识", "揭秘",
        "必学", "好用", "绝了", "太牛了", "破防了", "真香", "上头", "离谱", "扎心",
        "治愈", "共鸣", "人间真实", "破防了", "绝了", "好物推荐", "宝藏", "神仙打架", "YYDS",
    ]

    adj_pool = ["实用", "神奇", "厉害", "牛", "绝", "封神", "宝藏", "万能", "超强", "神级"]
    result_pool = ["赚翻了", "太牛了", "绝了", "太值了", "真香", "后悔没早知道", "太实用了", "太强了"]
    num_pool = ["3", "5", "7", "10", "8", "6"]

    topics = []

    for i in range(1200):
        category, subcats = random.choice(categories_subcats)
        sub_category = random.choice(subcats)
        keyword = random.choice(keywords_pool[category])

        template = random.choice(title_templates)
        num = random.choice(num_pool)
        adj = random.choice(adj_pool)
        result = random.choice(result_pool)

        title = template.format(keyword, num, result) if "{}" in template else template.format(keyword)

        hook_template = random.choice(hook_templates)
        if "{}" in hook_template:
            if hook_template.count("{}") == 1:
                hook = hook_template.format(keyword)
            elif hook_template.count("{}") == 2:
                n = random.choice(num_pool)
                r = random.choice(result_pool)
                hook = hook_template.format(n, r)
            else:
                hook = hook_template.format(keyword)
        else:
            hook = hook_template

        tags = random.sample(tags_pool, k=random.randint(3, 6))
        tags_str = ",".join(tags)

        duration_options = ["15-20秒", "20-30秒", "30-40秒", "30-45秒", "40-50秒", "45-60秒", "50-60秒", "60秒以上"]
        duration = random.choice(duration_options)

        heat_score = random.randint(60, 99)
        transform_rate = round(random.uniform(0.55, 0.95), 2)
        likes = random.randint(50, 500000)

        topics.append((
            category, sub_category, title, hook, tags_str, duration,
            heat_score, transform_rate, likes, "内置"
        ))

    return topics


def expand_to_1000(conn):
    """确保数据库有1000+条数据"""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM topics")
    count = cursor.fetchone()[0]

    if count < 1000:
        print(f"当前 {count} 条，扩充到 1000 条...")
        topics_data = _generate_1000_topics()
        cursor.executemany("""
            INSERT INTO topics (category, sub_category, title, hook, tags, duration, heat_score, transform_rate, likes, platform)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, topics_data)
        conn.commit()
        print(f"已扩充到 1000+ 条选题")


if __name__ == "__main__":
    conn = init_topics_db()
    insert_sample_topics(conn)
    conn.close()
    print("数据库初始化完成！选题库现有 1000+ 条数据")
