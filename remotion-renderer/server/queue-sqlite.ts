/**
 * queue-sqlite.ts - Durable Job Queue with DAG Scheduling + Event Sourcing
 *
 * 核心 primitive：
 *   queue.add(name, data, opts)              — 创建 job + 写 CREATED event
 *   queue.addGraph(name, nodes)              — 创建带依赖的 DAG + 写 CREATED events
 *   queue.claimJob(workerId)                 — 原子 claim + 写 CLAIMED event
 *   queue.getJob(id)                         — 读 job 状态
 *   queue.getTrace(graphId)                  — 获取 execution trace（时间轴事件流）
 *   job.heartbeat()                          — 写 HEARTBEAT event
 *   job.complete(result)                      — 写 COMPLETED event + 触发下游 UNBLOCKED
 *   job.fail(error, doRetry, poisonPill)     — 写 RETRY / FAILED / POISON_PILL events
 *
 * Event stream model（JobEvent）：
 *   每一行 = 一个不可变事实（append-only log）
 *   可以 replay：按 ts 排序 → 重现整个执行时间轴
 *   可视化：event type → 颜色映射 + 动画规则
 *
 * 状态机（扩展后）：
 *   blocked → waiting  (上游全部 completed → UNBLOCKED event)
 *   waiting → active   (CLAIMED event)
 *   active  → completed (COMPLETED event)
 *   active  → waiting   (RETRY event, backoff)
 *   active  → failed   (FAILED event)
 *   blocked → failed  (POISON_PILL event)
 */
import Database from "better-sqlite3";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { randomUUID } from "node:crypto";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVER_DIR = path.resolve(__dirname, "..");
const DB_PATH = path.join(SERVER_DIR, "queue.db");

let _db: Database.Database | null = null;

function getDb(): Database.Database {
  if (_db) return _db;
  _db = new Database(DB_PATH);
  _db.pragma("journal_mode = WAL");
  _db.pragma("foreign_keys = ON");
  initSchema(_db);
  return _db;
}

function initSchema(db: Database.Database) {
  db.exec(`
    CREATE TABLE IF NOT EXISTS jobs (
      id              TEXT PRIMARY KEY,
      name            TEXT NOT NULL,
      data            TEXT NOT NULL,
      status          TEXT NOT NULL DEFAULT 'waiting',
      progress        REAL NOT NULL DEFAULT 0,
      attempts        INTEGER NOT NULL DEFAULT 0,
      max_attempts    INTEGER NOT NULL DEFAULT 3,
      backoff_delay   INTEGER NOT NULL DEFAULT 5000,
      worker_id       TEXT,
      result          TEXT,
      error           TEXT,
      last_error_at   INTEGER,
      last_error_type TEXT,
      created_at      INTEGER NOT NULL,
      updated_at      INTEGER NOT NULL,
      run_at          INTEGER,
      started_at      INTEGER,
      heartbeat_at    INTEGER,
      graph_id        TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_jobs_status          ON jobs(status);
    CREATE INDEX IF NOT EXISTS idx_jobs_status_run_at  ON jobs(status, run_at);
    CREATE INDEX IF NOT EXISTS idx_jobs_worker          ON jobs(worker_id) WHERE worker_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_jobs_heartbeat        ON jobs(heartbeat_at) WHERE heartbeat_at IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_jobs_graph           ON jobs(graph_id) WHERE graph_id IS NOT NULL;

    CREATE TABLE IF NOT EXISTS job_deps (
      job_id       TEXT NOT NULL,
      depends_on   TEXT NOT NULL,
      PRIMARY KEY (job_id, depends_on),
      FOREIGN KEY (job_id)    REFERENCES jobs(id) ON DELETE CASCADE,
      FOREIGN KEY (depends_on) REFERENCES jobs(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_deps_depends_on ON job_deps(depends_on);

    CREATE TABLE IF NOT EXISTS job_events (
      id        INTEGER PRIMARY KEY AUTOINCREMENT,
      job_id    TEXT NOT NULL,
      type      TEXT NOT NULL,
      worker_id TEXT,
      ts        INTEGER NOT NULL,
      payload   TEXT,
      graph_id  TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_events_job   ON job_events(job_id);
    CREATE INDEX IF NOT EXISTS idx_events_graph  ON job_events(graph_id);
    CREATE INDEX IF NOT EXISTS idx_events_ts     ON job_events(ts);
  `);
}

