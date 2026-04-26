/**
 * VideoScene.tsx - V16 Constraint-based Combinatorial Editorial Optimizer
 *
 * 系统形式化：
 *   π* = argmax_{π ∈ 𝒫} F(π)
 *   其中 π = transition sequence (whip/fade/zoom)
 *   𝒫 = 满足 budget + cooldown + diversity 约束的合法路径集合
 *   F(π) = evaluateFullSequence(π) — 全局能量函数
 *
 * v16 三层优化结构：
 *   Layer 1 (Decision)   → decideTransition()     — constrained action space
 *   Layer 2 (Search)      → beamSearchTransitionPlan — beam search over discrete space
 *   Layer 3 (Eval)       → evaluateFullSequence()  — continuous global energy function
 *
 * v15 vs v16 本质区别：
 *   v15: score = Σ Δs_t（Markov 贪婪累加，beam search 保留多条 greedy）
 *   v16: F(π) = GlobalStructure(energy, entropy, pacing, semantics)
 *         rolloutEstimate ≈ E[F(π_full)]（有限视野近似，接近 MCTS rollout）
 *
 * 架构定位：
 *   从 "剪辑逻辑系统" → "Sequence-level optimization engine"
 *
 *   v12: reactive, frame-level, module-state
 *   v13: planned, shot-level, pure function
 *   v14: greedy, timeline-level, constraint-aware
 *   v15: beam search, globally-aware scoring (伪全局)
 *   v16: full-sequence scoring + Monte Carlo rollout ≈ Deterministic MCTS
 */
import React, { useMemo } from "react";
import { AbsoluteFill, Audio, Sequence, useCurrentFrame, useVideoConfig, interpolate, Easing, Img, spring } from "remotion";
import { FONT_FAMILY } from "./constants";
import type { VideoLayout, VideoElement, TextElement, ImageElement, StickerElement, BackgroundElement, ShapeElement, Shot } from "./types";
import { getCameraShotTransform } from "./cameraExpression";
import { evaluateDirector, type DirectorState } from "./directorEval";
import { getLayoutDurationInFrames } from "./layoutUtils";
import { getLayerAnimationStyle } from "./layerAnimation";
import { buildFeatureVector, w0, b0, w1, b1, w2, b2, norm_X_mean, norm_X_std, norm_y_mean, norm_y_std } from "./rewardModel";
import { ShotDensityStack } from "./shotDensity";
import {
  getLinearTransitionProgress,
  getPresentationStyle,
  resolveTransitionPresentation,
} from "./transitionPresentation";

/**
 * (π, E, J) — MCTS runtime control params
 *
 *   E_bias      → reward shaping 乘子（默认 1.0）
 *   Pi_temp     → softmax 温度（默认 1.0）
 *   J_noise     → Dirichlet 噪声 ε（默认 0.25）
 *   SIMULATION_COUNT → MCTS rollouts（默认 5）
 */
export interface MctsControlParams {
  E_bias?: number;
  Pi_temp?: number;
  J_noise?: number;
  SIMULATION_COUNT?: number;
  /** Style preset — biases render params toward a creator style */
  stylePreset?: StylePreset;
}

/** Stats emitted after beam search completes (fed back to UI) */
export interface BeamSearchStats {
  /** Root-level transition candidates with normalized scores */
  rootChildren: Array<{
    type: TransitionType;
    score: number;
    visits: number;
    modelScore: number;
  }>;
  /** Rule-based reward breakdown */
  reward: {
    energy_alignment: number;
    entropy: number;
    pacing_smoothness: number;
    micro_cut_semantic: number;
    energy_transition_alignment: number;
  };
  /** Control params used for this run */
  control: MctsControlParams;
}

function _relu(x: number): number { return x > 0 ? x : 0; }
function _sigmoid(x: number): number { return x >= 0 ? 1/(1+Math.exp(-x)) : Math.exp(x)/(1+Math.exp(x)); }

function predictInline(xRaw: number[]): number {
  // Normalize input
  const x: number[] = [];
  for (let i = 0; i < 28; i++) {
    x.push((xRaw[i] - norm_X_mean[i]) / (norm_X_std[i] + 1e-8));
  }
  // Layer 1: 28 -> 64 (relu)
  const h1: number[] = [];
  for (let j = 0; j < 64; j++) {
    let s = b0[j];
    for (let i = 0; i < 28; i++) s += x[i] * w0[i][j];
    h1.push(_relu(s));
  }
  // Layer 2: 64 -> 32 (relu)
  const h2: number[] = [];
  for (let j = 0; j < 32; j++) {
    let s = b1[j];
    for (let i = 0; i < 64; i++) s += h1[i] * w1[i][j];
    h2.push(_relu(s));
  }
  // Layer 3: 32 -> 1 (linear)
  let s = b2[0];
  for (let i = 0; i < 32; i++) s += h2[i] * w2[0][i];
  return s * norm_y_std + norm_y_mean;
}

// ============================================================
// v13: Transition Planner（核心新模块）
// ============================================================

export type TransitionType = "whip" | "fade" | "zoom";

/**
 * E breakdown → render control vector
 * E features directly control how the video looks, not just scoring.
 */
export interface RenderParams {
  /** 0~1: higher = more camera shake + faster zoom */
  motionIntensity: number;
  /** Controls transition curve and timing feel */
  transitionStyle: "hard" | "smooth" | "glitch";
  /** 0~1: higher = more frequent micro-cuts */
  cutDensity: number;
}

/** Style presets — human-readable style buttons for creator UI */
export type StylePreset = "tiktok_fast" | "cinematic" | "glitch_edit";

/**
 * Style preset → RenderParams
 * This is the "human language to machine params" translation layer.
 * Exposed so the server/UI can pass a preset name instead of raw params.
 */
export function styleToRenderParams(
  preset: StylePreset,
  /** Intensity slider 0~1, multiplies motionIntensity */
  intensity = 0.7,
): RenderParams {
  switch (preset) {
    case "tiktok_fast":
      return {
        motionIntensity: Math.min(1, 0.6 + intensity * 0.4),
        transitionStyle: "glitch",
        cutDensity: Math.min(1, 0.5 + intensity * 0.5),
      };
    case "cinematic":
      return {
        motionIntensity: Math.min(1, 0.2 + intensity * 0.3),
        transitionStyle: "smooth",
        cutDensity: Math.min(1, 0.2 + intensity * 0.3),
      };
    case "glitch_edit":
      return {
        motionIntensity: Math.min(1, 0.5 + intensity * 0.5),
        transitionStyle: "glitch",
        cutDensity: Math.min(1, 0.7 + intensity * 0.3),
      };
  }
}

/**
 * 单个镜头的 transition 决策
 * microCut: shot 内部的 micro-cut（v13 新增，在 shot 60%~62% 处做微冲击）
 */
export interface TransitionDecision {
  shotIndex: number;
  type: TransitionType;
  /** shot 内部 micro-cut 的时间点（0~1，相对于 shot 长度） */
  microCutAt?: number;
  /** micro-cut 的强度（0~1） */
  microCutIntensity?: number;
  /** E breakdown → render control signal */
  renderParams?: RenderParams;
  /** Temporal highlight: this shot is in a rhythm peak zone (from TemporalHighlightPlanner) */
  isHighlight?: boolean;
}

/**
 * 全量 transition 规划（整个 timeline 一次性算好）
 * Map: shotIndex → TransitionDecision
 */
export type TransitionPlan = Map<number, TransitionDecision>;

/**
 * v13: 剪辑预算系统（Budget-based Editorial Policy）
 *
 * whip   = -3 budget（高消耗）
 * zoom   = -1 budget
 * fade   = +0.5 budget（恢复）
 *
 * budget 耗尽 → 强制 fade/zoom
 * budget 缓慢自动恢复（+0.5/shot）
 *
 * cooldown: cooldown > 0 时禁止 whip
 */
interface EditorState {
  budget: number;
  cooldown: number; // frames
  lastTransition: TransitionType;
}

const MAX_BUDGET = 6;
const BUDGET_REGEN = 0.5;      // 每 shot 恢复 0.5 budget
const WHIP_COST = 3;
const ZOOM_COST = 1;
const COOLDOWN_FRAMES = 8;     // whip 后强制 cooldown 8 帧（≈ 0.27秒@30fps）
const MAX_CONSECUTIVE_WHIP = 2;
const Q_DECAY = 0.7;           // v20 fix: Q-table decay factor (prevents cross-episode lock-in)

// ============================================================
// v14: Global Energy Curve & Optimizer Helpers
// ============================================================

/**
 * v14: 全局连续能量曲线
 *
 * 将离散的 per-shot emotion 采样 → 连续平滑曲线
 * 使用分段线性插值（spline 也可以但当前用 smoothstep 效果更好）
 *
 * 用于：
 *   - 全局节奏密度分析
 *   - micro-cut 位置语义锚点
 *   - whip transition 与高能量区间的对齐评分
 */
function buildGlobalEnergyCurve(
  shots: Shot[],
  emotions: number[],
  fps: number
): Array<{ frame: number; energy: number }> {
  if (shots.length === 0) return [];

  const samples: Array<{ frame: number; energy: number }> = [];
  // 在每个 shot 的 20%, 50%, 80% 处采样（不用 midpoint，用三点更平滑）
  for (let i = 0; i < shots.length; i++) {
    const shot = shots[i];
    const emotion = emotions[i] ?? 0.5;
    const f20 = shot.start + shot.duration * 0.2;
    const f50 = shot.start + shot.duration * 0.5;
    const f80 = shot.start + shot.duration * 0.8;
    // 三点均值，减少异常值影响
    const avgEmotion = emotion;
    samples.push({ frame: f20, energy: avgEmotion });
    samples.push({ frame: f50, energy: avgEmotion });
    samples.push({ frame: f80, energy: avgEmotion });
  }

  // 按 frame 排序（理论上已经是有序的）
  return samples.sort((a, b) => a.frame - b.frame);
}

/**
 * v14: 在 shot 内找能量峰值帧
 *
 * 用折返方式找能量最高的采样点 frame
 * 作为 semantic micro-cut anchor
 *
 * 效果：micro-cut 不再是"第 60% 帧"
 * 而是"这个 shot 里能量最高的时刻"
 */
function findEmotionPeakFrame(
  shot: Shot,
  energyCurve: Array<{ frame: number; energy: number }>,
  defaultFrac: number,
  fps: number
): number {
  const inShot = energyCurve.filter(
    (p) => p.frame > shot.start + fps * 0.1 && p.frame < shot.start + shot.duration - fps * 0.1
  );
  if (inShot.length === 0) {
    return shot.start + shot.duration * defaultFrac;
  }
  const peak = inShot.reduce((best, p) => (p.energy > best.energy ? p : best));
  return peak.frame;
}

/**
 * v14: Whip 密度约束（全局窗口控制）
 *
 * 保证：每 150 帧（约 5 秒 @30fps）最多 1 次 whip
 * 防止：连续 whip 集中在某一时段导致"节奏窒息"
 *
 * 策略：贪婪移除最低强度的 whip，直到满足密度约束
 */
function enforceWhipDensityConstraint(
  plan: TransitionPlan,
  shots: Shot[],
  fps: number
): void {
  const WINDOW_FRAMES = 150;  // 150帧 ≈ 5秒 @30fps
  const MAX_WHIP_PER_WINDOW = 1;

  // 收集所有 whip transition 的 shotIndex
  const whipIndices: number[] = [];
  plan.forEach((dec, idx) => {
    if (dec.type === "whip") whipIndices.push(idx);
  });

  // 滑动窗口检测：统计每个窗口内的 whip 数量
  function countWhipsInWindow(startFrame: number): number {
    return whipIndices.filter((i) => {
      const t = shots[i].start;
      return t >= startFrame && t < startFrame + WINDOW_FRAMES;
    }).length;
  }

  // 持续收紧直到满足密度约束
  let changed = true;
  while (changed) {
    changed = false;
    const sortedWhips = [...whipIndices].sort((a, b) => {
      // 按 shot 能量降序：能量高的 whip 优先保留
      const aDec = plan.get(a)!;
      const bDec = plan.get(b)!;
      return (bDec.microCutIntensity ?? 0) - (aDec.microCutIntensity ?? 0);
    });

    for (const idx of sortedWhips) {
      const startFrame = shots[idx].start;
      if (countWhipsInWindow(startFrame) > MAX_WHIP_PER_WINDOW) {
        // 强制降级为 zoom
        const dec = plan.get(idx)!;
        plan.set(idx, { ...dec, type: "zoom" });
        whipIndices.splice(whipIndices.indexOf(idx), 1);
        changed = true;
      }
    }
  }
}

/**
 * v14: Plan 全局质量评分（用于调试和未来优化方向）
 *
 * 评分维度：
 *   1. 多样性（transition type 分布是否均匀）
 *   2. Budget 利用率（是否在 budget 范围内高效消耗）
 *   3. 节奏对齐（whip 是否对齐高能量区间）
 */
function scorePlan(
  plan: TransitionPlan,
  shots: Shot[],
  energyCurve: Array<{ frame: number; energy: number }>,
  _fps: number
): number {
  if (plan.size === 0) return 0;

  // 1. 多样性评分（0~1，越高越好）
  const typeCount = { whip: 0, fade: 0, zoom: 0 };
  plan.forEach((dec) => { typeCount[dec.type]++; });
  const total = plan.size;
  const typeProbs = [typeCount.whip / total, typeCount.fade / total, typeCount.zoom / total];
  const diversity = 1 - Math.max(...typeProbs); // 最高类型占比越低，多样性越高

  // 2. Budget 利用率（whip 是高消耗高回报）
  const whipRatio = typeCount.whip / total;
  const budgetScore = Math.min(1, whipRatio * 2); // whip 占比 50% 时得满分

  // 3. 节奏对齐（whip 落在高能量区间的比例）
  const energyThreshold = 0.65;
  let alignmentHits = 0;
  plan.forEach((dec) => {
    if (dec.type === "whip") {
      const peakFrame = findEmotionPeakFrame(shots[dec.shotIndex], energyCurve, 0.6, _fps);
      const peakEnergy = energyCurve.find((p) => p.frame === peakFrame)?.energy ?? 0;
      if (peakEnergy >= energyThreshold) alignmentHits++;
    }
  });
  const alignmentScore = typeCount.whip > 0 ? alignmentHits / typeCount.whip : 1;

  // 加权总分
  return diversity * 0.4 + budgetScore * 0.3 + alignmentScore * 0.3;
}

