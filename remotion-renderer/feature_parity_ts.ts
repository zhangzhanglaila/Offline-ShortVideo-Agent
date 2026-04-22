/**
 * feature_parity_ts.ts — TypeScript side feature vector parity check
 *
 * Compares buildFeatureVector (TS) against a reference that mirrors Python exactly.
 *
 * Run: npx ts-node feature_parity_ts.ts
 */

import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ── Exact copy of the Python reference implementation ────────────────────────
interface RewardFeatures {
  energy_alignment: number;
  entropy: number;
  pacing_smoothness: number;
  micro_cut_semantic: number;
  energy_transition_alignment: number;
}

interface PlanContext {
  shot_count: number;
  fps: number;
  duration_frames: number;
  emotion_histogram: number[];
  energy_histogram: number[];
}

function buildReferenceVector(features: RewardFeatures, ctx: PlanContext): number[] {
  const feat = [
    features.energy_alignment,
    features.entropy,
    features.pacing_smoothness,
    features.micro_cut_semantic,
    features.energy_transition_alignment,
  ];

  const emotion_hist = ctx.emotion_histogram.length >= 10
    ? ctx.emotion_histogram.slice(0, 10)
    : [...ctx.emotion_histogram, ...new Array(10 - ctx.emotion_histogram.length).fill(0)];

  const energy_hist = ctx.energy_histogram.length >= 10
    ? ctx.energy_histogram.slice(0, 10)
    : [...ctx.energy_histogram, ...new Array(10 - ctx.energy_histogram.length).fill(0)];

  const context: number[] = [
    ctx.shot_count / 20.0,
    ctx.fps / 30.0,
    ctx.duration_frames / 3000.0,
    ...emotion_hist,
    ...energy_hist,
  ];

  return [...feat, ...context];
}

// ── Load JSONL ───────────────────────────────────────────────────────────────
const jsonlPath = path.join(__dirname, "dataset/reward_data.jsonl");

if (!fs.existsSync(jsonlPath)) {
  console.error(`[FAIL] Not found: ${jsonlPath}`);
  process.exit(1);
}

const lines = fs.readFileSync(jsonlPath, "utf-8").split("\n").filter(l => l.trim());
const episodes = lines.slice(-200).map(l => JSON.parse(l));

console.log(`\n[parity] Checking ${episodes.length} episodes (TS side) ...\n`);

const fieldNames = [
  "energy_alignment", "entropy", "pacing_smoothness", "micro_cut_semantic",
  "energy_transition_alignment", "shot_count/20", "fps/30", "duration_frames/3000",
  ...Array.from({length: 10}, (_, i) => `emotion_hist[${i}]`),
  ...Array.from({length: 10}, (_, i) => `energy_hist[${i}]`),
];

interface FieldStats { mean: number; std: number; maxAbs: number; }
const stats: FieldStats[] = Array.from({length: 28}, () => ({ mean: 0, std: 0, maxAbs: 0 }));
const allValues: number[][] = Array.from({length: 28}, () => []);

for (const ep of episodes) {
  const x = buildReferenceVector(ep.selected_features, ep.context);
  for (let i = 0; i < 28; i++) allValues[i].push(x[i]);
}

console.log(`{"Field":<30} {"Mean":>12} {"Std":>12} {"MaxAbs":>12}`);
console.log("-".repeat(70));

for (let i = 0; i < 28; i++) {
  const vals = allValues[i];
  const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
  const std = Math.sqrt(vals.reduce((a, b) => a + (b - mean) ** 2, 0) / vals.length);
  const maxAbs = Math.max(...vals.map(Math.abs));
  stats[i] = { mean, std, maxAbs };
  console.log(`  [${i.toString().padStart(2)}]  ${mean.toFixed(6).padStart(12)}  ${std.toFixed(6).padStart(12)}  ${maxAbs.toFixed(6).padStart(12)}  ${fieldNames[i]}`);
}

// Cross-check: reference vs actual buildFeatureVector on first episode
import { buildFeatureVector } from "./remotion/rewardModel.js";
const firstEp = episodes[0];
const xRef = buildReferenceVector(firstEp.selected_features, firstEp.context);
const xActual = buildFeatureVector(firstEp.selected_features, firstEp.context);

let maxDiff = 0, sumDiff = 0;
for (let i = 0; i < 28; i++) {
  const d = Math.abs(xRef[i] - xActual[i]);
  maxDiff = Math.max(maxDiff, d);
  sumDiff += d;
}

console.log(`\n[parity] Cross-check (reference vs buildFeatureVector):`);
console.log(`  max_abs_diff = ${maxDiff.toExponential(4)}`);
console.log(`  mean_diff    = ${(sumDiff / 28).toExponential(4)}`);
if (maxDiff < 1e-10) {
  console.log(`[parity] PASS: buildFeatureVector matches reference`);
} else {
  console.log(`[parity] WARN: buildFeatureVector differs!`);
  console.log(`  ref:   ${JSON.stringify(xRef.slice(0, 5))}...`);
  console.log(`  actual:${JSON.stringify(xActual.slice(0, 5))}...`);
}