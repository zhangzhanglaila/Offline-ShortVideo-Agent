# VideoScene 剪辑引擎演进文档

> 覆盖 v1 → v16，记录从 Reactive Animation System 到 Global Editorial Optimizer 的完整演进路径。
>
> 当前定位：**Constraint-based Editorial Optimizer v1.0**（v16 完成时）

---

## 版本总览

| 版本 | 核心升级 | 系统性质 |
|------|----------|----------|
| v1 | 基础元素动画系统 | Reactive |
| v6 | 词级字幕高亮 + TTS 时间同步 | Reactive |
| v7 | 缓存 + 二分查找 + useMemo | Optimized Reactive |
| v8 | 短语级绑定（phrase-level） | Semantic Layer |
| v9 | 情绪映射系统（emotion → visual） | Control Layer |
| v10 | Shot 驱动相机 + 裁剪变换 | Cinematic Layer |
| v10.1 | Shot 内连续插值 | Continuity Layer |
| v10.2 | 缓动曲线 + 多频漂移 | Realism Layer |
| v10.3 | Shot 间交叉淡入淡出 | Transition Layer |
| v10.4 | 全局相机管道 + 方向感知 | Camera Continuity |
| v10.5 | Impact Frame（节拍冲击点） | Rhythm Layer |
| v11 | Transition 多样性（情绪分型） | Diversity Layer |
| v12 | Transition Memory + 节律函数 | Policy Layer |
| v13 | 规划层（Timeline Compiler） | Deterministic |
| v14 | 全局优化器 + 能量曲线 | Editorial Compiler |
| v15 | Beam Search + Per-step Cost | Global Approximate |
| v16 | Full-Sequence Scoring + Rollout | Global Optimizer |

---

## v1：基础元素动画系统

**架构目标**
建立最底层的元素渲染能力：文本、图片、形状以动画方式入场和退场。

**核心机制变化**
- 元素拥有 `start` / `duration` / `zIndex`
- 入场：前 `animDuration` 帧 opacity 0→1
- 退场：后 `animDuration` 帧 opacity 1→0
- Transform 由动画类型（fade / slide-up / zoom-in 等）决定

**解决的关键问题**
- 从静态布局到动态时序的基本跨越
- 多元素并行时 zIndex 层级控制

**引入的设计模式**
- Frame-based time model（帧驱动，非时钟驱动）
- Element-level animation state machine

**系统复杂度影响**
- 提升：逻辑简单，线性遍历元素数组
- 性质：O(n·elements)，无优化空间

---

## v6：词级字幕高亮 + TTS 时间同步

**架构目标**
在 v1 基础上叠加语义层：字幕不再是整体淡入淡出，而是精确到每个词的起止时间，并能与 TTS 音频流同步。

**核心机制变化**
- `WordCue`：词级时间戳 `{ index, word, start, end }`
- `SubtitleCue`：多词组成的句子级字幕
- `allWords`：全视频词级时间线扁平缓存（避免每帧 flatten）
- TTS 生成 VTT 文件 → 解析得到词边界

**解决的关键问题**
- 字幕"整体淡入淡出"→ 逐词高亮
- 词边界由 TTS 真实时长决定，不是均匀分割

**引入的设计模式**
- Word-level time binding（词级时间绑定）
- AllWords cache（运行时缓存，避免 O(n²) flatten）
- VTT parsing pipeline（TTS 输出 → 词边界 → 高亮数据）

**系统复杂度影响**
- 提升：新增字幕渲染路径，`WordHighlightedText` 组件
- 性质：首次从"可见即可"到"时序精确驱动"

---

## v7：性能优化层（O(n) 查词）

**架构目标**
解决 v6 暴露的性能陷阱：每帧 flatten allWords 是 O(n)，useCurrentFrame 触发重渲染后词高亮状态计算冗余。

**核心机制变化**
- `buildAllWords(subtitleCues)`：启动时一次性扁平化，后续只读
- `evaluateDirector`：二分查找替代线性扫描，O(log n) 定位当前词
- `useMemo` 包裹所有词级状态计算

