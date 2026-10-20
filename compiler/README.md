# compiler/ — 视频编译器

把「一句主题」编译成「可渲染的帧序列」。所有子包按依赖方向自上而下排列，**不允许反向 import**。

```
主题/意图
   ↓  ir/           四层 IR 定义（Intent → Narrative → Scene → Timeline → Render）
   ↓  pass_*.py     纯函数式 IR 变换（确定性、可缓存、可单测）
   ↓  thinking/     人在回路：会话、补丁、分支、增量重算、产物版本
   ↓  runtime/      执行：LiveSession、DAG、重试、分布式 worker
   ↓  media/        媒体：场景渲染、拼接、内容寻址缓存
   ↓  backend/      落地：FFmpeg 命令 lowering、执行器、会话存储
   ↓
MP4
```

## 子包职责

| 子包 | 职责 | 关键入口 |
|---|---|---|
| `ir/` | 四层 IR 的 dataclass 与校验 | `TimelineIR`、`SceneIR`、`RenderIR` |
| `compiler/*.py` | IR → IR 的 pass 管线 | `PassPipeline`、`SceneToTimelinePass` |
| `thinking/` | 智能导演：编辑补丁、分支合并、撤销重做 | `ThinkingSession`、`Scheduler` |
| `runtime/` | 运行时：增量 DAG、worker、分布式调度 | `LiveSession`、`DistributedScheduler` |
| `media/` | 场景渲染、拼接、渲染缓存 | `SceneRenderer`、`ConcatRenderer` |
| `backend/` | FFmpeg 命令生成与执行、会话持久化 | `FFmpegExecutor`、`SessionStore` |

## 约定

1. **依赖方向**：`ir` ← `pass` ← `thinking` ← `runtime` ← `media` ← `backend`，禁止反向。
2. pass 必须是纯函数，不读文件、不调 LLM、不依赖全局状态。
3. 新增子包前先问：能不能放进现有层？顶层包数量已经够多了。
