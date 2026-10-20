# 🏗️ 动态化视频 技术设计

## 核心思路转变

```
旧: 场景 → 1张静态PNG → 定格N秒
新: 场景 → 多个动画图层 → 渲染为运动视频片段
```

**关键概念**: 每个场景不再是一张图，而是一条**动画时间轴**，
包含背景层、字幕层、元素层，每层有自己的运动参数。

---

## 渲染方案选型

### 三种技术路线

| 方案 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| **A. FFmpeg滤镜** | zoompan/overlay/fade表达式 | 高效、无需逐帧 | 复杂动画表达受限 |
| **B. 逐帧PIL** | 每帧用PIL绘制所有元素 | 完全可控 | 慢（30fps×时长） |
| **C. 混合** | 背景用A，元素预渲染PNG后用A overlay | 平衡效率与灵活 | 需分层设计 |

### 决策: 采用 **方案C 混合**
- **背景运镜**: FFmpeg `zoompan`（高效实现Ken Burns）
- **元素动画**: 预渲染带透明通道的元素层PNG → FFmpeg `overlay`
  用时间表达式控制位置/透明度/缩放（滑入、淡入、放大）
- **复杂序列**: 必要时局部逐帧PIL兜底
- **降级**: 任何环节失败 → 回退现有静态渲染

---

## 分层架构

### Scene → Layers 模型

```
Scene (场景)
├── BackgroundLayer  背景层
│   ├── 静图 + Ken Burns运镜  (zoompan)
│   └── 或 视频素材            (loop/scale)
├── OverlayLayer     遮罩层
│   └── 渐变/暗角 (让文字可读，但更轻)
├── SubtitleLayer    字幕层
│   └── 淡入/上滑入场
└── ElementLayer[]   元素层(可多个)
    ├── 标题(逐字/逐行)
    ├── 关键词强调(放大/高亮)
    ├── 图标/形状
    └── 序号/要点 (逐个出现)
```

### 动画时间轴

每个图层有 `AnimationSpec`:
```python
@dataclass
class AnimationSpec:
    anim_type: str      # fade_in/slide_up/zoom_in/typewriter/none
    start: float        # 相对场景的开始时间(秒)
    duration: float     # 动画时长
    easing: str         # linear/ease_out/spring (复用spring_easing.py)
    params: dict        # 方向、幅度等
```

多个元素通过不同 `start` 实现"逐个出现"的编排感。

---

## FFmpeg 关键技术

### Ken Burns (zoompan)
```
# 缓慢放大 + 向右平移
zoompan=z='min(zoom+0.0015,1.15)':x='iw/2-(iw/zoom/2)+t*10':
        y='ih/2-(ih/zoom/2)':d=<frames>:s=1080x1920:fps=30
```
- 方向随场景索引变化（放大/缩小/左移/右移），避免雷同

### 元素滑入 (overlay + 表达式)
```
# 字幕从底部滑入 + 淡入
overlay=x=(W-w)/2:y='H-h-100+max(0,50-t*100)':
        enable='gte(t,0.3)'
# 配合元素PNG的alpha渐变
```

### 逐字打字 (drawtext 或 分段overlay)
```
# 用 n 个关键帧PNG，每帧多显示一个字，overlay切换
# 或 drawtext + text表达式截取
```

### 视频素材背景
```
# 视频loop到场景时长 + cover-fit
-stream_loop -1 -i clip.mp4 -t <dur>
scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920
```

---

## 复用已有资产映射

| 需求 | 复用模块 | 说明 |
|------|---------|------|
| 缓动函数 | `core/spring_easing.py` | ease/spring曲线 |
| 文字动效逻辑 | `core/text_animator.py` | 5种动效参考 |
| 转场 | `legacy/transition_effects.py` | 13种转场（已归档；D4 实际用 FFmpeg xfade） |
| 节奏 | `core/rhythm_engine.py` | 场景时长/节拍 |
| 视频素材 | `core/stock_video_module.py` | Pexels/Pixabay视频 |
| 音频分析 | `core/audio_analyzer.py` | BPM/节拍对齐 |

---

## 模块设计

### 新增模块
```
core/compose/
├── motion/
│   ├── ken_burns.py         # zoompan运镜参数生成
│   ├── layer_renderer.py    # 元素层PNG渲染(带alpha)
│   ├── animation_spec.py    # AnimationSpec数据模型
│   └── clip_builder.py      # 场景→运动片段(FFmpeg滤镜编排)
```

### 改造点
- `scene_image_renderer.py`: 拆分为"背景渲染"+"元素层渲染"
- `ffmpeg_composer.py`: 支持带滤镜的片段生成(zoompan/overlay)
- `video_compose_agent.py`: 编排调用运动合成，失败降级静态

### 数据模型扩展
- `Scene` 增加可选 `layout` / `elements` 字段（LLM可产出，或规则生成）
- 内容分析Agent的prompt可增强，让LLM输出元素编排建议

---

## 降级链（延续三级容错）

```
运动合成(zoompan+overlay+视频素材)
   ↓ 失败
简单运镜(仅zoompan)
   ↓ 失败
静态渲染(当前Phase 3方案)
   ↓ 失败
纯色卡+文字
```

---

## 性能考量

| 措施 | 说明 |
|------|------|
| 滤镜优先 | zoompan/overlay比逐帧PIL快数倍 |
| 元素层缓存 | 相同元素PNG复用 |
| 分辨率自适应 | 预览可用540x960，成片1080x1920 |
| 并行片段 | 各场景片段可并行渲染 |
| 超时保护 | 单片段FFmpeg超时回退静态 |

---

## 测试策略

- 每个motion模块独立单元测试（参数生成、层渲染）
- FFmpeg滤镜串用短片段冒烟测试
- 降级路径必测（模拟FFmpeg失败）
- 真实端到端抽帧验证运动（对比首尾帧差异）

---

*最后更新: 2026-07-26*
*文档版本: 1.0*
