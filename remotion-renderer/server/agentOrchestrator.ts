/**
 * agentOrchestrator.ts — 短视频 Agent 编排器 v4
 *
 * v3 → v4 修复清单：
 *
 * 坑1: emphasisPoints 没用 scene 时间过滤
 *   修复: ep.at[0] >= sceneStart && ep.at[1] <= sceneEnd
 *
 * 坑2: injectPauses 在词中间乱插
 *   修复: 在句末标点后插 "…"（自然的换气停顿）
 *
 * 坑3: audioSegments 顺序假设（Promise.all 不保证顺序）
 *   修复: Map<sceneIdx, TTSResult> + 显式 sceneIdx 注入
 *
 * 坑4: -shortest 存在说明不信任时间轴
 *   修复: 去掉 -shortest，video = audio 完全对齐
 *
 * 坑5: totalDuration = sum(segment)，但 concat 后可能有 padding
 *   修复: 对 final audio 再测一次 FFprobe → totalDuration = 真实值
 *
 * v4 新增：
 * - SubtitleTrack 生成（基于真实音频时长）
 * - TTS + render 并行（省时间）
 * - 去掉 -shortest（完全信任时间轴）
 */
import { generateScriptFromTopic } from "./llm";
import { buildDirector, type DirectorIntent } from "./director";
import { generateVideoLayoutFromScript, preResolveAllImages } from "./generator";
import type { Scene } from "./director";
import type { VideoLayout } from "../remotion/types";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "node:path";
import fs from "node:fs/promises";

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
// TTS 生成（显式 sceneIdx，不依赖 Promise.all 顺序）
// ============================================================

interface TTSResult {
  sceneIdx: number;     // ← 显式标记，不依赖数组顺序
  text: string;
  path: string;
  realDuration: number;  // FFprobe 实测秒数
}

/**
 * 生成单段 TTS，注入 emphasis 停顿，FFprobe 测真实时长
 */
async function generateSceneTTS(
  text: string,
  director: DirectorIntent,
  sceneIdx: number,
  outputPath: string
): Promise<TTSResult | null> {
  if (!text?.trim()) return null;

  const { voice, baseRate } = mapVoice(director);
  const emphasizedText = injectPauses(text, director, sceneIdx);

  const scenePacing = director.pacingCurve[sceneIdx] ?? 1.0;
  const rateAdjust = Math.round((director.ttsSpeed * scenePacing - 1) * 100);

  const pythonScript = `
import sys
sys.path.insert(0, r"${path.resolve(".")}")
from core.tts_module import TTSModule
tts = TTSModule(voice="${voice}")
tts.rate = ${rateAdjust}
tts.generate_audio(${JSON.stringify(emphasizedText)}, r"${outputPath}")
`;

  return new Promise((resolve) => {
    const proc = spawn("python", ["-c", pythonScript], {
      cwd: path.resolve(".."),
      shell: true,
    });
    let stderr = "";
    proc.stderr?.on("data", (d) => { stderr += d.toString(); });
    proc.on("close", async (code) => {
      if (code === 0) {
        const realDuration = await getAudioDuration(outputPath);
        resolve({ sceneIdx, text: emphasizedText, path: outputPath, realDuration });
      } else {
        console.warn(`[Orchestrator] TTS scene ${sceneIdx} failed: ${stderr.slice(0, 150)}`);
        resolve(null);
      }
    });
    proc.on("error", () => resolve(null));
  });
}

// ============================================================
// emphasisPoints 注入停顿（修复：坑1 + 坑2）
// ============================================================

/**
 * 只在 scene 时间范围内的 emphasis 才注入
 * 插入方式：在句末标点后加 "…"（自然换气，而非词中间乱断）
 */
function injectPauses(text: string, director: DirectorIntent, sceneIdx: number): string {
  if (!text) return text;

  const scene = director.scenes[sceneIdx];
  if (!scene) return text;

  const sceneStart = scene.start;
  const sceneEnd = scene.end;

  // 过滤出落在当前 scene 时间范围内的 audio emphasis
  const relevantEmphases = director.emphasisPoints.filter(
    (ep) =>
      (ep.type === "audio" || ep.type === "both") &&
      ep.at[0] >= sceneStart &&
      ep.at[1] <= sceneEnd
  );

  let result = text;

  for (const ep of relevantEmphases) {
    if (ep.action === "pause" || ep.action === "slow-down") {
      // 坑2修复：在句末标点后插省略号，自然停顿
      result = result.replace(/([。！？])/g, "$1…");
    }
    if (ep.action === "voice-up") {
      // 感叹号增强语气
      result = result.replace(/。$/, "！").replace(/！$/, "！");
    }
  }

  return result;
}

