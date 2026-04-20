/**
 * director.ts — 可计算的导演系统（Computable Director）
 *
 * 核心概念：
 *   每个视频是一个「时间轴函数系」
 *   f(t) → emotion, pacing, visualFocus, audioIntensity
 *
 * 所有其他模块都是「执行器」：
 *   Remotion 执行 visualFocus(t)
 *   TTS 执行 audioIntensity(t)
 *   Layout 执行 pacing(t)
 *
 * 唯一的决策中枢 = Director Intent
 */

import type { VideoScript } from "./generator";

// ============================================================
// 导演意图类型定义
// ============================================================

export type NarrativeArc = "hook-first" | "problem-solution" | "story" | "viral";
export type VisualStyle = "cinematic" | "bold" | "minimalist" | "tech" | "warm";
export type Pacing = "fast" | "medium" | "slow";
export type TTSVoice = "male_deep" | "female_energetic" | "female_calm" | "neutral";
export type SceneType = "hook" | "explain" | "cta";

/**
 * 单个镜头/场景
 *
 * 每个 scene 有自己的：
 * - 时间区间 [start, end]（秒）
 * - 局部情绪曲线（相对于 scene 内的时间）
 * - 局部节奏曲线
 * - 视觉风格
 *
 * 这样每个 scene 可以有独立的"情绪重置"：
 *   hook: 0.9 冲击
 *   explain: 0.3 冷静
 *   cta: 1.0 爆发
 */
export interface Scene {
  start: number;
  end: number;
  type: SceneType;
  /** 局部情绪曲线（scene 内均分） */
  emotionalCurve: number[];
  /** 局部节奏曲线（scene 内均分） */
  pacingCurve: number[];
  visualStyle: VisualStyle;
}

export interface EmphasisPoint {
  /** 时间区间（秒），构建期用；VTT 解析后被 wordIndex 替代 */
  at: [number, number];
  /** 强调类型 */
  type: "visual" | "audio" | "both";
  /** 具体动作 */
  action: "zoom-in" | "flash" | "pause" | "slow-down" | "subtitle-pulse" | "voice-up";
}

/** VTT 解析后，词/短语级绑定的强调点（运行时消费） */
export interface EmphasisPointWord {
  /** 强调的词序号区间（phrase 级，支持多词） */
  wordIndices: number[];
  type: "visual" | "audio" | "both";
  action: "zoom-in" | "flash" | "pause" | "slow-down" | "subtitle-pulse" | "voice-up";
}

export interface DirectorIntent {
  /** 叙事弧线类型 */
  arc: NarrativeArc;
  /** 镜头序列（Scene-aware 编排层） */
  scenes: Scene[];
  /** 全局情绪曲线（用于 scene 间插值） */
  emotionalCurve: number[];
  /** 全局节奏曲线 */
  pacingCurve: number[];
  /** TTS 音色 */
  ttsVoice: TTSVoice;
  /** TTS 语速倍率 */
  ttsSpeed: number;
  /** 视觉强调点 */
  emphasisPoints: EmphasisPoint[];
  /** 镜头运动策略 */
  cameraStrategy: "zoom-in-out" | "pan" | "static" | "shake";
  /** 颜色主题覆盖（Override script's colorScheme） */
  colorOverride?: {
    primary: string;
    fill: string;
    text: string;
  };
  /** word-level 字幕（由 agentOrchestrator 从 VTT 解析后注入，VideoScene 用于逐词高亮） */
  subtitleCues: SubtitleCue[];
  /** 运行时缓存：所有词展平（避免每帧 flatten） */
  allWords: WordCue[];
  /** VTT 解析后的词级强调点（运行时消费，绑定到具体 wordIndex） */
  emphasisPointsWord: EmphasisPointWord[];
}

/** 单个词级字幕 */
export interface WordCue {
  /** 全局词序号（在完整视频所有词中的顺序） */
  index: number;
  word: string;
  start: number;   // 秒（毫秒精度）
  end: number;     // 秒
}

/** 一句话 = 多个 WordCue */
export interface SubtitleCue {
  id: string;
  start: number;
  end: number;
  words: WordCue[];
}

// ============================================================
// 工具函数
// ============================================================

/** 从 topic 推断叙事弧线 */
function inferArc(topic: string): NarrativeArc {
  const t = topic.toLowerCase();
  if (/科普|原理|为什么|解释/.test(t)) return "problem-solution";
  if (/赚钱|暴利|方法|技巧|揭秘/.test(t)) return "viral";
  if (/故事|经历|发生|回忆/.test(t)) return "story";
  return "hook-first";
}

