/**
 * server.ts — FastAPI server for AI Agent Time Machine
 *
 * HTTP Endpoints (control):
 *   POST /run            → start a new agent run
 *   POST /pause/:traceId → pause
 *   POST /resume/:traceId → resume
 *   GET  /trace/:traceId/:branchId → get trace data
 *   GET  /branches/:traceId → list all branches for a trace
 *   POST /branch/:traceId → modify step → create branch + replay
 *
 * WebSocket (events):
 *   WS /ws/:traceId → stream live events as JSON
 *
 * MVP simplicity:
 *   - No complex state sync
 *   - HTTP for control (pause/resume/modify)
 *   - WS only for server→browser push
 */

import { randomUUID } from "node:crypto";
import express, { Request, Response } from "express";
import { createServer } from "node:http";
import { WebSocketServer, WebSocket } from "ws";
import { InstrumentedAgent } from "./agent-core.ts";
import { TraceStore } from "./trace-store.ts";
import type { AgentEvent, Trace, BranchMeta } from "./types.ts";

const app = express();
app.use(express.json());
app.use(express.static(".")); // serve demo.html

const server = createServer(app);
const wss = new WebSocketServer({ server });

// ── Global state ──────────────────────────────────────────────────────────────
const store = new TraceStore();
const agents: Map<string, InstrumentedAgent> = new Map();
const wsClients: Map<string, Set<WebSocket>> = new Map();

// ── WebSocket: broadcast to subscribers ──────────────────────────────────────
function broadcast(traceId: string, event: AgentEvent): void {
  const clients = wsClients.get(traceId);
  if (!clients) return;
  const payload = JSON.stringify(event);
  for (const ws of clients) {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(payload);
    }
  }
}

function broadcastSnapshot(traceId: string, data: object): void {
  const clients = wsClients.get(traceId);
  if (!clients) return;
  const payload = JSON.stringify({ type: "snapshot", data });
  for (const ws of clients) {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(payload);
    }
  }
}

// ── WebSocket route ───────────────────────────────────────────────────────────
wss.on("connection", (ws, req) => {
  const url = new URL(req.url ?? "/", `http://${req.headers.host}`);
  const pathParts = url.pathname.split("/").filter(Boolean);
  // Expected: /ws/traceId
  const traceId = pathParts[pathParts.length - 1];

  if (!traceId) {
    ws.close(4000, "Missing traceId");
    return;
  }

  if (!wsClients.has(traceId)) wsClients.set(traceId, new Set());
  wsClients.get(traceId)!.add(ws);

  // Send current state immediately on connect
  ws.send(JSON.stringify({ type: "connected", traceId }));

  ws.on("close", () => {
    wsClients.get(traceId)?.delete(ws);
  });

  ws.on("error", () => {
    wsClients.get(traceId)?.delete(ws);
  });
});

// ── POST /run — Start a new agent run ───────────────────────────────────────
app.post("/run", async (req: Request, res: Response) => {
  const task: string = req.body?.task ?? "Analyze this task and produce a multi-step reasoning response";

  const agent = new InstrumentedAgent(store, { maxSteps: 8 });
  const result = await agent.run(task);

  // Store agent ref for pause/resume
  agents.set(result.traceId, agent);

  res.json({
    traceId: result.traceId,
    output: result.output,
    stepsCompleted: result.stepsCompleted,
  });
});

// ── POST /run-stream — Start with live WS streaming ────────────────────────────
app.post("/run-stream", async (req: Request, res: Response) => {
  const task: string = req.body?.task ?? "Analyze this task and produce a multi-step reasoning response";
  const traceId: string = randomUUID().slice(0, 8);

  const agent = new InstrumentedAgent(store, { maxSteps: 8 });
  agents.set(traceId, agent);

  // Intercept emit to broadcast events
  const origEmit = store.emit.bind(store);
  // We need to wrap this — but since we can't easily intercept store.emit,
  // we set up a polling route instead (or use WS broadcast from the agent)
  // Instead: WS clients poll /trace/:id or we use a streaming response
  // For MVP: just return immediately and let clients poll /trace
  res.json({ traceId, status: "started" });

  // Run agent in background, broadcasting events
  agent.run(task).catch(console.error);
});

// ── GET /trace/:traceId/:branchId ──────────────────────────────────────────
app.get("/trace/:traceId/:branchId", (req: Request, res: Response) => {
  const { traceId, branchId } = req.params;
  const trace = store.getTraceByBranch(traceId, branchId ?? "main");

  if (!trace) {
    res.status(404).json({ error: "Trace not found" });
    return;
  }

  const steps = store.collapseToSteps(traceId);
  const branches = store.getAllBranches(traceId);

  res.json({
    traceId: trace.id,
    branchId: trace.branchId,
    status: trace.status,
    currentStep: trace.currentStep,
    finalOutput: trace.finalOutput,
    steps,
    branches,
    totalEvents: trace.events.length,
  });
});

