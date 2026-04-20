/**
 * Timeline Layout Types - V6 视频级元素系统
 *
 * 升级：从"boxes + arrows" → "elements[]"
 * 每个元素自带：位置、尺寸、动画元数据
 */

// ============================================================
// Director Intent Types（从 server/director.ts 复制）
// ============================================================

export type NarrativeArc = "hook-first" | "problem-solution" | "story" | "viral";
export type VisualStyle = "cinematic" | "bold" | "minimalist" | "tech" | "warm";
export type TTSVoice = "male_deep" | "female_energetic" | "female_calm" | "neutral";
export type SceneType = "hook" | "explain" | "cta";

export interface Scene {
  start: number;
  end: number;
  type: SceneType;
  emotionalCurve: number[];
  pacingCurve: number[];
  visualStyle: VisualStyle;
}

export interface EmphasisPoint {
  at: [number, number];
  type: "visual" | "audio" | "both";
  action: "zoom-in" | "flash" | "pause" | "slow-down" | "subtitle-pulse" | "voice-up";
}

/** VTT 解析后，词/短语级绑定的强调点（运行时消费） */
export interface EmphasisPointWord {
  /** 强调的词序号区间（phrase 级，支持多词） */
  wordIndices: number[];
  type: "visual" | "audio" | "both";
  action: "zoom-in" | "flash" | "pause" | "slow-down" | "subtitle-pulse" | "voice-up";
}

/** 情绪→视觉特效映射（emotion overlay layer） */
export type EmotionLabel = "intense" | "calm" | "neutral" | "warm" | "dramatic";

export interface EmotionEffect {
  label: EmotionLabel;
  /** 情绪驱动的相机策略覆盖 */
  cameraOverride: "shake" | "slow-zoom" | "static" | "pulse";
  /** 情绪色调（叠加在主色上） */
  colorOverlay: string;
  /** 情绪呼吸强度（0~1） */
  breatheIntensity: number;
  /** 情绪 zoom 基础值 */
  zoomBase: number;
}

export interface WordCue {
  index: number;
  word: string;
  start: number;   // 秒（toFixed(3) 毫秒精度）
  end: number;     // 秒
}

/** 一句话 = 多个 WordCue（渲染时按 t 高亮当前词） */
export interface SubtitleCue {
  id: string;
  start: number;   // 秒
  end: number;     // 秒
  words: WordCue[];
}

export interface DirectorIntent {
  arc: NarrativeArc;
  scenes: Scene[];
  emotionalCurve: number[];
  pacingCurve: number[];
  ttsVoice: TTSVoice;
  ttsSpeed: number;
  emphasisPoints: EmphasisPoint[];
  cameraStrategy: "zoom-in-out" | "pan" | "static" | "shake";
  /** word-level 字幕（来自 VTT 解析），传给 VideoScene 做逐词高亮） */
  subtitleCues: SubtitleCue[];
  /** 运行时缓存：所有词展平（避免每帧 flatten） */
  allWords: WordCue[];
  colorOverride?: {
    primary: string;
    fill: string;
    text: string;
  };
  /** VTT 解析后的词级强调点（运行时消费，绑定到具体 wordIndex） */
  emphasisPointsWord: EmphasisPointWord[];
}

// ============================================================
// 动画类型定义
// ============================================================
export type EnterAnimation = "fade" | "slide-up" | "slide-down" | "zoom-in" | "zoom-out" | "bounce-in" | "blur-in";
export type ExitAnimation = "fade" | "slide-up" | "slide-down" | "zoom-out" | "blur-out";

export interface ElementAnimation {
  enter?: EnterAnimation;
  exit?: ExitAnimation;
  duration?: number;   // 动画持续帧数，默认15
}

// ============================================================
// 基础元素
// ============================================================
export interface BaseElement {
  id: string;
  start: number;       // 开始帧
  duration: number;     // 持续帧数
  zIndex: number;
  animation?: ElementAnimation;
}