// ── Types ────────────────────────────────────────────────────────────────────

type JobStatus = "waiting" | "active" | "completed" | "failed" | "blocked";

type JobEventType =
  | "CREATED" | "CLAIMED" | "HEARTBEAT" | "PROGRESS"
  | "COMPLETED" | "FAILED" | "RETRY" | "UNBLOCKED" | "POISON_PILL";

interface JobEvent {
  id: number;
  job_id: string;
  type: JobEventType;
  worker_id: string | null;
  ts: number;
  payload: string | null;
  graph_id: string | null;
}

interface JobRow {
  id: string;
  name: string;
  data: string;
  status: JobStatus;
  progress: number;
  attempts: number;
  max_attempts: number;
  backoff_delay: number;
  worker_id: string | null;
  result: string | null;
  error: string | null;
  last_error_at: number | null;
  last_error_type: string | null;
  created_at: number;
  updated_at: number;
  run_at: number | null;
  started_at: number | null;
  heartbeat_at: number | null;
  graph_id: string | null;
}

export interface JobData {
  jobId: string;
  layout: Record<string, unknown>;
  compositionId: string;
  port: number;
}

export interface JobResult {
  outputPath: string;
  downloadUrl: string;
}

export interface QueueOpts {
  attempts?: number;
  backoff?: { type: "exponential" | "fixed"; delay: number };
  jobId?: string;
  graphId?: string;
}

export interface GraphNode {
  name: string;
  data: JobData;
  dependsOn?: string[];
}

export interface TraceEntry {
  ts: number;
  type: JobEventType;
  jobId: string;
  jobName: string;
  status: JobStatus;
  workerId: string | null;
  payload: string | null;
}

export interface Trace {
  graphId: string;
  startTs: number;
  endTs: number;
  duration: number;
  events: TraceEntry[];
  jobs: Array<{
    id: string;
    name: string;
    status: JobStatus;
    dependsOn: string[];
  }>;
}

// ── SqliteJob ─────────────────────────────────────────────────────────────────

export class SqliteJob {
  id: string;
  name: string;
  data: JobData;
  status: JobStatus;
  progress: number;
  attempts: number;
  maxAttempts: number;
  backoffDelay: number;
  workerId: string | null;
  result: JobResult | null;
  error: string | null;
  lastErrorAt: number | null;
  lastErrorType: string | null;
  startedAt: number | null;
  heartbeatAt: number | null;
  graphId: string | null;
  private db: Database.Database;

  constructor(row: JobRow, db: Database.Database) {
    this.id = row.id;
    this.name = row.name;
    this.data = JSON.parse(row.data) as JobData;
    this.status = row.status;
    this.progress = row.progress;
    this.attempts = row.attempts;
    this.maxAttempts = row.max_attempts;
    this.backoffDelay = row.backoff_delay;
    this.workerId = row.worker_id;
    this.result = row.result ? JSON.parse(row.result) : null;
    this.error = row.error;
    this.lastErrorAt = row.last_error_at;
    this.lastErrorType = row.last_error_type;
    this.startedAt = row.started_at;
    this.heartbeatAt = row.heartbeat_at;
    this.graphId = row.graph_id;
    this.db = db;
  }

  get progressValue(): number { return this.progress; }
  set progressValue(v: number) {
    this.progress = v;
    this.db.prepare("UPDATE jobs SET progress = ?, updated_at = ? WHERE id = ?").run(v, Date.now(), this.id);
  }

  async heartbeat(): Promise<void> {
    const now = Date.now();
    const { changes } = this.db.prepare(
      "UPDATE jobs SET heartbeat_at = ?, updated_at = ? WHERE id = ? AND status = 'active' AND worker_id = ?"
    ).run(now, now, this.id, this.workerId);
    this.heartbeatAt = now;
    if (changes > 0) {
      this.db.prepare(
        "INSERT INTO job_events (job_id, type, worker_id, ts, payload, graph_id) VALUES (?, ?, ?, ?, ?, ?)"
      ).run(this.id, "HEARTBEAT", this.workerId, now, JSON.stringify({ progress: this.progress }), this.graphId);
    }
  }

