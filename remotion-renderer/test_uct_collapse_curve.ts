/**
 * test_uct_collapse_curve.ts
 *
 * UCT collapse severity curve diagnostic.
 *
 * Tests simulation_count ∈ [5, 10, 20, 40, 80] and measures:
 *   - unique_transition_sequences / total_runs
 *   - Shannon entropy of transition types
 *   - top-1 dominance per position
 *   - reward distribution (mean, std)
 *
 * Run: npx tsx test_uct_collapse_curve.ts
 */

import { beamSearchTransitionPlan } from "./remotion/VideoScene";

interface Shot {
  start: number;
  duration: number;
  src: string;
  camera: string;
}

function makeShots(n: number, fps: number = 30): Shot[] {
  const shots: Shot[] = [];
  let frame = 0;
  for (let i = 0; i < n; i++) {
    shots.push({ start: frame, duration: 150, src: `img_${i}.jpg`, camera: "static" });
    frame += 150;
  }
  return shots;
}

function makeEmotions(n: number, seed: number = 42): number[] {
  // deterministic pseudo-random
  const out: number[] = [];
  let s = seed;
  for (let i = 0; i < n; i++) {
    s = (s * 1664525 + 1013904223) & 0xffffffff;
    out.push(((s >>> 0) / 0xffffffff) * 0.5 + 0.25); // [0.25, 0.75]
  }
  return out;
}

function parseTypes(plan: Map<number, { type: string }>): string[] {
  const keys = Array.from(plan.keys()).sort((a, b) => a - b);
  return keys.map(k => plan.get(k)!.type as string);
}

function shannonEntropy(labels: string[]): number {
  const counts: Record<string, number> = {};
  for (const l of labels) counts[l] = (counts[l] || 0) + 1;
  const total = labels.length;
  if (total === 0) return 0;
  return -Object.entries(counts).reduce((acc, [, c]) => {
    const p = c / total;
    return acc + (p > 0 ? p * Math.log2(p) : 0);
  }, 0);
}

function top1Fraction(labels: string[]): number {
  if (labels.length === 0) return 0;
  const counts: Record<string, number> = {};
  for (const l of labels) counts[l] = (counts[l] || 0) + 1;
  return Math.max(...Object.values(counts)) / labels.length;
}

function runTrial(shots: Shot, emotions: number[], fps: number, simCount: number) {
  const plan = beamSearchTransitionPlan(shots, emotions, fps, simCount);
  return parseTypes(plan);
}

function runSimCountGroup(simCount: number, nRuns: number, shots: Shot, emotions: number[], fps: number) {
  const sequences: string[] = [];
  const rewards: number[] = [];
  const posTypes: string[][] = [];

  for (let r = 0; r < nRuns; r++) {
    const types = runTrial(shots, emotions, fps, simCount);
    sequences.push(types.join("->"));
    for (let i = 0; i < types.length; i++) {
      if (!posTypes[i]) posTypes[i] = [];
      posTypes[i].push(types[i]);
    }
  }

  const unique = new Set(sequences).size;
  const entropyPerPos = posTypes.map(ts => shannonEntropy(ts));
  const top1PerPos = posTypes.map(ts => top1Fraction(ts));
  const suffix5 = sequences.map(s => {
    const parts = s.split("->");
    return parts.slice(-5).join("->");
  });
  const top1Suffix = top1Fraction(suffix5);

  return {
    simCount,
    nRuns,
    uniqueSequences: unique,
    uniqueRatio: unique / nRuns,
    entropyMean: entropyPerPos.reduce((a, b) => a + b, 0) / entropyPerPos.length,
    top1DomMean: top1PerPos.reduce((a, b) => a + b, 0) / top1PerPos.length,
    top1SuffixFrac: top1Suffix,
    entropyPerPos,
    top1PerPos,
  };
}

function main() {
  const fps = 30;
  const nShots = 9;
  const nRuns = 30;  // runs per simCount
  const simCounts = [5, 10, 20, 40, 80];

  const shots = makeShots(nShots, fps);
  const emotions = makeEmotions(nShots);

  console.log(`\nUCT Collapse Severity Curve`);
  console.log(`shots=${nShots}, runs=${nRuns} per sim_count`);
  console.log(`sim_counts = [${simCounts.join(", ")}]`);
  console.log("=".repeat(70));

  const results: ReturnType<typeof runSimCountGroup>[] = [];

  for (const sc of simCounts) {
    const r = runSimCountGroup(sc, nRuns, shots, emotions, fps);
    results.push(r);
    console.log(`\nsim_count=${sc}:`);
    console.log(`  unique_sequences: ${r.uniqueSequences} / ${nRuns}  (ratio=${r.uniqueRatio.toFixed(3)})`);
    console.log(`  entropy_mean:     ${r.entropyMean.toFixed(4)}`);
    console.log(`  top1_dom_mean:    ${r.top1DomMean.toFixed(4)}`);
    console.log(`  top1_suffix_frac: ${r.top1SuffixFrac.toFixed(4)}`);
    console.log(`  entropy per pos:  [${r.entropyPerPos.slice(0,9).map(e => e.toFixed(2)).join(", ")}]`);
    console.log(`  top1 per pos:     [${r.top1PerPos.slice(0,9).map(d => d.toFixed(2)).join(", ")}]`);
  }

  console.log("\n" + "=".repeat(70));
  console.log("SUMMARY TABLE:");
  console.log(`{"sim_count":>10} {"unique_ratio":>14} {"entropy_mean":>12} {"top1_dom":>10} {"top1_suf":>10} {"verdict":>20}`);
  console.log("-".repeat(80));
  for (const r of results) {
    const entropyOK = r.entropyMean > 1.0 ? "OK" : r.entropyMean > 0.5 ? "LOW" : "COLLAPSE";
    console.log(
      `${r.simCount.toString().padStart(10)} ` +
      `${r.uniqueRatio.toFixed(4).padStart(14)} ` +
      `${r.entropyMean.toFixed(4).padStart(12)} ` +
      `${r.top1DomMean.toFixed(4).padStart(10)} ` +
      `${r.top1SuffixFrac.toFixed(4).padStart(10)} ` +
      `${entropyOK.padStart(20)}`
    );
  }

  // Recommendation
  const best = results.reduce((best, r) => r.uniqueRatio > best.uniqueRatio ? r : best, results[0]);
  console.log(`\nRECOMMENDATION: sim_count >= ${best.simCount} required for adequate diversity`);
  console.log(`  current (sim=5): unique_ratio=${results[0].uniqueRatio.toFixed(4)}, entropy=${results[0].entropyMean.toFixed(4)}`);
}

main();