**解决的关键问题**
- ❌ 每帧 `words.flat()` → O(n²)
- ❌ 线性扫描找当前词 → O(n) per frame
- ✅ 启动时一次 flatten，后续只读
- ✅ 二分查找 → O(log n)

**引入的设计模式**
- Binary search on sorted time array（二分查找活跃词）
- Read-through cache pattern（一次性构建，多次查询）
- Memoized per-frame state（React useMemo 避免无效重算）

**系统复杂度影响**
- 降低：从 O(n²) → O(n) 启动 + O(log n) per frame
- 性质：性能问题从算法层转移到数据流编排层

---

## v8：短语级语义绑定（Phrase-level）

**架构目标**
词级粒度太细，多词强调（如"真的非——常——重要"）需要 phrase 级别控制。

**核心机制变化**
- `EmphasisPointWord.wordIndices[]`：phrase 级索引数组，支持跨词强调
- `wordIndices` 按 shot 内 phrase 分组，全局连续编号
- 排序保证：按 start time 排序 → 二分查找稳定
- Dead code 清理：移除 9 个废弃文件

**解决的关键问题**
- 单字强调不够用 → phrase（多词）级别
- 代码库废弃代码累积 → 9 个文件清理

**引入的设计模式**
- Phrase-level binding（短语级绑定，粒度从词升级到语义单元）
- Word index continuity across segments（全局跨段连续编号）
- Sort guarantee → binary search stability（排序作为算法前提保证）

**系统复杂度影响**
- 降低：dead code 清理后编译更快
- 提升：新增 phrase 分组逻辑，数据结构更复杂

---

## v9：情绪视觉映射系统

**架构目标**
情绪不能只是数据（emotion 分值），必须成为控制系统：情绪值 → 具体的视觉参数（相机策略、色调、呼吸强度）。

**核心机制变化**
- `EmotionLabel`：`"intense" | "calm" | "neutral" | "warm" | "dramatic"`
- `EmotionEffect`：情绪→视觉的完整映射
  ```typescript
  {
    label: EmotionLabel,
    cameraOverride: "shake" | "slow-zoom" | "static" | "pulse",
    colorOverlay: string,       // 情绪色调叠加
    breatheIntensity: number,   // 呼吸强度 0~1
    zoomBase: number,          // 情绪 zoom 基础值
  }
  ```
- `emotionMap`：5 种情绪的视觉规则表
- `evaluateDirector`：输出 `emotionLabel` + `emotionEffect`
- Color overlay div：情绪色调覆盖在背景层

**解决的关键问题**
- ❌ 情绪是数据，不驱动任何视觉变化
- ✅ 情绪是控制系统（camera / color / breathe 三路输出）

**引入的设计模式**
- Signal-to-visual mapping（信号→视觉映射表，经典游戏引擎模式）
- Emotion as control layer（情绪独立于内容单独作用）
- Multi-channel output（单一情绪值 → 多路视觉参数）

**系统复杂度影响**
- 提升：emotionMap 表格驱动，新增 color overlay 渲染层
- 性质：数据流从线性变为广播式（一个 emotion 值分发到多个视觉通道）

---

## v10：Shot 驱动相机系统

**架构目标**
图片不再是静态背景，而是参与镜头语法的动态单元。每个图片配合一段 camera motion，构成完整的"镜头"。

**核心机制变化**
- `Shot` 接口：
  ```typescript
  {
    start, duration, src,
    camera: "push-in" | "pan-left" | "pan-right" | "pull-out" | "tilt-up" | "tilt-down" | "static",
    cropX, cropY, cropW, cropH,  // 裁剪区域 0~1
    opacity,
  }
  ```
- `buildShots()`：每个 step 图片生成 2~3 个 shot，camera 类型循环
- `getShotTransform`：crop + scale + camera 运动 → CSS transform
- `useCurrentShot`：帧→当前 shot 的 O(log n) 查找

**解决的关键问题**
- ❌ 图片是背景，不参与镜头语法
- ✅ 图片是镜头，camera 类型决定运动方式

