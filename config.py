# -*- coding: utf-8 -*-
"""
配置文件 - Offline-ShortVideo-Agent
所有路径和参数配置集中管理
"""
import os
from pathlib import Path

# ==================== 项目路径配置 ====================
# 项目根目录
PROJECT_ROOT = Path(__file__).parent.absolute()

# 各模块路径
DATA_DIR = PROJECT_ROOT / "data"
ASSETS_DIR = PROJECT_ROOT / "assets"
OUTPUT_DIR = PROJECT_ROOT / "output"
BGM_DIR = ASSETS_DIR / "bgm"
MATERIAL_DIR = ASSETS_DIR / "素材池_待剪辑"

# 输出子目录
OUTPUT_DY = OUTPUT_DIR / "抖音"      # 抖音发布包
OUTPUT_XHS = OUTPUT_DIR / "小红书"  # 小红书发布包
OUTPUT_WX = OUTPUT_DIR / "视频号"   # 视频号发布包

# ==================== 数据库配置 ====================
TOPICS_DB = DATA_DIR / "topics.db"

# ==================== Ollama LLM配置 ====================
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2:7b"  # 本地Qwen2-7B模型
OLLAMA_TIMEOUT = 120  # 生成超时(秒)

# ==================== Whisper字幕配置 ====================
WHISPER_MODEL = "base"  # faster-whisper模型: tiny/base/small/medium/large
WHISPER_LANGUAGE = "zh"

# ==================== 视频处理配置 ====================
# 输出视频参数
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920   # 9:16竖屏
OUTPUT_FPS = 30
OUTPUT_CRF = 23        # 视频质量 (18-28, 越小越清晰)
OUTPUT_AUDIO_BITRATE = "192k"
OUTPUT_VIDEO_BITRATE = "2M"

# 默认视频时长(秒)
DEFAULT_VIDEO_DURATION = 30

# ==================== 平台适配配置 ====================
PLATFORM_CONFIGS = {
    "抖音": {
        "max_duration": 60,      # 最大时长(秒)
        "min_duration": 15,      # 最小时长(秒)
        "aspect_ratio": "9:16",
        "output_dir": OUTPUT_DY,
        "title_max_len": 40,      # 标题最大长度
        "desc_max_len": 200,      # 描述最大长度
        "hashtags_max": 20,       # 最大话题数
    },
    "小红书": {
        "max_duration": 300,
        "min_duration": 10,
        "aspect_ratio": "9:16",
        "output_dir": OUTPUT_XHS,
        "title_max_len": 20,
        "desc_max_len": 1000,
        "hashtags_max": 15,
    },
    "视频号": {
        "max_duration": 120,
        "min_duration": 15,
        "aspect_ratio": "9:16",
        "output_dir": OUTPUT_WX,
        "title_max_len": 30,
        "desc_max_len": 500,
        "hashtags_max": 10,
    },
}

# ==================== 赛道分类配置 ====================
CATEGORIES = {
    "知识付费": ["干货分享", "技能教学", "职场晋升", "创业故事"],
    "美食探店": ["各地美食", "网红餐厅", "家常菜谱", "小吃推荐"],
    "生活方式": ["日常VLOG", "极简生活", "穿搭美妆", "健身打卡"],
    "情感心理": ["情感故事", "心理分析", "两性关系", "自我成长"],
    "科技数码": ["产品测评", "APP推荐", "科技前沿", "使用技巧"],
    "娱乐搞笑": ["搞笑段子", "萌宠动物", "热点吐槽", "影视解说"],
}

# ==================== 爆款标签库 ====================
TRENDING_TAGS = [
    "#爆款", "#必看", "#干货分享", "#建议收藏", "#涨知识",
    "#揭秘", "#干货", "#好物推荐", "#宝藏", "#治愈",
    "#人间真实", "#破防了", "#绝绝子", "#神仙打架", "#YYDS",
]

# ==================== BGM配置 ====================
BGM_VOLUME = 0.3       # BGM音量 (0.0-1.0)
BG_MUTE_DURATION = 2   # 视频开始时BGM静音秒数

# ==================== 日志配置 ====================
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"

# ==================== SQLite配置 ====================
DB_TOPICS_TABLE = "topics"
DB_SCRIPTS_TABLE = "scripts"
DB_ANALYTICS_TABLE = "analytics"

# ==================== 确保目录存在 ====================
def ensure_dirs():
    """确保所有必要目录存在"""
    for dir_path in [DATA_DIR, ASSETS_DIR, OUTPUT_DIR, BGM_DIR, MATERIAL_DIR,
                     OUTPUT_DY, OUTPUT_XHS, OUTPUT_WX]:
        dir_path.mkdir(parents=True, exist_ok=True)
