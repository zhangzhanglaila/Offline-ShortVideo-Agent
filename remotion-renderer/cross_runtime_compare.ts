/**
 * cross_runtime_compare.ts
 *
 * Reads parity_python_vectors.json (Python feature vectors for N episodes)
 * and compares them against the TS buildFeatureVector output for the same episodes.
 *
 * Run: npx tsx cross_runtime_compare.ts
 */

import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
import { buildFeatureVector } from "./remotion/rewardModel.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const jsonlPath = path.join(__dirname, "dataset/reward_data.jsonl");
const pythonVecPath = path.join(__dirname, "dataset/parity_python_vectors.json");

if (!fs.existsSync(jsonlPath)) {
  console.error(`[FAIL] Not found: ${jsonlPath}`);
  process.exit(1);
}
if (!fs.existsSync(pythonVecPath)) {
  console.error(`[FAIL] Not found: ${pythonVecPath}`);
  console.error(`  Run cross_runtime_parity.py first`);
  process.exit(1);
}

const pythonResults: { episode_id: string; vector: number[]; reward: number }[] = JSON.parse(
  fs.readFileSync(pythonVecPath, "utf-8")
);

const lines = fs.readFileSync(jsonlPath, "utf-8").split("\n").filter(l => l.trim());
const episodes = lines.slice(-pythonResults.length).map(l => JSON.parse(l));

console.log(`\n[parity] Cross-runtime comparison: ${pythonResults.length} episodes`);
console.log(`{"Index":<6} {"Field":<28} {"Python":>10} {"TS":>10} {"Diff":>10} {"MAE":>10}`);
console.log("-".repeat(78));

const fieldNames = [
  "energy_alignment", "entropy", "pacing_smoothness", "micro_cut_semantic",
  "energy_transition_alignment", "shot_count/20", "fps/30", "duration_frames/3000",
  ...Array.from({length: 10}, (_, i) => `emotion_hist[${i}]`),
  ...Array.from({length: 10}, (_, i) => `energy_hist[${i}]`),
];

const fieldDiffs: number[][] = Array.from({length: 28}, () => []);

for (let i = 0; i < pythonResults.length; i++) {
  const pyVec = pythonResults[i].vector;
  const ep = episodes[i];
  const tsVec = buildFeatureVector(ep.selected_features, ep.context);
  for (let j = 0; j < 28; j++) {
    fieldDiffs[j].push(tsVec[j] - pyVec[j]);
  }
}

console.log(`\nPer-field cross-runtime MAE (Python vs TypeScript):\n`);
console.log(`{"Field":<30} {"MAE":>10} {"MAX_ABS":>10} {"Std":>10}`);
console.log("-".repeat(55));

let maxFieldMAE = 0;
for (let j = 0; j < 28; j++) {
  const diffs = fieldDiffs[j];
  const mae = diffs.reduce((a, b) => a + Math.abs(b), 0) / diffs.length;
  const maxAbs = Math.max(...diffs.map(Math.abs));
  const std = Math.sqrt(diffs.reduce((a, b) => a + b * b, 0) / diffs.length);
  maxFieldMAE = Math.max(maxFieldMAE, mae);
  console.log(
    `  [${j.toString().padStart(2)}] ${fieldNames[j].padEnd(28)} ${mae.toExponential(4).padStart(10)} ${maxAbs.toExponential(4).padStart(10)} ${std.toExponential(4).padStart(10)}`
  );
}

console.log("\n" + "=".repeat(55));
console.log(`[parity] MAX FIELD MAE: ${maxFieldMAE.toExponential(4)}`);

if (maxFieldMAE < 1e-3) {
  console.log(`[parity] CASE A: MAE < 1e-3  → TS cleanup + naming unification`);
} else if (maxFieldMAE < 1e-2) {
  console.log(`[parity] CASE B: MAE 1e-3 ~ 1e-2 → align TS to Python`);
} else {
  console.log(`[parity] CASE C: MAE > 1e-2 → rewrite TS from Python spec`);
}
console.log("=".repeat(55));
