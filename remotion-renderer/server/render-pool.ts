/**
 * render-pool.ts - Persistent Worker Pool (Claim-Based)
 *
 * 架构原则（消除 state drift）：
 *   SQLite = 唯一 truth
 *   Worker 只有 job data（无本地状态）
 *   claimJob() = 原子 ownership，no duplicate renders
 *
 * Worker loop（每个 worker 独立循环）：
 *   while (true):
 *     job = await queue.claimJob(workerId)   ← 原子获取 ownership
 *     if (!job) sleep(pollInterval)
 *     else execute → complete | fail
 *
 * 关键设计：
 *   - 每个 job = 一个 render-worker.ts 子进程（进程隔离）
 *   - 子进程退出 → 自动 respawn（pool 透明替换 handle）
 *   - heartbeat 防止正常长任务被 watchdog 误杀
 *   - watchdog 回收 stuck jobs（worker crash 时）
 */
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import fs from "node:fs/promises";
import { getJobQueue } from "./queue-sqlite.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(__dirname, "..");
const RENDER_DIR = path.join(SERVER_DIR, "renders");
const PORT = Number(process.env.PORT) || 3333;

const CLAIM_POLL_INTERVAL = 1500;  // ms between claim attempts when queue empty
const WORKER_SPAWN_DELAY  = 3000;  // ms between spawning workers
const HEARTBEAT_INTERVAL = 30_000; // worker refreshes started_at every 30s

interface WorkerRef {
  id: number;
  pid: number;
  state: "idle" | "busy" | "dead";
  /** writable stdin of the live subprocess */
  stdin: ReturnType<typeof spawn>["stdin"] | null;
  /** alive stdout of the live subprocess */
  stdout: ReturnType<typeof spawn>["stdout"] | null;
  /** kill function for this subprocess */
  kill: () => void;
}

function log(msg: string) { console.log(`[pool] ${msg}`); }

function makeWorkerPath() { return path.join(__dirname, "render-worker.ts"); }

/** Sleep helper */
function sleep(ms: number) { return new Promise(r => setTimeout(r, ms)); }

// ─────────────────────────────────────────────────────────────────────────────

export class RenderPool {
  /** Pool slots — each slot may be dead or alive; respawn fills the same slot */
  private slots: Array<WorkerRef | null> = [];
  private poolSize: number;
  private serverPort: number;
  private initPromise: Promise<void>;
  private shuttingDown = false;

  constructor(poolSize = 2, serverPort = PORT) {
    this.poolSize = Math.min(Math.max(poolSize, 1), 4);
    this.serverPort = serverPort;
    this.initPromise = this.init();
  }

  private async init() {
    await fs.mkdir(RENDER_DIR, { recursive: true });
    log(`Spawning ${this.poolSize} workers...`);
    for (let i = 0; i < this.poolSize; i++) {
      this.slots[i] = this.spawnWorker(i);
      if (i < this.poolSize - 1) await sleep(WORKER_SPAWN_DELAY);
    }
    log(`All ${this.poolSize} workers ready`);
  }

  /**
   * Spawn a fresh subprocess and wrap it in a WorkerRef.
   * The slot index stays fixed; only the underlying proc changes on respawn.
   */
  private spawnWorker(id: number): WorkerRef {
    const proc = spawn(process.execPath, ["--import", "tsx/esm", makeWorkerPath()], {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, PORT: String(this.serverPort) },
      detached: false,
    });

    const ref: WorkerRef = {
      id,
      pid: proc.pid!,
      state: "idle",
      stdin: proc.stdin,
      stdout: proc.stdout,
      kill: () => proc.kill(),
    };

    proc.on("error", (err) => {
      log(`Worker ${id} error: ${err.message}`);
      ref.state = "dead";
    });

    proc.on("exit", (code) => {
      log(`Worker ${id} exited code=${code}`);
      ref.state = "dead";
      if (!this.shuttingDown) {
        // Replace this slot with a freshly-spawned worker after a delay
        setTimeout(() => {
          if (!this.shuttingDown) {
            this.slots[id] = this.spawnWorker(id);
          }
        }, WORKER_SPAWN_DELAY);
      }
    });

