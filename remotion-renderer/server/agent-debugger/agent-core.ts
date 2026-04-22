/**
 * agent-core.ts — Instrumented ReAct Agent
 *
 * Day 1 MVP: This is the only agent implementation needed.
 * - Hardcoded tools (search, calculator, string)
 * - Emits events on every reasoning/tool boundary
 * - Polls pause flag before each step (MVP approach, NOT async interrupts)
 * - Full event log → trace store → replay
 */

import { randomUUID } from "node:crypto";
import type { AgentEvent, AgentTool, AgentConfig, Trace } from "./types.ts";
import type { TraceStore } from "./trace-store.ts";

export interface AgentRunResult {
  traceId: string;
  output: string;
  stepsCompleted: number;
}

// ─────────────────────────────────────────────────────────────────────────
// Built-in tools (MVP — enough to demo branching)
// ─────────────────────────────────────────────────────────────────────────

const calculatorTool: AgentTool = {
  name: "calculator",
  description: "Evaluate a JavaScript math expression and return the result",
  execute: async (args) => {
    const expr = String(args.expression || "0");
    try {
      // Safe eval using Function constructor (no access to scope)
      const result = new Function(`"use strict"; return (${expr})`)();
      return { success: true, result };
    } catch (e) {
      return { success: false, error: String(e) };
    }
  },
};

const searchTool: AgentTool = {
  name: "search",
  description: "Simulate a web search — returns hardcoded results for demo purposes",
  execute: async (args) => {
    const query = String(args.query || "");
    // Simulate search with plausible-looking results
    return {
      results: [
        { title: `${query} — Wikipedia`, url: `https://en.wikipedia.org/wiki/${encodeURIComponent(query)}`, snippet: `This is a simulated search result for "${query}". Useful context here.` },
        { title: `${query} — MDN Docs`, url: `https://developer.mozilla.org/search?q=${encodeURIComponent(query)}`, snippet: `Documentation about "${query}" from MDN.` },
      ],
    };
  },
};

const stringTool: AgentTool = {
  name: "string_ops",
  description: "Perform string operations: uppercase, lowercase, reverse, length",
  execute: async (args) => {
    const text = String(args.text || "");
    const op = String(args.op || "length");
    switch (op) {
      case "uppercase": return { result: text.toUpperCase() };
      case "lowercase": return { result: text.toLowerCase() };
      case "reverse": return { result: text.split("").reverse().join("") };
      case "length": return { result: text.length };
      default: return { error: `Unknown op: ${op}` };
    }
  },
};

const echoTool: AgentTool = {
  name: "echo",
  description: "Echo back the input — useful for testing and injecting custom data",
  execute: async (args) => ({ echoed: args }),
};

const defaultTools: AgentTool[] = [calculatorTool, searchTool, stringTool, echoTool];

// ─────────────────────────────────────────────────────────────────────────
// Simple LLM simulator (deterministic + controllable for demo)
// ─────────────────────────────────────────────────────────────────────────

/**
 * Simulated LLM for the demo.
 * Uses rule-based responses with controllable outputs so the demo is predictable.
 * In production, replace with real LLM API calls.
 */
function generateReasoning(step: number, history: { toolName?: string; toolResult?: unknown }[], task: string): string {
  const past = history.slice(-2);
  const lastTool = past[past.length - 1]?.toolName;

  if (step === 0) {
    return `Let me analyze this task: "${task}"

I need to break this down:
1. First, I'll check the current state with echo
2. Then perform calculations as needed
3. And search for any relevant information

Let me start by echoing the input to verify I have it correctly.`;
  }

  if (step === 1 && !lastTool) {
    return `I've confirmed the input. Now let me think about the approach.

For this task, I'll need to:
- Parse the requirements
- Execute the core operation
- Format the response

Let me proceed step by step.`;
  }

  if (lastTool === "calculator") {
    return `Good, the calculation is complete. Now I need to:
- Verify the result makes sense
- Format it for the final answer

The calculation result looks correct. Let me continue building toward the answer.`;
  }

  if (lastTool === "search") {
    return `I found relevant search results. Now I need to:
- Review the information
- Synthesize it with my reasoning
- Produce a coherent answer

The search results provide good context. I can now formulate a complete response.`;
  }

  if (lastTool === "string_ops") {
    return `String operation completed. The result is ready to be included in my final response.

Let me now synthesize all the steps I've taken and produce the answer.`;
  }

  if (step >= 2) {
    return `I've gathered all the necessary information. Now I'll produce the final answer based on:
- The task requirements
- The computed results
- The search context

This should satisfy the task completely.`;
  }

  return `Step ${step}: Processing...`;
}

function shouldCallTool(step: number, history: { toolName?: string }[], task: string): { call: boolean; toolName: string; args: Record<string, unknown> } {
  const lastTool = history[history.length - 1]?.toolName;

  if (step === 0) {
    return { call: true, toolName: "echo", args: { purpose: "confirm_input", task, analysis_ready: true } };
  }

  if (step === 1 && lastTool !== "calculator") {
    return { call: true, toolName: "calculator", args: { expression: "42 * 17 + 3" } };
  }

  if (step === 2 && lastTool !== "search") {
    return { call: true, toolName: "search", args: { query: "artificial intelligence reasoning" } };
  }

  if (step === 3 && lastTool !== "string_ops") {
    return { call: true, toolName: "string_ops", args: { text: "Hello AI Agent", op: "uppercase" } };
  }

  return { call: false, toolName: "", args: {} };
}

