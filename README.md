# Offline-ShortVideo-Agent

<div align="center">

# 100%离线短视频AI生产Agent
### 零API · 零付费 · 无联网请求 · 无封号风险

**本地一键完成** 爆款选题→脚本分镜→自动剪辑→字幕烧录→多平台适配→数据复盘

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

</div>

---

##  产品定位

> 一句话：本地一键完成爆款选题→脚本分镜→自动剪辑→字幕烧录→多平台适配→数据复盘的全链路短视频生产Agent，替代5个工具，自媒体零门槛批量出片。

###  核心价值

- **100%离线运行** - 无任何OpenAI/云端API依赖
- **零成本** - 所有依赖开源免费，内置FFmpeg
- **无封号风险** - 只生成发布素材包，不自动发布
- **全闭环** - 6大核心模块，覆盖短视频生产全链路

---

##  功能模块

| 模块 | 功能 | 技术 |
|------|------|------|
| **1. 爆款选题** | 1000+预制爆款选题库，智能筛选推荐 | SQLite |
| **2. 脚本生成** | 黄金3秒钩子、口播脚本、分镜表 | Ollama(qwen2:7b) |
| **3. 自动剪辑** | 9:16竖屏裁剪、BGM、转场、高清输出 | FFmpeg |
| **4. 字幕模块** | 语音转字幕、SRT生成、硬字幕烧录 | faster-whisper |
| **5. 平台适配** | 抖音/小红书/视频号标题+标签+发布包 | 本地算法 |
| **6. 数据复盘** | 播放分析、爆款规律、迭代推荐 | SQLite |

---

##  快速开始

###  环境要求

- Windows 10/11 或 macOS/Linux
- Python 3.10+
- FFmpeg (视频处理)
- Ollama + qwen2:7b模型 (脚本生成，可选)

###  一键安装

```bash
# 1. 克隆项目
git clone https://github.com/yourname/Offline-ShortVideo-Agent.git
cd Offline-ShortVideo-Agent

# 2. 安装Python依赖
pip install -r requirements.txt

# 3. 安装FFmpeg (Windows)
winget install ffmpeg

# 或macOS
brew install ffmpeg

# 4. 安装Ollama (可选，用于脚本生成)
# https://ollama.com/download
ollama pull qwen2:7b

# 5. 启动
# Windows一键启动
run.bat

# 或命令行启动
python main.py
```

###  目录结构

```
Offline-ShortVideo-Agent/
├── main.py                 # 主程序入口
├── config.py               # 配置文件
├── requirements.txt        # Python依赖
├── run.bat                 # Windows一键启动
├── core/
│   ├── __init__.py
│   ├── db_init.py          # 数据库初始化
│   ├── topics_module.py     # 爆款选题模块
│   ├── script_module.py     # 脚本生成模块
│   ├── video_module.py      # 视频剪辑模块
│   ├── subtitle_module.py   # 字幕模块
│   ├── platform_module.py   # 多平台适配
│   └── analytics_module.py  # 数据复盘模块
├── data/
│   └── topics.db           # 选题数据库
├── assets/
│   ├── bgm/                # BGM素材目录
│   └── 素材池_待剪辑/        # 图片素材目录
└── output/                  # 输出目录
    ├── 抖音/
    ├── 小红书/
    └── 视频号/
```

---

##  使用教程

###  方式1: 交互式生产 (推荐新手)

```bash
python main.py
# 选择 5. 交互式生产
```

按提示选择赛道、选题、平台，系统自动执行完整流程。

###  方式2: 快速演示

```bash
python main.py
# 选择 6. 快速演示
```

无需素材池，快速查看脚本生成效果。

###  方式3: 完整生产流程

```bash
python main.py
# 选择 4. 执行完整生产流程
```

###  方式4: 代码调用

```python
from main import ShortVideoAgent

agent = ShortVideoAgent()

# 执行完整流程
result = agent.run_full_workflow(
    topic_id=1,           # 选题ID
    platform="抖音",        # 目标平台
    duration=30            # 视频时长(秒)
)

# 或分步执行
topics = agent.step2_recommend_topics(category="知识付费", count=5)
script = agent.step3_generate_script(topics[0], "抖音", 30)
```

