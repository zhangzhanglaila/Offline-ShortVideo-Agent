/**
 * Remotion Render Server - Static Bundle Approach
 */
import express from "express";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import fs from "node:fs/promises";
import { createServer } from "node:http";
import { WebSocketServer, WebSocket } from "ws";
import { getJobQueue } from "./queue-sqlite.js";
import { getRenderPool } from "./render-pool.js";

import { selectComposition, getCompositions } from "@remotion/renderer";
import { generateLayoutFromTopic as generateLayoutFromTopicRule, generateMiniLayout } from "./generator";
import { generateLayoutFromTopic as generateLayoutFromTopicLLM, generateVideoLayoutFromTopic } from "./llm";
import { controlHub } from "./controlHub.js";
import type { ControlParams } from "./controlHub.js";
import { mctsConfig } from "./mctsConfig.js";
import { mctsStatsStore } from "./mctsStatsStore.js";
import type { TimelineLayout } from "@remotion/types";
import type { VideoLayout } from "@remotion/types";

// ============================================================
// 统一入参类型（只在边界做一次 Record<string, unknown> 转换）
// ============================================================
interface RenderInput {
  timeline?: TimelineLayout;
  video?: VideoLayout;
}

function toUnknownProps(input: RenderInput): Record<string, unknown> {
  return input as unknown as Record<string, unknown>;
}

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(__dirname, "..");
const BUNDLE_DIR = path.join(SERVER_DIR, "build");
const RENDER_DIR = path.join(SERVER_DIR, "renders");
const PUBLIC_DIR = path.join(SERVER_DIR, "public");

console.info("[Server] Directories:", { SERVER_DIR, BUNDLE_DIR, RENDER_DIR, PUBLIC_DIR });

// Ensure directories exist
fs.mkdir(RENDER_DIR, { recursive: true }).catch(console.error);

/* ======================== Job State ======================== */

type JobStatus =
  | { status: "pending"; layout: VideoLayout | TimelineLayout }
  | { status: "rendering"; progress: number; outputPath: string }
  | { status: "completed"; outputPath: string; downloadUrl: string }
  | { status: "failed"; error: string };

const jobs = new Map<string, JobStatus>();

/* ======================== Static Bundle URL ======================== */

const PORT = Number(process.env.PORT) || 3333;
const SERVE_URL = `http://localhost:${PORT}`;

// Pass serveUrl to all renders so compositions can construct full asset URLs
const RENDER_OPTS = {
  serveUrl: SERVE_URL,
};

/* ======================== Rendering ======================== */

async function renderComposition(
  jobId: string,
  layout: TimelineLayout,
  port: number
): Promise<void> {
  const outputPath = path.join(RENDER_DIR, `${jobId}.mp4`);

  try {
    jobs.set(jobId, { status: "rendering", progress: 0, outputPath });

    console.info(`[render] ${jobId} DEBUG: calling selectComposition with id=TimelineFlow, serveUrl=${SERVE_URL}`);

    const composition = await selectComposition({
      serveUrl: SERVE_URL,
      id: "TimelineFlow",
      // 唯一边界转换点
      inputProps: toUnknownProps({ timeline: layout }),
    });

    console.info(`[render] ${jobId} DEBUG: selectComposition result = ${JSON.stringify(composition ? { id: composition.id, duration: composition.durationInFrames } : null)}`);

    if (!composition) {
      throw new Error(
        `Composition "CinematicTest" not found. Check that registerRoot() is called in your entry point.`
      );
    }

    const lastBoxEnd = (layout.boxes || []).reduce(
      (max: number, box: { showFrom: number; durationInFrames: number }) =>
        Math.max(max, box.showFrom + box.durationInFrames),
      0
    );
    const durationInFrames = Math.max(lastBoxEnd + 90, 300);

    console.info(`[render] Starting render for ${jobId}...`);
    console.info(`[render] Duration: ${durationInFrames} frames`);
    console.info(`[render] ${jobId} DEBUG: calling renderMedia, composition.id=${composition.id}`);

    // Add timeout wrapper
    const timeoutMs = 60000;
    const renderPromise = renderMedia({
      serveUrl: SERVE_URL,
      composition: {
        ...composition,
        durationInFrames,
        fps: 30,
        props: toUnknownProps({ timeline: layout }),
      },
      inputProps: toUnknownProps({ timeline: layout }),
      codec: "h264",
      outputLocation: outputPath,
      onProgress: (progress: { progress: number }) => {
        const pct = (progress.progress * 100).toFixed(1);
        console.info(`[render] ${jobId}: ${pct}%`);
        jobs.set(jobId, { status: "rendering", progress: progress.progress, outputPath });
      },
    });

    console.info(`[render] ${jobId} DEBUG: renderMedia called, waiting...`);

    const timeoutPromise = new Promise((_, reject) => {
      setTimeout(() => reject(new Error(`TIMEOUT after ${timeoutMs}ms`)), timeoutMs);
    });

    await Promise.race([renderPromise, timeoutPromise]);
    console.info(`[render] ${jobId} DEBUG: renderMedia completed`);

    jobs.set(jobId, {
      status: "completed",
      outputPath,
      downloadUrl: `http://localhost:${port}/renders/${jobId}.mp4`,
    });

    console.info(`[render] ${jobId} COMPLETE: ${outputPath}`);
  } catch (err) {
    const error = err instanceof Error ? err.message : String(err);
    console.error(`[render] ${jobId} FAILED:`, error);
    jobs.set(jobId, { status: "failed", error });
  }
}