// ============================================================
// ============================================================
// v16: Global Sequence Optimization (Full-Objective Editor)
// ============================================================

/**
 * v16: Beam 结构（改为 cost-based，score 从全局评估得到）
 *
 * 核心变化（相比 v15）：
 *   v15: beam.score = 增量累积（greedy accumulation）
 *   v16: beam.score = 待评估（beam search 时为 pending，
 *                               最终在 evaluateFullSequence 中统一计算）
 */
interface Beam {
  plan: TransitionPlan;
  state: EditorState;
  pendingCost: number;
  consecutiveWhip: number;
}

interface MctsNode {
  shotIndex: number;
  abstractionKey: string;  // v17.1: 抽象状态键，用于 Q-Table 共享
  state: EditorState;
  parent: MctsNode | null;
  children: MctsNode[];
  visits: number;
  Q: number;
  consecutiveWhip: number;
  type: TransitionType | null;
  plan: TransitionPlan;
  expansionCount: number;  // v20 fix: progressive widening — times this node was expanded
}

const BEAM_WIDTH = 4;
const TRANSITION_TYPES: TransitionType[] = ["whip", "fade", "zoom"];

// ============================================================
// v17.1: State Abstraction Layer（让 MCTS 从"记忆型"变"泛化型"）
// ============================================================

/**
 * v17.1: State Abstraction — 核心升级
 *
 * 问题：tabular MCTS 每个 shot 都是独立 state，即使 energy/emotion 很相似也不会共享 Q
 *
 * 解决：离散化 + hash，让相似 state 共享 Q-value
 *
 * abstraction key 生成：
 *   energy_bucket  = round(energy / 0.10)  → 0~10 的离散桶
 *   emotion_bucket  = round(emotion / 0.10) → 0~10 的离散桶
 *   rhythm_phase    = floor(frame / 150)    → 每 150 帧（~5s@30fps）为同一相位
 *   lastTransition  = 直接字符串
 *   cooldown_bucket = round(cooldown / 4)  → 每 4 帧一个桶
 *
 * 效果：
 *   - 相似 state 共享 Q → search tree 压缩 5-10x
 *   - rollout variance ↓
 *   - MCTS 从"记忆型"变"泛化型"
 */
function discretize(value: number, step: number): number {
  return Math.round(value / step);
}

/**
 * v19.6b: Soft binning for energy — preserves fractional position within coarse bin
 * CRITICAL: use FLOOR not ROUND for coarse bin, so energies within [b*step, (b+1)*step)
 *   all share the same coarse bin but have different fractional keys.
 * Example: 0.62 → floor(0.62/0.10)=6, frac=(0.62-0.60)/0.10=0.2 → 6+0.1=6.1
 *          0.64 → floor(0.64/0.10)=6, frac=(0.64-0.60)/0.10=0.4 → 6+0.2=6.2
 *          0.65 → floor(0.65/0.10)=6, frac=(0.65-0.60)/0.10=0.5 → 6+0.25=6.25
 *   → 0.62 and 0.65 (within same 0.10 bin) now have different keys!
 *   → Whip@0.62 vs Whip@0.65 are distinguishable in Q-table.
 */
function softDiscretizeEnergy(energy: number): number {
  const coarseBin = Math.floor(energy / 0.10);  // FLOOR: 0.64 and 0.65 both in bin 6
  const frac = (energy - coarseBin * 0.10) / 0.10;  // fractional position within bin [0, 1)
  return coarseBin + frac * 0.5;  // fractional offset ∈ [0, 0.5)
}

function getStateAbstractionKey(
  energy: number,
  emotion: number,
  frame: number,
  lastTransition: TransitionType,
  cooldown: number,
  consecutiveWhip: number
): string {
  // v19.6b fix: refine abstraction granularity to resolve plan collapse
  // OLD: energy bucket = 0.10 → whip@0.62 and whip@0.68 merged into same state
  // v19.6b 1st attempt: energy bucket = 0.05 → 2x resolution but over-fragmented
  // v19.6b FINAL: soft binning — coarse 0.10 bucket + fractional offset per energy
  //   0.62 → 6.10 (coarse bin 6 + frac 0.2 → ×0.5 = 0.1), 0.68 → 6.40
  //   → Same coarse bucket as 0.10, but continuous resolution within it
  //   → Avoids aliasing AND fragmentation simultaneously
  const energyBucket = softDiscretizeEnergy(energy);  // soft binning: 6.1, 6.4, etc.
  const emotionBucket = discretize(emotion, 0.10);  // 0~10
  const rhythmPhase = Math.floor(frame / 150);        // 每 150 帧一个相位
  const cooldownBucket = discretize(cooldown, 4);    // 每 4 帧一个桶
  return `${energyBucket}|${emotionBucket}|${rhythmPhase}|${lastTransition}|${cooldownBucket}|${consecutiveWhip}`;
}

// 全局 Q-Table：abstraction key → Q-value（跨节点共享）
// v19.6b: MUST be reset per episode — old Q values are from previous reward function
//   and will cause corr(Q,reward) = -0.95 even with correct rollout + reward.
const globalQTable: Record<string, { visits: number; Q: number }> = {};

function decayGlobalQTable(decay: number): void {
  // v20 fix: Q-table decay — shift all Q-values toward neutral (0.5).
  // decay=0.7: Q_new = 0.7*Q_old + 0.3*0.5.
  // Preserves relative ordering while preventing absolute lock-in.
  for (const key of Object.keys(globalQTable)) {
    globalQTable[key].Q = decay * globalQTable[key].Q + (1 - decay) * 0.5;
  }
}

// ── abstraction key 的 rollout 统计（用于评估）─────────────
function getAbstractQ(abstractKey: string): number {
  return globalQTable[abstractKey]?.Q ?? 0.5;  // 未知 state 返回中性 0.5
}

function updateAbstractQ(abstractKey: string, reward: number): void {
  if (!globalQTable[abstractKey]) {
    globalQTable[abstractKey] = { visits: 0, Q: 0.5 };
  }
  const entry = globalQTable[abstractKey];
  entry.visits++;
  // v20 fix: decay-averaged update — blends new observation with prior.
  // This makes Q robust to single-episode noise without full reset.
  entry.Q = entry.Q + (reward - entry.Q) / (entry.visits ** 0.5);
}

// ============================================================
// v18: Reward Data Collector（把剪辑系统变成可训练环境）
// ============================================================

/**
 * v18: Reward Data Infrastructure
 *
 * 本质：将"剪辑优化系统"升级为"强化学习训练数据生成器"
 *
 * 收集的数据：
 *   1. best plan + reward（被选中的）
 *   2. rejected alternatives（被拒绝的 beam）→ 用于 future preference learning
 *   3. MCTS 统计（visit count, Q variance）→ 用于 uncertainty-aware reward model
 *
 * 输出格式：JSONL（每行一个 complete episode）
 * 用于未来训练 neural reward model：
 *   F(π) ≈ learned_reward_model(π, context)
 */
interface RewardFeatures {
  energy_alignment: number;
  entropy: number;
  pacing_smoothness: number;
  micro_cut_semantic: number;
  /** v19.6b: how well each transition type matches the shot's energy level */
  energy_transition_alignment: number;
}

interface RewardContext {
  shot_count: number;
  fps: number;
  duration_frames: number;
  emotion_histogram: number[];
  energy_histogram: number[];
}

interface RewardPlanEntry {
  shot: number;
  type: TransitionType;
  microCutAt?: number;
  microCutIntensity?: number;
}

interface MctsStats {
  avgVisits: number;
  qVariance: number;
  totalNodes: number;
  rootChildren: number;
}

interface AlternativePlan {
  /** Optional name: "all-zoom" | "all-fade" | "anti-energy" | "alternating" | undefined */
  name?: string;
  plan: RewardPlanEntry[];
  reward: number;
  features: RewardFeatures;
  qValue: number;
  visits: number;
}

interface RewardDataEntry {
  id: string;
  timestamp: number;
  selected_plan: RewardPlanEntry[];
  selected_reward: number;
  selected_features: RewardFeatures;
  alternatives: AlternativePlan[];
  mcts_stats: MctsStats;
  context: RewardContext;
  /** v19.6b: reward function version — used to filter corrupted vs clean data */
  reward_version: string;
}

class RewardDataCollector {
  private entries: RewardDataEntry[] = [];
  private episodeId: string = "";

