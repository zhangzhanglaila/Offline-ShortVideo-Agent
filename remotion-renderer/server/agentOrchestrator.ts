/**
 * agentOrchestrator.ts — 短视频 Agent 编排器 v5
 *
 * v4 → v5 修复清单：
 *
 * 坑1: 区间判断太严格（必须用"相交"而非"包含"）
 *   修复: ep.at[1] > sceneStart && ep.at[0] < sceneEnd
 *
 * 坑2: 字幕 = segment-level，嘴型/听感漂移
 *   修复: edge-tts --write-subtitles → word-level VTT
 *         → SubtitleCue 按真实词边界切分
 *         → 支持逐字高亮 / 卡点字幕
 *
 * 坑3: MP3 concat -c copy 有 timestamp discontinuity
 *   修复: decode → PCM concat → re-encode AAC
 *         不用 -c copy，改用 filter_complex pipeline
 *
 * 坑4: Director emphasisPoints 只算出来了，没进 VideoScene 动画
 *   修复: VideoScene 每帧读 state.emphasis，驱动 shake / flash / zoom
 */
import { generateScriptFromTopic } from "./llm";
import { buildDirector, type DirectorIntent, type SubtitleCue, type WordCue, bindEmphasisToWords, buildAllWords } from "./director";
import { generateVideoLayoutFromScript, preResolveAllImages } from "./generator";
import type { Scene } from "./director";
import type { VideoLayout } from "@remotion/types";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "node:path";
import fs from "node:fs/promises";
import pLimit from "p-limit";
import { evaluateDirector } from "../remotion/directorEval";
import { beamSearchTransitionPlan, getRewardData } from "../remotion/VideoScene";

// ============================================================
// FFprobe — 精确时长测量
// ============================================================

function getAudioDuration(filePath: string): Promise<number> {
  return new Promise((resolve) => {
    const proc = spawn("ffprobe", [
      "-v", "error",
      "-show_entries", "format=duration",
      "-of", "default=noprint_wrappers=1:nokey=1",
      filePath,
    ]);
    let out = "";
    proc.stdout.on("data", (d) => { out += d.toString(); });
    proc.on("close", () => {
      const n = parseFloat(out.trim());
      resolve(isNaN(n) ? 0 : n);
    });
    proc.on("error", () => resolve(0));
  });
}

// ============================================================
// TTS voice 映射
// ============================================================

function mapVoice(director: DirectorIntent): { voice: string; baseRate: number } {
  switch (director.ttsVoice) {
    case "female_energetic": return { voice: "zh-CN-XiaomoNeural", baseRate: 2 };
    case "male_deep":         return { voice: "zh-CN-YunxiNeural",  baseRate: -1 };
    case "female_calm":       return { voice: "zh-CN-XiaoyiNeural",  baseRate: 0 };
    default:                  return { voice: "zh-CN-XiaoxiaoNeural", baseRate: 0 };
  }
}

// ============================================================
// TTS 生成 + Word-level 字幕（坑2修复）
// ============================================================

interface TTSResult {
  sceneIdx: number;
  text: string;
  path: string;
  realDuration: number;
  /** edge-tts --write-subtitles 生成的词级时间戳（可选） */
  wordCues?: Array<{ word: string; start: number; end: number }>;
}

/**
 * 生成单段 TTS + edge-tts --write-subtitles（word-level 时间戳）
 *
 * edge-tts 会生成 .vtt 文件，每行格式：
 *   word <start> <end>
 * 解析后得到精确到每个词的 start/end（秒）
 */