**引入的设计模式**
- Shot as first-class citizen（镜头成为核心抽象）
- Crop-based framing（裁剪区域驱动视觉焦点）
- Camera motion as transform（相机运动归约为 CSS transform）

**系统复杂度影响**
- 提升：新增 shot 抽象层，VideoLayout 新增 `shots[]` 字段
- 性质：从 2D 布局引擎 → 时间轴镜头编辑器

---

## v10.1：Shot 内连续插值

**架构目标**
解决 v10 的"跳变感"：shot 内 progress 是线性插值，相机运动看起来像机关枪扫射，不自然。

**核心机制变化**
- `useCurrentShot` → `useShotsAroundFrame`：引入 shot 内插值 progress
- `progress = (frame - shot.start) / shot.duration`
- `clampedProgress = max(0, min(1, progress))`：防边界越界
- Camera motion 作用于 `clampedProgress`

**解决的关键问题**
- ❌ Linear camera motion（机关枪感）
- ✅ Continuous interpolation（平滑自然运动）

**引入的设计模式**
- Temporal interpolation within shot（shot 内时序插值）
- Progress clamping（防边界越界导致 transform 抖动）

**系统复杂度影响**
- 性质：单一 shot 内从帧映射 → 连续函数映射
- 降低：视觉自然度大幅提升，逻辑量增加不多

---

## v10.2：缓动 + 多频漂移 + X/Y 对称

**架构目标**
线性插值依然"太数学正确"，需要 easing 曲线打破规则感；相机运动加入多频扰动消除周期性；修复 cropH/Y 的计算错误。

**核心机制变化**
- Easing：`Easing.inOut(Easing.cubic)` 替代 linear
- 多频 drift：
  ```typescript
  const drift = Math.sin(frame * 0.021) * 4 + Math.sin(frame * 0.013 + 1.7) * 2;
  ```
  不同频率叠加 → 非周期运动假感消除
- Emotion-aware easing：shake → linear，pulse → quad，default → cubic
- `curCropH` 修复：`X/Y 同步必须用 curCropH`，不是固定 cropH
- `curCropX` / `curCropY`：分离计算起点和终点 crop 值

**解决的关键问题**
- ❌ Linear motion（太规则）
- ❌ cropH/Y 计算错误（scaleY = curScale / cropH 而非 / curCropH）
- ❌ 单频 drift（周期可预测，机械感残留）

**引入的设计模式**
- Emotion-aware easing selection（情绪决定运动曲线）
- Multi-frequency superposition（多频叠加消除机械感）
- Per-axis independent interpolation（X/Y 轴分离插值）

**系统复杂度影响**
- 提升：transform 计算逻辑增加约 40 行
- 降低：视觉质量质的飞跃，属于"免费复杂度"（数学叠加而非条件分支）

---

## v10.3：Shot 间过渡连续性

**架构目标**
从"硬切"（hard cut）→"像同一个镜头延续"。

**核心机制变化**
- `useShotsAroundFrame`：返回 `current + next` 双 shot + `isTransitioning`
- `TRANSITION_FRAMES = 8`：每个 shot 末尾 8 帧进入过渡窗口
- Cross-zoom：current 放大淡出（1 → 1.2），next 缩小淡入（1.15 → 1）
- 双层渲染：两个 Img 同时存在，过渡区间 opacity 渐变

**解决的关键问题**
- ❌ A shot → B shot = 硬切（无过渡）
- ✅ A shot → B shot = cross-zoom + fade（视觉连续）

**引入的设计模式**
- Dual-layer shot rendering（双 shot 图层叠加）
- Transition window（过渡窗口 = shot 末尾 N 帧）
- Cross-zoom as transition primitive（缩放交叉作为过渡基元）

**系统复杂度影响**
- 提升：新增过渡窗口逻辑，双图层渲染
- 性质：从"单 shot 显示"→"双 shot 过渡态"，渲染量翻倍但视觉连续性大幅提升

---

## v10.4：相机管道完整化 + 方向感知

**架构目标**
Next shot 不应该有"突然变简单"的感觉，必须和 current shot 一样有完整的 camera pipeline。Pan 运动在 transition 中需要方向延续。

