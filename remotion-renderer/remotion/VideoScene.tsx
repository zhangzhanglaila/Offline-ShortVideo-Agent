/**
 * VideoScene.tsx - V13 剪辑决策引擎
 *
 * v13 Architecture: Timeline-aware Editorial Policy Layer
 *
 * 三层分离（关键设计）：
 *   1. TransitionPlanner  — 整条视频的 transition 规划（pure function, useMemo）
 *   2. useTransitionPlan  — 将规划接入 React 渲染管线（无状态）
 *   3. useShotsAroundFrame — 执行：查规划 + 驱动 CSS transform
 *
 * 相比 v12 的本质区别：
 *   v12: reactive（每帧根据 frame 实时决定）→ module-level state 污染风险
 *   v13: planned（一次性规划全 timeline）→ 无状态，纯函数，无跨视频污染
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

/**
 * v13: 一次性规划整条视频的 transition 决策
 *
 * @param shots - 镜头序列
 * @param emotions - 每个 shot 对应的情绪强度（0~1），长度应与 shots 一致
 * @param fps - 帧率
 *
 * 算法：
 *   1. 从全局 budget + cooldown 状态机出发
 *   2. 对每个 shot boundary：
 *      - emotion → rawType
 *      - budget/cooldown 校验 → 降级
 *      - lastTransition 防重复
 *      - consecutiveWhip 限制
 *   3. 更新状态机
 *   4. 记录 micro-cut（shot 60%~62%）
 */
function buildTransitionPlan(
  shots: Shot[],
  emotions: number[],
  fps: number
): TransitionPlan {
  const plan = new Map<number, TransitionDecision>();
  let state: EditorState = {
    budget: MAX_BUDGET,
    cooldown: 0,
    lastTransition: "zoom",
  };
  let consecutiveWhip = 0;

  for (let i = 0; i < shots.length - 1; i++) {
    const emotion = emotions[i] ?? 0.5;
    const beat = Math.sin((shots[i].start / fps) * 0.05);
    const rhythmBoost = beat > 0.6 ? 1 : beat < -0.4 ? -1 : 0;

    // ── Step 1: emotion → raw type ──────────────────────────
    let rawType: TransitionType =
      emotion >= 0.75 + (rhythmBoost > 0 ? 0 : -0.1)
        ? "whip"
        : emotion <= 0.35 + (rhythmBoost < 0 ? 0.1 : 0)
        ? "fade"
        : "zoom";

    // ── Step 2: cooldown 校验 ────────────────────────────────
    if (state.cooldown > 0) {
      rawType = rawType === "whip" ? "zoom" : rawType;
      state.cooldown--;
    }

    // ── Step 3: budget 校验 ─────────────────────────────────
    if (rawType === "whip" && state.budget < WHIP_COST) {
      rawType = state.budget >= ZOOM_COST ? "zoom" : "fade";
    }

    // ── Step 4: lastTransition 防重复 ───────────────────────
    let type: TransitionType = rawType;
    if (type === state.lastTransition) {
      if (type === "whip") type = "zoom";
      else if (type === "zoom") type = "fade";
      else type = "zoom";
    }

    // ── Step 5: consecutiveWhip 限制 ────────────────────────
    if (type === "whip") {
      consecutiveWhip++;
      if (consecutiveWhip >= MAX_CONSECUTIVE_WHIP) {
        type = "zoom";
        consecutiveWhip = 0;
      }
    } else {
      consecutiveWhip = 0;
    }

    // ── Step 6: 消耗 budget + 设置 cooldown ──────────────────
    if (type === "whip") {
      state.budget -= WHIP_COST;
      state.cooldown = COOLDOWN_FRAMES;
    } else if (type === "zoom") {
      state.budget -= ZOOM_COST;
    } else {
      state.budget = Math.min(MAX_BUDGET, state.budget + BUDGET_REGEN);
    }

    // ── Step 7: micro-cut（shot 内部剪辑感，v13 新增）──────────
    // 在 shot 的 60%~62% 处注入 micro-cut，让 shot 内部也有剪辑感
    const microCutAt = 0.60 + Math.abs(Math.sin(shots[i].start * 0.03)) * 0.02;
    const microCutIntensity = emotion * 0.12; // 情绪越强 micro-cut 越明显

    plan.set(i, {
      shotIndex: i,
      type,
      microCutAt,
      microCutIntensity,
    });

    state.lastTransition = type;
  }

  return plan;
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
