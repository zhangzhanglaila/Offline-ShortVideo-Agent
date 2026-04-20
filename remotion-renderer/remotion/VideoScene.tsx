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
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate, Easing, Img, spring } from "remotion";
import { FONT_FAMILY } from "./constants";
import type { VideoLayout, VideoElement, TextElement, ImageElement, StickerElement, BackgroundElement, ShapeElement, Shot } from "./types";
import { evaluateDirector, type DirectorState } from "./directorEval";

// ============================================================
// v13: Transition Planner（核心新模块）
// ============================================================

export type TransitionType = "whip" | "fade" | "zoom";

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
  state: EditorState;
  parent: MctsNode | null;
  children: MctsNode[];
  visits: number;
  Q: number;
  consecutiveWhip: number;
  type: TransitionType | null;
  plan: TransitionPlan;
}

const BEAM_WIDTH = 4;
const TRANSITION_TYPES: TransitionType[] = ["whip", "fade", "zoom"];

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

  // Energy Alignment
  const ENERGY_THRESHOLD = 0.65;
  let energyHits = 0;
  let energyMisses = 0;
  plan.forEach((dec) => {
    if (dec.type === "whip") {
      const shot = shots[dec.shotIndex];
      if (!shot) return;
      const peakFrame = findEmotionPeakFrame(shot, energyCurve, 0.6, fps);
      const peakEnergy = energyCurve.find(
        (p) => Math.abs(p.frame - peakFrame) < fps * 0.5
      )?.energy ?? emotions[dec.shotIndex] ?? 0.5;
      if (peakEnergy >= ENERGY_THRESHOLD) energyHits++;
      else energyMisses++;
    }
  });
  const totalWhips = energyHits + energyMisses;
  const energyAlignmentScore = totalWhips > 0 ? energyHits / totalWhips : 1;

  // Rhythm Entropy
  const typeCount = { whip: 0, fade: 0, zoom: 0 };
  plan.forEach((dec) => { typeCount[dec.type]++; });
  const total = plan.size;
  const probs = [typeCount.whip / total, typeCount.fade / total, typeCount.zoom / total];
  const entropy = probs.reduce((h, p) => p > 0 ? h - p * Math.log(p) : h, 0);
  const maxEntropy = Math.log(3);
  const entropyScore = maxEntropy > 0 ? entropy / maxEntropy : 0;

  // Pacing Smoothness
  const whipCounts: number[] = [];
  plan.forEach((dec) => {
    if (dec.type === "whip") {
      const t = shots[dec.shotIndex]?.start ?? 0;
      const windowIdx = Math.floor(t / WINDOW_FRAMES);
      whipCounts[windowIdx] = (whipCounts[windowIdx] ?? 0) + 1;
    }
  });
  let pacingPenalty = 0;
  const PENALTY_PER_EXCESS_WHIP = 0.12;
  for (const count of whipCounts) {
    if (count > 1) pacingPenalty += (count - 1) * PENALTY_PER_EXCESS_WHIP;
  }
  const pacingScore = Math.max(0, 1 - pacingPenalty);

  // Micro-cut Semantic
  let microCutScoreSum = 0;
  let microCutCount = 0;
  plan.forEach((dec) => {
    if (dec.microCutAt !== undefined) {
      const shot = shots[dec.shotIndex];
      if (!shot) return;
      const peakFrame = findEmotionPeakFrame(shot, energyCurve, 0.6, fps);
      const peakFrac = shot.duration > 0 ? (peakFrame - shot.start) / shot.duration : 0.6;
      const dist = Math.abs(dec.microCutAt - peakFrac);
      microCutScoreSum += Math.max(0, 1 - dist * 3);
      microCutCount++;
    }
  });
  const microCutScore = microCutCount > 0 ? microCutScoreSum / microCutCount : 0.5;

  return Math.max(0, Math.min(1,
    energyAlignmentScore * 0.30 +
    entropyScore * 0.25 +
    pacingScore * 0.25 +
    microCutScore * 0.20
  ));
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
function beamSearchTransitionPlan(
  shots: Shot[],
  emotions: number[],
  fps: number
): TransitionPlan {
  const energyCurve = buildGlobalEnergyCurve(shots, emotions, fps);

  // ── Step 1: 构建根节点 ────────────────────────────────────
  // 根节点代表"尚未做任何决策"的初始状态
  const root: MctsNode = {
    shotIndex: -1,
    state: { budget: MAX_BUDGET, cooldown: 0, lastTransition: "zoom" as TransitionType },
    parent: null,
    children: [],
    visits: 0,
    Q: 0,
    consecutiveWhip: 0,
    type: null,
    plan: new Map(),
  };

  // ── Step 2: MCTS-UCT 主循环 ───────────────────────────────
  const SIMULATION_COUNT = 3;
  const EXPLORATION_CONSTANT = 1.4;

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
    if (currentNode.children.length === 0) {
      // ── Expansion：为此 shot 展开所有合法 action ─────────
      const emotion = emotions[currentNode.shotIndex] ?? 0.5;
      const beat = Math.sin((shots[currentNode.shotIndex].start / fps) * 0.05);

      for (const ttype of TRANSITION_TYPES) {
        const { type: legalType, newState, newConsecutiveWhip } = decideTransition(
          ttype, currentNode.state, emotion, beat, currentNode.consecutiveWhip
        );

        const childPlan = new Map(currentNode.plan);
        childPlan.set(currentNode.shotIndex, { shotIndex: currentNode.shotIndex, type: legalType });

        const childNode: MctsNode = {
          shotIndex: currentNode.shotIndex + 1,
          state: newState,
          parent: currentNode,
          children: [],
          visits: 0,
          Q: 0,
          consecutiveWhip: newConsecutiveWhip,
          type: legalType,
          plan: childPlan,
        };

        currentNode.children.push(childNode);
      }
    }

    // ── Simulation：对当前 shot 层所有子节点做 stochastic rollout ──
    for (const childNode of currentNode.children) {
      for (let sim = 0; sim < SIMULATION_COUNT; sim++) {
        const simResult = stochasticRollout(
          childNode, shots, emotions, energyCurve, fps
        );
        backpropagate(childNode, simResult);
      }
    }
  }

  // ── Step 3: 从根节点 children 中选最优 action ───────────────
  let bestRootChild = root.children.reduce((best, node) =>
    node.visits > best.visits ? node : best, root.children[0] ?? null
  );

  if (!bestRootChild) {
    return fallbackGreedyPlan(shots, emotions, fps, energyCurve);
  }

  // 从最优子节点回溯到完整 plan
  const fullPlan = new Map<number, TransitionDecision>();
  let node: MctsNode | null = bestRootChild;
  while (node !== null && node.type !== null) {
    fullPlan.set(node.shotIndex, { shotIndex: node.shotIndex, type: node.type });
    node = node.parent;
  }

  // ── 后处理 1: micro-cut 语义锚定 ─────────────────────────
  for (let i = 0; i < shots.length - 1; i++) {
    if (!fullPlan.has(i)) continue;
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

    const existing = fullPlan.get(i)!;
    fullPlan.set(i, { ...existing, microCutAt, microCutIntensity });
  }

  // ── 后处理 2: Whip 密度约束（硬约束）────────────────────
  enforceWhipDensityConstraint(fullPlan, shots, fps);

  return fullPlan;
}

