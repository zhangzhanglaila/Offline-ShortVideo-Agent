# 🚀 Offline-ShortVideo-Agent (Agent版) - 工作指南

**项目状态**: Agent系统Phase 0-5已完成 → 动态化(D)+增强(E)已完成 → 统一化重构(U)已完成 → 智能导演/编译器已落地
**当前版本**: 2.1-dev
**最后更新**: 2026-10-19

---

## 📋 项目概述

一个基于**多Agent协作架构**的智能短视频自动生成系统。用户提供简单需求，系统通过协调多个专职Agent自动生成短视频。

**已完成 (Phase 0-5)**: 内容分析→素材检索→视频合成→主控集成→CLI，
可一键生成"素材+字幕"视频。

**已完成 (D系列)**: 动态化增强 —— 运镜、元素动画、分层编排、转场、视频背景、BGM。

**已完成 (智能导演)**: `thinking/` + `runtime/` + `compiler/` + `ir/` 构成人在回路的
视频编译器（四层 IR、DAG 增量重算、分支合并、撤销重做），Web 端位于「🧠 智能导演」页。

### 核心价值
- ✅ 自动化短视频生成（文本 → 视频）
- ✅ 智能内容分析（DeepSeek LLM + 规则降级）
- ✅ 多源素材自动检索（Pexels/Pixabay/Unsplash）
- ✅ 素材+文字配合展示
- ✅ 动态运镜、元素动画、多样化（D1–D6 已完成）

---

## 📂 快速导航 - 标准文档

### 🎯 Agent系统 (Phase 0-5, 已完成)
| 文档 | 位置 | 用途 |
|------|------|------|
| **需求规范** | `docs/00-requirement-spec.md` | 系统需求、功能范围、验收标准 |
| **架构设计** | `docs/01-architecture-design.md` | Agent设计、消息协议、数据流 |
| **开发步骤** | `docs/02-development-phases.md` | Agent分阶段开发计划 |
| **编码规范** | `docs/03-coding-standards.md` | Python/Agent编码标准 |
| **使用说明** | `README_AGENT.md` | CLI用法、编程接口 |

### 🎬 动态化增强 (D系列, 已完成)
| 文档 | 位置 | 用途 |
|------|------|------|
| **动态化需求** | `docs/10-dynamic-requirements.md` | 静态问题分析、动态化目标 |
| **动态化设计** | `docs/11-dynamic-design.md` | 分层架构、FFmpeg技术、资产复用 |
| **动态化步骤** | `docs/12-dynamic-phases.md` | D1-D6阶段规划 |

### 📅 工作记录
| 文件夹 | 内容 | 更新频率 |
|--------|------|---------|
| `devlog/daily/` | 每日工作日志 | 每天 |
| `devlog/phase-*.md` | 各阶段总结文档 | Phase完成时 |

---

## 🔧 系统架构速览

```
用户需求
   ↓
主控Agent (CoordinatorAgent)
   ├→ 任务拆解
   ├→ 子Agent分发
   ├→ 结果汇总
   └→ 异常处理
   ↓
┌─────────────────────────────────────┐
│  内容分析   素材检索   视频合成      │
│   Agent      Agent       Agent      │
└─────────────────────────────────────┘
   ↓
消息总线 (MessageBus)
   ↓
输出: 视频 + 报告
```

**关键特点**:
- 4个独立Agent（1主+3子）
- 异步事件驱动通信
- 结构化JSON消息格式
- 完善的异常恢复机制

详见: `docs/01-architecture-design.md`

---

## 📅 开发阶段规划

### Agent系统 (Phase 0-5) — ✅ 全部完成
| 阶段 | 目标 | 状态 |
|------|------|------|
| Phase 0 | Agent框架 + MessageBus | ✅ |
| Phase 1 | 内容分析Agent | ✅ |
| Phase 2 | 素材检索Agent | ✅ |
| Phase 3 | 视频合成Agent | ✅ |
| Phase 4 | 主控Agent集成 | ✅ |
| Phase 5 | CLI用户接口 | ✅ |

