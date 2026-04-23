/**
 * render-worker.ts
 * 独立渲染进程，通过 stdin 接收任务，stdout 上报进度
 * 主进程崩溃不影响 worker 完成
 */
import { renderMedia, selectComposition } from "@remotion/renderer";
import type { VideoLayout } from "@remotion/types";
import { WebSocketServer } from "ws";
import http from "node:http";
import express from "express";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:http";
import fs from "node:fs/promises";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(__dirname, "..");
const BUNDLE_DIR = path.join(SERVER_DIR, "build");
const RENDER_DIR = path.join(SERVER_DIR, "renders");
const SERVE_URL = `http://localhost:${process.env.PORT || 3333}`;

interface RenderJob {
  jobId: string;
  layout: VideoLayout;
  port: number;
  compositionId?: string; // "VideoFlow" | "TimelineFlow"
}

function toUnknownProps(input: Record<string, unknown>): Record<string, unknown> {
  return input as unknown as Record<string, unknown>;
}

function log(msg: string) {
  console.log(`[worker] ${msg}`);
}

function sendMsg(type: string, data: Record<string, unknown> = {}) {
  process.stdout.write(JSON.stringify({ type, ...data }) + "\n");
}

async function renderJob(job: RenderJob): Promise<void> {
  const { jobId, layout, port, compositionId = "VideoFlow" } = job;
  const outputPath = path.join(RENDER_DIR, `${jobId}.mp4`);

  log(`Starting render: ${jobId} (composition=${compositionId})`);

  try {
    // Ensure render dir exists
    await fs.mkdir(RENDER_DIR, { recursive: true });

    const composition = await selectComposition({
      serveUrl: SERVE_URL,
      id: compositionId,
      inputProps: toUnknownProps({ video: layout }),
      chromiumOptions: { gl: "swiftshader" },
    });

    if (!composition) throw new Error(`Composition "${compositionId}" not found`);

    // Support both VideoFlow (elements[]) and TimelineFlow (boxes[])
    const elements = layout.elements as Array<{ start: number; duration: number }> | undefined;
    const boxes = layout.boxes as Array<{ showFrom: number; durationInFrames: number }> | undefined;

    let durationInFrames: number;
    if (elements) {
      const lastEnd = elements.reduce((max, el) => Math.max(max, el.start + el.duration), 0);
      durationInFrames = Math.max(lastEnd + 60, 300);
    } else if (boxes) {
      const lastEnd = boxes.reduce((max, b) => Math.max(max, b.showFrom + b.durationInFrames), 0);
      durationInFrames = Math.max(lastEnd + 90, 300);
    } else {
      durationInFrames = 300;
    }

    log(`Duration: ${durationInFrames} frames`);

    const timeoutMs = 300000; // 5 min for worker
    let settled = false;

    const renderPromise = renderMedia({
      serveUrl: SERVE_URL,
      composition: { ...composition, durationInFrames, fps: 30, props: toUnknownProps({ video: layout }) },
      inputProps: toUnknownProps({ video: layout }),
      codec: "h264",
      outputLocation: outputPath,
      chromiumOptions: {
        gl: "swiftshader", // force software rendering (no GPU needed)
      },
      onProgress: (progress: { progress: number }) => {
        if (!settled) {
          settled = true;
          sendMsg("progress", { jobId, progress: progress.progress, outputPath });
        }
      },
    });

    const timeoutPromise = new Promise<never>((_, reject) => {
      setTimeout(() => reject(new Error(`TIMEOUT after ${timeoutMs}ms`)), timeoutMs);
    });

    await Promise.race([renderPromise, timeoutPromise]);
    settled = true;

    sendMsg("complete", {
      jobId,
      outputPath,
      downloadUrl: `http://localhost:${port}/renders/${jobId}.mp4`,
    });
    log(`COMPLETE: ${outputPath}`);
    setTimeout(() => process.exit(0), 500); // flush stdout then exit
  } catch (err) {
    const error = err instanceof Error ? err.message : String(err);
    log(`FAILED: ${error}`);
    sendMsg("failed", { jobId, error });
    setTimeout(() => process.exit(1), 500);
  }
}

// stdin 接收任务
let activeJob: RenderJob | null = null;

process.stdin.on("data", async (chunk) => {
  try {
    const lines = chunk.toString().split("\n").filter(Boolean);
    for (const line of lines) {
      const msg = JSON.parse(line);
      if (msg.type === "render" && msg.job) {
        activeJob = msg.job as RenderJob;
        await renderJob(activeJob);
        activeJob = null;
      }
    }
  } catch (err) {
    const e = err instanceof Error ? err.message : String(err);
    log(`Parse error: ${e}`);
  }
});

process.stdin.on("end", () => {
  // Only exit if no job is running
  if (!activeJob) {
    log("stdin ended, no active job, exiting");
    process.exit(0);
  }
  // If job is running, let it finish (will exit after job completes)
});

log("Worker ready, waiting for jobs...");