/**
 * v17: Stochastic Rollout（替代 v16 deterministic zoom fill）
 *
 * v16 rollout: 对所有未覆盖 shot 填 zoom（deterministic）
 * v17 rollout: 基于能量分布的概率采样（stochastic）
 *
 * 策略：
 *   - energy > 0.75 → 高概率选 whip（但非 100%，有随机性）
 *   - energy 0.35~0.75 → 中概率 zoom
 *   - energy < 0.35 → 高概率 fade
 *   - 加随机噪声避免 deterministic collapse
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

    // ── Stochastic action selection（概率驱动，非贪婪）─────────
    const rand = Math.random();

    let chosenType: TransitionType;
    if (energy >= 0.75 && rand < 0.6) {
      chosenType = "whip";
    } else if (energy <= 0.35 && rand < 0.5) {
      chosenType = "fade";
    } else if (rand < 0.7) {
      chosenType = "zoom";
    } else {
      chosenType = TRANSITION_TYPES[Math.floor(Math.random() * TRANSITION_TYPES.length)];
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
  const t = Math.max(0, frame - start);
  const end = duration;
  const animDuration = animation?.duration ?? 15;

  // 入场进度 [0, animDuration]
  const enterProgress = Math.min(t / animDuration, 1);
  // 退场进度 [end-animDuration, end]
  const exitT = Math.max(0, t - (end - animDuration));
  const exitProgress = Math.min(exitT / animDuration, 1);

  // 基础 opacity
  let opacity = 1;
  let transform = "";

  if (t === 0) {
    opacity = 0;
  } else if (t < animDuration) {
    // 入场动画
    opacity = enterProgress;
  } else if (t > end - animDuration) {
    // 退场动画
    opacity = 1 - exitProgress;
  }

  // 入场 transform
  if (t < animDuration) {
    const enterType = animation?.enter ?? "fade";
    switch (enterType) {
      case "slide-up":
        transform = `translateY(${(1 - enterProgress) * 40}px)`;
        break;
      case "slide-down":
        transform = `translateY(${(1 - enterProgress) * -40}px)`;
        break;
      case "zoom-in":
        transform = `scale(${0.5 + enterProgress * 0.5})`;
        break;
      case "zoom-out":
        transform = `scale(${1.5 - enterProgress * 0.5})`;
        break;
      case "bounce-in": {
        const spring = enterProgress < 0.6
          ? 1.2
          : 1.05 - (enterProgress - 0.6) / 0.4 * 0.05;
        transform = `scale(${enterProgress * spring})`;
        break;
      }
      case "blur-in":
        opacity = enterProgress;
        transform = `blur(${(1 - enterProgress) * 8}px)`;
        break;
      default: // fade
        break;
    }
  } else {
    // 持续期间：轻微呼吸
    const breathe = 1 + Math.sin(t * 0.03) * 0.015;
    transform = `scale(${breathe})`;
  }

  // 退场 transform
  if (t > end - animDuration && exitProgress > 0) {
    const exitType = animation?.exit ?? "fade";
    switch (exitType) {
      case "slide-up":
        transform = `translateY(${-exitProgress * 40}px)`;
        break;
      case "slide-down":
        transform = `translateY(${exitProgress * 40}px)`;
        break;
      case "zoom-out":
        transform = `scale(${1 - exitProgress * 0.5})`;
        break;
      case "blur-out":
        transform = `blur(${exitProgress * 8}px)`;
        break;
      default: // fade
        break;
    }
    opacity = 1 - exitProgress;
  }

  return { opacity: Math.max(0, Math.min(1, opacity)), transform, isVisible: t >= 0 && t <= end };
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
  const { opacity, transform, isVisible } = useElementAnimation(element.start, element.duration, element.animation);
  if (!isVisible) return null;

  const { fps } = useVideoConfig();
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
        textShadow: `0 2px 12px rgba(0,0,0,0.8)`,
        zIndex: element.zIndex,
      }}
    >
      {element.text}
    </div>
  );
};

const ImageLayerEl: React.FC<{ element: ImageElement }> = ({ element }) => {
  const { opacity, transform, isVisible } = useElementAnimation(element.start, element.duration, element.animation);
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
  const { opacity, transform, isVisible } = useElementAnimation(element.start, element.duration, element.animation);
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
        filter: `drop-shadow(0 4px 12px rgba(0,0,0,0.4))`,
        zIndex: element.zIndex,
      }}
    >
      {element.emoji}
    </div>
  );
};

const ShapeLayer: React.FC<{ element: ShapeElement }> = ({ element }) => {
  const { opacity, transform, isVisible } = useElementAnimation(element.start, element.duration, element.animation);
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
    transform: element.rotation ? `${transform} rotate(${element.rotation}deg)` : transform,
    zIndex: element.zIndex,
  };

  return <div style={baseStyle} />;
};

const BackgroundLayer: React.FC<{ element: BackgroundElement; frame: number }> = ({ element, frame }) => {
  const { opacity, transform, isVisible } = useElementAnimation(element.start, element.duration, element.animation);
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

  if (!layout.director) return null;
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
  if (!shots || shots.length === 0) {
    return { current: null, next: null, currentTransform: "", nextTransform: "", nextShotTransform: "", nextEmotionTransform: "", currentOpacity: 1, nextOpacity: 0, isTransitioning: false as boolean };
  }

  const idx = shots.findIndex((s) => frame >= s.start && frame < s.start + s.duration);
  const current = idx >= 0 ? shots[idx] : null;
  const next = idx >= 0 && idx + 1 < shots.length ? shots[idx + 1] : null;

  if (!current) {
    return { current: null, next: null, currentTransform: "", nextTransform: "", nextShotTransform: "", nextEmotionTransform: "", currentOpacity: 1, nextOpacity: 0, isTransitioning: false };
  }

  const shotEnd = current.start + current.duration;
  const inWindow = !!(frame >= shotEnd - TRANSITION_FRAMES && frame < shotEnd && next);
  const t = inWindow ? (frame - (shotEnd - TRANSITION_FRAMES)) / TRANSITION_FRAMES : 0;
  const isTransitioning = !!(inWindow && t >= 0 && t <= 1);

  // ── v13: 从预建规划中查 TransitionDecision ───────────────────
  const decision = plan.get(idx);
  const transitionType: TransitionType = decision?.type ?? "zoom";

  // ── v13: Shot 内部 micro-cut ────────────────────────────────
  // 在 microCutAt 时刻注入 scale spike（制造 shot 内部剪辑感）
  const progressInShot = current.duration > 0 ? (frame - current.start) / current.duration : 0;
  const microCutAt = decision?.microCutAt ?? 0.60;
  const microCutIntensity = decision?.microCutIntensity ?? 0.08;
  const nearMicroCut = Math.abs(progressInShot - microCutAt) < 0.025;
  const microCutScale = nearMicroCut ? 1 + microCutIntensity : 1;

  // ── v10.5: Impact frame — 中间帧微冲击（制造"剪辑点"节奏感）──
  // 仅 zoom 类型生效，在 t≈0.5 时产生一个 scale spike
  const isImpact = transitionType === "zoom" && isTransitioning && Math.abs(t - 0.5) < 0.12;
  const impactScale = isImpact ? 1.08 : 1;

  // ── v10.4: Direction-aware pan continuity ──────────────────
  // current exit direction
  let exitTranslate = 0;
  if (current.camera === "pan-left") {
    exitTranslate = -t * 120;
  } else if (current.camera === "pan-right") {
    exitTranslate = t * 120;
  }
  // next enter direction（与 exit 相反，形成视觉连续）
  let enterTranslate = 0;
  if (next) {
    if (next.camera === "pan-left") {
      enterTranslate = (1 - t) * 120;
    } else if (next.camera === "pan-right") {
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
    const whipCurrent = isTransitioning ? interpolate(t, [0, 1], [0, 800], { easing: Easing.out(Easing.quad) }) : 0;
    const whipNext = isTransitioning ? interpolate(t, [0, 1], [200, 0], { easing: Easing.out(Easing.quad) }) : 0;
    currentTransform = `translateX(${whipCurrent}px) scale(${microCutScale})`;
    nextTransform = `translateX(${whipNext}px)`;
    // whip: 透明度在最后一段才切（不是全程淡）
    const whipCutoff = interpolate(t, [0, 1], [0, 1], { easing: Easing.linear });
    currentOpacity = isTransitioning ? Math.max(0, 1 - whipCutoff * 1.8) : 1;
    nextOpacity = isTransitioning ? Math.min(1, (whipCutoff - 0.3) * 1.5) : 0;
    // 微噪声
    const noise = Math.sin(frame * 13.7) * 0.012;
    currentOpacity = Math.max(0, Math.min(1, currentOpacity + noise));
    nextOpacity = Math.max(0, Math.min(1, nextOpacity + Math.sin(frame * 11.3 + 1.5) * 0.012));
  } else if (transitionType === "fade") {
    // fade：纯 opacity 渐变，无 scale（适合慢内容）
    currentTransform = `translateX(${exitTranslate * 0.3}px) scale(${microCutScale})`;
    nextTransform = `translateX(${-enterTranslate * 0.3}px)`;
    const fadeT = isTransitioning ? interpolate(t, [0, 1], [1, 0], { easing: Easing.linear }) : 1;
    const fadeNext = isTransitioning ? interpolate(t, [0, 1], [0, 1], { easing: Easing.linear }) : 0;
    const noise = Math.sin(frame * 7.3) * 0.008;
    currentOpacity = Math.max(0, Math.min(1, fadeT + noise));
    nextOpacity = Math.max(0, Math.min(1, fadeNext + Math.sin(frame * 5.9 + 1.5) * 0.008));
  } else {
    // zoom（默认）：cross-zoom + impact frame + pan continuity
    const exitZoom = isTransitioning ? interpolate(t, [0, 1], [1, 1.2], { easing: Easing.in(Easing.quad) }) : 1;
    const exitFade = isTransitioning ? interpolate(t, [0, 1], [1, 0], { easing: Easing.linear }) : 1;
    const enterZoom = isTransitioning ? interpolate(t, [0, 1], [1.15, 1], { easing: Easing.out(Easing.quad) }) : 1;
    const enterFade = isTransitioning ? interpolate(t, [0, 1], [0, 1], { easing: Easing.linear }) : 0;
    // v13 microCutScale: 镜头内部微冲击（叠加在 exitZoom 之上）
    currentTransform = `translateX(${exitTranslate}px) scale(${exitZoom * impactScale * microCutScale})`;
    nextTransform = `translateX(${-enterTranslate}px) scale(${enterZoom})`;
    const noise = Math.sin(frame * 13.7) * 0.015;
    const nextNoise = Math.sin(frame * 11.3 + 1.5) * 0.015;
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

export const VideoScene: React.FC<{ layout: VideoLayout }> = ({ layout }) => {
  const frame = useCurrentFrame();
  const { width, height, background, elements } = layout;

  // 按 zIndex 排序
  const sortedElements = [...elements].sort((a, b) => (a.zIndex ?? 0) - (b.zIndex ?? 0));

  // 全局淡入
  const fadeIn = interpolate(frame, [0, 20], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

  // ========== 导演驱动的镜头系统 ==========
  // 由 useDirectorState 每帧从 evaluateDirector() 实时查询
  const directorState = useDirectorState(layout);

  // ── v13: 为每个 shot 计算代表情绪（用于 transition 规划）──
  // emotion 在 shot 中点采样，得到 per-shot 的情绪序列
  const { fps, durationInFrames } = useVideoConfig();
  const emotions = useMemo(() => {
    if (!layout.shots || !layout.director) return [];
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
        const progress = Math.min((frame - current.start) / current.duration, 1);
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
              <Img
                src={current.src}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  height: "100%",
                  objectFit: "cover",
                  transform: `${shotTransform} ${emotionTransform} ${currentTransform}`.trim(),
                  transformOrigin: "center center",
                }}
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
                <Img
                  src={next.src}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    height: "100%",
                    objectFit: "cover",
                    transform: `${nextShotTransform} ${nextEmotionTransform} ${nextTransform}`.trim(),
                    transformOrigin: "center center",
                  }}
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
        const totalDuration = sortedElements.reduce(
          (max, el) => Math.max(max, el.start + el.duration),
          300
        );
        const progress = Math.min(frame / totalDuration, 1);
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