### 动态化增强 (D系列) — ✅ 全部完成
| 阶段 | 目标 | 状态 |
|------|------|------|
| **D1** | 运动镜头(Ken Burns)+全屏 | ✅ |
| **D2** | 文字/字幕入场动画 | ✅ |
| **D3** | 场景内多元素分层编排 | ✅ |
| **D4** | 丰富转场 | ✅ |
| **D5** | 视频素材背景 | ✅ |
| **D6** | 背景音乐 | ✅ |

动态化模块位于 `core/compose/motion/`(ken_burns/text_animations/
scene_composer/transitions/bgm)。测试见 test_{ken_burns,text_animations,
scene_composer,transitions,video_material,bgm}.py。

**每个阶段必须满足**:
- ✅ 功能完整可用 + 降级保底
- ✅ 单元测试通过 + 真实抽帧验证动态效果
- ✅ 更新devlog/daily/ + git提交(.env不入库)

详见: `docs/12-dynamic-phases.md`

---

## 🛠️ 开发工作流

### 每天工作流程

1. **开始工作**
   ```bash
   cd D:\Offline-ShortVideo-Agent
   git checkout main
   git pull origin main
   ```

2. **查看标准**
   - 阅读当前Phase的需求 (docs/xx-requirement.md)
   - 查看编码规范 (docs/03-coding-standards.md)
   - 回顾前一天的进度 (devlog/daily/YYYY-MM-DD.md)

3. **开发代码**
   - 遵循编码规范
   - 写完整的类型提示
   - 添加Docstring和日志
   - 编写对应的单元测试

4. **提交代码**
   ```bash
   git add .
   git commit -m "feat(phase-X): description"
   ```

5. **更新日志**
   - 编辑 `devlog/daily/2026-07-21.md`
   - 记录完成事项
   - 记录待办事项
   - 标注风险问题

### 代码组织结构

```
Offline-ShortVideo-Agent/
├── core/
│   ├── agents/           ← Agent实现（新建）
│   │   ├── base_agent.py
│   │   ├── message_bus.py
│   │   ├── coordinator_agent.py
│   │   ├── content_analysis_agent.py
│   │   ├── material_fetch_agent.py
│   │   └── video_compose_agent.py
│   │
│   ├── models/          ← 数据模型（新建）
│   │   ├── message.py
│   │   ├── request.py
│   │   └── result.py
│   │
│   ├── renderers/       ← 旧系统（保留）
│   ├── video_composer.py
│   └── ...
│
├── docs/
│   ├── 00-requirement-spec.md      ← 需求规范
│   ├── 01-architecture-design.md   ← 架构设计
│   ├── 02-development-phases.md    ← 开发步骤
│   └── 03-coding-standards.md      ← 编码规范
│
├── devlog/
│   ├── daily/
│   │   ├── 2026-07-21.md
│   │   ├── 2026-07-22.md
│   │   └── ...
│   ├── 2026-07-21.md               ← 项目主日志
│   └── phase-X-summary.md          ← 各阶段总结
│
├── test_*.py            ← 测试文件
├── CLAUDE.md            ← 本文件
├── README.md
└── ...
```

---

## 📖 重要标准与规范

### Agent编程规范
- 所有Agent继承自 `BaseAgent`
- 实现 `async execute(task)` 方法
- 返回标准化的Result对象
- 完善的异常处理和日志

详见: `docs/03-coding-standards.md` → Agent编程规范

### 消息格式
```json
{
  "msg_id": "msg_20260721_xxx",
  "timestamp": "2026-07-21T10:00:00Z",
  "sender": "coordinator",
  "receiver": "content_analysis",
  "msg_type": "task",
  "task_type": "analyze",
  "payload": { ... },
  "status": "pending"
}
```

详见: `docs/03-coding-standards.md` → 消息格式规范

### 开发检查清单
每个Phase完成前必须：
- [x] 所有单元测试通过
- [x] 代码通过PEP 8检查
- [x] 类型提示完整
- [x] Docstring完善
- [x] 关键路径有日志
- [x] 异常处理完善
- [x] Git提交清晰