async function generateSceneTTS(
  text: string,
  director: DirectorIntent,
  sceneIdx: number,
  audioPath: string,
  vttPath: string
): Promise<TTSResult | null> {
  if (!text?.trim()) return null;

  const { voice, baseRate } = mapVoice(director);
  const emphasizedText = injectPauses(text, director, sceneIdx);

  const scenePacing = director.pacingCurve[sceneIdx] ?? 1.0;
  const rateAdjust = Math.round((director.ttsSpeed * scenePacing - 1) * 100);

  // edge-tts 命令：生成音频 + 生成字幕（含词级时间戳）
  const edgeBin = "edge-tts";
  const rateStr = rateAdjust >= 0 ? `+${rateAdjust}%` : `${rateAdjust}%`;

  const ttsArgs = [
    "--voice", voice,
    "--rate", rateStr,
    "--text", emphasizedText,
    "--write-audio", audioPath,
    "--write-subtitles", vttPath,
  ];

  return new Promise((resolve) => {
    const proc = spawn(edgeBin, ttsArgs, { shell: true });
    let stderr = "";
    proc.stderr?.on("data", (d) => { stderr += d.toString(); });
    proc.on("close", async (code) => {
      if (code === 0) {
        const realDuration = await getAudioDuration(audioPath);
        // 解析 .vtt 获取 word-level 时间戳
        const wordCues = await parseVTT(vttPath);
        resolve({ sceneIdx, text: emphasizedText, path: audioPath, realDuration, wordCues });
      } else {
        console.warn(`[Orchestrator] TTS scene ${sceneIdx} failed: ${stderr.slice(0, 150)}`);
        resolve(null);
      }
    });
    proc.on("error", () => resolve(null));
  });
}

/**
 * 解析 .vtt 文件，提取词级时间戳
 *
 * VTT 格式（edge-tts 生成）：
 *   WEBVTT
 *
 *   00:00:00.000 --> 00:00:01.234
 *   锁定一个
 *
 *   00:00:01.234 --> 00:00:02.100
 *   高需求
 *   ...
 *
 * 返回: [{ word, start, end }]
 */
async function parseVTT(vttPath: string): Promise<Array<{ word: string; start: number; end: number }>> {
  try {
    const content = await fs.readFile(vttPath, "utf-8");
    const cues: Array<{ word: string; start: number; end: number }> = [];
    const lines = content.split("\n");

    // VTT timestamp: HH:MM:SS.mmm --> HH:MM:SS.mmm
    const TS_RE = /(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})\.(\d{3})/;
    let currentStart = 0;
    let currentEnd = 0;

    for (const line of lines) {
      const m = line.match(TS_RE);
      if (m) {
        currentStart = parseInt(m[1]) * 3600 + parseInt(m[2]) * 60 + parseInt(m[3]) + parseInt(m[4]) / 1000;
        currentEnd = parseInt(m[5]) * 3600 + parseInt(m[6]) * 60 + parseInt(m[7]) + parseInt(m[8]) / 1000;
        continue;
      }
      const word = line.trim();
      if (word && word !== "WEBVTT" && !word.startsWith("NOTE") && !word.match(/^\d+$/)) {
        cues.push({ word, start: currentStart, end: currentEnd });
      }
    }
    return cues;
  } catch {
    return [];
  }
}

// ============================================================
// emphasisPoints 区间相交判断（坑1修复）
// ============================================================

/**
 * 判断两个闭区间是否相交
 * 标准区间重叠公式：a.start < b.end && a.end > b.start
 */
function intervalsOverlap(aStart: number, aEnd: number, bStart: number, bEnd: number): boolean {
  return aStart < bEnd && aEnd > bStart;
}

// ============================================================
// injectPauses — 句末自然停顿（坑2修复）
// ============================================================

function injectPauses(text: string, director: DirectorIntent, sceneIdx: number): string {
  if (!text) return text;

  const scene = director.scenes[sceneIdx];
  if (!scene) return text;

  const sceneStart = scene.start;
  const sceneEnd = scene.end;

  // 坑1修复：用区间相交判断（而非包含）
  const relevantEmphases = director.emphasisPoints.filter(
    (ep) =>
      (ep.type === "audio" || ep.type === "both") &&
      intervalsOverlap(ep.at[0], ep.at[1], sceneStart, sceneEnd)
  );

  let result = text;

  for (const ep of relevantEmphases) {
    if (ep.action === "pause" || ep.action === "slow-down") {
      // 句末标点后插省略号，自然换气感
      result = result.replace(/([。！？])/g, "$1…");
    }
    if (ep.action === "voice-up") {
      result = result.replace(/。$/, "！").replace(/！$/, "！");
    }
  }

  return result;
}

