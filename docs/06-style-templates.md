# 风格模板设计规范

## 概述

本文档定义短视频风格模板的设计规范和配置格式。

---

## 风格分类

### 1. Minimal (极简风格)

**适用场景**: 知识科普、教育、商务

**设计特征**:
- 大量留白
- 纯色背景(白/黑/灰)
- 大字号粗体标题
- 极简线条装饰
- 单色或双色配色

**示例配置**:
```yaml
id: "minimal"
name: "极简风格"
category: "education"
colors:
  background: "#FFFFFF"
  primary: "#1A1A1A"
  secondary: "#666666"
  accent: "#3B82F6"
typography:
  title_font: "Arial-Bold"
  title_size: 56
  body_font: "Arial"
  body_size: 28
  line_height: 1.4
layout:
  padding: 80
  card_bg: "transparent"
  card_border: "#E5E5E5"
  card_border_width: 1
  card_radius: 8
effects:
  transition: "fade"
  emphasis: "scale"
  animation_speed: "normal"
```

---

### 2. Vibrant (活力风格)

**适用场景**: 生活方式、美妆、美食、旅行

**设计特征**:
- 高饱和度色彩
- 渐变背景
- 圆润几何形状
- 动态阴影
- 多色配色(3-4色)

**示例配置**:
```yaml
id: "vibrant"
name: "活力风格"
category: "lifestyle"
colors:
  background:
    type: "gradient"
    value: ["#FF6B6B", "#4ECDC4", "#45B7D1"]
  primary: "#FFFFFF"
  secondary: "#FFF0F0"
  accent: "#FF6B6B"
typography:
  title_font: "Arial-Bold"
  title_size: 52
  body_font: "Arial"
  body_size: 26
layout:
  padding: 40
  card_bg: "#FFFFFF"
  card_border: "transparent"
  card_shadow: "0 8 24 rgba(0,0,0,0.12)"
  card_radius: 20
effects:
  transition: "zoom"
  emphasis: "bounce"
  animation_speed: "fast"
```

---

### 3. Cinematic (电影风格)

**适用场景**: 故事叙述、纪录片、情感内容

**设计特征**:
- 暗调背景
- 胶片颗粒质感
- 电影感调色( teal & orange )
- 窄边框
- 衬线字体

**示例配置**:
```yaml
id: "cinematic"
name: "电影风格"
category: "storytelling"
colors:
  background: "#0A0A0A"
  primary: "#E5E5E5"
  secondary: "#8A8A8A"
  accent: "#FF9500"
typography:
  title_font: "Georgia-Bold"
  title_size: 48
  body_font: "Georgia"
  body_size: 24
layout:
  padding: 60
  card_bg: "#1A1A1A"
  card_border: "#333333"
  card_border_width: 2
  card_radius: 4
effects:
  transition: "fadegrays"
  emphasis: "slow_zoom"
  animation_speed: "slow"
  film_grain: true
  vignette: true
```

---

### 4. Tech (科技风格)

**适用场景**: 科技评测、编程教程、极客内容

**设计特征**:
- 深色背景
- 霓虹点缀(cyan/magenta)
- 网格/线条装饰
- 等宽字体代码块
- 发光效果

**示例配置**:
```yaml
id: "tech"
name: "科技风格"
category: "technology"
colors:
  background: "#0D1117"
  primary: "#C9D1D9"
  secondary: "#8B949E"
  accent: "#58A6FF"
typography:
  title_font: "Arial-Bold"
  title_size: 44
  body_font: "Consolas"
  body_size: 22
  code_font: "Consolas"
  code_size: 18
layout:
  padding: 50
  card_bg: "#161B22"
  card_border: "#30363D"
  card_border_width: 1
  card_radius: 6
  grid_overlay: true
effects:
  transition: "wipe"
  emphasis: "glow"
  animation_speed: "normal"
  glow_intensity: 0.6
```

---

### 5. Manga (漫画风格) - 优化现有

**适用场景**: 二次元、ACG、轻科普

**保留特征**:
- 网点纸纹理
- 速度线
- 气泡框
- 粗描边
- 红蓝点缀

**优化方向**:
- 色彩更柔和
- 排版更现代
- 支持浅色模式
- 减少视觉疲劳

---

## 配置格式标准

### 文件结构
```
config/styles/
├── minimal.yaml
├── vibrant.yaml
├── cinematic.yaml
├── tech.yaml
└── manga.yaml
```

### YAML Schema
```yaml
id: string              # 唯一标识
name: string            # 显示名称
category: string        # 分类: education/lifestyle/tech/etc
colors:                 # 色彩配置
  background: string | object    # 颜色或渐变
  primary: string
  secondary: string
  accent: string
typography:             # 字体配置
  title_font: string
  title_size: number
  body_font: string
  body_size: number
layout:                 # 布局配置
  padding: number
  card_bg: string
  card_border: string
  card_radius: number
effects:                # 效果配置
  transition: string
  emphasis: string
  animation_speed: string   # slow/normal/fast
```

---

## 渲染接口

```python
class StyleRenderer:
    def render_frame(
        self,
        content: dict,      # 脚本内容
        style_config: dict, # 风格配置
        output_path: str
    ) -> str
```

---

*最后更新: 2026-07-21*
