# 视频技能（Skills）

「技能」= 一份提示词配方 + 一组参数预设。用户选技能、填一两个字段，就能拿到结构完整的
专业提示词，而不是从零手写。

内置 12 个精选配方（电影感推镜 / 赛博朋克霓虹 / 产品微距 / 美食特写 / 航拍风光 /
国风水墨 / 科技 HUD / 治愈日常 / 街头纪实 / 抽象动态 / 人物情绪 / 日系动画）。

## 怎么用

Web → `🎨 AI 素材` → `🎬 生视频` → 点一个技能 → 填「画面主体」→ 提示词自动生成（可再手改）。

## 怎么加自己的技能

在 `skills/video/` 下新建 `<id>.json`，刷新页面即可看到（服务端每次请求重新扫描目录，不需要重启）：

```json
{
  "id": "my_style",
  "name": "我的风格",
  "description": "一句话说明适用场景",
  "tags": ["自定义"],
  "featured": false,
  "prompt_template": "{subject}，镜头缓慢推进，暖色调，胶片质感，高细节",
  "params": [
    { "key": "subject", "label": "画面主体", "placeholder": "例如：雨中的街道", "required": true },
    { "key": "mood", "label": "情绪", "placeholder": "宁静", "required": false, "default": "平静" }
  ],
  "defaults": { "size": "9:16", "resolution": "1080P", "duration": 5 },
  "comfyui": { "workflow": "workflows/video/wan22_t2v.json", "steps": 24, "seed": 42 },
  "examples": ["示例一", "示例二"]
}
```

### 字段说明

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | 建议 | 唯一标识，缺省用文件名 |
| `name` | ✅ | 选择器上显示的名字 |
| `description` | | 鼠标悬停提示 |
| `featured` | | 为 `true` 时排在最前并显示 ⭐ |
| `prompt_template` | ✅ | 用 `{参数key}` 占位；**占位符必须在 params 里声明** |
| `params[].key` | ✅ | 参数名，对应模板里的 `{key}` |
| `params[].required` | | 默认 `true`；选填项为空时用 `default` 或整段省略 |
| `defaults` | | 推荐生成参数，只认 `size` / `resolution` / `duration` / `provider` / `model` |
| `comfyui` | | 本地 ComfyUI 的 workflow / steps / seed（工作流支持 `{{prompt}}{{steps}}{{seed}}` 等占位符） |
| `examples` | | 展示给用户的示例输入 |

### 校验规则

- `name`、`prompt_template` 缺失 → 该技能被跳过（不会让服务起不来）
- 模板里出现未声明的占位符 → 该技能被跳过（避免渲染出 `{xxx}` 残留）
- 必填参数为空 → 接口返回 400，前端提示「请填写：xxx」

## 设计边界

- 技能**不改变模型能力**：万相/ComfyUI 仍然只吃文本提示词 + 尺寸/时长。提升来自
  提示词结构（主体/动作/运镜/光线/风格/画质）与参数搭配。
- 目前是纯模板填充（零成本）。后续可加「LLM 扩写」：把用户一句话 + 技能意图交给模型
  扩成完整提示词，生成前给用户预览。
- 负面提示词目前传不进生成接口（DashScope payload 与 ComfyUI 工作流都没有 negative 节点）。