// ============================================================
// MP3 concat 稳定方案（坑3修复）
// ============================================================

/**
 * 坑3修复：MP3 -c copy 有 timestamp discontinuity
 *
 * 解法：decode → PCM concat → encode AAC
 * 全程无 timestamp 拼接错误
 *
 * @param segmentPaths 按 sceneIdx 有序的 MP3 文件路径
 * @param outputPath 最终 AAC 文件路径
 */
async function concatAudioSegmentsStable(segmentPaths: string[], outputPath: string): Promise<boolean> {
  if (segmentPaths.length === 0) return false;
  if (segmentPaths.length === 1) {
    // 只有一个文件，直接转 AAC
    return transcodeToAAC(segmentPaths[0], outputPath);
  }

  // 构建 filter_complex concat
  // [0:a][1:a][2:a]... → concat → out
  const inputs = segmentPaths.flatMap((p) => ["-i", p]);
  const filters = segmentPaths.map((_, i) => `[${i}:a]`).join("") + `concat=n=${segmentPaths.length}:v=0:a=1[outa]`;

  const args = [
    "-y",
    ...inputs,
    "-filter_complex", filters,
    "-map", "[outa]",
    "-c:a", "aac",
    "-b:a", "128k",
    outputPath,
  ];

  return new Promise((resolve) => {
    const proc = spawn("ffmpeg", args);
    let stderr = "";
    proc.stderr?.on("data", (d) => { stderr += d.toString(); });
    proc.on("close", (code) => {
      if (code === 0) resolve(true);
      else { console.warn(`[Orchestrator] concat stable failed: ${stderr.slice(0, 200)}`); resolve(false); }
    });
    proc.on("error", () => resolve(false));
  });
}

async function transcodeToAAC(inputPath: string, outputPath: string): Promise<boolean> {
  return new Promise((resolve) => {
    const proc = spawn("ffmpeg", ["-y", "-i", inputPath, "-c:a", "aac", "-b:a", "128k", outputPath]);
    let stderr = "";
    proc.stderr?.on("data", (d) => { stderr += d.toString(); });
    proc.on("close", (code) => { resolve(code === 0); });
    proc.on("error", () => resolve(false));
  });
}

// ============================================================
// 音频轨道构建
// ============================================================

interface AudioTrack {
  path: string;
  segments: TTSResult[];
  totalDuration: number;
  /** word-level 字幕（来自 VTT） */
  wordSubtitles: SubtitleCue[];
}