// VideoFlow 渲染（V6 新元素系统）
async function renderVideoComposition(
  jobId: string,
  layout: VideoLayout,
  port: number
): Promise<void> {
  const outputPath = path.join(RENDER_DIR, `${jobId}.mp4`);

  try {
    jobs.set(jobId, { status: "rendering", progress: 0, outputPath });

    const composition = await selectComposition({
      serveUrl: SERVE_URL,
      id: "VideoFlow",
      inputProps: toUnknownProps({ video: layout }),
    });

    if (!composition) {
      throw new Error(`Composition "VideoFlow" not found.`);
    }

    // 从 elements 计算总时长
    const lastEnd = (layout.elements || []).reduce(
      (max: number, el: { start: number; duration: number }) =>
        Math.max(max, el.start + el.duration),
      0
    );
    const durationInFrames = Math.max(lastEnd + 60, 300);

    console.info(`[render:v2] ${jobId} VideoFlow render, duration=${durationInFrames} frames`);

    const timeoutMs = 120000;
    const renderPromise = renderMedia({
      serveUrl: SERVE_URL,
      composition: { ...composition, durationInFrames, fps: 30, props: toUnknownProps({ video: layout }) },
      inputProps: toUnknownProps({ video: layout }),
      codec: "h264",
      outputLocation: outputPath,
      onProgress: (progress: { progress: number }) => {
        const pct = (progress.progress * 100).toFixed(1);
        console.info(`[render:v2] ${jobId}: ${pct}%`);
        jobs.set(jobId, { status: "rendering", progress: progress.progress, outputPath });
      },
    });

    const timeoutPromise = new Promise<never>((_, reject) => {
      setTimeout(() => reject(new Error(`TIMEOUT after ${timeoutMs}ms`)), timeoutMs);
    });

    await Promise.race([renderPromise, timeoutPromise]);

    jobs.set(jobId, {
      status: "completed",
      outputPath,
      downloadUrl: `http://localhost:${port}/renders/${jobId}.mp4`,
    });

    console.info(`[render:v2] ${jobId} COMPLETE: ${outputPath}`);
  } catch (err) {
    const error = err instanceof Error ? err.message : String(err);
    console.error(`[render:v2] ${jobId} FAILED:`, error);
    jobs.set(jobId, { status: "failed", error });
  }
}

/* ======================== Express App ======================== */

const app = express();

app.get("/test", (req, res) => {
  console.info("[Test] Route hit!");
  res.json({ ok: true });
});