// ── GET /trace/:traceId ───────────────────────────────────────────────────────
app.get("/trace/:traceId", (req: Request, res: Response) => {
  const { traceId } = req.params;
  const trace = store.getTraceByBranch(traceId, "main");

  if (!trace) {
    res.status(404).json({ error: "Trace not found" });
    return;
  }

  const steps = store.collapseToSteps(traceId);
  const branches = store.getAllBranches(traceId);

  res.json({
    traceId: trace.id,
    branchId: trace.branchId,
    status: trace.status,
    currentStep: trace.currentStep,
    finalOutput: trace.finalOutput,
    steps,
    branches,
    totalEvents: trace.events.length,
  });
});

// ── GET /traces — List all traces ───────────────────────────────────────────
app.get("/traces", (_req: Request, res: Response) => {
  const traces = store.listAll();
  res.json({ traces });
});

// ── POST /pause/:traceId ──────────────────────────────────────────────────────
app.post("/pause/:traceId", (req: Request, res: Response) => {
  const { traceId } = req.params;
  const agent = agents.get(traceId);
  if (!agent) {
    res.status(404).json({ error: "Agent not found or already finished" });
    return;
  }
  agent.pause(traceId);
  store.pause(traceId);
  res.json({ traceId, status: "paused" });
});

// ── POST /resume/:traceId ────────────────────────────────────────────────────
app.post("/resume/:traceId", (req: Request, res: Response) => {
  const { traceId } = req.params;
  const agent = agents.get(traceId);
  if (!agent) {
    res.status(404).json({ error: "Agent not found" });
    return;
  }
  agent.resume(traceId);
  store.resume(traceId);
  res.json({ traceId, status: "resumed" });
});

// ── POST /branch/:traceId — Modify step → create branch ─────────────────────
// Body: { branchId, stepIndex, modifyType, newData }
app.post("/branch/:traceId", (req: Request, res: Response) => {
  const { traceId } = req.params;
  const { branchId, stepIndex, modifyType, newData } = req.body as {
    branchId?: string;
    stepIndex: number;
    modifyType: "tool_result" | "reasoning";
    newData: Record<string, unknown>;
  };

  const fromBranch = branchId ?? "main";

  try {
    const newTrace = store.createBranch(traceId, fromBranch, stepIndex, modifyType, newData);

    res.json({
      traceId: newTrace.id,
      branchId: newTrace.branchId,
      parentBranchId: newTrace.parentBranchId,
      modifyStep: stepIndex,
      status: "branch_created_paused",
    });
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
});

// ── GET /branch/:traceId/:branchId ───────────────────────────────────────────
app.get("/branch/:traceId/:branchId", (req: Request, res: Response) => {
  const { traceId, branchId } = req.params;
  const branches = store.getAllBranches(traceId);
  const branch = branches.find(b => b.id === branchId);
  if (!branch) { res.status(404).json({ error: "Branch not found" }); return; }
  res.json(branch);
});

// ── SSE: Server-Sent Events for live trace updates ────────────────────────────
// Alternative to WebSocket (simpler for one-way streaming)
app.get("/events/:traceId", (req: Request, res: Response) => {
  const { traceId } = req.params;

  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders();

  // Send heartbeat
  const heartbeat = setInterval(() => {
    res.write(`: heartbeat\n\n`);
  }, 5000);

  // Poll for new events every 100ms
  const poll = setInterval(async () => {
    const trace = store.getTraceByBranch(traceId, "main");
    if (!trace) { clearInterval(poll); res.end(); return; }

    const snapshot = {
      type: "trace_update",
      traceId: trace.id,
      branchId: trace.branchId,
      status: trace.status,
      currentStep: trace.currentStep,
      totalEvents: trace.events.length,
    };
    res.write(`data: ${JSON.stringify(snapshot)}\n\n`);

    if (trace.status === "done" || trace.status === "error") {
      clearInterval(poll);
      clearInterval(heartbeat);
      res.end();
    }
  }, 100);

  req.on("close", () => {
    clearInterval(poll);
    clearInterval(heartbeat);
  });
});

// ── Health check ──────────────────────────────────────────────────────────────
app.get("/health", (_req: Request, res: Response) => {
  res.json({ status: "ok", uptime: process.uptime() });
});

const PORT = parseInt(process.env.DEBUG_PORT ?? "3001");
server.listen(PORT, () => {
  console.info(`[TimeMachine] AI Agent Time Machine running on http://localhost:${PORT}`);
  console.info(`[TimeMachine] Serve demo.html to use the visual debugger`);
});