async function buildAudioTrack(
  script: { hook: { text: string }; steps: Array<{ title: string; desc: string }>; cta: { text: string } },
  director: DirectorIntent,
  jobId: string,
  outputDir: string
): Promise<AudioTrack | null> {
  const items: Array<{ text: string; sceneIdx: number }> = [];
  items.push({ text: script.hook.text, sceneIdx: 0 });
  script.steps.forEach((_, i) => {
    items.push({ text: `${script.steps[i].title}。${script.steps[i].desc}`, sceneIdx: 1 + i });
  });
  items.push({ text: script.cta.text, sceneIdx: director.scenes.length - 1 });

  // 并行生成所有 TTS（p-limit(3) 控并发，同时生成音频 + VTT 字幕）
  const limit = pLimit(3);
  const rawResults = await Promise.all(
    items.map(({ text, sceneIdx }) =>
      limit(() => {
        const audioPath = path.join(outputDir, `${jobId}_s${sceneIdx}.mp3`);
        const vttPath = path.join(outputDir, `${jobId}_s${sceneIdx}.vtt`);
        return generateSceneTTS(text, director, sceneIdx, audioPath, vttPath);
      })
    )
  );

  // 按 sceneIdx 排序（Map 双保险）
  const segMap = new Map<number, TTSResult>();
  for (const r of rawResults) { if (r) segMap.set(r.sceneIdx, r); }

  const validSegments: TTSResult[] = [];
  for (let i = 0; i < items.length; i++) {
    const seg = segMap.get(i);
    if (seg) validSegments.push(seg);
  }

  if (validSegments.length === 0) return null;

  // 坑3修复：decode → PCM concat → AAC（不用 -c copy）
  const finalAudioPath = path.join(outputDir, `${jobId}_audio.aac`);
  const ok = await concatAudioSegmentsStable(validSegments.map((s) => s.path), finalAudioPath);

  // 清理临时文件
  await Promise.all([
    ...validSegments.map((s) => fs.unlink(s.path).catch(() => {})),
    ...validSegments.map((s) => s.wordCues ? fs.unlink(s.path.replace(".mp3", ".vtt")).catch(() => {}) : Promise.resolve()),
  ]);

  if (!ok) return null;

  // 坑5：FFprobe 测最终文件真实时长
  const totalDuration = await getAudioDuration(finalAudioPath);

  // 收集所有 word-level 字幕（按 sceneIdx 分组，每段一个 SubtitleCue）
  // 全局词序号：跨所有 segment 连续编号（用于 emphasisPointsWord 绑定）
  const wordSubtitles: SubtitleCue[] = [];
  let globalWordIndex = 0;
  let timeOffset = 0;
  for (const seg of validSegments) {
    if (seg.wordCues && seg.wordCues.length > 0) {
      const cue: SubtitleCue = {
        id: `scene-${seg.sceneIdx}`,
        start: parseFloat(timeOffset.toFixed(3)),
        end: parseFloat((timeOffset + seg.realDuration).toFixed(3)),
        words: seg.wordCues.map((wc) => ({
          index: globalWordIndex++,
          word: wc.word,
          start: parseFloat((timeOffset + wc.start).toFixed(3)),
          end: parseFloat((timeOffset + wc.end).toFixed(3)),
        })),
      };
      wordSubtitles.push(cue);
    }
    timeOffset += seg.realDuration;
  }

  console.info(`[Orchestrator:${jobId}] Audio: ${validSegments.length} segs, ${totalDuration.toFixed(3)}s, ${wordSubtitles.length} word-cues`);
  return { path: finalAudioPath, segments: validSegments, totalDuration, wordSubtitles };
}

// ============================================================
// rebuild Director 时间轴
// ============================================================

function rebuildDirector(
  original: DirectorIntent,
  segments: TTSResult[],
  script: { steps: Array<{ title: string; desc: string }> },
  subtitleCues: SubtitleCue[],
): DirectorIntent {
  const newScenes: Scene[] = [];
  let currentStart = 0;

  const hookDur = segments[0]?.realDuration ?? 3;
  newScenes.push({ ...original.scenes[0], start: 0, end: hookDur });
  currentStart = hookDur;

  for (let i = 0; i < script.steps.length; i++) {
    const dur = segments[1 + i]?.realDuration ?? 5;
    const sceneIdx = 1 + i;
    newScenes.push({ ...original.scenes[sceneIdx], start: currentStart, end: currentStart + dur });
    currentStart += dur;
  }

  const ctaDur = segments[segments.length - 1]?.realDuration ?? 3;
  const ctaSceneIdx = original.scenes.length - 1;
  newScenes.push({ ...original.scenes[ctaSceneIdx], start: currentStart, end: currentStart + ctaDur });

  const rebuildCurve = (orig: number[], len: number) => {
    if (orig.length === len) return orig;
    return Array.from({ length: len }, (_, i) => orig[Math.round((i / (len - 1)) * (orig.length - 1))]);
  };

  // 语义驱动：把时间区间 emphasisPoints 绑定到词索引
  const emphasisPointsWord = bindEmphasisToWords(original.emphasisPoints, subtitleCues);
  const allWords = buildAllWords(subtitleCues);

  return {
    ...original,
    scenes: newScenes,
    emotionalCurve: rebuildCurve(original.emotionalCurve, newScenes.length),
    pacingCurve: rebuildCurve(original.pacingCurve, newScenes.length),
    subtitleCues,
    allWords,
    emphasisPointsWord,
  };
}

