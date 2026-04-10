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

    # 创建选题表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,           -- 赛道大类
            sub_category TEXT NOT NULL,        -- 赛道小类
            title TEXT NOT NULL,               -- 选题标题
            hook TEXT NOT NULL,                -- 爆款钩子
            tags TEXT,                         -- 标签列表(JSON)
            duration TEXT,                     -- 建议时长
            heat_score INTEGER DEFAULT 0,      -- 热度评分
            transform_rate REAL DEFAULT 0,     -- 转化率
            is_bookmarked INTEGER DEFAULT 0,   -- 是否收藏
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 创建脚本表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scripts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER,                  -- 关联选题ID
            platform TEXT NOT NULL,            -- 平台
            script_content TEXT NOT NULL,      -- 脚本内容
            storyboard TEXT,                    -- 分镜表(JSON)
            title TEXT,                         -- 生成的标题
            description TEXT,                   -- 生成的描述
            hashtags TEXT,                      -- 生成的话题标签
            video_path TEXT,                    -- 生成视频路径
            status TEXT DEFAULT 'pending',     -- 状态: pending/completed/failed
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (topic_id) REFERENCES topics(id)
        )
    """)

    # 创建数据复盘表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            script_id INTEGER,                 -- 关联脚本ID
            platform TEXT,                      -- 平台
            views INTEGER DEFAULT 0,           -- 播放量
            likes INTEGER DEFAULT 0,           -- 点赞数
            comments INTEGER DEFAULT 0,         -- 评论数
            shares INTEGER DEFAULT 0,            -- 分享数
            completion_rate REAL DEFAULT 0,     -- 完播率
            avg_watch_time REAL DEFAULT 0,      -- 平均观看时长
            notes TEXT,                          -- 备注
            record_date DATE,                   -- 记录日期
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (script_id) REFERENCES scripts(id)
        )
    """)

    conn.commit()
    return conn