// Debug route to check static files
app.get("/debug/static", (req, res) => {
  const fs = require('fs');
  const files = fs.readdirSync(BUNDLE_DIR);
  res.json({ BUNDLE_DIR, files: files.slice(0, 10) });
});

// Debug route to list all available compositions
app.get("/debug/compositions", async (_req, res) => {
  try {
    const compositions = await getCompositions(SERVE_URL);
    res.json({ compositions: compositions.map(c => ({ id: c.id, width: c.width, height: c.height })) });
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
});

// Serve static bundle files at root
app.use(express.static(BUNDLE_DIR));

// Serve public directory at / (Remotion's staticFile() references /static/... from public/)
app.use(express.static(PUBLIC_DIR));

// Serve rendered videos
app.use("/renders", express.static(RENDER_DIR));

app.use(express.json({ limit: "50mb" }));

const serverPORT = Number(process.env.PORT) || 3333;

/**
 * POST /generate-video
 * 从主题直接生成并渲染视频（V5 AI导演接口）
 *
 * body: { topic: string, mini?: boolean }
 * - topic: 视频主题，如 "AI副业赚钱"
 * - mini: true = 迷你测试（3步），false = 完整版（5步+CTA）
 */
app.post("/generate-video", async (req, res) => {
  const { topic, mini } = req.body as { topic?: string; mini?: boolean };

  if (!topic || typeof topic !== "string") {
    res.status(400).json({ error: "Missing or invalid 'topic' string" });
    return;
  }

  // 生成布局（优先 LLM，fallback 规则）
  const layout = mini
    ? generateMiniLayout(topic, 3)
    : await generateLayoutFromTopicLLM(topic);

  const jobId = randomUUID();
  jobs.set(jobId, { status: "pending", layout });

  console.info(`[server] generate-video job: ${jobId}, topic: "${topic}" (mini=${mini})`);
  console.info(`[server]   layout: ${layout.boxes.length} boxes, ${layout.arrows.length} arrows, hook="${(layout.boxes[0]?.label ?? "").slice(0, 40)}"`);

  enqueueRender(jobId, layout, "TimelineFlow");

  res.json({ jobId, topic, layout });
});

/**
 * 验证并兜底图片 URL
 * - HEAD 请求检查图片是否可达
 * - 不可达则替换为与背景渐变色一致的纯色 rect 元素
 */
async function validateImageUrls(layout: VideoLayout): Promise<VideoLayout> {
  const bgEl = layout.elements.find(e => e.type === "background");
  const fallbackColor = (bgEl as {gradient?: string})?.gradient
    ? "#1a2a3a"
    : "#0f2027";

  const imageEls = layout.elements.filter(e => e.type === "image");
  const results = await Promise.all(
    imageEls.map(async (el) => {
      try {
        const src = (el as {src?: string}).src;
        if (!src) return { el, ok: false };
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 3000);
        const res = await fetch(src, { method: "HEAD", signal: controller.signal });
        clearTimeout(timeout);
        return { el, ok: res.ok };
      } catch {
        return { el, ok: false };
      }
    })
  );

  const failedCount = results.filter(r => !r.ok).length;
  if (failedCount > 0) {
    console.info(`[image-validate] ${failedCount}/${imageEls.length} images failed, replacing with gradient`);
  }

  // 替换失败的图片元素为渐变色 shape
  const failedElements = results.filter(r => !r.ok).map(r => r.el);
  const elements = layout.elements.filter(e => !failedElements.includes(e));
  failedElements.forEach(el => {
    const id = (el as {id?: string}).id || "img";
    const x = (el as {x?: number}).x ?? 0;
    const y = (el as {y?: number}).y ?? 0;
    const w = (el as {width?: number}).width ?? 1080;
    const h = (el as {height?: number}).height ?? 600;
    const start = (el as {start?: number}).start ?? 0;
    const dur = (el as {duration?: number}).duration ?? 100;
    const z = (el as {zIndex?: number}).zIndex ?? 1;
    elements.push({
      id: id + "_fallback",
      type: "shape",
      shape: "rect",
      x, y, width: w, height: h,
      color: fallbackColor,
      fillColor: fallbackColor,
      borderRadius: 0,
      start,
      duration: dur,
      zIndex: z,
      animation: el.animation || { enter: "fade", duration: 20 },
    });
  });

  return { ...layout, elements };
}