// ============================================================
// 文本元素
// ============================================================
export interface TextElement extends BaseElement {
  type: "text";
  text: string;
  x: number;
  y: number;
  fontSize: number;
  color: string;
  fontWeight?: number;
  textAlign?: string;
  lineHeight?: number;
  maxWidth?: number;
  /** word-level 高亮数据（来自 VTT 解析，渲染时按帧高亮当前词） */
  wordCues?: WordCue[];
}

// ============================================================
// 图片元素
// ============================================================
export interface ImageElement extends BaseElement {
  type: "image";
  src: string;
  x: number;
  y: number;
  width: number;
  height: number;
  borderRadius?: number;
  objectFit?: "cover" | "contain" | "fill";
}

// ============================================================
// 贴纸/Emoji 元素
// ============================================================
export interface StickerElement extends BaseElement {
  type: "sticker";
  emoji: string;
  x: number;
  y: number;
  size: number;
}

// ============================================================
// 背景渐变元素
// ============================================================
export interface BackgroundElement extends BaseElement {
  type: "background";
  color?: string;
  gradient?: string;
  image?: string;
}

// ============================================================
// SVG/图形元素
// ============================================================
export interface ShapeElement extends BaseElement {
  type: "shape";
  shape: "rect" | "circle" | "line";
  x: number;
  y: number;
  width: number;
  height: number;
  color: string;
  fillColor?: string;
  borderRadius?: number;
  rotation?: number;
}

// ============================================================
// 并集类型
// ============================================================
export type VideoElement =
  | TextElement
  | ImageElement
  | StickerElement
  | BackgroundElement
  | ShapeElement;

// ============================================================
// 镜头系统（v10 新增）
// 素材不再是静态背景，而是参与镜头语法的动态单元
// ============================================================
export type ShotCamera = "push-in" | "pan-left" | "pan-right" | "pull-out" | "tilt-up" | "tilt-down" | "static";

export interface Shot {
  /** 镜头在视频中的起始帧 */
  start: number;
  /** 持续帧数 */
  duration: number;
  /** 图片 URL（来自 preResolveAllImages） */
  src: string;
  /** 相机运动类型 */
  camera: ShotCamera;
  /** 显示区域（相对于全屏，0~1） */
  cropX?: number;   // 默认 0
  cropY?: number;    // 默认 0
  cropW?: number;    // 默认 1（全宽）
  cropH?: number;    // 默认 1（全高）
  /** 叠加透明度 */
  opacity?: number; // 默认 1
}

// ============================================================
// 视频布局（新版）
// ============================================================
export interface VideoLayout {
  width: number;
  height: number;
  fps: number;
  background?: string;  // 背景色或渐变
  elements: VideoElement[];
  /** 导演意图（指导渲染层的动态状态） */
  director?: DirectorIntent;
  /** word-level 字幕（由 agentOrchestrator 注入，用于逐词高亮渲染） */
  subtitleCues?: SubtitleCue[];
  /** 镜头序列（v10 新增，素材参与镜头语法） */
  shots?: Shot[];
}

// ============================================================
// 兼容旧版 TimelineLayout（保持向后兼容）
// ============================================================
export type BoxData = {
  id: string;
  label: string;
  subLabel?: string;
  x: number;
  y: number;
  width: number;
  height: number;
  color: string;
  fillColor: string;
  textColor: string;
  fontSize: number;
  showFrom: number;
  durationInFrames: number;
  highlighted?: boolean;
  highlightColor?: string;
  rotation?: number;
  zIndex?: number;
  text?: string;
  content?: string;
  tag?: string;
  accent_color?: string;
  image?: string;
  icon?: string;
};

export type ArrowData = {
  id: string;
  fromBoxId: string;
  toBoxId: string;
  label?: string;
  color: string;
  showFrom: number;
};

export type TimelineLayout = {
  backgroundImage: string;
  backgroundImageAlt?: string;
  boxes: BoxData[];
  arrows: ArrowData[];
  width: number;
  height: number;
};