/** 从 arc 推断视觉风格 */
function inferVisualStyle(arc: NarrativeArc): VisualStyle {
  switch (arc) {
    case "problem-solution": return "cinematic";
    case "viral": return "bold";
    case "story": return "warm";
    default: return "cinematic";
  }
}

/** 从 arc 推断 TTS 音色 */
function inferTTSVoice(arc: NarrativeArc): TTSVoice {
  switch (arc) {
    case "viral": return "female_energetic";
    case "problem-solution": return "male_deep";
    case "story": return "female_calm";
    default: return "neutral";
  }
}

/** 从 arc 推断语速 */
function inferTTSSpeed(arc: NarrativeArc, pacing: Pacing): number {
  const base = pacing === "fast" ? 1.15 : pacing === "slow" ? 0.9 : 1.0;
  if (arc === "viral") return base * 1.1;
  if (arc === "problem-solution") return base * 0.95;
  return base;
}

/** 生成情绪曲线 */
function buildEmotionalCurve(arc: NarrativeArc, steps: VideoScript["steps"]): number[] {
  // 分成 len(steps) + 2 个段落：hook + steps + cta
  const total = steps.length + 2;
  const curve: number[] = [];

  for (let i = 0; i < total; i++) {
    const progress = i / (total - 1);

    switch (arc) {
      case "hook-first":
        // 开场高 → 中间稳 → CTA 爆发
        if (i === 0) curve.push(0.9);        // hook 冲击
        else if (i === total - 1) curve.push(0.85); // CTA 爆发
        else curve.push(0.4 + progress * 0.2);
        break;

      case "viral":
        // 全程高能量，最后更爆
        curve.push(0.7 + (1 - Math.abs(progress - 0.6) * 1.5) * 0.25);
        break;

      case "problem-solution":
        // 问题沉重 → 解答释然 → CTA 平静
        if (i <= 1) curve.push(0.3 + i * 0.1);
        else if (i === total - 1) curve.push(0.7);
        else curve.push(0.5);
        break;

      case "story":
        // 悬念 → 升温 → 高潮 → 收尾
        if (i === 0) curve.push(0.3);
        else if (i === total - 1) curve.push(0.6);
        else curve.push(0.4 + Math.sin(progress * Math.PI) * 0.4);
        break;
    }
  }
  return curve;
}

/** 生成节奏曲线（相对速度） */
function buildPacingCurve(arc: NarrativeArc, steps: VideoScript["steps"]): number[] {
  const total = steps.length + 2;
  const curve: number[] = [];

  for (let i = 0; i < total; i++) {
    const progress = i / (total - 1);

    if (i === 0) {
      // hook 始终快（冲击）
      curve.push(arc === "viral" ? 1.3 : 1.15);
    } else if (i === total - 1) {
      // CTA 快（推动行动）
      curve.push(arc === "viral" ? 1.2 : 1.0);
    } else {
      // 中间内容：科普慢，viral 快
      if (arc === "problem-solution") curve.push(0.8);
      else if (arc === "viral") curve.push(1.1);
      else curve.push(0.95);
    }
  }
  return curve;
}

/** 从 script 内容构建视觉强调点 */
function buildEmphasisPoints(script: VideoScript, arc: NarrativeArc): EmphasisPoint[] {
  const points: EmphasisPoint[] = [];
  const stepDuration = 3; // 每个 step 约 3 秒
  const hookDur = 3;
  const ctaDur = 2;

  // Hook flash
  points.push({
    at: [0, hookDur],
    type: "visual",
    action: "flash",
  });

  // Step 强调点（每个 step 开头）
  script.steps.forEach((_, i) => {
    const start = hookDur + i * stepDuration;
    points.push({
      at: [start, start + 0.8],
      type: "visual",
      action: "zoom-in",
    });
  });

  // CTA pulse
  const ctaStart = hookDur + script.steps.length * stepDuration;
  points.push({
    at: [ctaStart, ctaStart + ctaDur],
    type: "both",
    action: arc === "viral" ? "voice-up" : "subtitle-pulse",
  });

  return points;
}

// ============================================================
// 主入口：buildDirector
// ============================================================

/**
 * 从 topic + script 构建导演意图
 *
 * 这是系统中唯一的「决策点」：
 * - 后面所有模块都只执行 director 的输出
 * - 不再有"模块自己判断该怎么画/怎么说"的自治逻辑
 *
 * @param topic   原始主题
 * @param script  已生成的脚本（hook + steps + cta）
 */
