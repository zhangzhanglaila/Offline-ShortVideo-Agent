/**
 * types.ts — Core event types and trace structure for AI Agent Time Machine
 *
 * Design: Every agent step emits typed events into an append-only event log.
 * Events are NEVER mutated — branches create new log pointers.
 */

export type EventType =
  | "agent_start"
  | "reasoning_start"
  | "reasoning_content"
  | "reasoning_end"
  | "tool_call"
  | "tool_result"
  | "tool_error"
  | "agent_end"
  | "branch_created"
  | "step_modify";

export interface AgentEvent {
  id: string;           // globally unique event id
  traceId: string;       // which trace this belongs to
  branchId: string;     // which branch
  seq: number;          // sequential index within branch
  type: EventType;
  timestamp: number;     // Date.now()
  step: number;         // which reasoning step
  data: Record<string, unknown>;
}

export interface TraceStep {
  stepIndex: number;
  reasoning: string;       // collapsed reasoning text
  toolName?: string;
  toolArgs?: Record<string, unknown>;
  toolResult?: unknown;
  toolError?: string;
  durationMs?: number;
}

export interface Trace {
  id: string;
  branchId: string;
  parentBranchId: string | null;
  createdAt: number;
  events: AgentEvent[];     // full event log (append-only)
  steps: TraceStep[];       // collapsed step view
  status: "running" | "paused" | "done" | "error";
  currentStep: number;
  finalOutput?: string;
}

export interface BranchMeta {
  id: string;
  parentId: string | null;
  modifyStep: number | null;   // which step was modified to create this branch
  modifyType: "tool_result" | "reasoning" | null;
  createdAt: number;
  color: string;               // for visualization
}

/**
 * Tool definition for the instrumented agent.
 * Each tool has a name, description, and execute function.
 */
export interface AgentTool {
  name: string;
  description: string;
  execute: (args: Record<string, unknown>, context: AgentContext) => Promise<unknown>;
}

export interface AgentContext {
  traceId: string;
  stepIndex: number;
  history: TraceStep[];
  emit: (event: Omit<AgentEvent, "id" | "traceId" | "branchId" | "seq" | "timestamp">) => void;
}

/**
 * Configuration for the instrumented agent
 */
export interface AgentConfig {
  model?: string;
  maxSteps?: number;
  tools?: AgentTool[];
}