详见: `docs/03-coding-standards.md` → 代码审查清单

---

## 🐛 常见问题

### Q: 如何开始Phase X的开发？
A: 
1. 阅读 `docs/00-requirement-spec.md` 中的需求
2. 查看 `docs/02-development-phases.md` 中该Phase的具体任务
3. 按照 `docs/03-coding-standards.md` 编写代码
4. 每天更新 `devlog/daily/YYYY-MM-DD.md`

### Q: 代码不通过审查怎么办？
A: 
1. 查看具体的审查意见
2. 参考 `docs/03-coding-standards.md` 修正
3. 修正后重新提交
4. 重复直到通过

### Q: 遇到技术问题怎么办？
A: 
1. 检查相关文档（architecture/coding standards）
2. 查看之前Phase的类似代码
3. 如果仍无法解决，记录到 `devlog/daily/` 并标注🚨

### Q: 项目延期了怎么办？
A: 
1. 分析具体原因
2. 根据 `docs/02-development-phases.md` 中的风险应对方案
3. 可降低当前Phase范围，迁移到后续Phase
4. 更新 `devlog/` 记录风险和调整方案

---

## 📊 质量指标

### 代码质量
- 单元测试覆盖率: ≥ 80%
- 代码审查通过率: 100%
- PEP 8符合率: 100%

### 功能完成度
- 功能验收标准通过率: 100%
- 集成测试通过率: ≥ 95%

### 性能指标
- 单个视频生成耗时: < 2分钟
- 内存占用: < 500MB
- 并发能力: ≥ 3个同时生成

详见: `docs/00-requirement-spec.md` → 质量需求

---

## 🚨 风险预警与降级方案

### 如果素材获取失败
- 降级方案: 使用纯文本卡或占位符
- 重试策略: 最多2次
- 详见: `docs/00-requirement-spec.md` → 异常处理

### 如果视频合成超时
- 降级方案: 输出图文模式而非完整视频
- 重试策略: 无重试，直接降级
- 详见: `docs/00-requirement-spec.md` → 异常处理

### 如果进度落后
- 处理: 降低当前Phase范围或推迟到后续Phase
- 详见: `docs/02-development-phases.md` → 风险应对

---

## 📞 获取帮助

### 找不到信息？
1. 查看 `docs/` 中的标准文档
2. 查看 `devlog/daily/` 中的前几天日志
3. 查看旧系统的实现 (`core/renderers/` 等)

### 需要决策？
- 小决策 (编码规范等): 查看 `docs/03-coding-standards.md`
- 大决策 (架构改变): 更新 `docs/01-architecture-design.md` 并记录到 `devlog/`

### 需要和谐协作？
- 每天同步进度到 `devlog/daily/`
- 每周进行Progress Review
- 风险问题及时标注🚨

---

## ✅ 检查清单 - 开始新Phase前

- [ ] 阅读过需求规范 (`docs/00-requirement-spec.md`)
- [ ] 理解架构设计 (`docs/01-architecture-design.md`)
- [ ] 知道该Phase的任务 (`docs/02-development-phases.md`)
- [ ] 熟悉编码规范 (`docs/03-coding-standards.md`)
- [ ] 查看过前几天的 `devlog/daily/`
- [ ] 准备好开发工具和环境
- [ ] 创建了该Phase的日志文件

---

## 🎯 下一步行动

### 立即行动（今天）
1. ✅ 阅读本文件
2. ✅ 阅读 `docs/00-requirement-spec.md`
3. ✅ 阅读 `docs/01-architecture-design.md`
4. ⏳ 准备开始Phase 0开发

### 后续行动（本周）
- Phase 0 完成（Agent框架）
- Phase 1 开始（内容分析Agent）

---

## 📝 文档更新历史

| 日期 | 内容 | 作者 |
|------|------|------|
| 2026-07-21 | 初始化工作指南和标准体系 | Assistant |

---

**本文档是Project的工作指南。所有开发活动都应遵循本文档及关联标准。**

*最后更新: 2026-10-19*  
*CLAUDE.md 版本: 1.1*