export function buildDirector(topic: string, script: VideoScript, subtitleCues?: SubtitleCue[]): DirectorIntent {
  const arc = inferArc(topic);
  const pacing: Pacing = script.steps.length > 4 ? "slow" : script.steps.length > 3 ? "medium" : "fast";
  const visualStyle = inferVisualStyle(arc);
  const ttsVoice = inferTTSVoice(arc);
  const ttsSpeed = inferTTSSpeed(arc, pacing);

  // ── Scene-aware 编排层 ──────────────────────────────────────
  // 把视频切成 [hook, explain×N, cta] 三段，每段有独立情绪/节奏
  const scenes = buildScenes(arc, script, ttsSpeed);

  return {
    arc,
    scenes,
    emotionalCurve: buildEmotionalCurve(arc, script.steps),
    pacingCurve: buildPacingCurve(arc, script.steps),
    ttsVoice,
    ttsSpeed,
    emphasisPoints: buildEmphasisPoints(script, arc),
    cameraStrategy: arc === "viral" ? "shake" : "zoom-in-out",
    colorOverride: arc === "viral"
      ? { primary: "#FFD700", fill: "rgba(255,215,0,0.15)", text: "#FFFFFF" }
      : undefined,
    subtitleCues: subtitleCues ?? [],
    allWords: [],
    emphasisPointsWord: [],
  };
}

/**
 * 把时间区间的 emphasisPoints 绑定到具体词索引（语义驱动核心）
 *
 * 输入：emphasisPoints（时间区间）+ subtitleCues（词级时间戳）
 * 输出：EmphasisPointWord[]（绑定到 wordIndices[]，phrase 级）
 *
 * 收集所有与 emphasis 区间重叠的词（而非只选一个）：
 *   - phrase-level 强调支持（"三天赚钱" = 3个词同时高亮）
 *   - 避免单字词强调时语义不完整
 */
export function bindEmphasisToWords(
  emphasisPoints: EmphasisPoint[],
  subtitleCues: SubtitleCue[]
): EmphasisPointWord[] {
  const result: EmphasisPointWord[] = [];

  // 展平所有词
  const allWords: WordCue[] = buildAllWords(subtitleCues);

  for (const ep of emphasisPoints) {
    // 重叠量计算
    const overlap = (wStart: number, wEnd: number) =>
      Math.max(0, Math.min(ep.at[1], wEnd) - Math.max(ep.at[0], wStart));

    // 收集所有重叠 > 0 的词（phrase 级）
    const indices: number[] = [];
    for (const w of allWords) {
      if (overlap(w.start, w.end) > 0) {
        indices.push(w.index);
      }
    }

    if (indices.length > 0) {
      result.push({ wordIndices: indices, type: ep.type, action: ep.action });
    }
  }

  return result;
}

/**
 * 把 subtitleCues 展平为 allWords（一次性，不是每帧）
 * 按 start 排序（保证二分查找前提）
 */
export function buildAllWords(subtitleCues: SubtitleCue[]): WordCue[] {
  const out: WordCue[] = [];
  for (const cue of subtitleCues) {
    for (const w of cue.words) {
      out.push(w);
    }
  }
  // 按 start 严格升序（二分查找的前提保证）
  out.sort((a, b) => a.start - b.start);
  return out;
}

/**
 * 构建 Scene[] 序列
 *
 * 关键设计：
 * - 每个 scene 有独立的情绪/节奏曲线（局部时间）
 * - scene 之间情绪可以"重置"（hook高 → explain低 → cta爆发）
 * - local_t = (t - scene.start) / scene.duration → 在 scene 内插值
 */
function buildScenes(arc: NarrativeArc, script: VideoScript, ttsSpeed: number): Scene[] {
  const scenes: Scene[] = [];
  const stepDur = 5;    // 每个 step 的标准时长（秒）
  const hookDur = 3;
  const ctaDur = 3;

  // ── Hook ────────────────────────────────────────────────────
  const hookCurve = buildSceneCurve(arc, "hook");
  const hookPacing = arc === "viral" ? 1.3 : 1.15;
  scenes.push({
    start: 0,
    end: hookDur,
    type: "hook",
    emotionalCurve: hookCurve,
    pacingCurve: [hookPacing],
    visualStyle: arc === "viral" ? "bold" : "cinematic",
  });

  // ── Steps / Explain ─────────────────────────────────────────
  script.steps.forEach((step, i) => {
    const prevEnd = scenes[scenes.length - 1].end;
    const dur = stepDur;
    // 根据位置决定情绪：越靠前越高，越靠后越要推向 CTA
    const posInSteps = i / Math.max(script.steps.length - 1, 1);
    const explainCurve = buildExplainCurve(arc, posInSteps);
    scenes.push({
      start: prevEnd,
      end: prevEnd + dur,
      type: "explain",
      emotionalCurve: explainCurve,
      pacingCurve: [arc === "problem-solution" ? 0.85 : 1.0],
      visualStyle: inferVisualStyle(arc),
    });
  });

  // ── CTA ─────────────────────────────────────────────────────
  const lastEnd = scenes[scenes.length - 1].end;
  const ctaCurve = buildSceneCurve(arc, "cta");
  scenes.push({
    start: lastEnd,
    end: lastEnd + ctaDur,
    type: "cta",
    emotionalCurve: ctaCurve,
    pacingCurve: [arc === "viral" ? 1.2 : 1.0],
    visualStyle: arc === "viral" ? "bold" : "cinematic",
  });

  return scenes;
}