  async complete(result: JobResult): Promise<void> {
    const now = Date.now();
    const { changes } = this.db.prepare(
      "UPDATE jobs SET status='completed', result=?, worker_id=NULL, heartbeat_at=NULL, updated_at=? " +
      "WHERE id=? AND status='active' AND worker_id=?"
    ).run(JSON.stringify(result), now, this.id, this.workerId);

    if (changes === 0) return;
    this.status = "completed";
    this.result = result;
    this.workerId = null;

    this.db.prepare(
      "INSERT INTO job_events (job_id, type, worker_id, ts, payload, graph_id) VALUES (?, ?, ?, ?, ?, ?)"
    ).run(this.id, "COMPLETED", null, now, JSON.stringify(result), this.graphId);

    await this.evaluateDownstream("completed");
  }

  async fail(error: string, doRetry = true, poisonPill = false): Promise<void> {
    const now = Date.now();
    const errorType = error.includes("timeout") ? "timeout"
      : error.includes("stuck") ? "stuck"
      : error.includes("crash") ? "crash"
      : "render";

    if (doRetry && this.attempts < this.maxAttempts) {
      const delay = this.backoffDelay * Math.pow(2, this.attempts - 1);
      const runAt = now + delay;
      const { changes } = this.db.prepare(`
        UPDATE jobs SET status='waiting', attempts=attempts+1,
        error=?, worker_id=NULL, run_at=?, heartbeat_at=NULL,
        last_error_at=?, last_error_type=?, updated_at=?
        WHERE id=? AND status='active' AND worker_id=?
      `).run(error, runAt, now, errorType, now, this.id, this.workerId);

      if (changes === 0) return;
      this.status = "waiting";
      this.attempts += 1;
      this.error = error;
      this.lastErrorAt = now;
      this.lastErrorType = errorType;
      this.workerId = null;

      this.db.prepare(
        "INSERT INTO job_events (job_id, type, worker_id, ts, payload, graph_id) VALUES (?, ?, ?, ?, ?, ?)"
      ).run(this.id, "RETRY", null, now, JSON.stringify({ delay, error, errorType, attempt: this.attempts }), this.graphId);

    } else {
      const { changes } = this.db.prepare(`
        UPDATE jobs SET status='failed', error=?, worker_id=NULL, heartbeat_at=NULL,
        last_error_at=?, last_error_type=?, updated_at=?
        WHERE id=? AND status='active' AND worker_id=?
      `).run(error, now, errorType, now, this.id, this.workerId);

      if (changes === 0) return;
      this.status = "failed";
      this.error = error;
      this.lastErrorAt = now;
      this.lastErrorType = errorType;
      this.workerId = null;

      this.db.prepare(
        "INSERT INTO job_events (job_id, type, worker_id, ts, payload, graph_id) VALUES (?, ?, ?, ?, ?, ?)"
      ).run(this.id, "FAILED", null, now, JSON.stringify({ error, errorType }), this.graphId);
    }

    if (poisonPill) {
      await this.propagateFailure(error, errorType);
    } else {
      await this.evaluateDownstream("failed");
    }
  }

  private async propagateFailure(error: string, errorType: string): Promise<void> {
    const now = Date.now();
    const dependents = this.db.prepare(
      "SELECT job_id FROM job_deps WHERE depends_on = ?"
    ).all(this.id) as Array<{ job_id: string }>;

    for (const dep of dependents) {
      this.db.prepare(`
        UPDATE jobs SET status='failed', error=?, last_error_at=?, last_error_type=?, updated_at=?
        WHERE id=? AND status NOT IN ('completed','failed')
      `).run(`[cascaded fail from ${this.id}] ${error}`, now, errorType, now, dep.job_id);

      this.db.prepare(
        "INSERT INTO job_events (job_id, type, worker_id, ts, payload, graph_id) VALUES (?, ?, ?, ?, ?, ?)"
      ).run(dep.job_id, "POISON_PILL", null, now, JSON.stringify({ sourceJob: this.id, error }), this.graphId);
    }

    if (dependents.length > 0) {
      console.warn(`[queue] poison pill: ${dependents.length} downstream jobs failed`);
    }
  }

