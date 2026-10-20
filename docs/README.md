# 文档索引

按阶段编号，编号唯一、连续。想看「系统现在是什么样」，从 `01` 和 `20` 开始读。

## 基础规范（00–06）

| 文档 | 内容 | 状态 |
|---|---|---|
| [00-requirement-spec](00-requirement-spec.md) | 需求范围、验收标准、质量指标 | 现行 |
| [01-architecture-design](01-architecture-design.md) | 多 Agent 架构、消息协议、数据流 | 现行 |
| [02-development-phases](02-development-phases.md) | Phase 0–5 开发计划与任务拆解 | 历史（已完成） |
| [03-coding-standards](03-coding-standards.md) | 编码规范、审查清单 | 现行 |
| [04-tech-spec](04-tech-spec.md) | 技术选型与模块边界 | 历史 |
| [05-roadmap](05-roadmap.md) | 早期路线图（风格/素材/节奏/工作流） | 历史 |
| [06-style-templates](06-style-templates.md) | 视觉风格模板格式 | 现行 |

## 动态化 D 系列（10–12）

| 文档 | 内容 |
|---|---|
| [10-dynamic-requirements](10-dynamic-requirements.md) | 静态 PPT 式画面的问题与目标 |
| [11-dynamic-design](11-dynamic-design.md) | 分层架构、FFmpeg 技术、资产复用 |
| [12-dynamic-phases](12-dynamic-phases.md) | D1–D6 阶段拆解 |

## E 系列：模板 / AI 媒体 / 历史（20–27）

| 文档 | 内容 | 状态 |
|---|---|---|
| [20-architecture-v2](20-architecture-v2.md) | 服务层架构（API / 服务 / 核心三层） | 现行 |
| [21-phase-E-plan](21-phase-E-plan.md) | E0–E5 总规划 | 现行 |
| [22-E0-tasks](22-E0-tasks.md) | 基础设施任务 | 已完成 |
| [23-E1-templates](23-E1-templates.md) | 模板系统设计 | 已完成 |
| [24-E2-ai-image](24-E2-ai-image.md) | AI 生图集成 | 已完成 |
| [25-E3-ai-video](25-E3-ai-video.md) | AI 生视频集成 | 已完成 |
| [26-E4-history-ui](26-E4-history-ui.md) | 历史管理 UI | 部分（作品库走 `/api/works`） |
| [27-E5-comfyui](27-E5-comfyui.md) | ComfyUI 本地生成集成 | 已完成 |

## 模板与统一化（28–29）

| 文档 | 内容 | 状态 |
|---|---|---|
| [28-template-renderer-usage](28-template-renderer-usage.md) | 模板渲染用法 | 现行 |
| [29-unify-generation-design](29-unify-generation-design.md) | 主题 → 脚本 → 渲染的统一化重构（U1–U3）及后续修复 | 现行 |

## 子目录

- [`superpowers/`](superpowers/)：计划（plans）与规格（specs）草稿，按日期命名，属于过程产物，不是权威文档。
- 工作日志在 [`../devlog/`](../devlog/)，每日记录 + 阶段总结。
- 使用说明：[`../README.md`](../README.md)（功能全景 / 快速开始 / FAQ），CLI 与编程接口见 [`../README_AGENT.md`](../README_AGENT.md)。

## 约定

1. **新增文档必须取未占用编号**，不要再出现 `01-` 或 `28-` 两份。
2. 阶段完成后把状态改为「已完成」，并在 `devlog/phase-*.md` 留总结。
3. 过程草稿放 `superpowers/`，不要混进编号文档。