**核心机制变化**
- Next shot 完整 camera pipeline：
  ```typescript
  const { shotTransform: nextShotTransform, emotionTransform: nextEmotionTransform } =
    getShotTransform(next, 0, cameraOverride, frame);
  ```
  → next 也跑 `getShotTransform`，不是 bare `scale()`
- Direction-aware pan continuity：
  ```typescript
  if (current.camera === "pan-left") exitTranslate = -t * 120;
  if (next?.camera === "pan-right")  enterTranslate = -(1 - t) * 120;
  ```
- 微噪声 opacity：`Math.sin(frame * 13.7) * 0.015` → 真实曝光变化非均匀性

**解决的关键问题**
- ❌ current 有 camera motion，next 只有 scale（"运动→静止"的视觉断层）
- ❌ pan-left → pan-right 是方向反转而非连续
- ❌ 平滑 opacity 变化太"数学"

**引入的设计模式**
- Transition as camera handoff（过渡是相机交接，不是静态切换）
- Direction continuity across cuts（方向作为连续性维度）
- Noise-modulated opacity（噪声调制透明度模拟真实曝光）

**系统复杂度影响**
- 提升：`getShotTransform` 调用量翻倍，transform 字符串拼接更复杂
- 降低：视觉断层消除后，系统整体感知质量提升远大于复杂度成本

---

## v10.5：Impact Frame（节拍冲击点）

**架构目标**
所有 transition"太平滑"→ 需要在过渡中间加一个微冲击，制造剪辑点节拍感。

**核心机制变化**
```typescript
const isImpact = isTransitioning && Math.abs(t - 0.5) < 0.12;
const impactScale = isImpact ? 1.08 : 1;
```
- 在 cross-zoom 的 50% 处注入 `scale(1.08)` 微冲击
- 只在 zoom 类型生效（其他类型有各自节奏）

**解决的关键问题**
- ❌ 所有 transition 一直平滑 → 看起来"顺"但不"抓人"
- ✅ 平滑放大 → "啪"轻顶一下 → 消失（节拍感）

**引入的设计模式**
- Impact frame as rhythmic marker（冲击帧作为节奏标记）
- Transition midpoint as editorial moment（过渡中点 = 剪辑时刻）

**系统复杂度影响**
- 极低：增加一个条件判断和一个 scale 乘法
- 降低：节拍感大幅提升，"剪辑感"质的飞跃

---

## v11：Transition 多样性（情绪分型）

**架构目标**
所有镜头都是同一种 transition → 观众看 5 个视频就感觉"套路一样"。需要 emotion → transition type 映射，让剪辑风格随情绪变化。

**核心机制变化**
```typescript
const transitionType =
  emotion >= 0.75 ? "whip"    // intense → 横向甩切
  : emotion <= 0.35 ? "fade"  // calm → 纯淡入淡出
  : "zoom";                    // default → cross-zoom
```

| 类型 | 效果 | 适用情绪 |
|------|------|----------|
| `whip` | 横向甩切 0→800px，opacity 在最后 30% 才快切 | intense |
| `fade` | 纯 opacity 渐变，无 scale | calm |
| `zoom` | cross-zoom + impact frame + pan | default |

- Whip transition：横向速度远快于 zoom，opacity 尾部突变
- Fade transition：pan continuity 减弱到 0.3×（慢内容不需要方向感）

**解决的关键问题**
- ❌ 所有镜头 = 同一种 transition = 风格锁死
- ✅ 情绪决定剪辑风格（快内容快切，慢内容慢淡）

**引入的设计模式**
- Emotion-conditional transition selection（情绪条件化过渡选择）
- Type-specific transform composition（类型专属 transform 组合）

**系统复杂度影响**
- 提升：3 个分支，transform 计算逻辑分叉
- 性质：从"单一过渡类型"→"情绪驱动的多态过渡"，是 diversity layer 的核心

---

## v12：Policy Layer（Transition Memory + 节律函数）

