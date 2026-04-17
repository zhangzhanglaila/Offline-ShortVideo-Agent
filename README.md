# Offline-ShortVideo-Agent

<div align="center">

# 一个人就是一支短视频工厂

### 零API成本 · 100%离线运行 · 全自动爆款流水线

**爆款选题 → AI脚本 → Ken Burns动画 → TTS配音 → 字幕烧录 → 多平台发布包**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Windows](https://img.shields.io/badge/Windows-Supported-green.svg)](https://github.com)

*一个人干过一个MCN机构，你信吗？*

</div>

---

## 你是不是也有这些痛苦？

- 😩 **每天想选题想到头秃**，写脚本改八遍还觉得不够好
- 😩 **花几百块买API额度**，结果流量还是零
- 😩 **剪辑软件学了三周**，导出视频还是糊的
- 😩 **投了十几种AI工具"自动化"**，还是要手动一个个传素材
- 😩 **终于剪完了视频**，发现BGM版权问题导致下架

**这个项目，就是来解决这些问题的。**

---

## 核心亮点

### 🔥 全自动流水线，零人工干预

```
输入一个关键词 → 30秒后 → 抖音/小红书/B站 发布包已生成
```

从**爆款选题挖掘**、**AI口播脚本**、**Ken Burns电影级运镜**、**TTS配音**、**Whisper字幕同步**，到**多平台标题算法适配**，全链路一键完成。

### 💰 彻底0成本，不花一分钱API

- 脚本生成：本地Ollama（免费） + 云端API兜底降级
- TTS配音：Edge-TTS（微软免费） → 讯飞TTS → 百度TTS → edge-tts（无限）
- 字幕生成：faster-whisper（本地，免费） → 规则算法（降级）
- 图片素材：Pexels/Unsplash API（每天免费200张） → Bing图片爬虫（完全免费）
- 视频剪辑：FFmpeg（开源免费）

### 🎬 影院级视觉效果

| 效果 | 说明 |
|------|------|
| **Ken Burns** | 缩放+平移，静态图片也有电影感 |
| **技术讲座风格** | 顶部大标题 + 左侧知识点 + 右侧代码高亮，编程UP主同款 |
| **2D流程图动画** | 矩形模块+箭头流向+逐帧绘制，极客架构讲解神器 |
| **淡入淡出转场** | 专业级镜头衔接 |
| **逐字打字机字幕** | 重要内容逐字出现，完播率拉满 |

### 🌐 多平台智能适配

| 平台 | 最佳时长 | 封面风格 | 标题规则 |
|------|---------|---------|---------|
| 抖音 | 15-60秒 | 竖屏9:16 | ≤40字，悬念式 |
| 小红书 | 10秒-5分钟 | 竖屏+文字叠加 | ≤20字，干货感 |
| B站 | 30秒-10分钟 | 竖屏 | ≤60字，系列感 |

---

## 效果预览

```
【输入】关键词: Python异步编程
【输出】

🎬 视频1: Python异步编程.mp4
├── Ken Burns背景: 缩放+平移动画
├── 顶部: "Python异步编程 _async/await详解"
├── 左侧: • 协程基础  • 并发模型  • 实战案例
├── 右侧: 代码高亮（Monokai主题）
└── 配音: 晓晓音色，语速1.2x

📦 抖音发布包/
│   ├── Python异步编程.mp4
│   ├── 标题.txt        → "【干货】async/await三分钟入门，看完就会！"
│   ├── 描述.txt        → "#Python #编程 #干货分享..."
│   └── 封面建议.txt

📦 小红书发布包/
│   ├── Python异步编程.mp4
│   ├── 标题.txt        → "Python异步编程｜核心就这三个字"
│   └── 标签.txt        → "#编程 #Python #效率工具 #干货"
```

---

## 技术架构

```
┌──────────────────────────────────────────────────────┐
│                    用户交互层                          │
│   main.py (CLI)  │  web/ (Gradio)  │  main_fastapi.py │
└──────────────────────────┬───────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────┐
│                  DualModeVideoGenerator               │
│    ┌─────────────────┐    ┌────────────────────────┐ │
│    │   Mode A 全自动  │    │   Mode B 素材剪辑      │ │
│    │  选题→脚本→配音  │    │  用户素材→拼接→字幕   │ │
│    │  →配图→动画→合成 │    │                        │ │
│    └─────────────────┘    └────────────────────────────┘ │
└──────────────────────────┬───────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────────┐
│ Ken Burns动画  │  │ 流程图动画    │  │  多轨道FFmpeg合成  │
│ (animation)   │  │ (diagram)    │  │  配音+BGM+字幕    │
└───────────────┘  └───────────────┘  └───────────────────┘
```

---

## 快速开始

### 环境要求

- Windows 10/11 或 macOS / Linux
- Python 3.10+
- **FFmpeg**（视频处理核心，必须）
- Ollama + qwen2.5-14b（脚本生成，可选）

### 30秒安装

```bash
# 1. 克隆项目
git clone https://github.com/zhangzhanglaila/Offline-ShortVideo-Agent.git
cd Offline-ShortVideo-Agent

# 2. 安装Python依赖
pip install -r requirements.txt

# 3. 安装FFmpeg（Windows）
winget install ffmpeg

# 4. 一键启动
python main.py
```

### 目录结构

```
Offline-ShortVideo-Agent/
├── main.py                    # 主程序入口（CLI）
├── main_fastapi.py            # API服务器（Gradio前端）
├── config.py                  # 全局配置
├── requirements.txt           # Python依赖
├── core/
│   ├── dual_mode_module.py    # 双模式生成引擎
│   ├── animation_module.py    # Ken Burns + 技术讲座动画
│   ├── diagram_animation_module.py  # 2D流程图动画
│   ├── image_fetch_module.py   # 图片抓取（Pexels/Bing）
│   ├── tts_module.py          # TTS配音降级链
│   ├── subtitle_module.py     # 字幕生成（Whisper/SRT）
│   ├── script_module.py       # AI脚本生成
│   └── topics_module.py       # 爆款选题库
└── assets/
    ├── bgm/                   # BGM音乐素材
    └── 素材池_待剪辑/          # 图片/视频素材
```

---

## 使用示例

### 方式一：技术讲座风格（编程/科技类内容）

```python
from core.dual_mode_module import get_dual_mode_generator

gen = get_dual_mode_generator()

# 技术讲座风格，自动识别并生成2D架构图动画
result = gen.generate_mode_a(
    topic_keyword="HTTP请求流程",
    category="科技数码",           # ← 自动触发 tech_lecture 风格
    style="tech_lecture",
    platform="B站",
    duration=45,
    voice="zh-CN-YunxiNeural",   # 磁性男声
)
```

脚本中包含架构描述（用 ` ```diagram ` 代码块），自动生成流程图动画：

````
```diagram
[client] 用户浏览器 (300, 80, 160, 70, teal)
[cdn] CDN节点 (150, 200, 160, 70, blue)
[server] API网关 (450, 200, 160, 70, blue)
[auth] 认证服务 (150, 350, 160, 70, orange)
[db] 数据库 (450, 350, 160, 70, orange)

[client] -> [cdn] DNS解析
[cdn] -> [server] 回源
[server] -> [auth] JWT验证
[auth] -> [db] 权限查询
```
````

### 方式二：普通短视频（全自动流水线）

```python
result = gen.generate_mode_a(
    topic_keyword="女生自律的10个好处",
    category="生活方式",
    platform="抖音",
    duration=30,
    add_bgm=True,
    fetch_images=True,   # 联网抓配图
)
```

---

## 更新日志

### v1.1.0 (2026-04)
- ✨ 新增 **2D流程图动画引擎** - 矩形+箭头+逐帧绘制
- ✨ 新增 **技术讲座风格** - 顶部标题+左侧知识点+代码高亮
- ✨ 新增 **Bing图片爬虫** - Pexels/Unsplash API全部降级后的最终兜底
- ✨ 图片抓取降级链：**Pexels → Unsplash → Bing图片（无需Key）**
- 🐛 修复 Python 3.10 兼容性问题

### v1.0.0 (2026-04)
- 首发版本，6大核心模块完整实现
- 支持抖音/小红书/B站
- 100%离线、零API、零付费

---

## 常见问题

**Q: 运行报错 "FFmpeg not found"**
```bash
# Windows
winget install ffmpeg

# 验证安装
ffmpeg -version
```

**Q: TTS配音报错**
A: 检查 `.env` 文件中 TTS 配置。已内置5层降级：讯飞 → 百度 → Edge-TTS → gTTS → SAPI，不会完全失败。

**Q: 图片抓取为0张**
A: 三层降级：① Pexels API (需Key) ② Unsplash API (需Key) ③ **Bing图片爬虫（无需Key）**。确保网络畅通即可。

**Q: 如何生成技术讲座风格视频？**
A: 设置 `category="科技数码|技术教程|编程教学|极客科普"` 任一，或传入 `style="tech_lecture"`

---

## 贡献指南

欢迎提交 Issue 和 PR！如果你有新的动画风格想法、平台适配方案，欢迎交流。

---

<div align="center">

**Star这个项目，让更多人看到它**

*如果你在做短视频，这个工具值得你花30分钟体验一下。*

</div>