// ── Render Pool（Worker Pool 调度）───────────────────────

/**
 * 通过 Worker Pool 调度渲染任务
 * 替代原来的 spawn-per-job 模式，避免高并发时资源爆炸
 */
/**
 * 通过 SQLite 持久化队列调度渲染任务
 * job 写入 SQLite，worker pool 消费队列（poll 模式）
 * server 重启不丢任务，worker 崩溃 job 回到 waiting 状态
 */
async function enqueueRender(jobId: string, layout: VideoLayout | TimelineLayout, compositionId: string): Promise<void> {
  const queue = getJobQueue();
  await queue.add("render", {
    jobId,
    layout: layout as Record<string, unknown>,
    compositionId,
    port: serverPORT,
  }, {
    attempts: 3,
    backoff: { type: "exponential", delay: 5000 },
    jobId,
  });
  jobs.set(jobId, { status: "rendering", progress: 0, outputPath: "" });
  console.info(`[server] enqueued job ${jobId} (queue: ${JSON.stringify(queue.getStats())})`);
}

/**
 * POST /generate-video/v2
 * V6 视频级渲染（elements[] 系统）
 * 从主题生成 VideoLayout → render VideoFlow
 */
app.post("/generate-video/v2", async (req, res) => {
  const { topic } = req.body as { topic?: string };

  if (!topic || typeof topic !== "string") {
    res.status(400).json({ error: "Missing or invalid 'topic' string" });
    return;
  }

  // LLM 生成 VideoLayout
  const layout = await generateVideoLayoutFromTopic(topic);

  // 图片兜底验证（防止 CDN 不可达导致渲染崩溃）
  const safeLayout = await validateImageUrls(layout);

  const jobId = randomUUID();
  jobs.set(jobId, { status: "pending", layout: safeLayout });

  const firstText = safeLayout.elements.find(e => e.type === "text");
  console.info(`[server:v2] generate-video/v2 job: ${jobId}, topic: "${topic}"`);
  console.info(`[server:v2]   elements: ${safeLayout.elements.length}, hook="${((firstText as {text?: string})?.text ?? "").slice(0, 40)}"`);

  enqueueRender(jobId, safeLayout, "VideoFlow");

  res.json({ jobId, topic, layout: safeLayout });
});

/**
 * POST /render
 * Start a new render job.
 */
app.post("/render", async (req, res) => {
  const { layout } = req.body as { layout?: TimelineLayout };

  if (!layout) {
    res.status(400).json({ error: "Missing 'layout' in request body" });
    return;
  }

  if (!layout.boxes || layout.boxes.length === 0) {
    res.status(400).json({ error: "layout.boxes array cannot be empty" });
    return;
  }

  const jobId = randomUUID();
  jobs.set(jobId, { status: "pending", layout });

  console.info(`[server] New render job: ${jobId}`);
  console.info(`[server]   background: ${layout.backgroundImage}`);
  console.info(`[server]   boxes: ${layout.boxes.length}`);

  enqueueRender(jobId, layout, "TimelineFlow");

  res.json({ jobId });
});

/**
 * GET /status/:jobId
 * Get job status.
 */
app.get("/status/:jobId", (req, res) => {
  const { jobId } = req.params;
  const job = jobs.get(jobId);

  if (!job) {
    res.status(404).json({ error: "Job not found" });
    return;
  }

  const clientJob = {
    status: job.status,
    progress:
      job.status === "rendering" ? (job as { progress: number }).progress : 1,
    downloadUrl:
      job.status === "completed"
        ? (job as { downloadUrl: string }).downloadUrl
        : undefined,
    error: job.status === "failed" ? (job as { error: string }).error : undefined,
  };

  res.json(clientJob);
});

/**
 * GET /health
 * Health check.
 */
app.get("/health", (_req, res) => {
  res.json({ ok: true, jobs: jobs.size });
});