    log(`Worker ${id} spawned (pid=${proc.pid})`);
    return ref;
  }

  /** Get the current live ref for a slot (may be null if dead and not yet respawned) */
  private getRef(slot: number): WorkerRef | null {
    const ref = this.slots[slot];
    if (!ref || ref.state === "dead") return null;
    return ref;
  }

  // ── Worker loop ────────────────────────────────────────────────────────────

  start() {
    for (let i = 0; i < this.poolSize; i++) {
      this.workerLoop(i);
    }
  }

  private async workerLoop(slot: number) {
    const queue = getJobQueue();

    while (!this.shuttingDown) {
      // Wait for a live ref
      let ref = this.getRef(slot);
      while (!ref && !this.shuttingDown) {
        await sleep(CLAIM_POLL_INTERVAL);
        ref = this.getRef(slot);
      }
      if (this.shuttingDown) break;

      const workerId = `slot${slot}-pid${ref!.pid}`;

      try {
        // ── Claim (atomic) ────────────────────────────────────────
        const job = await queue.claimJob(workerId);
        if (!job) {
          await sleep(CLAIM_POLL_INTERVAL);
          continue;
        }

        ref!.state = "busy";
        const jobId = job.data.jobId;
        log(`Worker ${slot} claimed job ${jobId} (attempt ${job.attempts}/${job.maxAttempts})`);

        // ── Dispatch to render-worker.ts ────────────────────────
        const task = JSON.stringify({
          type: "render",
          job: {
            jobId: job.data.jobId,
            layout: job.data.layout,
            port: job.data.port,
            compositionId: job.data.compositionId,
          },
        });

        let settled = false;
        let heartbeatTimer: ReturnType<typeof setInterval> | null = null;

        // Heartbeat: refresh started_at every 30s so watchdog doesn't reclaim us
        heartbeatTimer = setInterval(() => {
          if (!settled) job.heartbeat().catch(() => {/* job may have completed */});
        }, HEARTBEAT_INTERVAL);

        const timeout = setTimeout(() => {
          if (!settled) {
            settled = true;
            clearInterval(heartbeatTimer!);
            log(`Worker ${slot} job ${jobId} timeout`);
            job.fail("Render timeout (300s)").catch(console.error);
          }
        }, 300_000);

        // Route stdout messages from the subprocess
        const listener = (chunk: Buffer) => {
          const lines = chunk.toString().split("\n").filter(Boolean);
          for (const line of lines) {
            try {
              const msg = JSON.parse(line);
              if (msg.jobId !== jobId) continue;
              if (settled) return;
              settled = true;
              clearInterval(heartbeatTimer!);
              clearTimeout(timeout);

              if (msg.type === "complete") {
                log(`Worker ${slot} job ${jobId} complete`);
                job.complete({ outputPath: msg.outputPath as string, downloadUrl: msg.downloadUrl as string }).catch(console.error);
              } else if (msg.type === "failed") {
                log(`Worker ${slot} job ${jobId} failed: ${msg.error}`);
                job.fail(msg.error as string).catch(console.error);
              }
            } catch { /* ignore non-JSON */ }
          }
        };

        ref!.stdout?.on("data", listener);

        // Send task; render-worker.ts stdin is single-use (process exits after job)
        ref!.stdin?.write(task);
        ref!.stdin?.end();

        // Wait for settle
        await new Promise<void>((resolve) => {
          const check = setInterval(() => {
            if (settled) {
              clearInterval(check);
              ref!.stdout?.removeListener("data", listener);
              resolve();
            }
          }, 200);
        });

        // Job is done; this slot's subprocess will exit and respawn automatically.
        // Mark slot idle; on next iteration getRef() may return the new proc.
        ref!.state = "idle";

      } catch (err) {
        const e = err instanceof Error ? err.message : String(err);
        console.error(`[pool] Worker ${slot} loop error: ${e}`);
        await sleep(5000);
      }
    }
  }

  // ── Stats ─────────────────────────────────────────────────────────────────

  getStats() {
    const queue = getJobQueue();
    return {
      poolSize: this.poolSize,
      busy:  this.slots.filter(s => s && s.state === "busy").length,
      idle:  this.slots.filter(s => s && s.state === "idle").length,
      dead:  this.slots.filter(s => !s || s.state === "dead").length,
      queue: queue.getStats(),
    };
  }

  async shutdown() {
    this.shuttingDown = true;
    for (const ref of this.slots) { ref?.kill(); }
    this.slots = [];
    log("Pool shut down");
  }
}

let _pool: RenderPool | null = null;

export function getRenderPool(): RenderPool {
  if (!_pool) {
    _pool = new RenderPool(2, PORT);
    _pool.start();   // begin claim loops immediately
  }
  return _pool;
}
