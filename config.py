# -*- coding: utf-8 -*-
"""
配置文件 - Offline-ShortVideo-Agent
所有路径和参数配置集中管理
"""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.absolute()

DATA_DIR = PROJECT_ROOT / "data"
ASSETS_DIR = PROJECT_ROOT / "assets"
OUTPUT_DIR = PROJECT_ROOT / "output"
BGM_DIR = ASSETS_DIR / "bgm"
MATERIAL_DIR = ASSETS_DIR / "素材池_待剪辑"
THUMBNAILS_DIR = ASSETS_DIR / "thumbnails"

OUTPUT_DY = OUTPUT_DIR / "抖音"
OUTPUT_XHS = OUTPUT_DIR / "小红书"
OUTPUT_BILIBILI = OUTPUT_DIR / "B站"

TOPICS_DB = DATA_DIR / "topics.db"

# ========== 本地Ollama配置 ==========
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5-14b"  # 支持原生Function Calling
OLLAMA_TIMEOUT = 120

# ========== 云端API配置（可选） ==========
# 若本地Ollama不可用，将自动使用云端API
# 支持：OpenAI、通义千问(qwen)、DeepSeek、GLM-4 等OpenAI格式API
OPENAI_API_KEY = ""                      # API密钥，如 "sk-xxxxx"
OPENAI_API_BASE = "https://api.openai.com/v1"  # 接口地址
OPENAI_MODEL = "gpt-4o"                 # 模型名称

# Agent企业级配置
AGENT_CONFIG = {
    "max_retries": 3,              # 最大重试次数
    "retry_backoff": 2,            # 指数退避因子
    "stream_output": True,          # 启用流式输出
    "enable_mcp": True,            # 启用MCP协议
    "enable_multi_user": False,    # 默认关闭多用户
    "log_to_ui": True,             # 日志推送到前端
    "persist_memory": True,         # 记忆持久化
}

WHISPER_MODEL = "base"
WHISPER_LANGUAGE = "zh"

OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
OUTPUT_FPS = 30
OUTPUT_CRF = 23
OUTPUT_AUDIO_BITRATE = "192k"
OUTPUT_VIDEO_BITRATE = "2M"
DEFAULT_VIDEO_DURATION = 30

PLATFORM_CONFIGS = {
    "抖音": {
        "max_duration": 60,
        "min_duration": 15,
        "aspect_ratio": "9:16",
        "output_dir": OUTPUT_DY,
        "title_max_len": 40,
        "desc_max_len": 200,
        "hashtags_max": 20,
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
    "B站": {
        "max_duration": 600,
        "min_duration": 30,
        "aspect_ratio": "9:16",
        "output_dir": OUTPUT_BILIBILI,
        "title_max_len": 60,
        "desc_max_len": 500,
        "hashtags_max": 10,
    },
}

CATEGORIES = {
    "知识付费": ["干货分享", "技能教学", "职场晋升", "创业故事", "学习技巧", "知识变现"],
    "美食探店": ["各地美食", "网红餐厅", "家常菜谱", "小吃推荐", "快手料理", "减脂餐"],
    "生活方式": ["日常VLOG", "极简生活", "穿搭美妆", "健身打卡", "家居收纳", "自律生活"],
    "情感心理": ["情感故事", "心理分析", "两性关系", "自我成长", "人际交往", "情绪管理"],
    "科技数码": ["产品测评", "APP推荐", "科技前沿", "使用技巧", "效率工具", "AI应用"],
    "娱乐搞笑": ["搞笑段子", "萌宠动物", "热点吐槽", "影视解说", "明星娱乐", "游戏解说"],
}

TRENDING_TAGS = [
    "#爆款", "#必看", "#干货分享", "#建议收藏", "#涨知识",
    "#揭秘", "#干货", "#好物推荐", "#宝藏", "#治愈",
    "#人间真实", "#破防了", "#绝绝子", "#神仙打架", "#YYDS",
]

BGM_VOLUME = 0.3
BG_MUTE_DURATION = 2

LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"

DB_TOPICS_TABLE = "topics"
DB_SCRIPTS_TABLE = "scripts"
DB_ANALYTICS_TABLE = "analytics"

CACHE_CONFIG = {
    "enabled": True,
    "maxsize": 2000,
    "preload_count": 500,
}

CRAWLER_CONFIG = {
    "enabled": True,
    "offline_mode_after_crawl": True,
    "headless": True,
    "request_delay": (1, 3),
    "max_topics_per_platform": 500,
}

LIBRARY_EXPAND_CONFIG = {
    "target_count": 1000,
    "synthetic_ratio": 0.8,
}


def ensure_dirs():
    """确保所有必要目录存在"""
    for dir_path in [DATA_DIR, ASSETS_DIR, OUTPUT_DIR, BGM_DIR, MATERIAL_DIR, THUMBNAILS_DIR,
                     OUTPUT_DY, OUTPUT_XHS, OUTPUT_BILIBILI]:
        dir_path.mkdir(parents=True, exist_ok=True)