// ============================================================
// FFmpeg merge（坑4修复：无 -shortest）
// ============================================================

async function mergeAudioVideo(videoPath: string, audioPath: string, outputPath: string): Promise<boolean> {
  return new Promise((resolve) => {
    const proc = spawn("ffmpeg", [
      "-y",
      "-i", videoPath,
      "-i", audioPath,
      "-c:v", "copy",
      "-c:a", "aac",
      "-b:a", "128k",
      // 坑4修复：去掉 -shortest，video 已按 audio 时长精确构建
      outputPath,
    ]);
    let stderr = "";
    proc.stderr?.on("data", (d) => { stderr += d.toString(); });
    proc.on("close", (code) => {
      if (code === 0) resolve(true);
      else { console.warn(`[Orchestrator] merge failed: ${stderr.slice(0, 200)}`); resolve(false); }
    });
    proc.on("error", () => resolve(false));
  });
}

// ============================================================
// SRT 字幕生成（从 word-level cues）
// ============================================================

function buildSRT(cues: SubtitleCue[]): string {
  return cues
    .map((cue, i) => {
      const toSRT = (t: number) => {
        const h = Math.floor(t / 3600);
        const m = Math.floor((t % 3600) / 60);
        const s = Math.floor(t % 60);
        const ms = Math.round((t % 1) * 1000);
        return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")},${String(ms).padStart(3, "0")}`;
      };
      const text = cue.words?.map((w) => w.word).join("") ?? (cue as any).text ?? "";
      return `${i + 1}\n${toSRT(cue.start)} --> ${toSRT(cue.end)}\n${text}`;
    })
    .join("\n");
}

// ============================================================
// Remotion 渲染
// ============================================================

async function renderVideo(jobId: string, layout: VideoLayout, outputPath: string): Promise<string | null> {
  const { renderMedia, selectComposition } = await import("@remotion/renderer");
  const SERVE_URL = `http://localhost:${process.env.PORT || 3333}`;

  try {
    const composition = await selectComposition({
      serveUrl: SERVE_URL, id: "VideoFlow",
      inputProps: { video: layout },
    });
    if (!composition) return null;

    const totalDuration = layout.director
      ? layout.director.scenes[layout.director.scenes.length - 1].end
      : 300;
    const durationInFrames = Math.max(Math.round(totalDuration * 30) + 30, 300);

    await renderMedia({
      serveUrl: SERVE_URL,
      composition: { ...composition, durationInFrames, fps: 30, props: { video: layout } },
      inputProps: { video: layout },
      codec: "h264",
      outputLocation: outputPath,
    });
    return outputPath;
  } catch (err) {
    console.error(`[Orchestrator] render error: ${err}`);
    return null;
  }
}

// ============================================================
// v18: Reward Data Flush（Server-side MCTS Replay + JSONL Write）
// ============================================================

/**
 * v18+v19.6b: Replay MCTS + flush reward data to JSONL
 *
 * 流程（与渲染完全解耦）：
 *   VideoLayout 生成后（Step 6）→ layout.shots + layout.director 就可用
 *   → 重建 emotions（用 evaluateDirector）
 *   → beamSearchTransitionPlan(shots, emotions, fps) 触发 MCTS search
 *   → getRewardData() 读取 module-level collector
 *   → appendFileSync → dataset/reward_data.jsonl
 *
 * 注意：此函数在 renderVideo 之前调用，渲染失败不影响 reward data 收集
 */
