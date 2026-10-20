# 统一化重构设计:主题→AI生图→视频

**日期**: 2026-07-25
**状态**: ✅ U1/U2/U3 已实现并提交;主应用 `main_fastapi.py` 已创建并联调通过
**背景**: 移除独立的电商带货子系统,把"输入主题/描述 → AI 生图 → 合成视频"作为系统的唯一核心流程。

> **端点路径**:主题生成入口为 `POST /api/topic/generate` 与 `GET /api/topic/generate/meta`
> (原拟用 `/api/generate`,但与 `generate_api.py` 既有的 `POST /api/generate` V2 端点冲突,故改前缀)。
> `main_fastapi.py` 自动挂载 `api/__all__` 中所有带 `router` 的模块,启动即注册,无需手动。

---

## 问题

仓库里存在两套并行系统:

| | 通用 Agent 系统 (Phase 0-5) | 电商带货系统 |
|---|---|---|
| 输入 | 主题/文本 | 商品 (name/price/卖点) |
| 入口 | `generate_video.py` **仅 CLI** | **整个当前前端** |
| 后端 | `core/agents/` CoordinatorAgent | `api/ecom_*`, `core/product_module`, `core/ecom_adapter` |
| 管道 UI | 无(一次性自动) | 有:脚本编辑→TTS→渲染,分镜预览,视觉风格 |

当前 web 应用**整个就是电商系统**。用户决定:电商不配单独的复杂逻辑,它顶多是"一个普通主题"。

## 目标

- 单一流程:主题/描述 → 内容分析/脚本 → 分镜(每场景 AI 生图,复用 E2 Bailian WanX)→ **可编辑** → TTS → 渲染 → 历史
- 保留交互管道(非一键)
- 移除所有电商专属:商品库、`product_module`、`ecom_adapter`、商品/分析页与端点
- 历史复用现有视频库(`ecom_videos` 表),撤下独立的 `/api/history` JSON 服务

## 关键技术事实(降低风险)

`/api/ecom/generate` 实际流程:`product_to_topic(product)` → `{title,hook,category,tags}` 主题字典 → 通用 `core.script_module.generate_script(topic_dict, platform, duration)`。**下游脚本/分镜/TTS/渲染早已与商品无关。** `ecom_videos.product_id` 可空;仓库用 ALTER-TABLE 迁移模式加列。

---

## 拆解

### U1 — 后端:主题驱动的生成入口
- 新增 `core/topic_adapter.py::topic_to_dict(topic, category, style)` → 返回同 `product_to_topic` 结构的主题字典(title=topic, hook="", category, tags=[])
- 新增 `POST /api/generate`,入参 `{topic, category, style, duration, orientation, visual_style, platform?}`,无 `product_id`;逻辑复用现有 generate(去掉商品校验和 prompt 快照),写入视频表 `product_id=NULL`
- 视频表加列:`topic TEXT`、`category TEXT`(沿用 ALTER-TABLE 迁移)
- 下游端点(`/script`/`/tts`/`/materials`/`/render`/`/status`/`/videos`)零改动
- 分类沿用 CLI:`教育讲解/短视频/纪录片/商业宣传`
- U1 阶段不删 ecom 端点(保持可跑)
- 降级:topic 空→400;`generate_script` 失败→500;LLM 空→现有校验

### U2 — 前端:主题入口
- `GenerateVideo` 入口"选择商品"→"输入主题/视频描述" + 分类选择器
- 移除商品预览卡、添加商品弹窗;改调 `/api/generate`
- 标题"一键生成带货视频"→通用
- 脚本编辑、分镜预览、TTS、渲染 UI 全部不变

### U3 — 清理 + 统一历史(含 E4)
- 删除 `ProductList`/`ProductInput`/`AnalyticsDashboard` 页与 `Sidebar` 导航项
- 删除 `core/product_module`、`core/ecom_adapter`、ecom 商品&分析端点
- `VideoList` → 通用"历史",补 E4 功能:日期筛选、重新生成、CSV 导出、统计
- 撤下 `services/history` + `api/history_api.py`(改为复用视频库)

---

## 验收

- U1: `/api/generate` 能仅凭主题产出 video_id + 脚本/分镜;下游 tts/render 正常;单元测试覆盖 `topic_to_dict` 与端点校验
- U2: 前端输入主题即可走完 脚本→TTS→渲染;无商品依赖
- U3: 无电商专属页/端点残留;历史页展示主题/分类/状态,支持筛选/重生成/导出/统计

## 风险

| 风险 | 缓解 |
|---|---|
| 删除电商丢失有价值管道 UI | 原地重构,只删商品入口层,保留脚本/TTS/渲染/分镜 |
| 主流程 5001 服务端文件不在仓库 | U1/U2 改动限于 `api/`+`core/`+`frontend/`,不依赖找到主 app;新端点靠现有 router 自动注册 |
| 视频表 product_id 外键 | 可空,topic 流程写 NULL;list/detail 用 LEFT JOIN 兼容 |

---

## 后续修复 (2026-10-19)

**问题**: U1 的 `/api/topic/generate` 与前端实际调用的步进式流水线端点并不一致。
`api/topic_pipeline_api.py` 从未登记进 `api/__all__`,而 `main_fastapi` 只按该列表挂载,
于是 `api/web/index.html` 调用的 `/api/topic/generate/stream` 和 `/api/topic/videos/{id}/*`
全部未注册,首页"题材全自动生成"与管道页落空。

**处理**:

1. `api/topic_pipeline_api.py` 去掉与统一入口重复的 `POST /api/topic/generate`,
   只保留 SSE 流式生成与步进式操作端点;
2. `api/__init__.py` 登记 `topic_pipeline_api`,由 `main_fastapi` 自动挂载;
3. `topic_video_api` 的 `topic` 入参兼容旧字段 `topic_keyword`;
4. 未注册的 `/api/*` 改为显式返回 404(此前是 200 + `{"detail":"Not Found"}`,前端无法识别失败);
5. 新增 `tests/test_frontend_api_contract.py`:扫描 `api/web/index.html` 里所有 `API + '...'`
   调用,逐个断言已在 app 中注册 —— 防止同类问题再次出现。

**现存两条落库路径**(有意保留,不做数据迁移):

| 入口 | 表 | 使用者 |
|---|---|---|
| `POST /api/topic/generate` | `ecom_videos` (product_id=NULL) | 编程接口 / 测试 |
| `POST /api/topic/generate/stream` + `/api/topic/videos/*` | `topic_videos` | `api/web/index.html` 步进式流水线 |

两条链路共用 `core/pipeline_helpers.py`,差异仅在落库表与产物目录。