**架构目标**
解决"连续 whip / 连续 fade"导致的风格坍塌（style collapse）。同时从"纯 emotion 触发"升级到"节律 gate"——节律窗口决定何时允许高能 transition。

**核心机制变化**
- 节律函数：`beat = Math.sin(frame * 0.05)` — ~1.5 秒周期
- Rhythm boost：beat > 0.6 允许 intense→whip，beat < -0.4 强制冷静窗口
- Transition Memory：`lastTransitionType` 记录上一次实际使用的类型
  ```typescript
  if (transitionType === lastTransitionType) {
    if (transitionType === "whip") transitionType = "zoom";
    else if (transitionType === "zoom") transitionType = "fade";
  }
  ```
- Consecutive whip limit：连续 whip ≥ 2 → 强制 zoom

**解决的关键问题**
- ❌ emotion=0.8 → whip → whip → whip（连续高能 → 观众麻）
- ❌ 每个 shot 独立决策 → 剪辑风格无法跨 shot 协调
- ✅ whip → zoom → whip（有起伏，不疲劳）

**引入的设计模式**
- Transition Memory（历史状态驱动的决策修正）
- Rhythm gate（节律窗口作为 transition 的使能条件）
- Anti-repetition constraint（防重复约束）
- Stateful editing policy（状态化剪辑策略）

**系统复杂度影响**
- 提升：引入 module-level state（`lastTransitionType`、`consecutiveWhipCount`）
- ⚠️ 风险：module state 跨 render session 不清，跨视频状态污染

**架构意义**
v12 是整个演进的关键转折点：系统从"每帧独立决策"升级到"带历史记忆的上下文感知决策"。但 module-level state 也暴露了 v12 的架构天花板——需要规划层替代 reactive 层。

---

## v13：Timeline Compiler（规划层替代 Reactive 层）

**架构目标**
将 v12 的 reactive 决策（每帧实时计算）改为一次性规划（整条视频只算一次），消除 module-level state 污染风险，让剪辑决策从"运行时想"变成"剪辑表已写好"。

**核心机制变化**

*新增 Pure Function 层*
```typescript
buildTransitionPlan(shots, emotions, fps): TransitionPlan
```
- 输入：全量 shots + per-shot emotions + fps
- 输出：`Map<shotIndex, TransitionDecision>`
- 全程无副作用，无状态，纯函数

*新增 React Hook 接入层*
```typescript
useTransitionPlan(shots, emotions, fps): TransitionPlan
```
- `useMemo` 包裹：`[shots, emotions.join(","), fps]` 稳定时不重算
- 规划结果稳定不变，每个视频只计算一次

*TransitionDecision 接口*
```typescript
{
  shotIndex: number,
  type: "whip" | "fade" | "zoom",
  microCutAt: number,        // shot 内微切时刻（0~1）
  microCutIntensity: number,  // 微切强度
}
```

*Budget / Cooldown 状态机（EditorState）*
```typescript
interface EditorState {
  budget: number;     // MAX=6，whip=-3，zoom=-1，fade=+0.5
  cooldown: number;  // whip 后强制冷却 8 帧
  lastTransition: TransitionType;
}
```

*Shot 内 Micro-cut（v13 新增）
```typescript
const progressInShot = (frame - shot.start) / shot.duration;
const nearMicroCut = Math.abs(progressInShot - microCutAt) < 0.025;
const microCutScale = nearMicroCut ? 1 + emotion * 0.12 : 1;
```
→ 镜头内部也有剪辑感（不只是 shot boundary）

**解决的关键问题**
- ❌ v12 module state 跨视频污染风险
- ❌ 每帧都在"实时决策"（render 非确定性）
- ✅ 规划只跑一次，执行层只做 lookup
- ✅ Shot 内部也有剪辑感

**引入的设计模式**
- Planned vs Reactive separation（规划层与执行层分离）
- EditorState as pure function parameter（状态机作为纯函数参数）
- React useMemo as plan cache（`useMemo` 作为规划缓存）
- Look-up editorial execution（查表式执行替代计算式决策）