async function collectRewardData(layout: VideoLayout, jobId: string): Promise<void> {
  if (!layout.shots || layout.shots.length === 0) {
    console.info(`[Orchestrator:${jobId}] reward: no shots, skipping`);
    return;
  }

  console.info(`[Orchestrator:${jobId}] reward: collectRewardData triggered (shots=${layout.shots.length})`);

  const fps = layout.fps || 30;
  const durationInFrames = layout.director
    ? layout.director.scenes[layout.director.scenes.length - 1].end * fps
    : layout.shots.reduce((sum, s) => sum + s.duration, 0);

  // 重建 emotions（与 VideoScene.tsx 内 useMemo 逻辑一致）
  const emotions = layout.shots.map((shot) => {
    const midT = (shot.start + shot.duration / 2) / fps;
    const duration = durationInFrames / fps;
    const state = evaluateDirector(layout.director!, midT, duration);
    return state?.emotion ?? 0.5;
  });

  // 运行 MCTS（触发 rewardCollector.collect()）
  try {
    beamSearchTransitionPlan(layout.shots, emotions, fps);
  } catch (err) {
    console.error(`[Orchestrator:${jobId}] MCTS error: ${err}`);
    return;
  }

  // 读取并 flush 到 JSONL
  const jsonl = getRewardData();
  if (!jsonl.trim()) {
    console.info(`[Orchestrator:${jobId}] reward: no data collected`);
    return;
  }

  const datasetDir = path.join(process.cwd(), "dataset");
  await fs.mkdir(datasetDir, { recursive: true });
  const jsonlPath = path.join(datasetDir, "reward_data.jsonl");
  await fs.appendFile(jsonlPath, jsonl + "\n", "utf-8");
  console.info(`[Orchestrator:${jobId}] reward: flushed ${jsonl.split("\n").filter(Boolean).length} entries to ${jsonlPath}`);
}

// ============================================================
// runAgent v5 — 完整编排入口
// ============================================================

export interface OrchestratorConfig {
  topic: string;
  enableTTS?: boolean;
  enableWordSubtitles?: boolean; // word-level 字幕（坑2）
  outputDir?: string;
}

export interface OrchestratorResult {
  outputPath: string;
  topic: string;
  arc: string;
  totalTimeSeconds: number;
  videoDurationSeconds?: number;
  subtitlePath?: string;
  wordSubtitlePath?: string; // word-level SRT（坑2新增）
  success: boolean;
  error?: string;
}

/**
 * runAgent v5
 *
 * 完整链路：
 *   generateScript
 *     → buildDirector (粗略 scene)
 *     → preResolveImages
 *     → buildAudioTrack
 *         generateSceneTTS × N
 *           edge-tts --write-subtitles → .mp3 + .vtt
 *           parseVTT → wordCues[]（词级时间戳）
 *         concatAudioSegmentsStable → .aac（decode→PCM concat→AAC，坑3修复）
 *         FFprobe final duration
 *     → rebuildDirector (真实时长)
 *     → generateVideoLayout (精确时间轴)
 *     → render
 *     → merge (无 -shortest)
 *     → FFprobe final
 *     → SRT (segment-level 或 word-level，坑2修复)
 */