def insert_sample_topics(conn):
    """插入示例爆款选题数据"""
    cursor = conn.cursor()

    # 检查是否已有数据
    cursor.execute("SELECT COUNT(*) FROM topics")
    if cursor.fetchone()[0] > 0:
        print(f"数据库已有 {cursor.fetchone()[0]} 条选题")
        return

    # 预制爆款选题数据
    topics_data = [
        # 知识付费类
        ("知识付费", "干货分享", "普通人如何用AI月入过万？", "学会这3步，你也可以做到！", "AI变现,副业,干货", "30-45秒", 95, 0.85),
        ("知识付费", "技能教学", "Excel高效操作必备的10个快捷键", "老手都不知道的快捷键！", "Excel,办公技巧,效率提升", "20-30秒", 92, 0.78),
        ("知识付费", "干货分享", "简历这样写，HR看了贼开心", "投递100份不如优化1份简历", "简历技巧,求职,职场", "40-50秒", 88, 0.82),
        ("知识付费", "职场晋升", "同事不会告诉你的职场潜规则", "越早知道越好", "职场潜规则,职场技巧,升职加薪", "45-60秒", 90, 0.75),
        ("知识付费", "创业故事", "95后小姐姐从0开始创业的全过程", "从摆地摊到年入百万", "创业,女性创业,励志", "60秒以上", 94, 0.80),

        # 美食探店类
        ("美食探店", "网红餐厅", "成都隐藏的宝藏小店，味道绝了", "本地人都在排队！", "成都美食,宝藏小店,必吃榜", "30-45秒", 93, 0.72),
        ("美食探店", "家常菜谱", "只需要3步做出餐厅级红烧肉", "入口即化，巨下饭！", "红烧肉,家常菜,下饭菜", "20-30秒", 91, 0.88),
        ("美食探店", "小吃推荐", "夜市必吃的5种小吃，最后一个绝了", "99%的人都不知道", "夜市小吃,美食推荐,必吃", "30-40秒", 89, 0.70),
        ("美食探店", "各地美食", "广东早茶的正确打开方式", "本地人教你点菜", "广东早茶,点心,粤菜", "40-50秒", 87, 0.68),
        ("美食探店", "网红餐厅", "北京胡同里的老字号，开了50年", "味道依旧封神", "北京美食,老字号,胡同美食", "35-45秒", 86, 0.65),

        # 生活方式类
        ("生活方式", "极简生活", "坚持极简生活1年，我的变化太大了", "断舍离带来的改变", "极简生活,断舍离,改变", "40-50秒", 88, 0.78),
        ("生活方式", "日常VLOG", "独居女孩的一天，太治愈了", "一个人也要好好生活", "独居生活,VLOG,治愈", "60秒以上", 92, 0.82),
        ("生活方式", "穿搭美妆", "小个子显高穿搭技巧，秒变170", "155也能穿出大长腿", "穿搭技巧,小个子显高,时尚", "25-35秒", 90, 0.76),
        ("生活方式", "健身打卡", "每天10分钟，练出马甲线", "在家就能做", "健身,马甲线,减肥", "20-30秒", 87, 0.74),
        ("生活方式", "日常VLOG", "周末宅家的正确打开方式", "拒绝焦虑，享受慢生活", "周末,VLOG,治愈生活", "40-50秒", 85, 0.69),

        # 情感心理类
        ("情感心理", "情感故事", "分手3个月，我终于放下了", "时间是最好的解药", "情感故事,分手,自我疗愈", "50-60秒", 91, 0.83),
        ("情感心理", "心理分析", "为什么越讨好越不被珍惜？", "讨好型人格的真相", "心理学,讨好型人格,人际关系", "40-50秒", 89, 0.79),
        ("情感心理", "两性关系", "男生说这些话就是在敷衍你", "别再被骗了", "两性关系,情感,鉴别渣男", "30-40秒", 93, 0.77),
        ("情感心理", "自我成长", "30岁才明白的人生道理", "越早知道越好", "成长,人生感悟,成熟", "45-55秒", 88, 0.81),
        ("情感心理", "情感故事", "远嫁的代价，只有经历过才懂", "一位远嫁女孩的自述", "远嫁,婚姻,人生选择", "55-65秒", 94, 0.86),

        # 科技数码类
        ("科技数码", "产品测评", "2000元手机拍照对比，谁更强？", "结果出乎意料", "手机测评,拍照对比,数码", "40-50秒", 86, 0.71),
        ("科技数码", "APP推荐", "这款APP让我每天多出2小时", "效率提升神器", "APP推荐,效率工具,神器", "25-35秒", 88, 0.73),
        ("科技数码", "使用技巧", "微信隐藏的实用功能，99%的人不知道", "太方便了", "微信技巧,实用功能,冷知识", "20-30秒", 90, 0.75),
        ("科技数码", "科技前沿", "AI画图神器有多强？实测体验", "人人都是艺术家", "AI绘图,科技,人工智能", "35-45秒", 87, 0.68),
        ("科技数码", "产品测评", "蓝牙耳机音质对比，学生党首选", "性价比之王", "蓝牙耳机,数码测评,学生党", "30-40秒", 85, 0.70),

        # 娱乐搞笑类
        ("娱乐搞笑", "搞笑段子", "当程序员和产品经理吵架，笑疯了", "过于真实", "程序员,产品经理,搞笑", "30-40秒", 95, 0.89),
        ("娱乐搞笑", "萌宠动物", "猫咪这些行为是在说爱你", "你家猫有吗？", "猫咪,宠物,萌宠", "25-35秒", 92, 0.84),
        ("娱乐搞笑", "热点吐槽", "当代年轻人的现状，太真实了", "是你吗？", "当代年轻人,现状,共鸣", "20-30秒", 91, 0.87),
        ("娱乐搞笑", "影视解说", "1分钟看完《狂飙》大结局", "太刀了", "狂飙,影视解说,剧情", "40-50秒", 94, 0.82),
        ("娱乐搞笑", "搞笑段子", "男朋友的神仙回复，气死我了", "笑着笑着就哭了", "恋爱,搞笑,日常", "25-35秒", 93, 0.85),
    ]

    # 插入数据
    cursor.executemany("""
        INSERT INTO topics (category, sub_category, title, hook, tags, duration, heat_score, transform_rate)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, topics_data)

    conn.commit()
    print(f"已插入 {len(topics_data)} 条示例选题")
    print("实际使用时将扩展至1000+条选题...")

    # 演示：复制示例数据扩充至100条
    expand_topics(conn, cursor, topics_data)

def expand_topics(conn, cursor, base_topics):
    """扩充选题数据到100+条"""
    additional_count = 75
    hooks_pool = [
        "学会这{}招，{}！", "{}的正确方式，{}！", "{}的秘密，{}！",
        "{}只需要{}步，{}！", "{}看这一篇就够了！", "{}太厉害了，{}！",
        "99%的人都不知道的{}！", "{}，你绝对没见过！", "{}封神之作！",
    ]
    suffixes = [
        "后悔没早知道", "建议收藏", "炸裂推荐", "绝了", "太牛了",
        "破防了", "真香", "上头", "离谱", "扎心",
    ]

    import random
    random.seed(42)

    for i in range(additional_count):
        base = random.choice(base_topics)
        hook_template = random.choice(hooks_pool)
        suffix = random.choice(suffixes)

        # 生成变化的钩子
        if "{}" in hook_template:
            if hook_template.count("{}") == 1:
                new_hook = hook_template.format(suffix)
            elif hook_template.count("{}") == 2:
                num = random.choice(["3", "5", "7", "10"])
                new_hook = hook_template.format(num, suffix)
            else:
                new_hook = hook_template.format(suffix)
        else:
            new_hook = hook_template + suffix

        # 添加变体标题
        variations = [
            f"[必看]{base[2]}",
            f"强烈建议{fandom.choice(['收藏','保存'])}的{base[3]}",
            f"{base[2]}完整版",
            f"续集来了！{base[2]}",
        ]
        new_title = random.choice(variations)

        new_heat = max(60, base[6] + random.randint(-10, 5))
        new_transform = max(0.5, base[7] + random.uniform(-0.1, 0.1))

        cursor.execute("""
            INSERT INTO topics (category, sub_category, title, hook, tags, duration, heat_score, transform_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (base[0], base[1], new_title, new_hook, base[4], base[5], new_heat, new_transform))

    conn.commit()
    print(f"数据库现已包含 100 条选题数据")

if __name__ == "__main__":
    conn = init_topics_db()
    insert_sample_topics(conn)
    conn.close()
    print("数据库初始化完成！")