function generateFinalOutput(history: { toolResult?: unknown; toolName?: string }[], task: string): string {
  const calcResult = history.find(h => h.toolName === "calculator")?.toolResult as { result: number } | undefined;
  const searchResult = history.find(h => h.toolName === "search")?.toolResult as { results: { title: string }[] } | undefined;
  const stringResult = history.find(h => h.toolName === "string_ops")?.toolResult as { result: string } | undefined;

  return `Task completed: "${task}"

Summary of steps:
${calcResult ? `- Calculator: expression evaluated to ${calcResult.result}` : "- Calculator: skipped"}
${searchResult ? `- Search: found ${searchResult.results.length} relevant results` : "- Search: skipped"}
${stringResult ? `- String ops: result = "${stringResult.result}"` : "- String ops: skipped"}

Final answer: The task has been processed successfully with multiple tool invocations demonstrating the agent's capability to chain reasoning and tool use.`;
}

// ─────────────────────────────────────────────────────────────────────────
// Instrumented Agent Loop
// ─────────────────────────────────────────────────────────────────────────

export class InstrumentedAgent {
  private config: AgentConfig;
  private tools: Map<string, AgentTool>;
  private store: TraceStore;
  private pauseFlags: Map<string, boolean> = new Map();

  constructor(store: TraceStore, config: AgentConfig = {}) {
    this.store = store;
    this.config = {
      maxSteps: config.maxSteps ?? 8,
      tools: config.tools ?? defaultTools,
    };
    this.tools = new Map(this.config.tools.map(t => [t.name, t]));
  }

  // ── Pause control (MVP: polling-based) ──────────────────────────────────
  pause(traceId: string): void { this.pauseFlags.set(traceId, true); }
  resume(traceId: string): void { this.pauseFlags.set(traceId, false); }
  isPaused(traceId: string): boolean { return this.pauseFlags.get(traceId) ?? false; }

  private async waitIfPaused(traceId: string): Promise<void> {
    // Poll every 50ms — simple, no async complexity
    while (this.isPaused(traceId)) {
      await new Promise(r => setTimeout(r, 50));
    }
  }

  // ── Run the agent ─────────────────────────────────────────────────────────
  async run(task: string): Promise<AgentRunResult> {
    const trace = this.store.createTrace();
    const traceId = trace.id;  // use the store-assigned ID, not a separate UUID
    const branchId = trace.branchId;

    const emit = (event: Omit<AgentEvent, "id" | "traceId" | "branchId" | "seq" | "timestamp">) => {
      this.store.emit(traceId, event);
    };

    const history: { toolName?: string; toolResult?: unknown; toolError?: string }[] = [];

    emit({ type: "agent_start", step: 0, data: { task } });

    for (let step = 0; step < this.config.maxSteps; step++) {
      // MVP pause check
      await this.waitIfPaused(traceId);

      if (this.isPaused(traceId)) {
        // Extra check after wait
        await this.waitIfPaused(traceId);
      }

      // Emit reasoning
      emit({ type: "reasoning_start", step, data: {} });

      const reasoningContent = generateReasoning(step, history, task);
      emit({ type: "reasoning_content", step, data: { content: reasoningContent } });
      emit({ type: "reasoning_end", step, data: { content: reasoningContent } });

      // Decide whether to call a tool
      const toolDecision = shouldCallTool(step, history, task);

      if (toolDecision.call) {
        emit({ type: "tool_call", step, data: { name: toolDecision.toolName, args: toolDecision.args } });

        try {
          const tool = this.tools.get(toolDecision.toolName);
          if (!tool) throw new Error(`Tool ${toolDecision.toolName} not found`);

          const result = await tool.execute(toolDecision.args, {
            traceId,
            stepIndex: step,
            history: history as any,
            emit,
          });

          history.push({ toolName: toolDecision.toolName, toolResult: result });
          emit({ type: "tool_result", step, data: { result } });
        } catch (err) {
          const errorMsg = err instanceof Error ? err.message : String(err);
          history.push({ toolError: errorMsg });
          emit({ type: "tool_error", step, data: { error: errorMsg } });
        }
      } else {
        // No tool call — generate final answer and exit
        const output = generateFinalOutput(history, task);
        emit({ type: "agent_end", step, data: { output } });
        this.store.setDone(traceId, output);
        this.store.collapseToSteps(traceId);

        return {
          traceId,
          output,
          stepsCompleted: step + 1,
        };
      }
    }

    // Max steps reached
    const output = generateFinalOutput(history, task);
    emit({ type: "agent_end", step: this.config.maxSteps, data: { output, reason: "max_steps" } });
    this.store.setDone(traceId, output);
    this.store.collapseToSteps(traceId);

    return { traceId, output, stepsCompleted: this.config.maxSteps };
  }
}