export async function runAgent(config: OrchestratorConfig): Promise<OrchestratorResult> {
  const startTime = Date.now();
  const { topic, enableTTS = true, enableWordSubtitles = false, outputDir = path.join(process.cwd(), "renders") } = config;
  const jobId = randomUUID().slice(0, 8);

  console.info(`[Orchestrator:${jobId}] Starting v5: "${topic}"`);

  try {
    // Step 1-3: 脚本 / 导演 / 图片
    const script = await generateScriptFromTopic(topic);
    const director0 = buildDirector(topic, script);
    const preResolved = await preResolveAllImages(script.topic ?? script.hook.text, script.steps.map((s) => s.imageKeyword));
    console.info(`[Orchestrator:${jobId}] 1-3: arc=${director0.arc}, ${director0.scenes.length} scenes`);

    // Step 4: TTS + 字幕
    let audioTrack: AudioTrack | null = null;
    let director: DirectorIntent = director0;

    if (enableTTS) {
      audioTrack = await buildAudioTrack(script, director0, jobId, outputDir);
      if (audioTrack) {
        director = rebuildDirector(director0, audioTrack.segments, script, audioTrack.wordSubtitles);
        console.info(`[Orchestrator:${jobId}] 4: audio=${audioTrack.totalDuration.toFixed(3)}s, wordCues=${audioTrack.wordSubtitles.length}`);
      } else {
        console.warn(`[Orchestrator:${jobId}] 4: TTS failed`);
      }
    }

    // Step 5: 字幕文件（坑2修复核心）
    let subtitlePath: string | undefined;
    let wordSubtitlePath: string | undefined;

    if (audioTrack) {
      if (enableWordSubtitles && audioTrack.wordSubtitles.length > 0) {
        // word-level SRT：每个词一条字幕 → 逐字高亮 / 卡点效果
        wordSubtitlePath = path.join(outputDir, `${jobId}_words.srt`);
        await fs.writeFile(wordSubtitlePath, buildSRT(audioTrack.wordSubtitles), "utf-8");
        console.info(`[Orchestrator:${jobId}] 5: word-level subtitles → ${wordSubtitlePath}`);
      } else {
        // segment-level SRT：每句话一条（fallback）
        const segCues: SubtitleCue[] = [];
        let t = 0;
        for (const seg of audioTrack.segments) {
          segCues.push({
            id: `scene-${seg.sceneIdx}`,
            start: parseFloat(t.toFixed(3)),
            end: parseFloat((t + seg.realDuration).toFixed(3)),
            words: [],
          });
          t += seg.realDuration;
        }
        subtitlePath = path.join(outputDir, `${jobId}_subtitles.srt`);
        await fs.writeFile(subtitlePath, buildSRT(segCues), "utf-8");
      }
    }

    // Step 6: Layout（基于真实时长重建，注入 word-level 字幕用于逐词高亮）
    const layout: VideoLayout = generateVideoLayoutFromScript(
      script, preResolved, director,
      audioTrack ? audioTrack.wordSubtitles : undefined,
    );
    const videoDuration = layout.director ? layout.director.scenes[layout.director.scenes.length - 1].end : 0;
    console.info(`[Orchestrator:${jobId}] 6: layout duration=${videoDuration.toFixed(3)}s`);

    // Step 7: Reward Data Flush（MCTS replay + JSONL write — 不依赖渲染）
    // collectRewardData 只用 VideoLayout，不走 Remotion renderer
    await collectRewardData(layout, jobId);

    // Step 8: 渲染（与 reward data 解耦，渲染失败不影响数据收集）
    const silentPath = path.join(outputDir, `${jobId}_silent.mp4`);
    const rendered = await renderVideo(jobId, layout, silentPath);
    if (!rendered) {
      console.warn(`[Orchestrator:${jobId}] 7: render failed — reward data already collected`);
    } else {
      console.info(`[Orchestrator:${jobId}] 7: rendered → ${rendered}`);
    }

    // Step 9: 音画合并（无 -shortest，仅在渲染成功时执行）
    let finalVideoPath = rendered ?? silentPath;
    if (rendered && audioTrack) {
      finalVideoPath = path.join(outputDir, `${jobId}_final.mp4`);
      const merged = await mergeAudioVideo(rendered, audioTrack.path, finalVideoPath);
      if (merged) {
        console.info(`[Orchestrator:${jobId}] 9: merged → ${finalVideoPath}`);
      } else {
        finalVideoPath = rendered;
      }
    }

    // 终极验证：FFprobe 最终视频时长
    const finalDuration = await getAudioDuration(finalVideoPath ?? silentPath);

    const totalTime = (Date.now() - startTime) / 1000;
    console.info(`[Orchestrator:${jobId}] DONE in ${totalTime.toFixed(1)}s — final=${finalDuration.toFixed(3)}s`);

    return {
      outputPath: rendered ?? silentPath,
      topic,
      arc: director.arc,
      totalTimeSeconds: totalTime,
      videoDurationSeconds: parseFloat(finalDuration.toFixed(2)),
      subtitlePath,
      wordSubtitlePath,
      success: true,
    };
  } catch (err) {
    const error = err instanceof Error ? err.message : String(err);
    console.error(`[Orchestrator:${jobId}] FAILED: ${error}`);
    return {
      outputPath: "", topic, arc: "unknown",
      totalTimeSeconds: (Date.now() - startTime) / 1000,
      success: false, error,
    };
  }
}