---

##  素材准备

###  图片素材

将图片放入 `assets/素材池_待剪辑/` 目录，支持 jpg/png/webp 格式。

###  BGM音乐

将MP3/WAV格式的BGM文件放入 `assets/bgm/` 目录。

###  FFmpeg安装

**Windows:**
- 方法1: [官网下载](https://ffmpeg.org/download.html)
- 方法2: `winget install ffmpeg`
- 方法3: `scoop install ffmpeg`

**macOS:**
```bash
brew install ffmpeg
```

**Linux:**
```bash
sudo apt install ffmpeg  # Ubuntu/Debian
sudo yum install ffmpeg   # CentOS
```

###  Ollama安装 (可选)

脚本生成功能需要 Ollama + qwen2:7b 模型：

```bash
# macOS/Linux
curl -fsSL https://ollama.com/install.sh | sh

# Windows: https://ollama.com/download

# 下载模型
ollama pull qwen2:7b
```

---

##  输出说明

###  视频输出

生成的视频保存在 `output/平台/日期/` 目录下：

```
output/
├── 抖音/
│   └── 20240115/
│       ├── video_20240115_143022.mp4
│       ├── video_20240115_143022.txt    # 发布说明
│       └── video_20240115_143022.json   # 完整配置
├── 小红书/
└── 视频号/
```

###  发布说明文件

`.txt` 文件包含:
- 标题
- 描述/文案
- 话题标签
- 发布建议

`.json` 文件包含完整数据，可导入其他系统。

---

##  配置修改

编辑 `config.py` 文件：

```python
# 输出视频参数
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920   # 9:16竖屏
OUTPUT_FPS = 30
OUTPUT_CRF = 23         # 视频质量 (18-28)

# Ollama配置
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2:7b"

# Whisper配置
WHISPER_MODEL = "base"  # tiny/base/small/medium/large
```

---

##  赛道分类

| 大类 | 子类 |
|------|------|
| 知识付费 | 干货分享、技能教学、职场晋升、创业故事 |
| 美食探店 | 各地美食、网红餐厅、家常菜谱、小吃推荐 |
| 生活方式 | 日常VLOG、极简生活、穿搭美妆、健身打卡 |
| 情感心理 | 情感故事、心理分析、两性关系、自我成长 |
| 科技数码 | 产品测评、APP推荐、科技前沿、使用技巧 |
| 娱乐搞笑 | 搞笑段子、萌宠动物、热点吐槽、影视解说 |

---

##  数据复盘

系统会记录每条视频的数据:

- 播放量 (views)
- 点赞数 (likes)
- 评论数 (comments)
- 分享数 (shares)
- 完播率 (completion_rate)
- 平均观看时长

基于数据分析，自动:
- 识别爆款规律
- 分析最佳时长
- 推荐高概率选题

---

##  常见问题

### Q: 运行时提示 "Ollama连接失败"
A: Ollama未安装或未启动。安装后运行 `ollama serve`

### Q: 视频生成失败
A: 检查:
1. FFmpeg是否安装: `ffmpeg -version`
2. 素材池是否有图片
3. 输出目录是否有写入权限

### Q: 字幕生成失败
A:
1. 如果安装了大模型whisper (可选)，确保模型已下载
2. 或使用脚本直接生成字幕(默认方案)

### Q: 内存不足
A: 减小素材数量，或在config.py中降低OUTPUT_CRF值

---

##  更新日志

### v1.0.0 (2026-04)
- 首发版本
- 6大核心模块完整实现
- 支持抖音/小红书/视频号
- 100%离线、零API、零付费

---

##  免责声明

本项目仅供学习和研究使用。本项目生成的视频内容、选题、脚本等仅供参考，使用者需自行确保内容符合各平台规范，不得生成违规违法内容。

---

##  Star History

如果这个项目对你有帮助，请点个 Star！

<div align="center">

**Offline-ShortVideo-Agent** - 让短视频生产简单到极致

</div>
