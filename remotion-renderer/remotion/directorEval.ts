/**
 * directorEval.ts — 纯函数式导演状态计算
 *
 * 从 server/director.ts 的 evaluateDirector 逻辑独立出来，
 * 专供 Remotion React 组件使用（Remotion 不能 import server/ 目录）。
 *
 * 只包含纯计算函数，无副作用，无 async。
 */
import type { DirectorIntent, EmphasisPoint, EmphasisPointWord, Scene, EmotionEffect, EmotionLabel } from "./types";

/**
 * 线性插值离散曲线
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
 */
function findScene(scenes: Scene[], t: number): Scene {
  const found = scenes.find(s => t >= s.start && t <= s.end);
  return found ?? scenes[scenes.length - 1];
}

/**
 * 情绪 → 视觉特效映射（emotion overlay layer）
 *
 * emotion 数值（0~1）映射到离散情绪标签，再查表得到视觉特效。
 * 这是"导演层上色"的核心：
 *   emotion 高 → intense（shake + flash）
 *   emotion 低 → calm（slow-zoom + soft）
 *   scene type 辅助判断：hook → intense，explain → calm，cta → dramatic
 */
const EMOTION_THRESHOLDS = { intense: 0.75, dramatic: 0.6, warm: 0.45, calm: 0.3 };

function computeEmotionLabel(emotion: number, sceneType: string): EmotionLabel {
  if (emotion >= EMOTION_THRESHOLDS.intense || sceneType === "hook") return "intense";
  if (emotion >= EMOTION_THRESHOLDS.dramatic || sceneType === "cta") return "dramatic";
  if (emotion >= EMOTION_THRESHOLDS.warm) return "warm";
  if (emotion >= EMOTION_THRESHOLDS.calm || sceneType === "explain") return "calm";
  return "neutral";
}

const emotionMap: Record<EmotionLabel, EmotionEffect> = {
  intense: {
    label: "intense",
    cameraOverride: "shake",
    colorOverlay: "rgba(255, 59, 48, 0.15)",
    breatheIntensity: 0.8,
    zoomBase: 1.05,
  },
  dramatic: {
    label: "dramatic",
    cameraOverride: "pulse",
    colorOverlay: "rgba(255, 149, 0, 0.12)",
    breatheIntensity: 0.5,
    zoomBase: 1.08,
  },
  warm: {
    label: "warm",
    cameraOverride: "slow-zoom",
    colorOverlay: "rgba(255, 138, 0, 0.10)",
    breatheIntensity: 0.4,
    zoomBase: 1.02,
  },
  calm: {
    label: "calm",
    cameraOverride: "slow-zoom",
    colorOverlay: "rgba(74, 144, 226, 0.08)",
    breatheIntensity: 0.3,
    zoomBase: 1.0,
  },
  neutral: {
    label: "neutral",
    cameraOverride: "static",
    colorOverlay: "rgba(255, 255, 255, 0.05)",
    breatheIntensity: 0.2,
    zoomBase: 1.0,
  },
};

export interface DirectorState {
  emotion: number;
  pacing: number;
  visualFocus: number;
  audioIntensity: number;
  /** 当前情绪标签（intense / calm / neutral 等） */
  emotionLabel: EmotionLabel;
  /** 情绪特效（cameraOverride、colorOverlay、breatheIntensity、zoomBase） */
  emotionEffect: EmotionEffect;
  /** 时间区间驱动的强调点（构建期遗留，仍用于 injectPauses） */
  emphasis: EmphasisPoint | null;
  /** 词索引驱动的强调点（运行时语义驱动，绑定到具体词） */
  emphasisPointWord: EmphasisPointWord | null;
  scene: Scene;
}

/**
 * 在任意时刻 t 查询导演意图状态
 *
 * f(t) = evaluateDirector(director, t, duration)
 *
 * 每帧 render 时调用，驱动：
 * - camera scale / shake / breathing
 * - element opacity / blur
 * - audio volume / speed
 *
 * @param director  导演意图（从 VideoLayout.director 传入）
 * @param t        当前视频时间（秒）
 * @param duration  视频总时长（秒）
 */
export function evaluateDirector(
  director: DirectorIntent,
  t: number,
  duration: number
): DirectorState {
  // Step 1: 找到当前 scene
  const scene = findScene(director.scenes, t);

  // Step 2: 计算 scene 内的局部时间
  const sceneDuration = scene.end - scene.start;
  const local_t = sceneDuration > 0
    ? Math.min((t - scene.start) / sceneDuration, 1)
    : 0;

  // Step 3: 在 scene 局部曲线上插值
  const emotion = lerpCurve(scene.emotionalCurve, local_t);
  const pacing = lerpCurve(scene.pacingCurve, local_t);

  // Step 4: 派生状态
  // cameraOverride 来自 emotionEffect（情绪层），而非 director 固定配置
  const cameraOverride = emotionMap[computeEmotionLabel(emotion, scene.type)].cameraOverride;
  const cameraBase = cameraOverride === "shake" ? 1.1
    : cameraOverride === "slow-zoom" ? 1.0
    : cameraOverride === "pulse" ? 1.05
    : 0.95;
  const visualFocus = Math.min(1.0, emotion * 1.2 * cameraBase);
  const audioIntensity = Math.min(1.0, emotion * director.ttsSpeed * 0.9 + 0.1);

  // 当前时刻是否有时间区间强调点（构建期遗留）
  const emphasis = director.emphasisPoints.find(
    (ep: EmphasisPoint) => t >= ep.at[0] && t <= ep.at[1]
  ) ?? null;

  // 当前时刻正在说的词（用 allWords 缓存，O(log n) 二分查找）
  // allWords 已按 start 有序（来自 buildAllWords）
  const allWords = director.allWords;
  let activeWordIndex: number | null = null;
  if (allWords.length > 0) {
    // 二分查找：找最后一个 start <= t 的词
    let lo = 0, hi = allWords.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (allWords[mid].start <= t) {
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    // hi 是最后一个 start <= t 的词，检查是否在区间内
    if (hi >= 0 && t <= allWords[hi].end) {
      activeWordIndex = allWords[hi].index;
    }
  }

  // 词索引驱动的强调点（语义驱动，phrase 级）
  const emphasisPointWord = activeWordIndex !== null
    ? director.emphasisPointsWord.find((ep) => ep.wordIndices.includes(activeWordIndex)) ?? null
    : null;

  // Step 5: 情绪标签 + emotionEffect（emotion overlay layer）
  const emotionLabel = computeEmotionLabel(emotion, scene.type);
  const emotionEffect = emotionMap[emotionLabel];

  return {
    emotion, pacing, visualFocus, audioIntensity,
    emotionLabel,
    emotionEffect,
    emphasis,
    emphasisPointWord,
    scene,
  };
}