---

## 📂 E 系列增强 (Phase E)

**目标**: 在 Agent 系统 (Phase 0-5) 和 动态化 (D1-D6) 基础上，添加模板系统、AI 生图/生视频、历史管理 UI。

### 📚 E 系列文档

| 文档 | 位置 | 用途 |
|------|------|------|
| **总规划** | `docs/21-phase-E-plan.md` | E0-E5 阶段总规划 |
| **新架构** | `docs/20-architecture-v2.md` | 服务层架构设计 |
| **E0 任务** | `docs/22-E0-tasks.md` | 基础设施准备 |
| **E1 模板** | `docs/23-E1-templates.md` | 模板系统详细设计 |
| **E2 生图** | `docs/24-E2-ai-image.md` | AI 生图集成设计 |
| **E3 生视频** | `docs/25-E3-ai-video.md` | AI 生视频集成设计 |
| **E4 历史** | `docs/26-E4-history-ui.md` | 历史管理 UI 设计 |
| **E5 ComfyUI** | `docs/27-E5-comfyui.md` | ComfyUI 集成设计 |

### 🎯 E 阶段概览

| 阶段 | 名称 | 预计周期 | 状态 |
|------|------|---------|------|
| **E0** | 基础设施准备 | 3-5 天 | ✅ 已完成 |
| **E1** | 模板系统 | 4 周 | ✅ 已完成（31 模板 + `🧩 模板` 页 + 实时预览） |
| **E2** | AI 生图集成 | 4 周 | ✅ 已完成（`🎨 AI 素材` 页「生图」+ `/api/ai/*`） |
| **E3** | AI 生视频集成 | 4 周 | ✅ 已完成（`🎨 AI 素材` 页「生视频」+ SSE + 12 个技能配方 `skills/video/`） |
| **E4** | 历史管理 UI | 2 周 | 🟡 作品库改由文件系统实现（`/api/works`），独立 `services/history` 未启用 |
| **E5** | ComfyUI 可选集成 | 4 周 | ✅ 已完成 |

### 📂 新增目录结构

```
templates/              # 视频模板（按分辨率分类）
workflows/              # AI 工作流配置
services/               # 新增服务层
├── template/          # 模板服务
├── ai_media/          # AI 媒体服务
└── history/           # 历史服务
```

### 📝 E 系列工作原则

- **保守节奏**: 每月一个大功能
- **质量优先**: 每阶段完成后展示效果
- **兼容保证**: 所有新功能向后兼容
- **降级保底**: AI 失败时降级到现有能力

### 📅 每日工作日志

- 记录位置: `devlog/daily/YYYY-MM-DD.md`
- 记录内容: 完成事项、进行中、待办、阻塞问题

---

## 🔀 统一化重构 (U 系列, 2026-07-25 完成)

**背景**: 曾并存两套系统 —— 通用 Agent 流程(主题→视频, 仅 CLI)与电商带货前端(商品→视频)。
现已统一为单一核心流程: **输入主题/描述 → AI 生图 → 合成视频**。电商不再是独立子系统。

| 阶段 | 内容 |
|------|------|
| **U1** | 后端主题驱动入口 `POST /api/topic/generate` (`api/topic_video_api.py` + `core/topic_adapter.py`);视频表加 `topic`/`category` 列 |
| **U2** | 前端 `GenerateVideo` 改为"主题输入 + 分类选择",移除商品表单 |
| **U3** | 删除商品/数据分析页与 `product_module`/`ecom_adapter`;`VideoList`→通用历史;精简 `ecom_api` 为通用视频管线 |

**重要遗留命名**: 后端管线端点仍用 `/api/ecom/videos/*` 前缀、数据表仍名 `ecom_videos`
(为兼容历史数据),**但已与电商无关,是通用视频管线**。

**主应用**: `main_fastapi.py`(端口 5001)自动挂载 `api/__all__` 中所有路由 + 静态文件
`/static/output`,启动即初始化数据库。运行 `python main_fastapi.py`。

详见: `docs/28-unify-generation-design.md`

