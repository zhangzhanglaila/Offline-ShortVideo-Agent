/**
 * trace-store.ts — In-memory trace storage with branching support
 *
 * Key design decisions:
 * - Event logs are IMMUTABLE (append-only)
 * - Branches share event history with parent up to fork point (copy-on-write)
 * - Each branch has its own event list and step view
 * - Modifications at step N → new branch starting from step N
 */

import { randomUUID } from "node:crypto";
import type { AgentEvent, Trace, TraceStep, BranchMeta, EventType } from "./types.ts";

// Neon palette for visualization (must be distinct on dark background)
const BRANCH_COLORS = [
  "#00ff88",  // neon green (branch 1)
  "#ff3366",  // hot pink (branch 2)
  "#00d4ff",  // cyan (branch 3)
  "#ffaa00",  // amber (branch 4)
  "#cc44ff",  // violet (branch 5)
  "#ff6600",  // orange (branch 6)
];

function nextColor(branchIndex: number): string {
  return BRANCH_COLORS[branchIndex % BRANCH_COLORS.length];
}

export class TraceStore {
  private traces: Map<string, Trace> = new Map();
  private branches: Map<string, BranchMeta> = new Map();
  private _eventCounter: number = 0;
  private _branchCounter: number = 0;

  // ─────────────────────────────────────────────────────────────────────────
  // Create a new trace (root branch)
  // ─────────────────────────────────────────────────────────────────────────
  createTrace(): Trace {
    const id = randomUUID().slice(0, 8);
    const branchId = "main";
    const trace: Trace = {
      id,
      branchId,
      parentBranchId: null,
      createdAt: Date.now(),
      events: [],
      steps: [],
      status: "running",
      currentStep: 0,
    };
    this.traces.set(id, trace);
    this.branches.set(branchId, {
      id: branchId,
      parentId: null,
      modifyStep: null,
      modifyType: null,
      createdAt: Date.now(),
      color: BRANCH_COLORS[0],
    });
    return trace;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Emit an event into a trace's current branch
  // ─────────────────────────────────────────────────────────────────────────
  emit(traceId: string, event: Omit<AgentEvent, "id" | "traceId" | "branchId" | "seq" | "timestamp">): AgentEvent {
    const trace = this.traces.get(traceId);
    if (!trace) throw new Error(`Trace ${traceId} not found`);

    const fullEvent: AgentEvent = {
      ...event,
      id: `evt_${++this._eventCounter}`,
      traceId,
      branchId: trace.branchId,
      seq: trace.events.length,
      timestamp: Date.now(),
    };

    trace.events.push(fullEvent);

    // Update current step
    if (event.type === "reasoning_start") {
      trace.currentStep = event.step;
    }

    return fullEvent;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Collapse events into steps (for visualization)
  // ─────────────────────────────────────────────────────────────────────────
  collapseToSteps(traceId: string): TraceStep[] {
    const trace = this.traces.get(traceId);
    if (!trace) return [];

    const steps: TraceStep[] = [];
    let currentStep: Partial<TraceStep> | null = null;
    let stepStartTime: number | null = null;

    for (const evt of trace.events) {
      switch (evt.type) {
        case "reasoning_start":
          if (currentStep) steps.push(currentStep as TraceStep);
          currentStep = { stepIndex: evt.step, reasoning: "" };
          stepStartTime = evt.timestamp;
          break;
        case "reasoning_content":
          if (currentStep) currentStep.reasoning = (currentStep.reasoning || "") + evt.data.content;
          break;
        case "reasoning_end":
          if (currentStep) currentStep.reasoning = (currentStep.reasoning || "") + (evt.data.content || "");
          break;
        case "tool_call":
          if (currentStep) {
            currentStep.toolName = evt.data.name as string;
            currentStep.toolArgs = evt.data.args as Record<string, unknown>;
          }
          break;
        case "tool_result":
          if (currentStep) {
            currentStep.toolResult = evt.data.result;
            if (stepStartTime) currentStep.durationMs = evt.timestamp - stepStartTime;
          }
          break;
        case "tool_error":
          if (currentStep) currentStep.toolError = evt.data.error as string;
          break;
      }
    }
    if (currentStep) steps.push(currentStep as TraceStep);

    trace.steps = steps;
    return steps;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Pause / resume / end
  // ─────────────────────────────────────────────────────────────────────────
  pause(traceId: string): void {
    const t = this.traces.get(traceId);
    if (t) t.status = "paused";
  }

  resume(traceId: string): void {
    const t = this.traces.get(traceId);
    if (t) t.status = "running";
  }

  setDone(traceId: string, output: string): void {
    const t = this.traces.get(traceId);
    if (t) { t.status = "done"; t.finalOutput = output; }
  }

  setError(traceId: string, error: string): void {
    const t = this.traces.get(traceId);
    if (t) { t.status = "error"; t.finalOutput = error; }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Modify a step → create a new branch (REPLAY-BASED, NOT ROLLBACK)
  //
  // How it works:
  //   User modifies tool result at step N
  //   → Take ALL events from step 0..N-1 (shared, COW)
  //   → Inject modified tool_result event at step N
  //   → Continue replay from step N with modified tool
  // ─────────────────────────────────────────────────────────────────────────
  createBranch(
    traceId: string,
    fromBranchId: string,
    modifyStepIndex: number,
    modifyType: "tool_result" | "reasoning",
    newData: Record<string, unknown>
  ): Trace {
    const parentTrace = this.traces.get(traceId);
    if (!parentTrace) throw new Error(`Trace ${traceId} not found`);

    const newBranchId = `branch_${++this._branchCounter}`;
    const parentBranch = this.branches.get(fromBranchId);

    // Record branch metadata
    this.branches.set(newBranchId, {
      id: newBranchId,
      parentId: fromBranchId,
      modifyStep: modifyStepIndex,
      modifyType,
      createdAt: Date.now(),
      color: nextColor(this._branchCounter),
    });

    // Build new event list: replay up to the step, then inject modification
    const parentEvents = parentTrace.events;
    const newEvents: AgentEvent[] = [];

    // Copy events UP TO (but not including) the first event of modifyStepIndex
    let eventSeq = 0;
    for (const evt of parentEvents) {
      if (evt.step < modifyStepIndex) {
        newEvents.push({
          ...evt,
          id: `evt_${++this._eventCounter}`,
          branchId: newBranchId,
          seq: eventSeq++,
          timestamp: Date.now(), // fresh timestamps for replayed events
        });
      } else if (evt.step === modifyStepIndex) {
        // Stop before this step — we'll inject our modified version
        break;
      }
    }

    // Find the step's tool_call to pair with the modified result
    // We need to preserve the tool_call that triggered this result
    const stepToolCall = parentEvents.find(
      e => e.step === modifyStepIndex && e.type === "tool_call"
    );

    // Inject: reasoning events (if any) for this step, then modified result
    if (modifyType === "tool_result") {
      // Inject modified tool_result
      const injectedToolResult: AgentEvent = {
        id: `evt_${++this._eventCounter}`,
        traceId,
        branchId: newBranchId,
        seq: eventSeq++,
        timestamp: Date.now(),
        step: modifyStepIndex,
        type: "tool_result",
        data: { result: newData.result },
      };
      newEvents.push(injectedToolResult);
    } else if (modifyType === "reasoning") {
      // Inject modified reasoning
      const injectedReasoning: AgentEvent = {
        id: `evt_${++this._eventCounter}`,
        traceId,
        branchId: newBranchId,
        seq: eventSeq++,
        timestamp: Date.now(),
        step: modifyStepIndex,
        type: "reasoning_content",
        data: { content: newData.content },
      };
      newEvents.push(injectedReasoning);
    }

    // Create new trace pointing to this branch
    const newTrace: Trace = {
      id: traceId,
      branchId: newBranchId,
      parentBranchId: fromBranchId,
      createdAt: Date.now(),
      events: newEvents,
      steps: [],
      status: "paused", // user will resume after reviewing
      currentStep: modifyStepIndex,
    };

    this.traces.set(`${traceId}_${newBranchId}`, newTrace);

    // Also update the original trace's branch to reflect a fork record
    // We don't modify original events — they're immutable
    parentTrace.status = "done";

    return newTrace;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Getters
  // ─────────────────────────────────────────────────────────────────────────
  getTrace(id: string): Trace | undefined {
    return this.traces.get(id);
  }

  getTraceByBranch(traceId: string, branchId: string): Trace | undefined {
    // Try exact key first
    if (this.traces.has(`${traceId}_${branchId}`)) {
      return this.traces.get(`${traceId}_${branchId}`);
    }
    // Fall back to main branch
    if (branchId === "main") return this.traces.get(traceId);
    return undefined;
  }

  getAllBranches(traceId: string): BranchMeta[] {
    return Array.from(this.branches.values()).filter(
      b => {
        // Get branches that belong to this trace
        // Main branch is always present
        if (b.id === "main") return true;
        // Child branches have parent chain that references the original trace
        let cur: BranchMeta | undefined = b;
        while (cur?.parentId) {
          if (cur.parentId === traceId || cur.parentId === "main") return true;
          cur = this.branches.get(cur.parentId);
        }
        return false;
      }
    );
  }

  getAllTraces(): Trace[] {
    return Array.from(this.traces.values());
  }

  listAll(): { traceId: string; branchId: string; status: string; steps: number }[] {
    return Array.from(this.traces.values()).map(t => ({
      traceId: t.id,
      branchId: t.branchId,
      status: t.status,
      steps: this.collapseToSteps(t.id).length,
    }));
  }
}
