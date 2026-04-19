/**
 * Remotion Render Server - Static Bundle Approach
 */
import express from "express";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import fs from "node:fs/promises";

import { renderMedia, selectComposition, getCompositions } from "@remotion/renderer";

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
  | { status: "pending"; layout: object }
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
  layout: object,
  port: number
): Promise<void> {
  const outputPath = path.join(RENDER_DIR, `${jobId}.mp4`);

  try {
    jobs.set(jobId, { status: "rendering", progress: 0, outputPath });

    console.info(`[render] ${jobId} DEBUG: calling selectComposition with id=TimelineFlow, serveUrl=${SERVE_URL}`);

    const composition = await selectComposition({
      serveUrl: SERVE_URL,
      id: "TimelineFlow",
      inputProps: layout,
    });

    console.info(`[render] ${jobId} DEBUG: selectComposition result = ${JSON.stringify(composition ? { id: composition.id, duration: composition.durationInFrames } : null)}`);

    if (!composition) {
      throw new Error(
        `Composition "CinematicTest" not found. Check that registerRoot() is called in your entry point.`
      );
    }

    const layoutObj = layout as { boxes?: Array<{ showFrom: number; durationInFrames: number }> };
    const lastBoxEnd = (layoutObj.boxes || []).reduce(
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
        props: layout,
      },
      inputProps: layout,
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
 * POST /render
 * Start a new render job.
 */
app.post("/render", async (req, res) => {
  const { layout } = req.body as { layout?: object };

  if (!layout) {
    res.status(400).json({ error: "Missing 'layout' in request body" });
    return;
  }

  const layoutObj = layout as { backgroundImage?: string; boxes?: unknown[] };
  if (!layoutObj.boxes || (layoutObj.boxes as unknown[]).length === 0) {
    res.status(400).json({ error: "layout.boxes array cannot be empty" });
    return;
  }

  const jobId = randomUUID();
  jobs.set(jobId, { status: "pending", layout });

  console.info(`[server] New render job: ${jobId}`);
  console.info(`[server]   background: ${layoutObj.backgroundImage}`);
  console.info(`[server]   boxes: ${(layoutObj.boxes as unknown[]).length}`);

  renderComposition(jobId, layout, serverPORT).catch((err) => {
    console.error(`[server] renderComposition error for ${jobId}:`, err);
  });

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

app.listen(serverPORT, () => {
  console.info(`Remotion render server running on http://localhost:${serverPORT}`);
});

export { app };
