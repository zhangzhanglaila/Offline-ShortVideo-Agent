# legacy/ — 已下线但保留的实现

这里放的是**不在主流程里、也没有任何模块导入**的代码。保留原因通常是：
曾经是某个阶段的核心实现、有展示/参考价值，或者未来可能重新接线。

> 约定：**生产代码不得 `import legacy.*`**。要用就先接线、补测试，再搬回主目录。

## 内容

| 文件 | 原位置 | 说明 | 为什么下线 |
|---|---|---|---|
| `eval_engine.py` | `core/eval_engine.py` | LLM-as-Judge 四维打分、Badcase 收集、回归跑分、质量报告 | 从未接入生成流程；需要 LLM 才能跑 |
| `transition_effects.py` | `core/transition_effects.py` | 13 种 PIL 转场 | D4 改用 FFmpeg `xfade`，画质更好；文档明确保留但不用 |

## 相关但未移动的旧实现

以下模块同样不在当前主流程，但被文档标注为「旧系统（保留）」或因互相引用而未移动：

- `core/video_composer.py`（旧合成器）
- `core/style_renderer.py` / `core/style_renderers.py` / `core/renderers/*`（旧风格渲染栈）
- `engine/`（Narrative OS 遗留管线，`engine.render` 仅被 `engine/bridge/bridge.py` 引用）

如果确认不再需要，建议下一轮统一迁到 `legacy/` 或删除。