  /** When a job completes or fails, check if any downstream jobs are now ready. */
  private async evaluateDownstream(outcome: "completed" | "failed"): Promise<void> {
    const dependentRows = this.db.prepare(
      "SELECT job_id FROM job_deps WHERE depends_on = ?"
    ).all(this.id) as Array<{ job_id: string }>;

    if (dependentRows.length === 0) return;

    for (const dep of dependentRows) {
      const jobId = dep.job_id;

      const stats = this.db.prepare(`
        SELECT
          (SELECT COUNT(*) FROM job_deps WHERE job_id = ?) as total_deps,
          (SELECT COUNT(*) FROM job_deps d
           JOIN jobs j ON d.depends_on = j.id
           WHERE d.job_id = ? AND j.status = 'completed') as completed_deps
      `).get(jobId, jobId) as { total_deps: number; completed_deps: number };

      if (stats.completed_deps === stats.total_deps) {
        const { changes } = this.db.prepare(`
          UPDATE jobs SET status='waiting', updated_at=? WHERE id=? AND status='blocked'
        `).run(Date.now(), jobId);

        if (changes > 0) {
          this.db.prepare(
            "INSERT INTO job_events (job_id, type, worker_id, ts, payload, graph_id) VALUES (?, ?, ?, ?, ?, ?)"
          ).run(jobId, "UNBLOCKED", null, Date.now(), JSON.stringify({ triggeredBy: this.id, outcome }), this.graphId);
          console.info(`[queue] DAG: job ${jobId} ready (all ${stats.total_deps} deps satisfied)`);
        }
      }
    }
  }

  async update(): Promise<void> {
    this.db.prepare(
      "UPDATE jobs SET status=?, progress=?, attempts=?, result=?, error=?, updated_at=? WHERE id=?"
    ).run(
      this.status, this.progress, this.attempts,
      this.result ? JSON.stringify(this.result) : null,
      this.error, Date.now(), this.id
    );
  }
}

// ── SqliteJobQueue ─────────────────────────────────────────────────────────────

export class SqliteJobQueue {
  private db: Database.Database;
  private watchdogTimer: ReturnType<typeof setInterval> | null = null;

  private static readonly VISIBILITY_TIMEOUT = 90_000;
  private static readonly WATCHDOG_INTERVAL  = 60_000;

  constructor() {
    this.db = getDb();
  }

  async add(name: string, data: JobData, opts: QueueOpts = {}): Promise<SqliteJob> {
    const jobId = opts.jobId ?? randomUUID();
    const now = Date.now();
    const runAt = opts.backoff?.delay
      ? now + (opts.backoff.type === "exponential" ? opts.backoff.delay : 0)
      : null;

    const existing = this.db.prepare("SELECT id FROM jobs WHERE id = ?").get(jobId);
    if (existing) {
      return new SqliteJob(this.db.prepare("SELECT * FROM jobs WHERE id = ?").get(jobId) as JobRow, this.db);
    }

    this.db.prepare(`
      INSERT INTO jobs (id, name, data, status, attempts, max_attempts, backoff_delay,
                        created_at, updated_at, run_at, graph_id)
      VALUES (?, ?, ?, 'waiting', 0, ?, ?, ?, ?, ?, ?)
    `).run(
      jobId, name, JSON.stringify(data),
      opts.attempts ?? 3, opts.backoff?.delay ?? 5000,
      now, now, runAt, opts.graphId ?? null
    );

    this.db.prepare(
      "INSERT INTO job_events (job_id, type, worker_id, ts, payload, graph_id) VALUES (?, ?, ?, ?, ?, ?)"
    ).run(jobId, "CREATED", null, now, JSON.stringify({ name, data }), opts.graphId ?? null);

    return new SqliteJob(
      this.db.prepare("SELECT * FROM jobs WHERE id = ?").get(jobId) as JobRow, this.db
    );
  }