**系统复杂度影响**
- 转移：复杂度从"每帧计算"→"一次性规划 + 查表执行"
- 提升：代码量增加约 80 行，但渲染时每帧只做 Map.get()
- 性质：从 Reactive System → Deterministic System

**架构意义**
v13 是质变点：系统从"导演在拍的时候实时想剪辑"变成"剪辑师提前写好分镜表，拍摄时照着执行"。这是所有专业 NLE（Final Cut Pro / Premiere）的底层架构逻辑。

---

## v14：Global Timeline Optimizer（全局优化层）

**架构目标**
v13 的逐 shot 贪婪决策仍可能出现"局部合理、全局疲劳"：whip 集中在前 3 秒、后半段完全平淡。需要引入全局约束和评分机制，让 transition 规划从 greedy → globally-aware。

**核心机制变化**

*1. Global Energy Curve（全局连续能量曲线）*
```typescript
// 每个 shot 的 20%, 50%, 80% 三点采样
buildGlobalEnergyCurve(shots, emotions, fps)
// → Array<{ frame, energy }>，sorted by frame
```
替代离散的 mid-shot 采样，提供全局节奏图谱。

*2. Semantic Micro-cut Anchor（能量峰值锚定）*
```typescript
// 找 shot 内能量最高的帧作为 micro-cut 锚点
const peakFrame = findEmotionPeakFrame(shot, energyCurve, 0.6, fps);
const microCutAt = (peakFrame - shot.start) / shot.duration;
// microCut 强度由能量峰值决定
const microCutIntensity = peakEnergy * 0.14;
```
→ 之前：第 60% 帧（固定位置）
→ 现在：这个 shot 里能量最高的时刻（语义驱动）

*3. Whip Density Constraint（全局密度控制）*
```typescript
// 每 150 帧（约 5 秒 @30fps）最多 1 次 whip
enforceWhipDensityConstraint(plan, shots, fps);
// 贪婪移除最低能量 whip，直到满足密度约束
```
→ 防止"前半段连甩 → 后半段无劲"

*4. Plan Scoring（全局质量评分）*
```typescript
score = diversity×0.4 + budgetScore×0.3 + alignmentScore×0.3

// diversity：transition type 分布是否均匀
// budgetScore：whip 占比是否合理（50% 时满分）
// alignmentScore：whip 是否落在高能量区间
```

**解决的关键问题**
- ❌ 离散 emotion 采样 → 全局节奏图谱缺失
- ❌ 固定 0.60 micro-cut → 无语义驱动
- ❌ 无 whip 密度控制 → 能量分布不均匀
- ❌ 规划质量无法量化

**引入的设计模式**
- Global energy curve as editorial context（全局长度能量曲线）
- Constraint satisfaction as post-processing（密度约束作为后处理）
- Plan scoring for editorial quality（剪辑质量可量化评分）
- Greedy-with-constraints → Global-ish optimization（贪婪+约束=全局近似最优）

**系统复杂度影响**
- 提升：约 120 行新代码（能量曲线、密度约束、评分函数）
- 性质：从"per-shot greedy" → "timeline-level constrained optimization"
- 降低：v14 之后，视觉质量的提升不再靠加效果，而是靠调参数（曲线参数、权重参数）

---

## v15：Beam Search（全局近似最优搜索）

**架构目标**
v14 是逐 shot 贪婪决策（每步只选当前最优），缺乏全局视角。v15 引入 Beam Search：在每步保留多条候选路径，避免过早锁定局部最优。

**核心机制变化**

*v15 的 Beam 结构*
```typescript
interface Beam {
  plan: TransitionPlan;      // 到当前 shot 的完整路径
  state: EditorState;        // budget + cooldown + lastTransition
  score: number;             // 累积增量分数（v15 的核心问题）
  consecutiveWhip: number;
  whipDistribution: number[];
}
```

*Per-step Cost Function*
```typescript
evaluateTransitionType(type, state, emotion, beat, consecutiveWhip)
→ { type, incrementalScore, newState, newConsecutiveWhip }
```
- 增量分数 = `emotionMatch×0.5 + changeBonus×0.2 + budgetBonus×0.3`
- 每步每个候选 beam 展开为 3 个新候选（whip/fade/zoom）
- 剪枝保留 top BEAM_WIDTH=4