/** 构建指定 scene 类型的情绪曲线（局部） */
function buildSceneCurve(arc: NarrativeArc, type: "hook" | "cta"): number[] {
  if (type === "hook") {
    if (arc === "viral") return [0.9, 0.85];
    if (arc === "problem-solution") return [0.4, 0.5];
    return [0.85, 0.75];
  }
  // cta
  if (arc === "viral") return [0.85, 1.0, 0.9];
  if (arc === "problem-solution") return [0.6, 0.75, 0.7];
  return [0.7, 0.85, 0.8];
}

/** 构建 explain 场景的局部情绪曲线（随位置渐变） */
function buildExplainCurve(arc: NarrativeArc, positionInSteps: number): number[] {
  if (arc === "viral") return [0.75, 0.8, 0.7];
  if (arc === "problem-solution") return [0.4, 0.5, 0.55];
  if (arc === "story") {
    const rise = 0.4 + positionInSteps * 0.35;
    return [rise - 0.1, rise, rise - 0.05];
  }
  return [0.5, 0.55, 0.5];
}

/**
 * 插值 emotionalCurve 中的一个值
 * 把离散的 [0.2, 0.5, 0.9] 曲线变成连续函数
 */
function lerpCurve(curve: number[], progress: number): number {
  const len = curve.length;
  if (len === 0) return 0.5;
  if (len === 1) return curve[0];

  const scaled = progress * (len - 1);
  const lo = Math.floor(scaled);
  const hi = Math.min(lo + 1, len - 1);
  const t = scaled - lo;
  return curve[lo] * (1 - t) + curve[hi] * t;
}

/**
 * 找到 t 所在的 Scene
 * 如果 t 超出所有 scene 范围，返回最后一个 scene
 */
function findScene(scenes: Scene[], t: number): Scene {
  const found = scenes.find(s => t >= s.start && t <= s.end);
  return found ?? scenes[scenes.length - 1];
}

/**
 * 在任意时刻 t 查询导演意图状态
 *
 * 这是「Scene-aware 函数导演」的核心：
 *   t → findScene(t) → local_t → lerpCurve(scene.emotionalCurve, local_t)
 *
 * 每帧 render 时调用，整条视频不再是一条全局曲线，
 * 而是多个 scene 的局部曲线拼接而成。
 *
 * @param director  导演意图（静态构建）
 * @param t         当前视频时间（秒）
 * @param duration  视频总时长（秒）
 */
export function evaluateDirector(
  director: DirectorIntent,
  t: number,
  duration: number
): {
  emotion: number;
  pacing: number;
  visualFocus: number;
  audioIntensity: number;
  emphasis: EmphasisPoint | null;
  scene: Scene;
} {
  // ── Step 1: 找到当前 scene ────────────────────────────────
  const scene = findScene(director.scenes, t);

  // ── Step 2: 计算 scene 内的局部时间 ──────────────────────
  const sceneDuration = scene.end - scene.start;
  const local_t = sceneDuration > 0
    ? Math.min((t - scene.start) / sceneDuration, 1)
    : 0;

  // ── Step 3: 在 scene 局部曲线上插值 ─────────────────────
  const emotion = lerpCurve(scene.emotionalCurve, local_t);
  const pacing = lerpCurve(scene.pacingCurve, local_t);

  // ── Step 4: 派生状态 ────────────────────────────────────
  const cameraBase = director.cameraStrategy === "shake" ? 1.1
    : director.cameraStrategy === "zoom-in-out" ? 1.0
    : 0.95;
  const visualFocus = Math.min(1.0, emotion * 1.2 * cameraBase);
  const audioIntensity = Math.min(1.0, emotion * director.ttsSpeed * 0.9 + 0.1);

  // 当前时刻是否有强调点
  const emphasis = director.emphasisPoints.find(
    (ep: EmphasisPoint) => t >= ep.at[0] && t <= ep.at[1]
  ) ?? null;

  return { emotion, pacing, visualFocus, audioIntensity, emphasis, scene };
}

/** 兼容旧名称 */
export const queryDirector = evaluateDirector;