  async addGraph(graphName: string, nodes: GraphNode[]): Promise<SqliteJob[]> {
    const now = Date.now();
    const graphId = graphName;
    const created: Array<{ id: string; dependsOn: string[] }> = [];

    // Phase 1: create all jobs as 'blocked'
    for (const node of nodes) {
      const jobId = node.data.jobId;
      const deps = node.dependsOn ?? [];

      this.db.prepare(`
        INSERT OR IGNORE INTO jobs (id, name, data, status, attempts, max_attempts, backoff_delay,
                                     created_at, updated_at, graph_id)
        VALUES (?, ?, ?, 'blocked', 0, 3, 5000, ?, ?, ?)
      `).run(jobId, node.name, JSON.stringify(node.data), now, now, graphId);

      this.db.prepare(
        "INSERT INTO job_events (job_id, type, worker_id, ts, payload, graph_id) VALUES (?, ?, ?, ?, ?, ?)"
      ).run(jobId, "CREATED", null, now, JSON.stringify({ name: node.name, dependsOn: deps }), graphId);

      created.push({ id: jobId, dependsOn: deps });
    }

    // Phase 2: insert dependency edges
    for (const { id, dependsOn } of created) {
      for (const depId of dependsOn) {
        this.db.prepare(
          "INSERT OR IGNORE INTO job_deps (job_id, depends_on) VALUES (?, ?)"
        ).run(id, depId);
      }
    }

    // Phase 3: jobs with zero deps become 'waiting' immediately
    for (const { id, dependsOn } of created) {
      if (dependsOn.length === 0) {
        this.db.prepare("UPDATE jobs SET status='waiting' WHERE id=? AND status='blocked'").run(id);
      }
    }

    const jobs = created.map(c =>
      new SqliteJob(this.db.prepare("SELECT * FROM jobs WHERE id=?").get(c.id) as JobRow, this.db)
    );

    console.info(`[queue] graph "${graphId}" created: ${jobs.length} nodes, ${nodes.filter(n => n.dependsOn?.length).length} with deps`);
    return jobs;
  }

  /** Atomic claim. blocked jobs are skipped (handled by evaluateDownstream). */
  async claimJob(workerId: string): Promise<SqliteJob | null> {
    const now = Date.now();

    const result = this.db.prepare(`
      UPDATE jobs
      SET status       = 'active',
          worker_id    = ?,
          started_at   = ?,
          heartbeat_at = ?,
          error        = NULL,
          updated_at   = ?
      WHERE id = (
        SELECT id FROM jobs
        WHERE status = 'waiting'
          AND (run_at IS NULL OR run_at <= ?)
        ORDER BY created_at ASC
        LIMIT 1
      )
      AND status = 'waiting'
      RETURNING *
    `).get(workerId, now, now, now, now) as JobRow | undefined;

    if (!result) return null;
    const job = new SqliteJob(result, this.db);

    this.db.prepare(
      "INSERT INTO job_events (job_id, type, worker_id, ts, payload, graph_id) VALUES (?, ?, ?, ?, ?, ?)"
    ).run(job.id, "CLAIMED", workerId, now, JSON.stringify({ startedAt: now }), job.graphId);

    return job;
  }

  async getJob(id: string): Promise<SqliteJob | null> {
    const row = this.db.prepare("SELECT * FROM jobs WHERE id = ?").get(id) as JobRow | undefined;
    return row ? new SqliteJob(row, this.db) : null;
  }

  getStats() {
    const s = (cond: string) =>
      (this.db.prepare(`SELECT COUNT(*) as c FROM jobs WHERE ${cond}`).get() as { c: number }).c;
    return {
      waiting:   s("status='waiting'"),
      blocked:   s("status='blocked'"),
      active:    s("status='active'"),
      completed: s("status='completed'"),
      failed:    s("status='failed'"),
    };
  }

  /**
   * Reconstruct the full execution trace for a graph.
   * Returns events in chronological order with job metadata joined.
   * Use this to replay/animate the workflow execution.
   */
  async getTrace(graphId: string): Promise<Trace> {
    const events = this.db.prepare(`
      SELECT e.ts, e.type, e.job_id, e.worker_id, e.payload, e.graph_id,
             j.name as job_name, j.status as job_status
      FROM job_events e
      JOIN jobs j ON e.job_id = j.id
      WHERE e.graph_id = ?
      ORDER BY e.ts ASC
    `).all(graphId) as Array<{
      ts: number; type: string; job_id: string; worker_id: string | null;
      payload: string | null; graph_id: string | null;
      job_name: string; job_status: string;
    }>;

    const jobs = this.db.prepare(`
      SELECT j.id, j.name, j.status,
             (SELECT json_group_array(depends_on) FROM job_deps WHERE job_id = j.id) as deps_json
      FROM jobs j
      WHERE j.graph_id = ?
    `).all(graphId) as Array<{
      id: string; name: string; status: string;
      deps_json: string;
    }>;

    const parsedJobs = jobs.map(j => ({
      id: j.id,
      name: j.name,
      status: j.status as JobStatus,
      dependsOn: JSON.parse(j.deps_json || "[]").filter(Boolean) as string[],
    }));

    const traceEvents: TraceEntry[] = events.map(e => ({
      ts: e.ts,
      type: e.type as JobEventType,
      jobId: e.job_id,
      jobName: e.job_name,
      status: e.job_status as JobStatus,
      workerId: e.worker_id ?? null,
      payload: e.payload ?? null,
    }));

    const startTs = traceEvents.length > 0 ? traceEvents[0].ts : Date.now();
    const endTs = traceEvents.length > 0 ? traceEvents[traceEvents.length - 1].ts : Date.now();

    return {
      graphId,
      startTs,
      endTs,
      duration: endTs - startTs,
      events: traceEvents,
      jobs: parsedJobs,
    };
  }