*Beam Search 主循环*
```typescript
beams = [{empty}]
for each shot:
  for each beam:
    for each type:
      candidate = evaluate(type, beam.state, ...)
      newBeams.push({...candidate, score: beam.score + incrementalScore})
  beams = top BEAM_WIDTH by score
return best beam's plan
```

**解决的关键问题**
- ❌ v14 贪婪：每 shot 只选 1 个最优，路径锁死后无法恢复
- ✅ Beam search：保留 4 条路径，剪枝用全局评分

**引入的设计模式**
- Beam search over sequence space（组合序列空间的束搜索）
- Incremental score accumulation（增量分数累积）

**系统复杂度影响**
- 提升：O(shots × beamWidth × 3) — 仍远低于穷举的 O(3^shots)
- 性质：从"单路径贪婪"→"多路径探索"

**架构意义**
v15 是从"贪婪"到"近似全局最优"的关键跃迁，但增量分数累积仍是局部视角。

---

## v16：Full-Sequence Scoring（全局目标函数 + Monte Carlo Rollout）

**架构目标**
v15 的 beam.score = 增量累积，这是"greedy accumulation disguised as beam search"——本质上仍是局部贪婪。v16 的核心突破：**用全局目标函数评估完整序列**，剪枝时用 Monte Carlo Rollout 估算。

**核心机制变化**

*1. Pure Decision Function（无评分）*
```typescript
decideTransition(type, state, emotion, beat, consecutiveWhip)
→ { type, newState, newConsecutiveWhip }
```
- 只返回合法决策和状态更新，**不返回分数**
- 评分全部推迟到 `evaluateFullSequence`

*2. 统一全局目标函数（Full-Sequence Scoring）*
```typescript
evaluateFullSequence(plan, energyCurve, shots, emotions, fps): number
```
四维度加权评分：
| 维度 | 权重 | 说明 |
|------|------|------|
| Energy Alignment | 0.30 | whip 是否落在高能量区间（≥0.65） |
| Rhythm Entropy | 0.25 | transition type 分布的 Shannon 熵（越均匀越高） |
| Pacing Smoothness | 0.25 | whip 在 5 秒窗口的分布均匀度（越均匀越高） |
| Micro-cut Semantic | 0.20 | micro-cut 位置与能量峰值的距离（越近越高） |

*3. Monte Carlo Rollout（剪枝估算）*
```typescript
rolloutEstimate(beam, shots, emotions, energyCurve, fps, currentIdx): number
```
- beam 尚未覆盖完整 timeline 时，用 zoom 模拟剩余 shot
- 估算完整序列的 score 下界，用于剪枝决策
- 解决了 v15"近期偏差"问题

*4. 最终选择：全局评估（非近似）*
```typescript
bestBeam = argmax_{b in beams} evaluateFullSequence(b.plan, ...)
```
- 所有 beams 展开完毕后，对每条完整路径执行真正的全局目标函数
- 选出全局最优，而非累积分数最高的

**解决的关键问题**
- ❌ v15：score = 增量累积（近期偏差，无法反映全局质量）
- ❌ v15：剪枝用累积分数（误导性）
- ✅ v16：剪枝用 rollout 估算（全局 score 的近似）
- ✅ v16：最终选择用 evaluateFullSequence（真正的全局目标函数）

**引入的设计模式**
- Full-sequence objective function（完整序列目标函数，替代增量累加）
- Monte Carlo rollout for beam pruning（用随机模拟估算远期 score）
- Shannon entropy for diversity measurement（用信息熵度量 transition 多样性）
- Non-greedy beam search（剪枝不看局部，看全局 roll-out 估算）

**系统复杂度影响**
- 提升：每步新增 rollout 估算（O(shots) per beam per type）
- 性质：从"greedy with beam"→"globally-informed approximate optimization"

**架构意义**
v16 完成了从"局部贪婪"到"全局近似最优"的最后一步。系统现在能在整个 timeline 尺度上协调 transition 分布、能量对齐和节奏平滑性，而不是在每个 shot 局部做贪心选择。