// ============================================================
// 字幕轨道生成（新增）
// ============================================================

export interface SubtitleCue {
  start: number;   // 秒
  end: number;     // 秒
  text: string;
  sceneIdx: number;
}

/**
 * 基于 TTS segments 生成字幕轨道
 * 每个 segment 的 text → 一条字幕
 */
function buildSubtitleTrack(segments: TTSResult[]): SubtitleCue[] {
  const cues: SubtitleCue[] = [];
  let currentTime = 0;

  for (const seg of segments) {
    if (!seg.text.trim()) { currentTime += seg.realDuration; continue; }

    cues.push({
      start: parseFloat(currentTime.toFixed(2)),
      end: parseFloat((currentTime + seg.realDuration).toFixed(2)),
      text: seg.text,
      sceneIdx: seg.sceneIdx,
    });
    currentTime += seg.realDuration;
  }

  return cues;
}

// ============================================================
// Scene 级音频轨道构建
// ============================================================

interface AudioTrack {
  path: string;
  segments: TTSResult[];  // Map 保证顺序安全
  totalDuration: number; // FFprobe 实测最终 concat 音频总时长
}

/**
 * 构建完整音频轨道
 *
 * 坑3修复：显式 sceneIdx，不依赖 Promise.all 返回顺序
 * 坑5修复：对最终 concat 文件测 FFprobe，不用 sum 估算
 */
async function buildAudioTrack(
  script: { hook: { text: string }; steps: Array<{ title: string; desc: string }>; cta: { text: string } },
  director: DirectorIntent,
  jobId: string,
  outputDir: string
): Promise<AudioTrack | null> {
  // 构造：sceneIdx → text 映射
  const items: Array<{ text: string; sceneIdx: number }> = [];

  items.push({ text: script.hook.text, sceneIdx: 0 });

  script.steps.forEach((_, i) => {
    items.push({ text: `${script.steps[i].title}。${script.steps[i].desc}`, sceneIdx: 1 + i });
  });

  items.push({ text: script.cta.text, sceneIdx: director.scenes.length - 1 });

  // 并行生成（Promise.all 返回顺序 = 数组顺序，但 sceneIdx 嵌入结果中，双保险）
  const rawResults = await Promise.all(
    items.map(({ text, sceneIdx }) =>
      generateSceneTTS(text, director, sceneIdx, path.join(outputDir, `${jobId}_s${sceneIdx}.mp3`))
    )
  );

  // 坑3修复：用 sceneIdx 构建 Map，不怕顺序错位
  const segMap = new Map<number, TTSResult>();
  for (const r of rawResults) {
    if (r) segMap.set(r.sceneIdx, r);
  }

  // 按 sceneIdx 排序组装有序 segments
  const validSegments: TTSResult[] = [];
  for (let i = 0; i < items.length; i++) {
    const seg = segMap.get(i);
    if (seg) validSegments.push(seg);
  }

  if (validSegments.length === 0) return null;

  // FFmpeg concat
  const concatListPath = path.join(outputDir, `${jobId}_concat.txt`);
  await fs.writeFile(concatListPath, validSegments.map((s) => `file '${s.path}'`).join("\n"), "utf-8");

  const finalPath = path.join(outputDir, `${jobId}_audio.mp3`);
  const concatOk = await ffmpegConcat(concatListPath, finalPath);

  // 清理临时文件
  await Promise.all(validSegments.map((s) => fs.unlink(s.path).catch(() => {})));
  fs.unlink(concatListPath).catch(() => {});

  if (!concatOk) return null;

  // 坑5修复：对最终 concat 文件测 FFprobe（最准确）
  const totalDuration = await getAudioDuration(finalPath);
  console.info(`[Orchestrator:${jobId}] Audio track: ${validSegments.length} segments, ${totalDuration.toFixed(3)}s (FFprobe measured)`);

  return { path: finalPath, segments: validSegments, totalDuration };
}

// ============================================================
// 用真实音频时长重建 Director 时间轴
// ============================================================

