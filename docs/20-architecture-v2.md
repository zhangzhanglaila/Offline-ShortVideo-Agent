# Phase E 系列架构设计 v2

## 新架构概览

在现有 Agent 系统（Phase 0-5）和动态化增强（D1-D6）基础上，新增三层架构：

```
┌─────────────────────────────────────────────────────┐
│                    用户层                             │
│   CLI (generate_video.py)  │  Web SPA (web/index)    │
└──────────────────────────┬──────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────┐
│                   API 层                              │
│   FastAPI Routes (api/*)  │  SSE (进度推送)         │
└──────────────────────────┬──────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────┐
│                  服务层（新增）                       │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │TemplateSvc  │  │AIMediaSvc    │  │HistorySvc  │ │
│  └─────────────┘  └──────────────┘  └────────────┘ │
└──────────────────────────┬──────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────┐
│                  核心层（保持）                       │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────┐ │
│  │Agent System  │  │Motion Engine │  │FFmpeg Comp │ │
│  │(Phase 0-5)   │  │(D1-D6)       │  │             │ │
│  └──────────────┘  └──────────────┘  └────────────┘ │
└─────────────────────────────────────────────────────┘
```

> **2026-10 更新**：原 React/Vite 前端已在 commit `c818e35` 删除，`api/web/index.html`
> 是当前唯一前端；下文提到的 "React Frontend" 均以此为准，进度推送用 SSE 而非 WebSocket。

---

## 新增服务层

### TemplateService（模板服务）

```python
class TemplateService:
    """模板渲染服务"""

    def list_templates(self, aspect_ratio: str) -> List[TemplateInfo]
    def render_template(self, template_path: str, context: dict) -> Image
    def get_template_preview(self, template_path: str) -> str
    def validate_template(self, template_path: str) -> bool
```

**职责**:
- 管理所有 HTML 模板
- 解析模板参数
- 渲染模板为图片
- 提供模板预览

### AIMediaService（AI 媒体服务）

```python
class AIMediaService:
    """AI 生图/生视频服务"""

    async def generate_image(
        self, prompt: str, provider: str, **kwargs
    ) -> str

    async def generate_video(
        self, prompt: str, provider: str, **kwargs
    ) -> AsyncGenerator[Progress, None]

    def cancel_video_task(self, task_id: str)
    def get_supported_providers(self) -> Dict[str, List[str]]
```

**职责**:
- 统一 AI 媒体生成接口
- 管理多个供应商（OpenAI/DashScope/Kling）
- 任务队列管理
- 降级策略执行

### HistoryService（历史服务）

```python
class HistoryService:
    """历史记录服务"""

    def add_record(self, record: GenerationRecord)
    def get_records(
        self, filter: HistoryFilter, pagination: Pagination
    ) -> List[GenerationRecord]
    def delete_record(self, record_id: str)
    def regenerate(self, record_id: str) -> str
    def export_records(self, format: str) -> bytes
```

**职责**:
- 管理生成历史记录
- 提供查询和筛选
- 支持重新生成

---

## 配置管理

### 新增配置文件

```
config/
├── ai_providers.yaml       # AI 供应商配置
├── templates.yaml           # 模板配置
└── comfyui.yaml             # ComfyUI 配置
```

### ai_providers.yaml

```yaml
ai_providers:
  openai:
    api_key: "${OPENAI_API_KEY}"
    base_url: "https://api.openai.com/v1"
    models:
      image: ["dall-e-3", "gpt-image-2"]
    enabled: true
  dashscope:
    api_key: "${DASHSCOPE_API_KEY}"
    base_url: "https://dashscope.aliyuncs.com/api/v1"
    models:
      image: ["wan2.6-t2i", "wan2.7-image"]
      video: ["wan2.7-t2v", "happyhorse-1.0-t2v"]
    enabled: true
  kling:
    api_key: "${KLING_API_KEY}"
    base_url: "https://api-beijing.klingai.com"
    models:
      video: ["kling-v3", "kling-v2.5-turbo"]
    enabled: false
  fallback:
    to_stock_images: true
    to_ken_burns: true
```

---

## 模板系统架构

### 模板目录结构

```
templates/
├── 1080x1920/              # 竖屏 9:16
│   ├── image_*.html        # AI 生图模板
│   ├── video_*.html        # AI 生视频模板
│   ├── static_*.html      # 纯文字模板
│   └── thumbnails/        # 预览缩略图
├── 1920x1080/              # 横屏 16:9
└── 1080x1080/              # 方形 1:1
```

### 模板渲染流程

```
用户选择模板
    ↓
TemplateService 解析模板参数
    ↓
根据场景上下文填充参数
    ↓
调用 Jinja2 渲染 HTML
    ↓
Playwright/HTML2Image 转为图片
    ↓
返回渲染后的图片
```

---

## AI 媒体生成流程

### 生图流程

```
用户选择 AI 生图
    ↓
AIMediaService.generate_image()
    ↓
选择供应商（OpenAI/DashScope）
    ↓
调用 API 生成
    ↓
下载并缓存图片
    ↓
失败时降级到真实素材库
    ↓
返回图片路径
```

### 生视频流程

```
用户选择 AI 生视频
    ↓
AIMediaService.generate_video()
    ↓
创建异步任务
    ↓
选择供应商（WAN/Kling）
    ↓
轮询任务进度（WebSocket 推送）
    ↓
下载并缓存视频
    ↓
失败时降级到 Ken Burns
    ↓
返回视频路径
```

---

## 数据模型

### GenerationRecord（生成记录）

```python
@dataclass
class GenerationRecord:
    id: str
    input_text: str
    input_type: Literal["topic", "fixed_script"]
    params: GenerationParams
    output_path: Optional[str]
    status: Literal["pending", "processing", "success", "failed"]
    error: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]

@dataclass
class GenerationParams:
    duration: int
    style: str
    template: Optional[str]
    ai_provider: Optional[str]
    aspect_ratio: str
    use_ai_image: bool
    use_ai_video: bool
```

---

## 兼容性保证

1. **向后兼容**: 所有现有功能保持不变
2. **可选启用**: 新功能通过参数控制，默认关闭
3. **降级保底**: 每个新功能都有降级方案
4. **API 兼容**: 现有 API 接口保持兼容

---

*创建时间: 2026-07-24*