---

## 演进全景总结

### 系统性质的演变

```
v1      : Reactive Element Renderer        （帧驱动动画）
v6-v8   : Reactive + Semantic Layer        （时间精确 + 语义绑定）
v9      : Reactive + Control Layer          （情绪控制系统）
v10-v10.2: Reactive + Cinematic Layer      （镜头语法）
v10.3-v10.5: Continuity + Rhythm Layer   （过渡 + 节拍）
v11     : Diversity Layer                  （情绪分型）
v12     : Policy Layer                     （历史感知策略）
v13     : Deterministic Editorial Compiler （规划层）
v14     : Global Optimizer                 （全局约束优化）
v15     : Beam Search Planner              （全局近似搜索）
v16     : Full-Sequence Optimizer         （全局目标函数 + Rollout）
```

### 三层架构（v16 最终形态）

```
┌─────────────────────────────────────────────────────┐
│  PLANNING LAYER  （一次性，render 外运行）             │
│  beamSearchTransitionPlan(shots, emotions, fps)       │
│    → decideTransition()   // 纯决策，无评分           │
│    → rolloutEstimate()    // Monte Carlo 前向估算     │
│    → evaluateFullSequence() // 全局目标函数评分      │
│  → TransitionPlan (Map<shotIndex, Decision>)        │
│  → useMemo 缓存，shots 不变则不重算                 │
├──────────────────────────────────────────────────────┤
│  EDITORIAL POLICY LAYER                              │
│  EditorState: budget + cooldown + lastTransition      │
│  energyCurve + whipDensityConstraint                  │
│  → pure function, no side effects                   │
├──────────────────────────────────────────────────────┤
│  EXECUTION LAYER  （每帧运行，render 内）             │
│  useShotsAroundFrame() → Map.get(idx)               │
│  → CSS transform + opacity + microCutScale            │
│  → React Img rendering                               │
│  EXECUTION LAYER  （每帧运行，在 render 内）          │
│  useShotsAroundFrame() → Map.get(idx)               │
│  → CSS transform + opacity + microCutScale          │
│  → React Img rendering                              │
└─────────────────────────────────────────────────────┘
```

### 核心设计模式演进

| 设计模式 | 首次引入 | 后续演变 |
|----------|----------|----------|
| Frame-based time model | v1 | 持续 |
| Word-level time binding | v6 | v7 优化 |
| Binary search | v7 | v13 查表替代 |
| Signal-to-visual mapping | v9 | 持续 |
| Shot as first-class citizen | v10 | v13 规划单元 |
| Emotion-aware easing | v10.2 | 持续 |
| Transition Memory | v12 | v13 收入纯函数 |
| Planned vs Reactive separation | v13 | v14 扩展 |
| Global energy curve | v14 | v16 全局评分维度 |
| Editorial constraint system | v14 | v16 Beam Search |
| Full-sequence scoring | v16 | — |
| Monte Carlo Rollout | v16 | — |
| Beam search (approximate) | v15 | v16 完整实现 |

### 未解决的方向（v16 之后的演进空间）

| 方向 | 当前状态 | 说明 |
|------|----------|------|
| 全局能量曲线 | ✅ 完成 | 三点采样，可升级为 spline |
| Whip 密度控制 | ✅ 完成 | 滑动窗口，贪婪移除 |
| Plan 评分 | ✅ 完成 | 四维度加权，可调权重 |
| Audio beat sync | ❌ 未做 | TTS VTT → 音频节拍驱动剪辑 |
| Motion peak detection | ❌ 未做 | 图片序列 motion 分析 |
| Semantic cut point | ❌ 未做 | 对象运动峰值 + 人脸表情峰值 |
| Simulated annealing / SA | ❌ 未做 | 从 beam search → 真正全局随机优化 |
| Per-video policy learning | ❌ 未做 | 用户行为数据 → 个性化剪辑风格 |
| RL-based scoring | ❌ 未做 | 用 engagement 数据训练 reward function |