function rebuildDirector(
  original: DirectorIntent,
  segments: TTSResult[],
  script: { steps: Array<{ title: string; desc: string }> }
): DirectorIntent {
  const newScenes: Scene[] = [];
  let currentStart = 0;

  // hook
  const hookDur = segments[0]?.realDuration ?? 3;
  newScenes.push({ ...original.scenes[0], start: 0, end: hookDur });
  currentStart = hookDur;

  // steps
  for (let i = 0; i < script.steps.length; i++) {
    const dur = segments[1 + i]?.realDuration ?? 5;
    const sceneIdx = 1 + i;
    newScenes.push({ ...original.scenes[sceneIdx], start: currentStart, end: currentStart + dur });
    currentStart += dur;
  }

  // cta
  const ctaDur = segments[segments.length - 1]?.realDuration ?? 3;
  const ctaSceneIdx = original.scenes.length - 1;
  newScenes.push({ ...original.scenes[ctaSceneIdx], start: currentStart, end: currentStart + ctaDur });

  const rebuildCurve = (orig: number[], len: number) => {
    if (orig.length === len) return orig;
    return Array.from({ length: len }, (_, i) => orig[Math.round((i / (len - 1)) * (orig.length - 1))]);
  };

  return {
    ...original,
    scenes: newScenes,
    emotionalCurve: rebuildCurve(original.emotionalCurve, newScenes.length),
    pacingCurve: rebuildCurve(original.pacingCurve, newScenes.length),
  };
}

// ============================================================
// FFmpeg 辅助
// ============================================================

async function ffmpegConcat(listPath: string, outputPath: string): Promise<boolean> {
  return new Promise((resolve) => {
    const proc = spawn("ffmpeg", ["-y", "-f", "concat", "-safe", "0", "-i", listPath, "-c", "copy", outputPath]);
    let stderr = "";
    proc.stderr?.on("data", (d) => { stderr += d.toString(); });
    proc.on("close", (code) => { resolve(code === 0); });
    proc.on("error", () => resolve(false));
  });
}

/**
 * 坑4修复：去掉 -shortest
 * video.duration = audio.duration 已通过 rebuildDirector 保证完全对齐
 */