/* ======================== WebSocket Server ======================== */
const httpServer = createServer(app);
const wss = new WebSocketServer({ server: httpServer });

wss.on("connection", (ws) => {
  // Send current control params on connect
  ws.send(JSON.stringify({ type: "control_state", ...controlHub.get() }));

  ws.on("message", (raw) => {
    try {
      const msg = JSON.parse(raw.toString());
      if (msg.type === "control_update") {
        const { E_bias, Pi_temp, J_noise, stylePreset, intensity } = msg.params ?? {};
        const patch: Record<string, unknown> = {};
        if (E_bias !== undefined) patch.E_bias = E_bias;
        if (Pi_temp !== undefined) patch.Pi_temp = Pi_temp;
        if (J_noise !== undefined) patch.J_noise = J_noise;
        if (stylePreset !== undefined) patch.stylePreset = stylePreset;
        if (intensity !== undefined) patch.intensity = intensity;
        if (Object.keys(patch).length > 0) {
          controlHub.set(patch);
          // Sync to mctsConfig so it takes effect in next MCTS run
          mctsConfig.set(patch);
          // Broadcast updated params to all clients
          const state = controlHub.get();
          const broadcast = JSON.stringify({ type: "control_state", ...state });
          for (const client of wss.clients) {
            if (client.readyState === WebSocket.OPEN) client.send(broadcast);
          }
        }
      }
      if (msg.type === "control_reset") {
        controlHub.reset();
        mctsConfig.reset();
        const state = controlHub.get();
        const broadcast = JSON.stringify({ type: "control_state", ...state });
        for (const client of wss.clients) {
          if (client.readyState === WebSocket.OPEN) client.send(broadcast);
        }
      }
    } catch { /* ignore malformed */ }
  });
});

/* ======================== Stats Endpoint ======================== */

/**
 * GET /stats/latest
 * Returns the most recent MCTS (π, E, J) observation stats
 */
app.get("/stats/latest", (_req, res) => {
  const stats = mctsStatsStore.get();
  if (!stats) {
    res.status(204).send();
    return;
  }
  res.json(stats);
});

/* ======================== Trace / Event Sourcing ======================== */

/**
 * GET /trace/:graphId
 * Returns the full event trace for a DAG execution graph.
 * Used for replay and visualization.
 */
app.get("/trace/:graphId", async (req, res) => {
  const { graphId } = req.params;
  const queue = getJobQueue();
  const trace = await queue.getTrace(graphId);
  if (!trace.events.length && !trace.jobs.length) {
    res.status(404).json({ error: "Trace not found" });
    return;
  }
  res.json(trace);
});

/* ======================== Control Endpoints ======================== */

/**
 * GET /control
 * Returns current (π, E, J) control params
 */
app.get("/control", (_req, res) => {
  res.json(controlHub.get());
});

/**
 * POST /control
 * Update (π, E, J) control params
 * Body: { E_bias?: number, Pi_temp?: number, J_noise?: number }
 */
app.post("/control", (req, res) => {
  const { E_bias, Pi_temp, J_noise } = req.body as Partial<ControlParams>;
  const patch: Partial<ControlParams> = {};
  if (E_bias !== undefined) patch.E_bias = E_bias;
  if (Pi_temp !== undefined) patch.Pi_temp = Pi_temp;
  if (J_noise !== undefined) patch.J_noise = J_noise;
  if (Object.keys(patch).length === 0) {
    res.status(400).json({ error: "At least one param required" });
    return;
  }
  controlHub.set(patch);
  mctsConfig.set(patch);
  res.json(controlHub.get());
});

/* ======================== Start Server ======================== */

httpServer.listen(serverPORT, () => {
  console.info(`Remotion render server running on http://localhost:${serverPORT}`);
  console.info(`[ControlHub] WebSocket ready on ws://localhost:${serverPORT}`);
  console.info(`[ControlHub] Endpoints: GET/POST /control`);
  // Worker pool + watchdog start lazily on first use
  getRenderPool();
  getJobQueue();
  console.info(`[RenderPool] Persistent workers initialized`);
});

export { app };