  getGraphStats(graphId: string) {
    const s = (cond: string) =>
      (this.db.prepare(`SELECT COUNT(*) as c FROM jobs WHERE graph_id=? AND ${cond}`).get(graphId, cond) as { c: number }).c;
    return {
      total:     s("1=1"),
      blocked:   s("status='blocked'"),
      waiting:   s("status='waiting'"),
      active:    s("status='active'"),
      completed: s("status='completed'"),
      failed:    s("status='failed'"),
    };
  }

  async clean(ageMs = 24 * 3600 * 1000): Promise<void> {
    const cutoff = Date.now() - ageMs;
    const { changes } = this.db.prepare(
      "DELETE FROM jobs WHERE status IN ('completed','failed') AND updated_at < ?"
    ).run(cutoff);
    if (changes > 0) console.info(`[queue] cleaned ${changes} old jobs`);
  }

  startWatchdog() {
    if (this.watchdogTimer) return;
    this.watchdogTimer = setInterval(() => this.reclaimStuckJobs(), SqliteJobQueue.WATCHDOG_INTERVAL);
    console.info(`[queue] watchdog started (timeout=${SqliteJobQueue.VISIBILITY_TIMEOUT}ms)`);
  }

  private reclaimStuckJobs() {
    const now = Date.now();
    const deadline = now - SqliteJobQueue.VISIBILITY_TIMEOUT;

    const rows = this.db.prepare(`
      SELECT id, attempts, backoff_delay, error, last_error_type
      FROM jobs
      WHERE status = 'active' AND heartbeat_at IS NOT NULL AND heartbeat_at < ?
    `).all(deadline) as Array<{
      id: string; attempts: number; backoff_delay: number;
      error: string | null; last_error_type: string | null;
    }>;

    if (rows.length === 0) return;

    for (const row of rows) {
      const delay = row.backoff_delay * Math.pow(2, row.attempts);
      const nextRunAt = now + delay;
      const errorType = row.last_error_type === "timeout" ? "timeout" : "stuck";
      const taggedError = row.error
        ? `${row.error} | [${errorType}] worker timeout (attempts=${row.attempts + 1})`
        : `[${errorType}] worker timeout (attempts=${row.attempts + 1})`;

      this.db.prepare(`
        UPDATE jobs SET status='waiting', worker_id=NULL, attempts=attempts+1,
        run_at=?, error=?, heartbeat_at=NULL, last_error_at=?, last_error_type=?, updated_at=?
        WHERE id=? AND status='active'
      `).run(nextRunAt, taggedError, now, errorType, now, row.id);

      this.db.prepare(
        "INSERT INTO job_events (job_id, type, worker_id, ts, payload, graph_id) VALUES (?, ?, ?, ?, ?, ?)"
      ).run(row.id, "RETRY", null, now, JSON.stringify({ reason: "watchdog", delay, attempt: row.attempts + 1 }), row.graph_id ?? null);

      console.warn(`[queue] reclaimed stuck job ${row.id} (attempt ${row.attempts + 1}, retry in ${delay}ms)`);
    }
  }

  shutdown() {
    if (this.watchdogTimer) {
      clearInterval(this.watchdogTimer);
      this.watchdogTimer = null;
    }
  }
}

function sleep(ms: number) { return new Promise(r => setTimeout(r, ms)); }

// Singleton
let _queue: SqliteJobQueue | null = null;
export function getJobQueue(): SqliteJobQueue {
  if (!_queue) {
    _queue = new SqliteJobQueue();
    _queue.startWatchdog();
  }
  return _queue;
}