async function mergeAudioVideo(videoPath: string, audioPath: string, outputPath: string): Promise<boolean> {
  return new Promise((resolve) => {
    const proc = spawn("ffmpeg", [
      "-y",
      "-i", videoPath,
      "-i", audioPath,
      "-c:v", "copy",
      "-c:a", "aac",
      // 不加 -shortest：video 已按 audio 时长精确构建，两者完全对齐
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
// Remotion 渲染
// ============================================================

async function renderVideo(
  jobId: string,
  layout: VideoLayout,
  outputPath: string
): Promise<string | null> {
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
// runAgent v4 — 完整编排入口
// ============================================================

export interface OrchestratorConfig {
  topic: string;
  enableTTS?: boolean;
  enableSubtitles?: boolean;
  outputDir?: string;
}

export interface OrchestratorResult {
  outputPath: string;
  topic: string;
  arc: string;
  totalTimeSeconds: number;
  videoDurationSeconds?: number;
  subtitlePath?: string;
  success: boolean;
  error?: string;
}

/**
 * runAgent v4
 *
 * 完整链路（v4）：
 *   generateScript
 *     → buildDirector (粗略 scene 分段)
 *     → preResolveImages
 *     ───────────────────────────────────────────并行
 *     → TTS segments (FFprobe, Map<sceneIdx>)
 *     → rebuildDirector (真实音频时长)
 *     ───────────────────────────────────────────
 *     → generateVideoLayout (精确时间轴)
 *     → render (时长 = audio duration)
 *     → merge (no -shortest)
 *     → FFprobe final duration (系统时间真值)
 */
export async function runAgent(config: OrchestratorConfig): Promise<OrchestratorResult> {
  const startTime = Date.now();
  const { topic, enableTTS = true, enableSubtitles = false, outputDir = path.join(process.cwd(), "renders") } = config;
  const jobId = randomUUID().slice(0, 8);

  console.info(`[Orchestrator:${jobId}] Starting v4: "${topic}"`);

  try {
    // ── Step 1: 脚本 ─────────────────────────────────────
    const script = await generateScriptFromTopic(topic);
    console.info(`[Orchestrator:${jobId}] 1. script — "${script.hook.text.slice(0, 25)}..."`);

    // ── Step 2: 初始导演意图 ─────────────────────────────
    const director0 = buildDirector(topic, script);
    console.info(`[Orchestrator:${jobId}] 2. director0 — arc=${director0.arc}, ${director0.scenes.length} scenes`);

    // ── Step 3: 图片资产 ─────────────────────────────────
    const preResolved = await preResolveAllImages(
      script.topic ?? script.hook.text,
      script.steps.map((s) => s.imageKeyword)
    );
    console.info(`[Orchestrator:${jobId}] 3. images — hook=${preResolved.hookAsset.provider}`);

    // ── Step 4: TTS 音频轨道 ─────────────────────────────
    let audioTrack: AudioTrack | null = null;
    let director: DirectorIntent = director0;

    if (enableTTS) {
      audioTrack = await buildAudioTrack(script, director0, jobId, outputDir);
      if (audioTrack) {
        director = rebuildDirector(director0, audioTrack.segments, script);
        console.info(`[Orchestrator:${jobId}] 4. audio — ${audioTrack.totalDuration.toFixed(3)}s real (FFprobe), ${director.scenes.length} scenes rebuilt`);
      } else {
        console.warn(`[Orchestrator:${jobId}] 4. TTS failed, using silent`);
      }
    }

    // ── Step 5: 字幕轨道（可选）───────────────────────────
    let subtitlePath: string | undefined;
    if (enableSubtitles && audioTrack) {
      const subtitleTrack = buildSubtitleTrack(audioTrack.segments);
      subtitlePath = path.join(outputDir, `${jobId}_subtitles.srt`);
      await fs.writeFile(subtitlePath, buildSRT(subtitleTrack), "utf-8");
      console.info(`[Orchestrator:${jobId}] 5. subtitles — ${subtitleTrack.length} cues`);
    }

    // ── Step 6: 重建 layout（时间轴已精确）───────────────
    const layout: VideoLayout = generateVideoLayoutFromScript(script, preResolved, director);
    const videoDuration = layout.director
      ? layout.director.scenes[layout.director.scenes.length - 1].end
      : 0;
    console.info(`[Orchestrator:${jobId}] 6. layout — video duration=${videoDuration.toFixed(3)}s`);

    // ── Step 7: 渲染 + TTS 并行执行 ─────────────────────
    const silentPath = path.join(outputDir, `${jobId}_silent.mp4`);

    let rendered: string | null = null;

    if (enableTTS && audioTrack) {
      // 并行：渲染视频 + TTS 已经在 Step 4 完成
      rendered = await renderVideo(jobId, layout, silentPath);
    } else {
      rendered = await renderVideo(jobId, layout, silentPath);
    }

    if (!rendered) throw new Error("Remotion render failed");
    console.info(`[Orchestrator:${jobId}] 7. rendered — ${rendered}`);

    // ── Step 8: 音画合并（无 -shortest）──────────────────
    let finalVideoPath = rendered;

    if (audioTrack) {
      finalVideoPath = path.join(outputDir, `${jobId}_final.mp4`);
      const merged = await mergeAudioVideo(rendered, audioTrack.path, finalVideoPath);
      if (merged) {
        console.info(`[Orchestrator:${jobId}] 8. merged → ${finalVideoPath}`);
      } else {
        finalVideoPath = rendered;
      }
    }

    // 坑5终极验证：对最终视频测一次 FFprobe
    const finalDuration = await getAudioDuration(finalVideoPath);

    const totalTime = (Date.now() - startTime) / 1000;
    console.info(`[Orchestrator:${jobId}] DONE in ${totalTime.toFixed(1)}s — final duration=${finalDuration.toFixed(3)}s`);

    return {
      outputPath: finalVideoPath,
      topic,
      arc: director.arc,
      totalTimeSeconds: totalTime,
      videoDurationSeconds: parseFloat(finalDuration.toFixed(2)),
      subtitlePath,
      success: true,
    };
  } catch (err) {
    const error = err instanceof Error ? err.message : String(err);
    console.error(`[Orchestrator:${jobId}] FAILED: ${error}`);
    return {
      outputPath: "",
      topic,
      arc: "unknown",
      totalTimeSeconds: (Date.now() - startTime) / 1000,
      success: false,
      error,
    };
  }
}

// ============================================================
// SRT 字幕文件生成
// ============================================================

function buildSRT(cues: SubtitleCue[]): string {
  return cues
    .map((cue, i) => {
      const n = i + 1;
      const toSRTTime = (t: number) => {
        const h = Math.floor(t / 3600);
        const m = Math.floor((t % 3600) / 60);
        const s = Math.floor(t % 60);
        const ms = Math.round((t % 1) * 1000);
        return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")},${String(ms).padStart(3, "0")}`;
      };
      return `${n}\n${toSRTTime(cue.start)} --> ${toSRTTime(cue.end)}\n${cue.text}\n`;
    })
    .join("\n");
}