  resetEpisode(): void {
    this.episodeId = `episode_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    this.entries = [];
  }

  collect(
    selectedPlan: TransitionPlan,
    selectedReward: number,
    selectedFeatures: RewardFeatures,
    alternatives: AlternativePlan[],
    shots: Shot[],
    emotions: number[],
    fps: number
  ): void {
    // 构建 emotion histogram (10 buckets)
    const emotionHist = new Array(10).fill(0);
    for (const e of emotions) {
      const bucket = Math.min(9, Math.floor(e * 10));
      emotionHist[bucket]++;
    }

    // 构建 energy histogram from shots
    const energyHist = new Array(10).fill(0);
    for (const shot of shots) {
      const avgEnergy = emotions[shots.indexOf(shot)] ?? 0.5;
      const bucket = Math.min(9, Math.floor(avgEnergy * 10));
      energyHist[bucket]++;
    }

    const planEntries: RewardPlanEntry[] = [];
    selectedPlan.forEach((v, k) => {
      planEntries.push({ shot: k, type: v.type, microCutAt: v.microCutAt, microCutIntensity: v.microCutIntensity });
    });
    planEntries.sort((a, b) => a.shot - b.shot);

    const entry: RewardDataEntry = {
      id: `${this.episodeId}_${this.entries.length}`,
      timestamp: Date.now(),
      selected_plan: planEntries,
      selected_reward: selectedReward,
      selected_features: selectedFeatures,
      alternatives,
      mcts_stats: { avgVisits: 0, qVariance: 0, totalNodes: 0, rootChildren: 0 },
      context: {
        shot_count: shots.length,
        fps,
        duration_frames: shots.reduce((sum, s) => sum + s.duration, 0),
        emotion_histogram: emotionHist,
        energy_histogram: energyHist,
      },
      reward_version: "v19.6b_clean",
    };

    this.entries.push(entry);
  }

  // v18: 收集 MCTS 统计（需在 MCTS 完成后调用）
  setMctsStats(root: MctsNode): void {
    if (this.entries.length === 0) return;

    const lastEntry = this.entries[this.entries.length - 1];
    const visits: number[] = [];
    let totalQ = 0;
    let nodeCount = 0;

    // BFS 遍历所有节点收集统计
    const queue: MctsNode[] = [root];
    while (queue.length > 0) {
      const node = queue.shift()!;
      visits.push(node.visits);
      totalQ += node.Q;
      nodeCount++;
      for (const child of node.children) {
        queue.push(child);
      }
    }

    const avgVisits = visits.reduce((a, b) => a + b, 0) / (visits.length || 1);
    const avgQ = totalQ / (nodeCount || 1);
    const qVariance = visits.length > 0
      ? visits.reduce((sum, v, i) => sum + Math.pow(nodeCount > 0 ? totalQ / nodeCount : 0 - v, 2), 0) / visits.length
      : 0;

    lastEntry.mcts_stats = {
      avgVisits,
      qVariance,
      totalNodes: nodeCount,
      rootChildren: root.children.length,
    };
  }

  getEntries(): RewardDataEntry[] {
    return this.entries;
  }

  exportJSONL(): string {
    return this.entries.map(e => JSON.stringify(e)).join('\n');
  }
}

// 全局实例（每个视频生成任务一个 episode）
let rewardCollector = new RewardDataCollector();

/**
 * v18: 获取已收集的 Reward Data（供 server 端 flush 到 JSONL）
 */
export function getRewardData(): string {
  return rewardCollector.exportJSONL();
}

/**
 * v18: 获取原始 entries（供 server 做 intermediate 分析）
 */
export function getRewardEntries(): RewardDataEntry[] {
  return rewardCollector.getEntries();
}

/**
 * v16: Pure Decision Function（无评分，纯逻辑）
 *
 * 给定当前 state + emotion + beat，输出一个合法的 transition type
 * 不返回分数，只返回决策结果和更新后的状态
 *
 * 与 v15 的本质区别：
 *   v15: 评估每个候选的增量 score → 搜索空间被 score 引导
 *   v16: 只返回合法决策 → 评分全部推迟到全局评估
 */
function decideTransition(
  type: TransitionType,
  state: EditorState,
  emotion: number,
  beat: number,
  consecutiveWhip: number
): { type: TransitionType; newState: EditorState; newConsecutiveWhip: number } {
  const rhythmBoost = beat > 0.6 ? 1 : beat < -0.4 ? -1 : 0;

  let finalType = type;
  if (state.cooldown > 0 && finalType === "whip") {
    finalType = "zoom";
  }

  if (finalType === "whip" && state.budget < WHIP_COST) {
    finalType = state.budget >= ZOOM_COST ? "zoom" : "fade";
  }

  if (finalType === state.lastTransition) {
    if (finalType === "whip") finalType = "zoom";
    else if (finalType === "zoom") finalType = "fade";
    else finalType = "zoom";
  }

  let newConsecutiveWhip = consecutiveWhip;
  if (finalType === "whip") {
    newConsecutiveWhip++;
    if (newConsecutiveWhip >= MAX_CONSECUTIVE_WHIP) {
      finalType = "zoom";
      newConsecutiveWhip = 0;
    }
  } else {
    newConsecutiveWhip = 0;
  }

  const newState: EditorState = { ...state };
  if (finalType === "whip") {
    newState.budget -= WHIP_COST;
    newState.cooldown = COOLDOWN_FRAMES;
  } else if (finalType === "zoom") {
    newState.budget -= ZOOM_COST;
  } else {
    newState.budget = Math.min(MAX_BUDGET, newState.budget + BUDGET_REGEN);
  }
  newState.lastTransition = finalType;

  return { type: finalType, newState, newConsecutiveWhip };
}

/**
 * v16: 统一全局目标函数（Full-Sequence Scoring）
 *
 * 这是 v16 的核心创新：
 *   不是增量累积 score，而是在完整序列上统一评估全局目标
 *
 * Score 维度：
 *   1. Energy Alignment - whip 是否落在高能量区间
 *   2. Rhythm Entropy - transition type 分布的熵
 *   3. Pacing Smoothness - whip 在时间轴上的分布均匀程度
 *   4. Micro-cut Semantic - micro-cut 是否落在能量峰值
 */
function evaluateFullSequence(
  plan: TransitionPlan,
  energyCurve: Array<{ frame: number; energy: number }>,
  shots: Shot[],
  emotions: number[],
  fps: number
): number {
  if (plan.size === 0) return 0;
  const WINDOW_FRAMES = 150;

  // ── v19.6b: Continuous Energy Alignment (sigmoid-scaled) ────────
  // Continuously varies with energy. whip: sigmoid((peakE-0.5)*4)*0.5 ∈ [0, 0.5]
  //   fade: (0.3-|peakE-0.5|)/0.3 * 0.3 ∈ [0, 0.3]
  // No clamp → full dynamic range preserved.
  const shotIndices = [...plan.keys()].sort((a, b) => a - b);
  let energyScoreSum = 0;
  let energyCount = 0;
  plan.forEach((dec) => {
    const shot = shots[dec.shotIndex];
    if (!shot) return;
    const peakFrame = findEmotionPeakFrame(shot, energyCurve, 0.6, fps);
    const peakEnergy = energyCurve.find(
      (p) => Math.abs(p.frame - peakFrame) < fps * 0.5
    )?.energy ?? emotions[dec.shotIndex] ?? 0.5;
    if (dec.type === "whip") {
      // sigmoid((peakE-0.5)*4) maps [-0.5,+0.5] → [0.12,0.88]; ×0.5 → [0.06,0.44]
      const e = Math.max(-0.5, Math.min(0.5, peakEnergy - 0.5));
      energyScoreSum += 0.5 * (1 / (1 + Math.exp(-e * 4)));
      energyCount++;
    } else if (dec.type === "fade") {
      energyScoreSum += (0.3 - Math.abs(peakEnergy - 0.5)) / 0.3 * 0.3;
      energyCount++;
    }
  });
  // No clamp — let energy score use full [0, 1] range naturally
  const energyAlignmentScore = energyCount > 0 ? energyScoreSum / energyCount : 0;

  // ── v20: transitionSuitability — per-shot Gaussian scoring + variance penalty
  // Key insight: each transition type has an optimal energy level (whip=high, zoom=medium, fade=low).
  // Using Gaussian lets us score "how correct" each transition is, then penalize inconsistency.
  let transScores: number[] = [];
  for (const [, dec] of plan.entries()) {
    const shot = shots[dec.shotIndex];
    if (!shot) continue;
    const peakFrame = findEmotionPeakFrame(shot, energyCurve, 0.6, fps);
    const e = energyCurve.find(p => Math.abs(p.frame - peakFrame) < fps * 0.5)?.energy
      ?? emotions[dec.shotIndex] ?? 0.5;
    let s = 0.5;
    if (dec.type === "whip") {
      s = Math.exp(-Math.pow(e - 0.8, 2) / (2 * 0.04)); // sigma=0.2
    } else if (dec.type === "zoom") {
      s = Math.exp(-Math.pow(e - 0.5, 2) / (2 * 0.0625)); // sigma=0.25
    } else if (dec.type === "fade") {
      s = Math.exp(-Math.pow(e - 0.2, 2) / (2 * 0.04)); // sigma=0.2
    }
    transScores.push(s);
  }
  const tMean = transScores.length > 0 ? transScores.reduce((a, b) => a + b, 0) / transScores.length : 0.5;
  const tVar = transScores.length > 0
    ? transScores.reduce((sum, s) => sum + Math.pow(s - tMean, 2), 0) / transScores.length : 0;
  // High mean + low variance = plan is consistently well-aligned → reward it
  const transitionSuitability = 0.7 * tMean + 0.3 * (1 - Math.sqrt(tVar));

  // ── v20: structurePatternScore — penalizes repetitive and mechanical patterns
  // Key: not "how diverse" but "how pattern-abiding"
  const types = [...plan.entries()].sort((a, b) => a[0] - b[0]).map(e => e[1].type);
  let repeat = 0, alt = 0;
  for (let i = 0; i < types.length - 1; i++) {
    if (types[i] === types[i + 1]) repeat++;
    if (i < types.length - 2 && types[i] === types[i + 2]) alt++;
  }
  const repeatRatio = repeat / (types.length - 1 || 1);
  const altRatio = alt / (types.length - 2 || 1);
  // Penalize long runs (AAAA) and mechanical alternation (ABAB)
  const structurePatternScore = Math.max(0, 1 - 0.6 * repeatRatio - 0.3 * altRatio);

  // ── v20: Pacing = Gaussian on shot-duration std
  const durations: number[] = [];
  for (const idx of shotIndices) {
    const shot = shots[idx];
    if (shot) durations.push(shot.duration);
  }
  const meanDur = durations.reduce((a, b) => a + b, 0) / (durations.length || 1);
  const varDur = durations.reduce((sum, d) => sum + Math.pow(d - meanDur, 2), 0) / (durations.length || 1);
  const stdDur = Math.sqrt(varDur);
  const pacingScore = Math.exp(-Math.pow(Math.min(1, stdDur / 3.0) - 0.5, 2) / (2 * 0.0625));

  // ── v20: Micro-cut = exp(-avgDistanceToPeak*4)
  let microDistSum = 0, microCount = 0;
  for (const dec of plan.values()) {
    const shot = shots[dec.shotIndex];
    if (!shot || dec.microCutAt === undefined) continue;
    const peakFrame = findEmotionPeakFrame(shot, energyCurve, 0.6, fps);
    const peakFrac = shot.duration > 0 ? (peakFrame - shot.start) / shot.duration : 0.6;
    microDistSum += Math.abs(dec.microCutAt - peakFrac);
    microCount++;
  }
  const avgMicroDist = microCount > 0 ? microDistSum / microCount : 0.5;
  const microCutScore = Math.exp(-avgMicroDist * 4);

  // v20: Final reward — 5 components with clear semantic roles
  return transitionSuitability * 0.35 +
    structurePatternScore * 0.25 +
    pacingScore * 0.20 +
    energyAlignmentScore * 0.10 +
    microCutScore * 0.10;
}

/**
 * v18: Extract 4-dim reward feature breakdown
 * 用于 reward data collection（不改变 evaluateFullSequence 行为）
 */
function computeRewardFeatures(
  plan: TransitionPlan,
  energyCurve: Array<{ frame: number; energy: number }>,
  shots: Shot[],
  emotions: number[],
  fps: number
): { features: RewardFeatures; score: number } {
  if (plan.size === 0) {
    return { features: { energy_alignment: 0, entropy: 0, pacing_smoothness: 0, micro_cut_semantic: 0, energy_transition_alignment: 0 }, score: 0 };
  }
  const WINDOW_FRAMES = 150;

  // ── v19.6b: Continuous Energy Alignment (same as evaluateFullSequence)
  const shotIndices = [...plan.keys()].sort((a, b) => a - b);
  let energyScoreSum = 0;
  let energyCount = 0;
  plan.forEach((dec) => {
    const shot = shots[dec.shotIndex];
    if (!shot) return;
    const peakFrame = findEmotionPeakFrame(shot, energyCurve, 0.6, fps);
    const peakEnergy = energyCurve.find(
      (p) => Math.abs(p.frame - peakFrame) < fps * 0.5
    )?.energy ?? emotions[dec.shotIndex] ?? 0.5;
    if (dec.type === "whip") {
      const e = Math.max(-0.5, Math.min(0.5, peakEnergy - 0.5));
      energyScoreSum += 0.5 * (1 / (1 + Math.exp(-e * 4)));
      energyCount++;
    } else if (dec.type === "fade") {
      energyScoreSum += (0.3 - Math.abs(peakEnergy - 0.5)) / 0.3 * 0.3;
      energyCount++;
    }
  });
  const energyAlignmentScore = energyCount > 0 ? energyScoreSum / energyCount : 0;

  // ── v20: transitionSuitability (same as evaluateFullSequence)
  let transScores: number[] = [];
  for (const [, dec] of plan.entries()) {
    const shot = shots[dec.shotIndex];
    if (!shot) continue;
    const peakFrame = findEmotionPeakFrame(shot, energyCurve, 0.6, fps);
    const e = energyCurve.find(p => Math.abs(p.frame - peakFrame) < fps * 0.5)?.energy
      ?? emotions[dec.shotIndex] ?? 0.5;
    let s = 0.5;
    if (dec.type === "whip") {
      s = Math.exp(-Math.pow(e - 0.8, 2) / (2 * 0.04));
    } else if (dec.type === "zoom") {
      s = Math.exp(-Math.pow(e - 0.5, 2) / (2 * 0.0625));
    } else if (dec.type === "fade") {
      s = Math.exp(-Math.pow(e - 0.2, 2) / (2 * 0.04));
    }
    transScores.push(s);
  }
  const tMean = transScores.length > 0 ? transScores.reduce((a, b) => a + b, 0) / transScores.length : 0.5;
  const tVar = transScores.length > 0
    ? transScores.reduce((sum, s) => sum + Math.pow(s - tMean, 2), 0) / transScores.length : 0;
  const transitionSuitability = 0.7 * tMean + 0.3 * (1 - Math.sqrt(tVar));

  // ── v20: structurePatternScore (same as evaluateFullSequence)
  const types = [...plan.entries()].sort((a, b) => a[0] - b[0]).map(e => e[1].type);
  let repeat = 0, alt = 0;
  for (let i = 0; i < types.length - 1; i++) {
    if (types[i] === types[i + 1]) repeat++;
    if (i < types.length - 2 && types[i] === types[i + 2]) alt++;
  }
  const repeatRatio = repeat / (types.length - 1 || 1);
  const altRatio = alt / (types.length - 2 || 1);
  const structurePatternScore = Math.max(0, 1 - 0.6 * repeatRatio - 0.3 * altRatio);

  // ── v20: pacing = Gaussian (same as evaluateFullSequence)
  const durations: number[] = [];
  for (const idx of shotIndices) {
    const shot = shots[idx];
    if (shot) durations.push(shot.duration);
  }
  const meanDur = durations.reduce((a, b) => a + b, 0) / (durations.length || 1);
  const varDur = durations.reduce((sum, d) => sum + Math.pow(d - meanDur, 2), 0) / (durations.length || 1);
  const stdDur = Math.sqrt(varDur);
  const pacingScore = Math.exp(-Math.pow(Math.min(1, stdDur / 3.0) - 0.5, 2) / (2 * 0.0625));

  // ── v20: micro-cut = exp(-avgDistToPeak*4) (same as evaluateFullSequence)
  let microDistSum = 0, microCount = 0;
  for (const dec of plan.values()) {
    const shot = shots[dec.shotIndex];
    if (!shot || dec.microCutAt === undefined) continue;
    const peakFrame = findEmotionPeakFrame(shot, energyCurve, 0.6, fps);
    const peakFrac = shot.duration > 0 ? (peakFrame - shot.start) / shot.duration : 0.6;
    microDistSum += Math.abs(dec.microCutAt - peakFrac);
    microCount++;
  }
  const avgMicroDist = microCount > 0 ? microDistSum / microCount : 0.5;
  const microCutScore = Math.exp(-avgMicroDist * 4);

  // v20: same as evaluateFullSequence
  const score = transitionSuitability * 0.35 +
    structurePatternScore * 0.25 +
    pacingScore * 0.20 +
    energyAlignmentScore * 0.10 +
    microCutScore * 0.10;

  return {
    features: {
      energy_alignment: energyAlignmentScore,
      entropy: transitionSuitability,
      pacing_smoothness: pacingScore,
      micro_cut_semantic: microCutScore,
      energy_transition_alignment: structurePatternScore,
    },
    score,
  };
}

/**
 * v19.6b: Apply post-processing to a plan (micro-cut anchoring + whip density)
 * Both selected and alternatives must use this SAME post-processing before
 * computeRewardFeatures, otherwise reward is computed on inconsistent plans
 * (selected had micro-cut data, alternatives didn't → micro_cut_semantic=0.5 vs 0).
 */
function postProcessPlan(
  plan: TransitionPlan,
  shots: Shot[],
  emotions: number[],
  energyCurve: Array<{ frame: number; energy: number }>,
  fps: number,
): void {
  // ── 后处理 1: micro-cut 语义锚定 ─────────────────────────
  for (let i = 0; i < shots.length - 1; i++) {
    if (!plan.has(i)) continue;
    const shot = shots[i];
    const emotion = emotions[i] ?? 0.5;
    const peakFrame = findEmotionPeakFrame(shot, energyCurve, 0.6, fps);
    const microCutAt = shot.duration > 0
      ? Math.max(0.55, Math.min(0.9, (peakFrame - shot.start) / shot.duration))
      : 0.60;
    const peakEnergy = energyCurve.find(
      (p) => Math.abs(p.frame - peakFrame) < fps * 0.5
    )?.energy ?? emotion;
    const microCutIntensity = peakEnergy * 0.14;

    const existing = plan.get(i)!;
    plan.set(i, { ...existing, microCutAt, microCutIntensity });
  }

  // ── 后处理 2: Whip 密度约束（硬约束）────────────────────
  enforceWhipDensityConstraint(plan, shots, fps);
}

/**
 * TemporalHighlightPlanner — rhythm-aware highlight placement
 *
 * Short-form video rhythm pattern (TikTok/YouTube Shorts structure):
 *   0%----15%----35%----55%----70%----85%----100%
 *   INTRO   BUILD    PEAK1   MID    PEAK2   OUTRO
 *
 * Budget controller prevents highlight spam:
 *   - At most 1 main climax in peak1 (energy > 0.55)
 *   - At most 1 secondary peak in peak2 (energy > 0.65)
 *   - At most 1 build-up burst (energy > 0.75, position 15-35%)
 *   - Intro (0-15%) and outro (80-100%) never highlight
 *
 * If multiple candidates exist in a zone, only the highest-energy shot highlights.
 * This creates a curated narrative arc, not an "effects montage".
 */
function planHighlightPositions(
  plan: TransitionPlan,
  shots: Shot[],
  emotions: number[],
): void {
  const n = shots.length;
  if (n < 2) return;

  // First pass: gather all candidate positions per zone
  const candidates: Array<{ shotIdx: number; pos: number; energy: number; zone: "peak1" | "peak2" | "build" }> = [];

  for (let i = 0; i < n - 1; i++) {
    const pos = i / (n - 1);
    const energy = emotions[i] ?? 0.5;

    if (pos >= 0.35 && pos <= 0.55 && energy > 0.55) {
      candidates.push({ shotIdx: i, pos, energy, zone: "peak1" });
    } else if (pos >= 0.65 && pos <= 0.80 && energy > 0.65) {
      candidates.push({ shotIdx: i, pos, energy, zone: "peak2" });
    } else if (pos >= 0.15 && pos < 0.35 && energy > 0.75) {
      candidates.push({ shotIdx: i, pos, energy, zone: "build" });
    }
  }

  // Budget enforcement: zone priority peak1 > peak2 > build
  // Within each zone, keep only the highest-energy candidate
  const budget: Record<string, { shotIdx: number; energy: number } | null> = {
    peak1: null,
    peak2: null,
    build: null,
  };

  for (const c of candidates) {
    const slot = budget[c.zone];
    if (!slot || c.energy > slot.energy) {
      budget[c.zone] = { shotIdx: c.shotIdx, energy: c.energy };
    }
  }

  // Second pass: mark only budget-approved shots as highlights
  const highlightShotIdx = new Set<number>();
  for (const [zone, slot] of Object.entries(budget)) {
    if (slot) highlightShotIdx.add(slot.shotIdx);
  }

  // Third pass: apply to plan
  for (let i = 0; i < n - 1; i++) {
    const existing = plan.get(i);
    if (existing) {
      const isHL = highlightShotIdx.has(i);
      plan.set(i, { ...existing, isHighlight: isHL });
    }
  }
}

/**
 * v18: Backtrack full plan from any MCTS child node
 * 用于从 partial node 重建完整 transition plan
 */
function backtrackPlan(node: MctsNode, shots: Shot[]): TransitionPlan {
  const plan = new Map<number, TransitionDecision>();

  // 从叶节点回溯到根
  let cur: MctsNode | null = node;
  while (cur !== null && cur.type !== null) {
    plan.set(cur.shotIndex, { shotIndex: cur.shotIndex, type: cur.type });
    cur = cur.parent;
  }

  // v20 fix: stochastic fill with latent rhythm state + pattern-level diversity.
  //
  // Problem with Markov-1 (prevType only): cannot express long-range structures
  // like periodic whip-zoom-fade-whip or burst-after-smooth phase shifts.
  //
  // Solution: augment the state with a latent energy accumulator that carries
  // cumulative momentum from transitions — purely transition-driven, no time index.
  // energy += Δ(type) where Δ(whip)=+0.6, Δ(fade)=-0.5, Δ(zoom)=-0.05
  // High energy → whip bias; low energy → fade bias; neutral → zoom
  //
  // Combined with pattern mode bias, this gives:
  //   - short-range: prevType-conditional (alternating, burst)
  //   - medium-range: energy momentum (whip-zoom-fade-whip periodicity)
  //   - long-range: pattern mode (coherent rhythm arc)
  const patternMode = samplePatternMode();
  let energy = 0.0;  // latent momentum, transition-driven only

  for (let i = 0; i < shots.length - 1; i++) {
    if (!plan.has(i)) {
      const prevType = plan.get(i - 1)?.type ?? null;
      const sampled = sampleRolloutType(prevType, energy, patternMode);
      plan.set(i, { shotIndex: i, type: sampled });
      // Update latent energy: whip builds momentum, fade bleeds it, zoom slight decay
      if (sampled === 'whip') energy += 0.6;
      else if (sampled === 'fade') energy -= 0.5;
      else energy -= 0.05;  // zoom: slight momentum loss (editorial naturalness)
      // Clamp to prevent unbounded drift; clamp range [-1.5, 1.5]
      energy = Math.max(-1.5, Math.min(1.5, energy));
    }
  }

  return plan;
}

/**
 * v20: Global pattern mode sampler.
 * Each rollout tail picks one coherent rhythm mode — this is what creates
 * real structural diversity (alternating, burst, smooth, mixed), not just
 * local noise from prevType conditioning alone.
 */
type PatternMode = 'alternating' | 'burst' | 'smooth' | 'mixed';

function samplePatternMode(): PatternMode {
  // v20 fix: uniform sampling — no hand-crafted prior bias.
  // Each mode is equally likely; the reward function decides which is best.
  const modes: PatternMode[] = ['alternating', 'burst', 'smooth', 'mixed'];
  return modes[Math.floor(Math.random() * modes.length)];
}

/**
 * v20: Soft pattern-conditional + energy-biased rollout type sampler.
 *
 * Augments Markov-1 (prevType only) with a latent energy accumulator.
 * This enables long-range structures that Markov-1 cannot express:
 *   - whip-zoom-fade-whip (periodic rhythm from energy oscillation)
 *   - burst-after-smooth (phase shift from energy crossing threshold)
 *
 * Design invariant: NO explicit time/frame/shot-index dependency.
 * All dynamics are purely transition-driven:
 *   - pattern mode: shapes bias distribution (soft, not hard)
 *   - energy: accumulated from transitions only, not from step index
 *
 * The key difference from Markov-1:
 *   Markov-1: P(type_t | type_{t-1})         — no memory beyond 1 step
 *   Latent:   P(type_t | type_{t-1}, energy)  — energy carries momentum
 */
function sampleRolloutType(
  prevType: TransitionType | null,
  energy: number,
  mode: PatternMode
): TransitionType {
  // Distribution helper
  const sample = (dist: [number, number, number]): TransitionType => {
    const r = Math.random();
    if (r < dist[0]) return 'whip';
    if (r < dist[0] + dist[1]) return 'zoom';
    return 'fade';
  };

  // ── Energy-based type bias ─────────────────────────────────────────────
  // Maps latent energy ∈ [-1.5, 1.5] to type propensities.
  // High energy (+1.5) → whip;  neutral (0) → zoom;  low (-1.5) → fade.
  // sigmoid maps [-1.5, 1.5] → [0.27, 0.73] → continuous energy bias.
  const sigmoidE = 1 / (1 + Math.exp(-energy * 2.0));
  // sigmoidE ≈ 1.0 → energy high → whip bias
  // sigmoidE ≈ 0.5 → energy neutral → zoom bias
  // sigmoidE ≈ 0.0 → energy low → fade bias

  if (mode === 'alternating') {
    // Strong alternation: nearly force opposite of prevType, modulated by energy.
    const dist: Record<string, [number, number, number]> = {
      whip:  [0.03, 0.87, 0.10],
      zoom:  [0.87, 0.03, 0.10],
      fade:  [0.25, 0.40, 0.35],
      null:  [0.30, 0.50, 0.20],
    };
    return sample(dist[prevType ?? 'null']);
  }

  if (mode === 'burst') {
    // Burst: energy above 0.5 → whip-heavy; below → fade-heavy.
    // Energy is transition-accumulated, not time-indexed — so this is a
    // momentum-driven burst, not a hard temporal switch.
    const whipBias = Math.max(0, sigmoidE - 0.4);  // 0 at neutral, + at high
    const fadeBias = Math.max(0, 0.6 - sigmoidE);   // 0 at neutral, + at low
    const base = 0.30;
    const dist: [number, number, number] = [
      Math.min(0.85, base + whipBias * 1.5),   // whip
      0.50 - whipBias * 0.3 - fadeBias * 0.2, // zoom
      Math.max(0.05, base * fadeBias * 1.5),    // fade
    ];
    return sample(dist);
  }

  if (mode === 'smooth') {
    // Zoom-heavy: 85%+, modulated by energy for natural variation.
    const whipBias = Math.max(0, sigmoidE - 0.6);
    const dist: [number, number, number] = [
      Math.min(0.15, whipBias * 0.3),
      Math.max(0.70, 0.88 - whipBias * 0.5),
      Math.max(0.05, 0.10 - whipBias * 0.1),
    ];
    return sample(dist);
  }

  // mixed: base editorial distribution, energy-modulated
  const wP = 0.20 + (sigmoidE - 0.5) * 0.20;
  const zP = 0.50 - Math.abs(sigmoidE - 0.5) * 0.15;
  const fP = 1 - wP - zP;
  return sample([Math.max(0.05, wP), Math.max(0.05, zP), Math.max(0.05, fP)]);
}

/**
 * v16: Monte Carlo Rollout（简化版向前模拟）
 *
 * 当 beam 尚未覆盖完整 timeline 时，用 rollout 估算完整 score
 * 策略：对当前 beam 的 state，假设剩余 shot 使用 zoom，计算下界
 */
function rolloutEstimate(
  beam: Beam,
  shots: Shot[],
  emotions: number[],
  energyCurve: Array<{ frame: number; energy: number }>,
  fps: number,
  currentIdx: number
): number {
  const fullPlan = new Map(beam.plan);
  for (let i = currentIdx; i < shots.length - 1; i++) {
    if (!fullPlan.has(i)) {
      fullPlan.set(i, { shotIndex: i, type: "zoom" });
    }
  }
  return evaluateFullSequence(fullPlan, energyCurve, shots, emotions, fps);
}

/**
 * v16: Beam Search with Full-Sequence Evaluation
 *
 * 相比 v15 的本质变化：
 *   v15: beam.score = 增量累积（greedy）
 *   v16: beam.score = evaluateFullSequence(plan)（全局评估）
 *   v15: 剪枝用累积分数（误导性的近期偏差）
 *   v16: 剪枝用 rollout 估算（全局 score 的近似）
 */

/**
 * v17: MCTS-UCT Search + Stochastic Rollout
 *
 * v17 在 v16 基础上做本质跃迁：
 *
 *   v16: Beam Search（横向并行，只保留最优路径）
 *         beam.score = rolloutEstimate（近似全局）
 *         无 exploration term
 *         无 visit statistics
 *
 *   v17: Monte Carlo Tree Search + UCT（真正的树搜索）
 *         ① Node tree 替代 Beam[]（树结构替代列表）
 *         ② UCT selection：Q + c·√(ln(N_parent)/N_child)（探索+利用平衡）
 *         ③ Backpropagation：更新 visit count + Q-value
 *         ④ Stochastic rollout：非确定性策略，不再是"全填 zoom"
 *
 * v17 核心范式转变：
 *   "保留最优路径" → "统计意义上的最优策略"
 *
 * 与 v16 的根本区别：
 *   v16: deterministic argmax beam search
 *   v17: stochastic tree search with UCT
 *
 * MCTS-UCT 组件对应关系：
 *   Component       | v17 实现
 *   Policy          | decideTransition() — constrained action space
 *   Selection       | UCT selection — balance explore/exploit
 *   Expansion       | expand() — add all valid child nodes
 *   Simulation      | stochasticRollout() — Monte Carlo evaluation
 *   Backpropagation | backpropagate() — update visits + Q-values
 *   Value function  | evaluateFullSequence() — 直接复用 v16
 **/
export function beamSearchTransitionPlan(
  shots: Shot[],
  emotions: number[],
  fps: number,
  simCountOverride?: number,
  controlParams?: MctsControlParams,
  onComplete?: (stats: BeamSearchStats) => void,
): TransitionPlan {
  const energyCurve = buildGlobalEnergyCurve(shots, emotions, fps);

  // ── Step 1: 构建根节点 ────────────────────────────────────
  // 根节点代表"尚未做任何决策"的初始状态
  const rootAbstractKey = getStateAbstractionKey(
    0.5, 0.5, 0, "zoom", 0, 0
  );
  const root: MctsNode = {
    shotIndex: -1,
    abstractionKey: rootAbstractKey,
    state: { budget: MAX_BUDGET, cooldown: 0, lastTransition: "zoom" as TransitionType },
    parent: null,
    children: [],
    visits: 0,
    Q: 0,
    consecutiveWhip: 0,
    type: null,
    plan: new Map(),
    expansionCount: 0,
  };

  // v18: 重置 Reward Data Collector（新 episode）
  rewardCollector.resetEpisode();
  // v20 fix: Q-table decay (not full reset) — prevents cross-episode lock-in
  // while preserving some prior knowledge. Full reset (v19.6b) caused
  // Q-collapse within an episode; decay maintains exploration continuity.
  decayGlobalQTable(Q_DECAY);

// ── Step 2: MCTS-UCT 主循环 ───────────────────────────────
  // BETA=0.3: visit bonus regularizes Q-noise; β=0.1 made separability worse.
  const SIMULATION_COUNT = controlParams?.SIMULATION_COUNT ?? simCountOverride ?? 5;  // v20 fix: 3→5, noisy Q requires more rollouts for stability
  const EXPLORATION_CONSTANT = 1.0;  // v20 fix: 2.0→1.0, reduce noise-driven exploration
  // v20 fix: progressive widening — expand actions only when visit count is low.
  // |A(s)| = 1 + floor(expansionCount^0.5). Early: few children (explore).

  for (let i = 0; i < shots.length - 1; i++) {
    let currentNode = root;

    // ── Selection：从根向下 UCT 选择到当前层 ────────────────
    for (let depth = 0; depth <= i; depth++) {
      if (currentNode.children.length === 0) break;

      const N_parent = currentNode.visits;
      let bestChild = currentNode.children[0];
      let bestUCT = -Infinity;

      for (const child of currentNode.children) {
        if (child.visits === 0) {
          bestUCT = Infinity;
          bestChild = child;
          break;
        }
        // v19.6b fix: use node-local Q (updated via backpropagate from actual rollouts)
        // instead of globalQTable abstraction — which collapsed all plans into the same
        // abstract state (6-bucket discretization) making Q a visitation frequency counter,
        // not a plan quality estimator. Node-local Q properly tracks per-plan reward.
        const exploitation = child.Q;
        const exploration = EXPLORATION_CONSTANT * Math.sqrt(Math.log(N_parent) / child.visits);
        const uct = exploitation + exploration;
        if (uct > bestUCT) {
          bestUCT = uct;
          bestChild = child;
        }
      }

      currentNode = bestChild;
    }

    // currentNode 现在是第 i 层的最佳选择节点
    // v20 fix: progressive widening — expand only k actions at a time, where
    //   k = 1 + floor(expansionCount^0.5). Early: 1-2 actions (explore).
    //   After 9 expansions: 4 actions. After 25: 6. Caps at all 3 types.
    if (currentNode.children.length === 0) {
      const maxExpand = Math.min(TRANSITION_TYPES.length, 1 + Math.floor(Math.sqrt(currentNode.expansionCount + 1)));
      currentNode.expansionCount++;

      const childShotIndex = currentNode.shotIndex + 1;
      const emotion = emotions[childShotIndex] ?? 0.5;
      const beat = Math.sin((shots[childShotIndex].start / fps) * 0.05);
      const energy = energyCurve.find(
        (p) => p.frame >= shots[childShotIndex].start && p.frame < shots[childShotIndex].start + shots[childShotIndex].duration
      )?.energy ?? emotion;

      // v20 fix: shuffle action order — otherwise expansion always tries [whip, fade, zoom]
      // which biases early exploration toward whip (first in array).
      const shuffledTypes = [...TRANSITION_TYPES].sort(() => Math.random() - 0.5);
      const toExpand = shuffledTypes.slice(0, maxExpand);

      for (const ttype of toExpand) {
        const { type: legalType, newState, newConsecutiveWhip } = decideTransition(
          ttype, currentNode.state, emotion, beat, currentNode.consecutiveWhip
        );

        const childPlan = new Map(currentNode.plan);
        childPlan.set(currentNode.shotIndex, { shotIndex: currentNode.shotIndex, type: legalType });

        const childAbstractKey = getStateAbstractionKey(
          energy, emotion, shots[childShotIndex].start,
          newState.lastTransition, newState.cooldown, newConsecutiveWhip
        );

        // v20 fix: Q-decay initialization — inherit partial prior from globalQTable
        // but blend with neutral prior (0.5) so fresh episodes don't start locked-in.
        // Q_init = decay * prior + (1-decay) * 0.5
        const priorQ = getAbstractQ(childAbstractKey);
        const initQ = Q_DECAY * priorQ + (1 - Q_DECAY) * 0.5;

        const childNode: MctsNode = {
          shotIndex: childShotIndex,
          abstractionKey: childAbstractKey,
          state: newState,
          parent: currentNode,
          children: [],
          visits: 0,
          Q: initQ,  // v20 fix: decayed initialization
          consecutiveWhip: newConsecutiveWhip,
          type: legalType,
          plan: childPlan,
          expansionCount: 0,
        };

        currentNode.children.push(childNode);
      }
    }

    // ── Simulation：对当前 shot 层所有子节点做 deterministic evaluation ──
    // v19.6b fix: 关键结构性修复
    // OLD: stochasticRollout → backpropagate rollout reward
    //   问题: rollout 生成随机补全 plan，与 actual backtrack plan reward 结构性 mismatch
    //   → corr(Q,reward) = -0.98 (self-reinforced wrong policy basin)
    // NEW: backtrackPlan → evaluate → backpropagate actual plan reward
    //   直接消除 rollout-reward vs actual-reward 的 distribution gap
    for (const childNode of currentNode.children) {
      const childBacktrackPlan = backtrackPlan(childNode, shots);
      postProcessPlan(childBacktrackPlan, shots, emotions, energyCurve, fps);
      const { score: backtrackReward } = computeRewardFeatures(
        childBacktrackPlan, energyCurve, shots, emotions, fps
      );
      backpropagate(childNode, backtrackReward);
    }
  }

  // ── Step 3: 从根节点 children 中选最优 action ───────────────
  // v19.6b fix: softmax over Q + exploration bonus (not raw visits)
  //   logit = α*Q + β*ln(visits+1)
  //   α=1: reward estimate is primary signal
  //   β=0.3: visit bonus regularizes Q-noise from 3-rollout simulations.
  //     Increasing rollouts made separability WORSE (Q-accurate but biased vs real reward).
  //     Noisier Q paradoxically helps: lets alternatives occasionally win.
  //   This gives calibrated preference distribution, not frequency distribution.
  function softmaxSample(nodes: MctsNode[], temperature: number = 1.0): MctsNode {
    if (nodes.length === 0) return null as any;
    if (nodes.length === 1) return nodes[0];

    // ── Q normalization (critical for calibrated logits) ────────────────
    // Q ∈ [0,1] (sigmoid output) but log(visits+1) ∈ [0,~6]
    // Without normalization: visit term dominates → soft greedy
    // Fix: min-max normalize Q within root children to [0, 1]
    const qValues = nodes.map(n => n.Q);
    const qMin = Math.min(...qValues);
    const qMax = Math.max(...qValues);
    const qRange = qMax - qMin || 1;  // avoid div-by-zero for single-child nodes

    const ALPHA = 1.0;
    const BETA = 0.3;
    const logits = nodes.map((n, i) => {
      // Q_norm ∈ [0,1]: reward quality relative to sibling alternatives
      // visits bonus: unexplored nodes get slight uplift (controlled exploration)
      const qNorm = (n.Q - qMin) / qRange;
      return ALPHA * qNorm + BETA * Math.log(n.visits + 1);
    });
    const maxLogit = Math.max(...logits);

    const weights = logits.map(v => Math.exp((v - maxLogit) / temperature));
    const total = weights.reduce((a, b) => a + b, 0);
    const probs = weights.map(w => w / total);

    let r = Math.random() * (1 - 1e-9);
    for (let i = 0; i < nodes.length; i++) {
      r -= probs[i];
      if (r <= 0) return nodes[i];
    }
    return nodes[nodes.length - 1];
  }

  // 用 softmax 采样选择最优 action（非 greedy）
  // v21 fix: Dirichlet noise at root — breaks root-level soft attractor.
  // AlphaZero-style: P'(a) = (1-ε)*P(a) + ε*Dir(α), ε=0.25, α=0.7.
  // Before softmax, blend logits with uniform Dirichlet sample.
  function dirichletSample(k: number, alpha: number): number[] {
    // k-arm Dirichlet with concentration alpha
    // gamma(sample_i, alpha) / sum_j gamma(sample_j, alpha) — simplified to uniform-like noise
    // For k=2-3, we use: noise_i = -log(-log(random)) variance ~ 1.0
    const u = Array.from({ length: k }, () => -Math.log(-Math.log(Math.random() + 1e-9) + 1e-9));
    const sum = u.reduce((a, b) => a + b, 0);
    return u.map(v => v / sum);
  }

  const EPS_DIR = controlParams?.J_noise ?? 0.25;
  const ALPHA_DIR = 0.7;

  function softmaxSampleWithDirichlet(nodes: MctsNode[], epsilon: number, alpha: number, temperature: number = 1.0): MctsNode {
    if (nodes.length === 0) return null as any;
    if (nodes.length === 1) return nodes[0];

    const qValues = nodes.map(n => n.Q);
    const qMin = Math.min(...qValues);
    const qMax = Math.max(...qValues);
    const qRange = qMax - qMin || 1;

    const logits = nodes.map((n) => {
      const qNorm = (n.Q - qMin) / qRange;
      return 1.0 * qNorm + 0.3 * Math.log(n.visits + 1);
    });

    // v21: Dirichlet noise injection at root
    const noise = dirichletSample(nodes.length, alpha);
    const noisyLogits = logits.map((l, i) => l + epsilon * noise[i] * 5.0);

    const maxLogit = Math.max(...noisyLogits);
    const weights = noisyLogits.map(v => Math.exp((v - maxLogit) / temperature));
    const total = weights.reduce((a, b) => a + b, 0);
    const probs = weights.map(w => w / total);

    let r = Math.random() * (1 - 1e-9);
    for (let i = 0; i < nodes.length; i++) {
      r -= probs[i];
      if (r <= 0) return nodes[i];
    }
    return nodes[nodes.length - 1];
  }

  const bestRootChild = softmaxSampleWithDirichlet(root.children, EPS_DIR, ALPHA_DIR, controlParams?.Pi_temp ?? 1.0);

  // v20 FIX: argmax(reward) replaces softmax(Q) — Q is noisy, use real reward for selection
  if (root.children.length === 0) {
    const fallback = fallbackGreedyPlan(shots, emotions, fps, energyCurve);
    if (onComplete) onComplete({ rootChildren: [], reward: { energy_alignment: 0, entropy: 0, pacing_smoothness: 0, micro_cut_semantic: 0, energy_transition_alignment: 0 }, control: controlParams ?? {} });
    return fallback;
  }

  // Evaluate ALL root children with pure rule reward, pick the best
  // Model score is computed for logging only — NOT used in argmax (avoids distribution shift)
  // Collect root child stats for feedback + move to function scope
  const childRewards: Array<{ child: MctsNode; plan: TransitionPlan; score: number; modelScore: number }> = [];
  for (let ci = 0; ci < root.children.length; ci++) {
    const child = root.children[ci];
    const childPlan = backtrackPlan(child, shots);
    postProcessPlan(childPlan, shots, emotions, energyCurve, fps);
    const { features: childFeatures, score: childRuleScore } = computeRewardFeatures(childPlan, energyCurve, shots, emotions, fps);
    // Build context for model logging (matching dataset_loader._build_features)
    const shot_count = shots.length;
    const duration_frames = shots.reduce((s, sh) => s + sh.duration, 0);
    const emotionHist = new Array(10).fill(0);
    for (const e of emotions) { const bucket = Math.min(9, Math.floor(e * 10)); emotionHist[bucket]++; }
    const energyHist = new Array(10).fill(0);
    for (let si = 0; si < shots.length; si++) { const bucket = Math.min(9, Math.floor((emotions[si] ?? 0.5) * 10)); energyHist[bucket]++; }
    const ctx = { shot_count, fps, duration_frames, emotion_histogram: emotionHist, energy_histogram: energyHist };
    const x = buildFeatureVector(childFeatures as RewardFeatures, ctx);
    const rModel = predictInline(x);
    // E_bias: reward shaping — scales the rule score before argmax selection
    const scoredRule = (controlParams?.E_bias ?? 1.0) * childRuleScore;
    childRewards.push({ child, plan: childPlan, score: scoredRule, modelScore: rModel });
  }

  if (childRewards.length === 0) {
    const fallback = fallbackGreedyPlan(shots, emotions, fps, energyCurve);
    if (onComplete) onComplete({ rootChildren: [], reward: { energy_alignment: 0, entropy: 0, pacing_smoothness: 0, micro_cut_semantic: 0, energy_transition_alignment: 0 }, control: controlParams ?? {} });
    return fallback;
  }

  // argmax(reward) — NOT softmax(Q)
  let bestChild = childRewards[0].child;
  let bestPlan = childRewards[0].plan;
  let bestScore = childRewards[0].score;
  for (const cr of childRewards) {
    if (cr.score > bestScore) {
      bestScore = cr.score;
      bestChild = cr.child;
      bestPlan = cr.plan;
    }
  }

  const { features: selectedFeatures, score: selectedScore } = computeRewardFeatures(
    bestPlan, energyCurve, shots, emotions, fps
  );

  // ── E → RenderParams: base values ───────────────────────────────────────────────
  let baseMotion: number;
  let baseTransitionStyle: "hard" | "smooth" | "glitch";
  let baseCutDensity: number;

  if (controlParams?.stylePreset) {
    const rp = styleToRenderParams(controlParams.stylePreset);
    baseMotion = rp.motionIntensity;
    baseTransitionStyle = rp.transitionStyle;
    baseCutDensity = rp.cutDensity;
  } else {
    const f = selectedFeatures as unknown as Record<string, number>;
    baseMotion = f.energy_alignment ?? 0.5;
    baseTransitionStyle =
      (f.pacing_smoothness ?? 0.5) > 0.7 ? "smooth" :
      (f.pacing_smoothness ?? 0.5) < 0.3 ? "glitch" : "hard";
    baseCutDensity = f.micro_cut_semantic ?? 0.5;
  }

  // ── TemporalHighlightPlanner: per-shot highlight placement ────────────────────
  // Rhythm zones: 0-15% intro, 15-35% buildup, 35-55% peak1, 55-65% mid, 65-80% peak2, 80-100% outro.
  // Highlights placed at emotional peaks within peak zones.
  planHighlightPositions(bestPlan, shots, emotions);

  // ── VisualStyleEngine: per-shot nonlinear coupling ───────────────────────────
  // Applies E-breakdown coupling on top of highlight positions.
  const f = selectedFeatures as unknown as Record<string, number>;

  for (const [_shotIdx, decision] of bestPlan) {
    const isHL = (decision as TransitionDecision).isHighlight ?? false;
    let mi = baseMotion;
    let ts = baseTransitionStyle;
    let cd = baseCutDensity;

    if (isHL) {
      // Highlight burst: nonlinear boost beyond linear E sum
      mi = Math.min(1.5, baseMotion * 1.5);
      cd = Math.min(1.5, baseCutDensity * 1.8);
      ts = "glitch";
    } else {
      // Subtle coupling: high energy × high pacing = smooth cinematic (reduce motion)
      mi = baseMotion * (1 + (f.energy_alignment ?? 0) * 0.4 - (f.pacing_smoothness ?? 0.5) * 0.2);
      if ((f.energy_alignment ?? 0) > 0.6 && (f.pacing_smoothness ?? 0.5) < 0.4) {
        cd = Math.min(1.2, baseCutDensity * 1.3);
      }
    }

    mi = Math.max(0.1, Math.min(1.5, mi));
    cd = Math.max(0.1, Math.min(1.5, cd));

    (decision as TransitionDecision).renderParams = {
      motionIntensity: mi,
      transitionStyle: ts,
      cutDensity: cd,
    };
  }

  // Emit stats before returning
  if (onComplete) {
    onComplete({
      rootChildren: childRewards.map(cr => ({
        type: cr.child.type as TransitionType,
        score: cr.score,
        visits: cr.child.visits,
        modelScore: cr.modelScore,
      })),
      reward: {
        energy_alignment: (selectedFeatures as any).energy_alignment ?? 0,
        entropy: (selectedFeatures as any).entropy ?? 0,
        pacing_smoothness: (selectedFeatures as any).pacing_smoothness ?? 0,
        micro_cut_semantic: (selectedFeatures as any).micro_cut_semantic ?? 0,
        energy_transition_alignment: (selectedFeatures as any).energy_transition_alignment ?? 0,
      },
      control: controlParams ?? {},
    });
  }

  const alternatives = [];
  for (const cr of childRewards) {
    if (cr.child === bestChild) continue;
    alternatives.push({
      plan: Array.from(cr.plan.entries()).map(([shot, dec]) => ({
        shot,
        type: dec.type,
        microCutAt: dec.microCutAt,
        microCutIntensity: dec.microCutIntensity,
      })),
      reward: cr.score,
      features: (() => {
        const f = computeRewardFeatures(cr.plan, energyCurve, shots, emotions, fps);
        return f.features;
      })(),
      qValue: cr.child.Q,
      visits: cr.child.visits,
    });
  }

  const nonBest = childRewards.filter(cr => cr.child !== bestChild);
  if (nonBest.length > 0) {
    const sampled = softmaxSample(nonBest.map(cr => cr.child), 1.5);
    if (sampled) {
      const sampledCR = nonBest.find(cr => cr.child === sampled);
      if (sampledCR) {
        alternatives.push({
          plan: Array.from(sampledCR.plan.entries()).map(([shot, dec]) => ({
            shot,
            type: dec.type,
            microCutAt: dec.microCutAt,
            microCutIntensity: dec.microCutIntensity,
          })),
          reward: sampledCR.score,
          features: (() => {
            const f = computeRewardFeatures(sampledCR.plan, energyCurve, shots, emotions, fps);
            return f.features;
          })(),
          qValue: sampled.Q,
          visits: sampled.visits,
        });
      }
    }
  }

// ── v19.6b: Add hard negative plans（结构对比样本）───────────────────
  // These are intentionally extreme plans that differ structurally from selected.
  // They provide REAL ranking signal unlike MCTS siblings which are nearly identical.
  const hardNegatives = [
    { name: "all-zoom", plan: genAllZoomPlan(shots) },
    { name: "all-fade", plan: genAllFadePlan(shots) },
    { name: "anti-energy", plan: genAntiEnergyPlan(shots, emotions, energyCurve, fps) },
    { name: "alternating", plan: genAlternatingPlan(shots) },
  ];
  for (const hn of hardNegatives) {
    postProcessPlan(hn.plan, shots, emotions, energyCurve, fps);
    const hnResult = computeRewardFeatures(hn.plan, energyCurve, shots, emotions, fps);
    alternatives.push({
      name: hn.name,  // "all-zoom", "all-fade", "anti-energy", "alternating"
      plan: Array.from(hn.plan.entries()).map(([shot, dec]) => ({
        shot,
        type: dec.type,
        microCutAt: dec.microCutAt,
        microCutIntensity: dec.microCutIntensity,
      })),
      reward: hnResult.score,
      features: hnResult.features,
      qValue: 0,  // synthetic plan has no MCTS Q
      visits: 0,
    });
  }

  rewardCollector.collect(
    bestPlan, selectedScore, selectedFeatures,
    alternatives, shots, emotions, fps
  );
  rewardCollector.setMctsStats(root);

  return bestPlan;
}

/**
 * v19.6b: Reward-Aligned Stochastic Rollout
 *
 * CRITICAL: rollout policy MUST align with reward function semantics.
 *
 * Old mismatch (caused corr=-0.95):
 *   rollout threshold: energy >= 0.75 → whip
 *   reward threshold:  energy >= 0.65 → whip +1.0 / fade -0.5
 *   → rollout wasted [0.65, 0.75) on zoom while reward wanted whip
 *
 * New strategy: energy threshold matches reward (0.65).
 *   energy >= 0.65: whip is rewarded → pick it reliably
 *   energy <  0.65: whip is penalized, fade has small positive → prefer fade
 *   Small noise still added to avoid deterministic collapse.
 **/
function stochasticRollout(
  startNode: MctsNode,
  shots: Shot[],
  emotions: number[],
  energyCurve: Array<{ frame: number; energy: number }>,
  fps: number
): number {
  const plan = new Map(startNode.plan);
  let state = { ...startNode.state };
  let consecutiveWhip = startNode.consecutiveWhip;

  for (let i = startNode.shotIndex + 1; i < shots.length - 1; i++) {
    const emotion = emotions[i] ?? 0.5;
    const energy = energyCurve.find(
      (p) => p.frame >= shots[i].start && p.frame < shots[i].start + shots[i].duration
    )?.energy ?? emotion;

    const beat = Math.sin((shots[i].start / fps) * 0.05);

    // ── v19.6b: Reward-aligned stochastic action selection ────────
    // Matches evaluateFullSequence energy threshold (0.65).
    //   energy >= 0.65: whip → reward +1.0, fade → -0.5  → pick whip
    //   energy <  0.65: whip → -0.5,  fade → +0.2, zoom → 0  → pick fade
    const rand = Math.random();
    const isHighEnergy = energy >= 0.65;  // matches reward threshold

    let chosenType: TransitionType;
    if (isHighEnergy) {
      // reward-aligned: whip is best in high energy
      chosenType = rand < 0.85 ? "whip" : (rand < 0.95 ? "fade" : "zoom");
    } else {
      // reward-aligned: whip is bad, fade is best (small positive)
      chosenType = rand < 0.70 ? "fade" : (rand < 0.90 ? "zoom" : "whip");
    }

    const { type: legalType, newState, newConsecutiveWhip } = decideTransition(
      chosenType, state, emotion, beat, consecutiveWhip
    );

    plan.set(i, { shotIndex: i, type: legalType });
    state = newState;
    consecutiveWhip = newConsecutiveWhip;
  }

  return evaluateFullSequence(plan, energyCurve, shots, emotions, fps);
}

/**
 * v17: Backpropagation（更新 visit count + Q-value）
 *
 * MCTS 的核心：通过 backpropagation 累积统计量
 * 使得高频访问节点的 Q 值趋于稳定
 *
 * Q-value 更新公式（增量平均）：
 *   Q_new = Q_old + (R - Q_old) / visits
 *   其中 R = evaluateFullSequence(full_plan)
 **/
function backpropagate(node: MctsNode | null, reward: number): void {
  while (node !== null) {
    node.visits++;
    node.Q = node.Q + (reward - node.Q) / node.visits;
    // v17.1: 同时更新全局 Q-Table（跨节点共享）
    updateAbstractQ(node.abstractionKey, reward);
    node = node.parent;
  }
}

/**
 * v17: UCT Fallback Greedy（当 MCTS 未能展开时退保）
 **/
function fallbackGreedyPlan(
  shots: Shot[],
  emotions: number[],
  fps: number,
  energyCurve: Array<{ frame: number; energy: number }>
): TransitionPlan {
  const plan = new Map<number, TransitionDecision>();
  let state = { budget: MAX_BUDGET, cooldown: 0, lastTransition: "zoom" as TransitionType };
  let consecutiveWhip = 0;

  for (let i = 0; i < shots.length - 1; i++) {
    const emotion = emotions[i] ?? 0.5;
    const beat = Math.sin((shots[i].start / fps) * 0.05);

    let chosenType: TransitionType = "zoom";
    const { type: legalType, newState, newConsecutiveWhip } = decideTransition(
      chosenType, state, emotion, beat, consecutiveWhip
    );

    plan.set(i, { shotIndex: i, type: legalType });
    state = newState;
    consecutiveWhip = newConsecutiveWhip;
  }

  return plan;
}

// ── v19.6b: Hard Negative Generators（对比样本构造）────────────────
// These create structurally extreme plans that provide real ranking signal.
// Unlike MCTS siblings (which are very similar), these vary by composition.

function findShotEnergy(shotIdx: number, shots: Shot[], energyCurve: Array<{ frame: number; energy: number }>, emotions: number[], fps: number): number {
  const shot = shots[shotIdx];
  if (!shot) return 0.5;
  const peakFrame = findEmotionPeakFrame(shot, energyCurve, 0.6, fps);
  return energyCurve.find(p => Math.abs(p.frame - peakFrame) < fps * 0.5)?.energy ?? emotions[shotIdx] ?? 0.5;
}

/** 1. All-zoom plan: minimum transition diversity baseline */
function genAllZoomPlan(shots: Shot[]): TransitionPlan {
  const plan = new Map<number, TransitionDecision>();
  for (let i = 0; i < shots.length - 1; i++) {
    plan.set(i, { shotIndex: i, type: "zoom" });
  }
  return plan;
}

/** 2. All-fade plan: another monotonic baseline */
function genAllFadePlan(shots: Shot[]): TransitionPlan {
  const plan = new Map<number, TransitionDecision>();
  for (let i = 0; i < shots.length - 1; i++) {
    plan.set(i, { shotIndex: i, type: "fade" });
  }
  return plan;
}

/** 3. Anti-energy plan: whip at LOW energy, fade at HIGH energy — intentionally wrong alignment */
function genAntiEnergyPlan(
  shots: Shot[],
  emotions: number[],
  energyCurve: Array<{ frame: number; energy: number }>,
  fps: number
): TransitionPlan {
  const plan = new Map<number, TransitionDecision>();
  for (let i = 0; i < shots.length - 1; i++) {
    const e = findShotEnergy(i, shots, energyCurve, emotions, fps);
    // Intentional anti-alignment: whip when low energy, fade when high
    plan.set(i, { shotIndex: i, type: e < 0.5 ? "fade" : "whip" });
  }
  return plan;
}

/** 4. Alternating plan: whip→zoom→fade→whip cycling — maximum type variation */
function genAlternatingPlan(shots: Shot[]): TransitionPlan {
  const CYCLE: TransitionType[] = ["whip", "zoom", "fade"];
  const plan = new Map<number, TransitionDecision>();
  for (let i = 0; i < shots.length - 1; i++) {
    plan.set(i, { shotIndex: i, type: CYCLE[i % 3] });
  }
  return plan;
}

/**
 * v14: 一次性规划整条视频的 transition 决策（整合版）
 *
 * @param shots - 镜头序列
 * @param emotions - 每个 shot 对应的情绪强度（0~1），长度应与 shots 一致
 * @param fps - 帧率
 *
 * v14 升级：
 *   - microCut 由能量峰值语义锚定（不再用固定 0.60）
 *   - 构建完成后调用 enforceWhipDensityConstraint（全局 whip 密度约束）
 *   - 返回 plan 附带全局评分（用于调试和未来优化）
 */

/**
 * v15: 统一入口（代理到 Beam Search 实现）
 *
 * buildTransitionPlan 保持 API 不变，内部替换为 beamSearchTransitionPlan
 * 使 v13 的 useTransitionPlan 无需改动
 */
function buildTransitionPlan(
  shots: Shot[],
  emotions: number[],
  fps: number
): TransitionPlan {
  // v15: 全局最优搜索（beam search）
  return beamSearchTransitionPlan(shots, emotions, fps);
}

/**
 * v13: 将 buildTransitionPlan 接入 React 渲染管线
 * 依赖 shots + emotions（都加了 useMemo），结果稳定不变
 */
function useTransitionPlan(
  shots: VideoLayout["shots"],
  emotions: number[],
  fps: number
): TransitionPlan {
  const plan = useMemo(
    () => buildTransitionPlan(shots ?? [], emotions, fps),
    [shots, emotions.join(","), fps]
  );
  return plan;
}

// ============================================================
// 动画计算 Hook
// ============================================================

function useElementAnimation(start: number, duration: number, animation?: VideoElement["animation"]) {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return getLayerAnimationStyle({
    frame,
    fps,
    start,
    duration,
    animation,
  });
}

// ============================================================
// 逐词高亮组件（className 固定字符串，零 React style diff）
// ============================================================

/**
 * WordHighlightedText — className 驱动高亮
 *
 * 性能策略：
 *   - isActive 最多每秒变几次 → className 字符串稳定
 *   - React diff className = 字符串比较（O(1)）
 *   - 无 style 对象创建，无 CSS 对象 diff
 *   - span 在 isActive 不变时完全跳过更新（React.memo）
 */
const WordHighlightedText: React.FC<{
  wordCues: Array<{ index: number; word: string; start: number; end: number }>;
  activeIndex: number;
  color: string;
  fontWeight?: number;
}> = React.memo(({ wordCues, activeIndex, color, fontWeight }) => {
  const fontWeightVal = fontWeight ?? 600;

  const spans = useMemo(() => {
    return wordCues.map((wc) => {
      const isActive = wc.index === activeIndex;
      // 两个 className 都是稳定字符串引用
      return (
        <span
          key={wc.index}
          className={isActive ? "word-active" : "word-inactive"}
          style={{
            color: isActive ? "#FFD700" : color,
            textShadow: isActive ? "0 0 12px #FFD700, 0 2px 8px rgba(0,0,0,0.9)" : "0 2px 12px rgba(0,0,0,0.8)",
            transition: "color 0.08s ease, text-shadow 0.08s ease",
            fontWeight: isActive ? 900 : fontWeightVal,
          }}
        >
          {wc.word}
        </span>
      );
    });
  }, [wordCues, activeIndex, color, fontWeightVal]);

  return <span style={{ fontFamily: FONT_FAMILY }}>{spans}</span>;
});
WordHighlightedText.displayName = "WordHighlightedText";

const TextLayer: React.FC<{ element: TextElement; frame: number }> = ({ element, frame }) => {
  const { opacity, transform, filter, isVisible } = useElementAnimation(element.start, element.duration, element.animation);
  const { fps } = useVideoConfig();
  if (!isVisible) return null;
  const t = frame / fps;

  // ── 逐词高亮渲染 ─────────────────────────────────────────
  // 只在 wordCues 存在时才启用词级渲染
  if (element.wordCues && element.wordCues.length > 0) {
    // 找当前帧对应的词（区间相交判断）
    const activeWord = element.wordCues.find(
      (w) => t >= w.start && t <= w.end
    );
    if (activeWord) {
      // 渲染整句，active 词高亮
      return (
        <div
          style={{
            position: "absolute",
            left: element.x,
            top: element.y,
            fontFamily: FONT_FAMILY,
            fontSize: element.fontSize,
            fontWeight: element.fontWeight ?? 600,
            color: element.color,
            textAlign: (element.textAlign as "center") ?? "center",
            lineHeight: element.lineHeight ?? 1.3,
            maxWidth: element.maxWidth,
            opacity,
            transform,
            filter,
            textShadow: `0 2px 12px rgba(0,0,0,0.8)`,
            zIndex: element.zIndex,
          }}
        >
          {/* 整句渲染，当前词高亮 */}
          <WordHighlightedText wordCues={element.wordCues} activeIndex={activeWord.index} color={element.color} fontWeight={element.fontWeight} />
        </div>
      );
    }
  }

  // fallback：普通整句渲染
  return (
    <div
      style={{
        position: "absolute",
        left: element.x,
        top: element.y,
        fontFamily: FONT_FAMILY,
        fontSize: element.fontSize,
        fontWeight: element.fontWeight ?? 600,
        color: element.color,
        textAlign: (element.textAlign as "center") ?? "center",
        lineHeight: element.lineHeight ?? 1.3,
        maxWidth: element.maxWidth,
        opacity,
        transform,
        filter,
        textShadow: `0 2px 12px rgba(0,0,0,0.8)`,
        zIndex: element.zIndex,
      }}
    >
      {element.text}
    </div>
  );
};

const ImageLayerEl: React.FC<{ element: ImageElement }> = ({ element }) => {
  const { opacity, transform, filter, isVisible } = useElementAnimation(element.start, element.duration, element.animation);
  if (!isVisible) return null;

  return (
    <div
      style={{
        position: "absolute",
        left: element.x,
        top: element.y,
        width: element.width,
        height: element.height,
        borderRadius: element.borderRadius ?? 12,
        overflow: "hidden",
        opacity,
        transform,
        filter,
        boxShadow: `0 8px 32px rgba(0,0,0,0.5)`,
        zIndex: element.zIndex,
      }}
    >
      <Img
        src={element.src}
        style={{
          width: "100%",
          height: "100%",
          objectFit: (element.objectFit as "cover") ?? "cover",
        }}
      />
    </div>
  );
};

const StickerLayer: React.FC<{ element: StickerElement }> = ({ element }) => {
  const { opacity, transform, filter, isVisible } = useElementAnimation(element.start, element.duration, element.animation);
  if (!isVisible) return null;

  return (
    <div
      style={{
        position: "absolute",
        left: element.x,
        top: element.y,
        fontSize: element.size,
        opacity,
        transform,
        filter: `${filter === "none" ? "" : `${filter} `}drop-shadow(0 4px 12px rgba(0,0,0,0.4))`.trim(),
        zIndex: element.zIndex,
      }}
    >
      {element.emoji}
    </div>
  );
};

const ShapeLayer: React.FC<{ element: ShapeElement }> = ({ element }) => {
  const { opacity, transform, filter, isVisible } = useElementAnimation(element.start, element.duration, element.animation);
  if (!isVisible) return null;

  const baseStyle = {
    position: "absolute" as const,
    left: element.x,
    top: element.y,
    width: element.shape === "line" ? element.width : element.width,
    height: element.shape === "line" ? 2 : element.height,
    backgroundColor: element.shape === "line" ? element.color : (element.fillColor ?? "transparent"),
    border: element.shape !== "line" ? `2px solid ${element.color}` : undefined,
    borderRadius: element.shape === "circle"
      ? "50%"
      : element.borderRadius ?? 8,
    opacity,
    filter,
    transform: element.rotation ? `${transform} rotate(${element.rotation}deg)` : transform,
    zIndex: element.zIndex,
  };

  return <div style={baseStyle} />;
};

const BackgroundLayer: React.FC<{ element: BackgroundElement; frame: number }> = ({ element, frame }) => {
  const { opacity, transform, filter, isVisible } = useElementAnimation(element.start, element.duration, element.animation);
  if (!isVisible) return null;

  // 背景轻微呼吸
  const bgShift = Math.sin(frame * 0.008) * 10;

  return (
    <div
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        background: element.gradient ?? element.color ?? "#0A0E14",
        opacity,
        transform,
        filter,
        backgroundSize: "200% 200%",
        zIndex: element.zIndex,
      }}
    />
  );
};

// ============================================================
// 导演状态 Hook
// ============================================================

/**
 * 每帧从导演意图查询当前状态
 *
 * 全局唯一的动态状态来源：
 * evaluateDirector(director, t, duration) 每帧返回：
 * - emotion:      情绪强度（0~1）
 * - pacing:       节奏速度倍率
 * - visualFocus:  视觉聚焦程度
 * - audioIntensity: 音频强度
 * - scene:        当前 scene 类型
 *
 * 如果 layout.director 不存在（兼容旧代码），返回零值
 */
function useDirectorState(layout: VideoLayout): DirectorState | null {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const t = frame / fps;
  const duration = durationInFrames / fps;

  if (!layout.director || layout.director.scenes.length === 0) return null;
  return evaluateDirector(layout.director, t, duration);
}

// ============================================================
// 镜头系统（v10：shot 驱动相机）
// ============================================================

/**
 * 当前帧对应的镜头（shot）
 * shots 是一个时间轴序列，按 start 排序
 * 找不到时返回 undefined（layout.shots 不存在时也走这里）
 */
/**
 * v10.3: 镜头切换连续性（Temporal Continuity）
 *
 * 返回当前 shot + 下一 shot + 切换进度
 * 当 frame 进入 shot 末尾 TRANSITION_FRAMES 时：
 *   current 渐出（cross-zoom + direction exit）
 *   next    渐入（direction enter）
 *
 * 效果：从"硬切" → "像同一个镜头延续"
 */
const TRANSITION_FRAMES = 8;

/**
 * v13: 执行层 — 查规划 + 驱动 CSS transform
 *
 * 相比 v12 的根本区别：
 *   v12: 每帧 reactive 决策（module state → 跨视频污染风险）
 *   v13: 查预建规划（pure lookup → 无状态，无污染）
 *
 * microCut: shot 内部 micro-cut（v13 新增）
 *   在 shot 的 microCutAt 时刻注入微冲击
 *   效果：镜头内部也有剪辑感（不只是 shot boundary）
 */
function useShotsAroundFrame(
  shots: VideoLayout["shots"],
  cameraOverride: string,
  plan: TransitionPlan,
  emotions: number[]
): {
  current: Shot | null;
  next: Shot | null;
  currentTransform: string;
  nextTransform: string;
  nextShotTransform: string;
  nextEmotionTransform: string;
  currentOpacity: number;
  nextOpacity: number;
  isTransitioning: boolean;
} {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  if (!shots || shots.length === 0) {
    return { current: null, next: null, currentTransform: "", nextTransform: "", nextShotTransform: "", nextEmotionTransform: "", currentOpacity: 1, nextOpacity: 0, isTransitioning: false as boolean };
  }

  const idx = shots.findIndex((s) => frame >= s.start && frame < s.start + s.duration);
  const fallbackIdx = frame < shots[0].start ? 0 : shots.length - 1;
  const resolvedIdx = idx >= 0 ? idx : fallbackIdx;
  const current = shots[resolvedIdx] ?? null;
  const next = resolvedIdx + 1 < shots.length ? shots[resolvedIdx + 1] : null;

  if (!current) {
    return { current: null, next: null, currentTransform: "", nextTransform: "", nextShotTransform: "", nextEmotionTransform: "", currentOpacity: 1, nextOpacity: 0, isTransitioning: false };
  }

  const shotEnd = current.start + current.duration;
  const inWindow = !!(frame >= shotEnd - TRANSITION_FRAMES && frame < shotEnd && next);
  {
  const transitionFrame = inWindow ? frame - (shotEnd - TRANSITION_FRAMES) : 0;
  const transitionProgress = inWindow
    ? getLinearTransitionProgress({
        frame: transitionFrame,
        durationInFrames: TRANSITION_FRAMES,
      })
    : 0;
  const isTransitioning = !!(inWindow && next);

  // Keep the planning system intact, but drive visuals from timeline JSON.
  void plan;
  void emotions;

  const presentation = resolveTransitionPresentation(current, next);
  const currentPresentationStyle = isTransitioning
    ? getPresentationStyle({
        kind: presentation.kind,
        direction: presentation.direction,
        presentationDirection: "exiting",
        presentationProgress: transitionProgress,
      })
    : {};
  const nextPresentationStyle = isTransitioning
    ? getPresentationStyle({
        kind: presentation.kind,
        direction: presentation.direction,
        presentationDirection: "entering",
        presentationProgress: transitionProgress,
      })
    : {};

  const nextProgress = 0;
  const {
    shotTransform: nextShotTransform,
    emotionTransform: nextEmotionTransform,
  } = next
    ? getShotTransform(next, nextProgress, cameraOverride, frame)
    : { shotTransform: "", emotionTransform: "" };

  return {
    current,
    next,
    currentTransform:
      typeof currentPresentationStyle.transform === "string"
        ? currentPresentationStyle.transform
        : "",
    nextTransform:
      typeof nextPresentationStyle.transform === "string"
        ? nextPresentationStyle.transform
        : "",
    nextShotTransform,
    nextEmotionTransform,
    currentOpacity:
      typeof currentPresentationStyle.opacity === "number"
        ? currentPresentationStyle.opacity
        : 1,
    nextOpacity: !isTransitioning
      ? 0
      : typeof nextPresentationStyle.opacity === "number"
        ? nextPresentationStyle.opacity
        : 1,
    isTransitioning,
  };
  }
  const t = inWindow ? (frame - (shotEnd - TRANSITION_FRAMES)) / TRANSITION_FRAMES : 0;
  const isTransitioning = !!(inWindow && t >= 0 && t <= 1);

  // ── v13: 从预建规划中查 TransitionDecision ───────────────────
  const decision = plan.get(resolvedIdx);
  const transitionType: TransitionType = decision?.type ?? "zoom";

  // ── v13: Shot 内部 micro-cut ────────────────────────────────
  // 在 microCutAt 时刻注入 scale spike（制造 shot 内部剪辑感）
  const progressInShot = current.duration > 0 ? (frame - current.start) / current.duration : 0;
  const microCutAt = decision?.microCutAt ?? 0.60;
  const microCutIntensity = decision?.microCutIntensity ?? 0.08;
  const nearMicroCut = Math.abs(progressInShot - microCutAt) < 0.025;

  // ── E → RenderParams: E breakdown controls visual rendering ─────────────
  const rp = decision?.renderParams;
  const cutDensity = rp?.cutDensity ?? 0.5;
  const motionIntensity = rp?.motionIntensity ?? 0.5;
  const ts = rp?.transitionStyle ?? "hard";
  const isHighlight = (decision as TransitionDecision)?.isHighlight ?? false;

  // microCutScale — highlight gets extra micro-cut burst
  const microCutBase = nearMicroCut ? 1 + microCutIntensity * (0.5 + cutDensity) : 1;
  const microCutScale = isHighlight ? microCutBase * 1.4 : microCutBase;

  // transitionStyle drives easing curve
  const ease = ts === "smooth" ? Easing.inOut(Easing.quad) :
               ts === "glitch" ? Easing.step0 : Easing.out(Easing.quad);

  // ── v10.5: Impact frame — 中间帧微冲击（制造"剪辑点"节奏感）──
  // Highlight: stronger impact spike
  const isImpact = transitionType === "zoom" && isTransitioning && Math.abs(t - 0.5) < 0.12;
  const impactScale = isImpact ? (isHighlight ? 1.15 : 1.08) : 1;

  // ── v10.4: Direction-aware pan continuity ──────────────────
  // current exit direction
  let exitTranslate = 0;
  const currentCamera = current.camera ?? "static";
  const nextCamera = next?.camera ?? "static";

  if (currentCamera === "pan-left") {
    exitTranslate = -t * 120;
  } else if (currentCamera === "pan-right") {
    exitTranslate = t * 120;
  }
  // next enter direction（与 exit 相反，形成视觉连续）
  let enterTranslate = 0;
  if (next) {
    if (nextCamera === "pan-left") {
      enterTranslate = (1 - t) * 120;
    } else if (nextCamera === "pan-right") {
      enterTranslate = -(1 - t) * 120;
    }
  }

  // ── v11: transition type 决定具体数值 ─────────────────────
  let currentTransform = "";
  let nextTransform = "";
  let currentOpacity = 1;
  let nextOpacity = 0;

  if (transitionType === "whip") {
    // whip pan：横向甩切（t=0→1，current快速右甩出，next从右滑入）
    // motionIntensity scales displacement magnitude
    const mi = 0.5 + motionIntensity;
    const whipCurrent = isTransitioning ? interpolate(t, [0, 1], [0, 800 * mi], { easing: ease }) : 0;
    const whipNext = isTransitioning ? interpolate(t, [0, 1], [200 * mi, 0], { easing: ease }) : 0;
    currentTransform = `translateX(${whipCurrent}px) scale(${microCutScale})`;
    nextTransform = `translateX(${whipNext}px)`;
    // whip: 透明度在最后一段才切（不是全程淡）
    const whipCutoff = interpolate(t, [0, 1], [0, 1], { easing: Easing.linear });
    currentOpacity = isTransitioning ? Math.max(0, 1 - whipCutoff * 1.8) : 1;
    nextOpacity = isTransitioning ? Math.min(1, (whipCutoff - 0.3) * 1.5) : 0;
    // 微噪声 — scales with motionIntensity
    const noise = Math.sin(frame * 13.7) * 0.012 * mi;
    currentOpacity = Math.max(0, Math.min(1, currentOpacity + noise));
    nextOpacity = Math.max(0, Math.min(1, nextOpacity + Math.sin(frame * 11.3 + 1.5) * 0.012 * mi));
  } else if (transitionType === "fade") {
    // fade：纯 opacity 渐变，无 scale（适合慢内容）
    currentTransform = `translateX(${exitTranslate * 0.3}px) scale(${microCutScale})`;
    nextTransform = `translateX(${-enterTranslate * 0.3}px)`;
    const fadeT = isTransitioning ? interpolate(t, [0, 1], [1, 0], { easing: ease }) : 1;
    const fadeNext = isTransitioning ? interpolate(t, [0, 1], [0, 1], { easing: ease }) : 0;
    const noise = Math.sin(frame * 7.3) * 0.008;
    currentOpacity = Math.max(0, Math.min(1, fadeT + noise));
    nextOpacity = Math.max(0, Math.min(1, fadeNext + Math.sin(frame * 5.9 + 1.5) * 0.008));
  } else {
    // zoom（默认）：cross-zoom + impact frame + pan continuity
    // motionIntensity scales zoom magnitude
    const mi = 0.5 + motionIntensity;
    const exitZoom = isTransitioning ? interpolate(t, [0, 1], [1, 1 + 0.2 * mi], { easing: ease }) : 1;
    const exitFade = isTransitioning ? interpolate(t, [0, 1], [1, 0], { easing: Easing.linear }) : 1;
    const enterZoom = isTransitioning ? interpolate(t, [0, 1], [1 + 0.15 * mi, 1], { easing: ease }) : 1;
    const enterFade = isTransitioning ? interpolate(t, [0, 1], [0, 1], { easing: Easing.linear }) : 0;
    // v13 microCutScale: 镜头内部微冲击（叠加在 exitZoom 之上）
    currentTransform = `translateX(${exitTranslate}px) scale(${exitZoom * impactScale * microCutScale})`;
    nextTransform = `translateX(${-enterTranslate}px) scale(${enterZoom})`;
    const noise = Math.sin(frame * 13.7) * 0.015 * mi;
    const nextNoise = Math.sin(frame * 11.3 + 1.5) * 0.015 * mi;
    currentOpacity = Math.max(0, Math.min(1, exitFade + noise));
    nextOpacity = Math.max(0, Math.min(1, enterFade + nextNoise));
  }

  // ── v10.4: next 也跑完整 camera pipeline（camera continuity）──
  // nextProgress = 0 表示"进入的第一帧应该是什么姿态"
  const nextProgress = 0;
  const {
    shotTransform: nextShotTransform,
    emotionTransform: nextEmotionTransform,
  } = next
    ? getShotTransform(next, nextProgress, cameraOverride, frame)
    : { shotTransform: "", emotionTransform: "" };

  return {
    current,
    next,
    currentTransform,
    nextTransform,
    nextShotTransform,
    nextEmotionTransform,
    currentOpacity,
    nextOpacity,
    isTransitioning,
  };
}

/**
 * 计算 shot 的 CSS transform（crop + camera motion）
 * cropX/cropY/cropW/cropH 是 0~1 相对值
 * camera 类型决定是否有额外的 scale/translate 动画
 */
function getShotTransform(
  shot: Shot,
  progress: number,
  cameraOverride: string,
  frame: number
): { shotTransform: string; emotionTransform: string } {
  void progress;
  return getCameraShotTransform({
    shot,
    frame,
    fps: 30,
    width: 1080,
    height: 1920,
    cameraOverride,
  });
  const { cropX = 0, cropY = 0, cropW = 1, cropH = 1, camera } = shot;
  const W = 1080, H = 1920;

  // v10.2: progress clamp（防止边界帧越界导致 easing 抖动）
  const clamped = Math.max(0, Math.min(1, progress));

  // v10.2 bonus: emotion-aware easing（情绪决定运动曲线）
  const easingFn = cameraOverride === "shake"
    ? Easing.linear
    : cameraOverride === "pulse"
    ? Easing.inOut(Easing.quad)
    : Easing.inOut(Easing.cubic);

  const eased = interpolate(clamped, [0, 1], [0, 1], {
    easing: easingFn,
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // camera 起始/结束 crop（用于 interpolate 连续运动）
  // push-in: scale 1.0 → 1.2（放大 = 推近）
  // pan-left: cropX 0 → 0.15（裁剪右移 = 向左看）
  // pan-right: cropX 0.15 → 0（裁剪左移 = 向右看）
  // pull-out: scale 1.2 → 1.0（缩小 = 拉远）
  // static: 无变化
  let startCropW = 1, endCropW = 1;
  let startCropX = 0, endCropX = 0;
  let startScale = 1, endScale = 1;
  let startCropH = 1, endCropH = 1;

  if (camera === "push-in") {
    startScale = 1.0; endScale = 1.2;
    startCropH = 1; endCropH = 0.88;
  } else if (camera === "pan-left") {
    startCropX = 0; endCropX = 0.15; startCropW = 1; endCropW = 0.88;
  } else if (camera === "pan-right") {
    startCropX = 0.12; endCropX = 0; startCropW = 0.88; endCropW = 1;
  } else if (camera === "pull-out") {
    startScale = 1.15; endScale = 1.0;
    startCropH = 0.88; endCropH = 1;
  }

  // 按 easing 曲线插值（连续运动，非跳变）
  const curCropW = startCropW + (endCropW - startCropW) * eased;
  const curCropH = startCropH + (endCropH - startCropH) * eased;
  const curCropX = startCropX + (endCropX - startCropX) * eased;
  const curScale = startScale + (endScale - startScale) * eased;

  // crop → scale 变换（X/Y 同步，防止比例不自然）
  const scaleX = curScale / curCropW;
  const scaleY = curScale / curCropH; // v10.2 修复：X/Y 同步用变量 curCropH（之前错用固定 cropH）
  const baseTranslateX = -(curCropX * W) * scaleX;
  const translateY = -(cropY * H) * scaleY;

  // v10.2: 多频 drift（消除完全规则运动的假感，不同频率叠加 → 非周期感）
  const drift = camera !== "static"
    ? Math.sin(frame * 0.021) * 4 + Math.sin(frame * 0.013 + 1.7) * 2
    : 0;
  const translateX = baseTranslateX + drift;

  const shotTransform = `translate(${translateX}px, ${translateY}px) scale(${scaleX}, ${scaleY})`;

  // emotion camera 叠加（shake / pulse / slow-zoom），不是覆盖
  let emotionTransform = "";
  if (cameraOverride === "shake") {
    const jitter = 8;
    emotionTransform = `translate(${Math.sin(frame * 3.1) * jitter}px, ${Math.cos(frame * 2.7) * jitter}px)`;
  } else if (cameraOverride === "pulse") {
    const pulse = 1 + Math.sin(frame * 0.05) * 0.02;
    emotionTransform = `scale(${pulse})`;
  } else if (cameraOverride === "slow-zoom") {
    const slow = 1 + Math.sin(frame * 0.015) * 0.01;
    emotionTransform = `scale(${slow})`;
  }

  return { shotTransform, emotionTransform };
}

// ============================================================
// 场景渲染
// ============================================================

// Handle Remotion merging: inputProps.video may be set by Remotion's prop merge
// We support both direct layout.elements and layout.video.elements
type VideoLayoutInput = VideoLayout | { video?: VideoLayout };
function getElements(layout: VideoLayoutInput): VideoElement[] {
  if (Array.isArray((layout as VideoLayout).elements)) {
    return (layout as VideoLayout).elements;
  }
  if ((layout as { video?: VideoLayout }).video) {
    return (layout as { video: VideoLayout }).video.elements;
  }
  return [];
}

export const VideoScene: React.FC<{ layout: VideoLayout }> = ({ layout }) => {
  const frame = useCurrentFrame();
  const { width, height, background } = layout;
  const elements = getElements(layout);

  // 按 zIndex 排序
  const sortedElements = [...elements].sort((a, b) => (a.zIndex ?? 0) - (b.zIndex ?? 0));

  // 全局淡入
  const fadeIn = interpolate(frame, [0, 20], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // ========== 导演驱动的镜头系统 ==========
  // 由 useDirectorState 每帧从 evaluateDirector() 实时查询
  const directorState = useDirectorState(layout);
  const totalDurationInFrames = getLayoutDurationInFrames(layout);

  // ── v13: 为每个 shot 计算代表情绪（用于 transition 规划）──
  // emotion 在 shot 中点采样，得到 per-shot 的情绪序列
  const { fps, durationInFrames } = useVideoConfig();
  const emotions = useMemo(() => {
    if (!layout.shots || !layout.director || layout.director.scenes.length === 0) return [];
    return layout.shots.map((shot) => {
      const midT = (shot.start + shot.duration / 2) / fps;
      const duration = durationInFrames / fps;
      const state = evaluateDirector(layout.director!, midT, duration);
      return state?.emotion ?? 0.5;
    });
  }, [layout.shots, layout.director, fps, durationInFrames]);

  // ── v13: Transition Planner（整条视频一次性规划）─────────────
  const transitionPlan = useTransitionPlan(layout.shots ?? [], emotions, fps);

  // ── 全局 zoom（开场冲击 → 情绪驱动）──
  // emotionEffect.zoomBase 由情绪层决定（intense=1.05, calm=1.0）
  const introZoom = directorState
    ? directorState.emotionEffect.zoomBase + directorState.emotion * 0.2
    : interpolate(frame, [0, 20, 40, 70], [1.2, 1.05, 1.08, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });

  // ── 情绪推进（随视频推进微微 zoom in）──
  const camPush = directorState
    ? 1 + directorState.pacing * 0.08
    : interpolate(frame, [0, 300], [1, 1.08], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });

  // ── 呼吸脉动（情绪强度控制幅度）──
  // emotionEffect.breatheIntensity：intense=0.8，calm=0.3
  const breatheIntensity = directorState?.emotionEffect.breatheIntensity ?? 0.4;
  const breathe = 1 + Math.sin(frame * 0.025) * 0.006 * breatheIntensity;

  // ── 情绪相机（cameraOverride 驱动）──
  // shake = intense，slow-zoom = calm/static，pulse = dramatic
  const cameraOverride = directorState?.emotionEffect.cameraOverride ?? "static";
  const isShake = cameraOverride === "shake";
  const isPulse = cameraOverride === "pulse";
  const shakeAmt = isShake && directorState ? directorState.emotion * 10 : 0;
  const pulseAmt = isPulse && directorState ? Math.sin(frame * 0.05) * 0.02 : 0;
  const shakeX = shakeAmt * Math.cos(frame * 3.1);
  const shakeY = shakeAmt * Math.sin(frame * 2.7);

  // ── 导演强调行为 → 视觉动画映射──
  // state.emphasisPointWord（词索引驱动，精确到词）优先于 state.emphasis（时间区间）
  const ep = directorState?.emphasisPointWord ?? directorState?.emphasis;
  const emphasisZoom = ep
    ? ep.action === "zoom-in" ? 1.15
    : ep.action === "subtitle-pulse" ? 1.1
    : ep.action === "flash" ? 1.05
    : 1.0
    : 1.0;
  const emphasisBreathe =
    ep?.action === "slow-down" ? 0.4
    : ep?.action === "pause" ? 0.0
    : 1.0;

  // ── 合成最终镜头变换──
  const cameraTransform =
    `scale(${introZoom * camPush * breathe * emphasisZoom + pulseAmt}) ` +
    `translate(${shakeX}px, ${shakeY}px)`;

  // 背景色
  const bgColor = background ?? "#0A0E14";

  // ── 情绪色调覆盖（emotion overlay layer）──
  // colorOverlay 叠加在背景色上，intense=红，calm=蓝
  const emotionColorOverlay = directorState?.emotionEffect.colorOverlay ?? "rgba(0,0,0,0)";

  return (
    <AbsoluteFill
      style={{
        backgroundColor: bgColor,
        width,
        height,
        overflow: "hidden",
        opacity: fadeIn,
        fontFamily: FONT_FAMILY,
        transform: cameraTransform,
        transformOrigin: "center center",
      }}
    >
      {/* 情绪色调覆盖层（intense=红晕，calm=蓝调） */}
      {(layout.audioTracks ?? []).map((track) => (
        <Sequence key={track.id} from={track.start} durationInFrames={track.duration}>
          <Audio src={track.src} />
        </Sequence>
      ))}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: emotionColorOverlay,
          zIndex: 0,
          pointerEvents: "none",
        }}
      />
      {/* v10.3: Shot 渲染层（镜头系统 + 切换连续性） */}
      {/* 渲染在 elements 下方（zIndex=-1），当前+下一 shot 同时渲染，transition 区间渐变 */}
      {(() => {
        const { current, next, currentTransform, nextTransform, nextShotTransform, nextEmotionTransform, currentOpacity, nextOpacity, isTransitioning } =
          useShotsAroundFrame(layout.shots, cameraOverride, transitionPlan, emotions);
        if (!current) return null;

        // 当前 shot
        const safeDuration = Math.max(current.duration, 1);
        const progress = Math.min(Math.max((frame - current.start) / safeDuration, 0), 1);
        const { shotTransform, emotionTransform } = getShotTransform(current, progress, cameraOverride, frame);

        return (
          <>
            {/* 当前 shot（出画：pan延续 + cross-zoom 放大 + 淡出） */}
            <div
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                height: "100%",
                overflow: "hidden",
                zIndex: -2,
                opacity: currentOpacity,
              }}
            >
              <ShotDensityStack
                shot={current}
                frame={frame}
                opacity={1}
                width={width}
                height={height}
                zIndex={0}
                transform={`${shotTransform} ${emotionTransform} ${currentTransform}`.trim()}
              />
            </div>
            {/* 下一 shot（入画：pan延续 + cross-zoom 缩小 + 淡入 + 完整camera pipeline） */}
            {isTransitioning && next && (
              <div
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  height: "100%",
                  overflow: "hidden",
                  zIndex: -1,
                  opacity: nextOpacity,
                }}
              >
                <ShotDensityStack
                  shot={next}
                  frame={frame}
                  opacity={1}
                  width={width}
                  height={height}
                  zIndex={0}
                  transform={`${nextShotTransform} ${nextEmotionTransform} ${nextTransform}`.trim()}
                />
              </div>
            )}
          </>
        );
      })()}
      {/* 渲染所有元素（排除进度条，由下面单独处理） */}
      {sortedElements
        .filter((el) => el.id !== "progress-bar-bg" && el.id !== "progress-bar")
        .map((el) => {
          switch (el.type) {
            case "text":
              return <TextLayer key={el.id} element={el} frame={frame} />;
            case "image":
              return <ImageLayerEl key={el.id} element={el} />;
            case "sticker":
              return <StickerLayer key={el.id} element={el} />;
            case "shape":
              return <ShapeLayer key={el.id} element={el} />;
            case "background":
              return <BackgroundLayer key={el.id} element={el} frame={frame} />;
            default:
              return null;
          }
        })}

      {/* 进度条（特殊处理：动态宽度） */}
      {(() => {
        const progress = Math.min(frame / totalDurationInFrames, 1);
        const barW = width * progress;
        return (
          <>
            {/* 进度条背景 */}
            <div
              style={{
                position: "absolute",
                left: 0,
                top: height - 8,
                width: width,
                height: 8,
                backgroundColor: "rgba(255,255,255,0.1)",
                zIndex: 998,
              }}
            />
            {/* 进度条前景 */}
            <div
              style={{
                position: "absolute",
                left: 0,
                top: height - 8,
                width: barW,
                height: 8,
                backgroundColor: "#FF6B6B",
                zIndex: 999,
                boxShadow: "0 0 10px #FF6B6B80",
              }}
            />
          </>
        );
      })()}
    </AbsoluteFill>
  );
};
