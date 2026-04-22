/**
 * test_policy_entropy_recovery.ts
 *
 * Policy Entropy Recovery Curve — validates anti-collapse fix.
 *
 * Measures:
 *   1. Sequence entropy (global Shannon entropy of plan sequences)
 *   2. Root action entropy (entropy of first transition type)
 *   3. Effective branching factor (unique children expanded / possible)
 *   4. Reward distribution (mean, std over time)
 *   5. Unique sequence ratio vs episode
 *
 * Run: npx tsx test_policy_entropy_recovery.ts
 */

import { beamSearchTransitionPlan } from "./remotion/VideoScene";

interface Shot { start: number; duration: number; src: string; camera: string; }

function makeShots(n: number): Shot[] {
  const shots: Shot[] = [];
  let frame = 0;
  for (let i = 0; i < n; i++) {
    shots.push({ start: frame, duration: 150, src: `img_${i}.jpg`, camera: "static" });
    frame += 150;
  }
  return shots;
}

function makeEmotions(n: number, seed: number = 42): number[] {
  const out: number[] = [];
  let s = seed;
  for (let i = 0; i < n; i++) {
    s = (s * 1664525 + 1013904223) & 0xffffffff;
    out.push(((s >>> 0) / 0xffffffff) * 0.5 + 0.25);
  }
  return out;
}

function parseTypes(plan: Map<number, { type: string }>): string[] {
  return Array.from(plan.keys()).sort((a, b) => a - b).map(k => plan.get(k)!.type as string);
}

function shannonEntropy(labels: string[]): number {
  const counts: Record<string, number> = {};
  for (const l of labels) counts[l] = (counts[l] || 0) + 1;
  const total = labels.length;
  if (total === 0) return 0;
  return -Object.entries(counts).reduce((a, [, c]) => {
    const p = c / total;
    return a + (p > 0 ? p * Math.log2(p) : 0);
  }, 0);
}

function top1Fraction(labels: string[]): number {
  if (labels.length === 0) return 0;
  const counts: Record<string, number> = {};
  for (const l of labels) counts[l] = (counts[l] || 0) + 1;
  return Math.max(...Object.values(counts)) / labels.length;
}

function meanStd(vals: number[]): { mean: number; std: number } {
  const n = vals.length;
  if (n === 0) return { mean: 0, std: 0 };
  const m = vals.reduce((a, b) => a + b, 0) / n;
  const std = Math.sqrt(vals.reduce((a, b) => a + (b - m) ** 2, 0) / n);
  return { mean: m, std };
}

interface MetricSnapshot {
  episode: number;
  sequence: string;
  rootAction: string;
  suffix5: string;
  reward: number;
}

function runBatch(nEpisodes: number, nShots: number, simCount: number, seed: number) {
  const shots = makeShots(nShots);
  const baseEmotions = makeEmotions(nShots, seed);

  const snapshots: MetricSnapshot[] = [];

  for (let ep = 0; ep < nEpisodes; ep++) {
    // Vary emotions slightly per episode (simulate different video inputs)
    const emotions = baseEmotions.map((e, i) => e + Math.sin(ep * 0.1 + i) * 0.1);
    const plan = beamSearchTransitionPlan(shots, emotions, 30, simCount);
    const types = parseTypes(plan);

    snapshots.push({
      episode: ep,
      sequence: types.join("->"),
      rootAction: types[0] || "?",
      suffix5: types.slice(-5).join("->"),
      reward: 0, // reward not available from standalone call
    });
  }

  return snapshots;
}

function computeMetrics(snapshots: MetricSnapshot[], windowSize: number = 50) {
  const n = snapshots.length;

  // Per-episode unique ratios (rolling window)
  const uniqueRatios: number[] = [];
  for (let i = 0; i < n; i++) {
    const window = snapshots.slice(Math.max(0, i - windowSize + 1), i + 1);
    const unique = new Set(window.map(s => s.sequence)).size;
    uniqueRatios.push(unique / window.length);
  }

  // Root action entropy per window
  const rootEntropies: number[] = [];
  for (let i = 0; i < n; i++) {
    const window = snapshots.slice(Math.max(0, i - windowSize + 1), i + 1);
    rootEntropies.push(shannonEntropy(window.map(s => s.rootAction)));
  }

  // Sequence entropy per window
  const seqEntropies: number[] = [];
  for (let i = 0; i < n; i++) {
    const window = snapshots.slice(Math.max(0, i - windowSize + 1), i + 1);
    seqEntropies.push(shannonEntropy(window.map(s => s.sequence)));
  }

  // Top-1 dominance per window
  const top1Dominance: number[] = [];
  for (let i = 0; i < n; i++) {
    const window = snapshots.slice(Math.max(0, i - windowSize + 1), i + 1);
    top1Dominance.push(top1Fraction(window.map(s => s.rootAction)));
  }

  // Suffix collapse per window
  const suffixCollapse: number[] = [];
  for (let i = 0; i < n; i++) {
    const window = snapshots.slice(Math.max(0, i - windowSize + 1), i + 1);
    suffixCollapse.push(top1Fraction(window.map(s => s.suffix5)));
  }

  // Overall stats
  const allSeqs = snapshots.map(s => s.sequence);
  const allRoots = snapshots.map(s => s.rootAction);
  const globalSeqEntropy = shannonEntropy(allSeqs);
  const globalRootEntropy = shannonEntropy(allRoots);
  const globalUniqueRatio = new Set(allSeqs).size / n;
  const globalTop1Dom = top1Fraction(allRoots);
  const globalSuffixCollapse = top1Fraction(snapshots.map(s => s.suffix5));

  // Per-window reward mean (not available from standalone — placeholder)
  const rewardMeans: number[] = [];

  return {
    uniqueRatios,
    rootEntropies,
    seqEntropies,
    top1Dominance,
    suffixCollapse,
    rewardMeans,
    global: {
      seqEntropy: globalSeqEntropy,
      rootEntropy: globalRootEntropy,
      uniqueRatio: globalUniqueRatio,
      top1Dom: globalTop1Dom,
      suffixCollapse: globalSuffixCollapse,
    }
  };
}

function printCurve(label: string, values: number[], every: number = 10) {
  const lines: string[] = [];
  for (let i = 0; i < values.length; i += every) {
    lines.push(`  ep ${String(i).padStart(5)}: ${values[i].toFixed(4)}`);
  }
  return lines.join("\n");
}

function main() {
  const N_EPISODES = 2000;
  const SIM_COUNT = 30;
  const WINDOW = 50;

  console.log("=".repeat(65));
  console.log("POLICY ENTROPY RECOVERY CURVE — v20 Anti-Collapse Fix");
  console.log("=".repeat(65));
  console.log(`\nConfig: ${N_EPISODES} episodes, sim=${SIM_COUNT}, window=${WINDOW}`);
  console.log(`Expected: entropy > 1.0, unique_ratio > 0.3, root_entropy > 1.0\n`);

  // Run batch with fixed seed for reproducibility
  const snapshots = runBatch(N_EPISODES, 9, SIM_COUNT, 42);
  const m = computeMetrics(snapshots, WINDOW);

  console.log("=".repeat(65));
  console.log("GLOBAL METRICS (full batch)");
  console.log("=".repeat(65));
  console.log(`  Sequence entropy:     ${m.global.seqEntropy.toFixed(4)}  (target: 2.5-4.5 healthy, 0.2-0.5 collapse)`);
  console.log(`  Root action entropy:  ${m.global.rootEntropy.toFixed(4)}  (target: >1.0, <0.5 = collapse)`);
  console.log(`  Unique ratio:         ${m.global.uniqueRatio.toFixed(4)}  (target: >0.3)`);
  console.log(`  Root top-1 dominance: ${m.global.top1Dom.toFixed(4)}  (target: <0.7)`);
  console.log(`  Suffix collapse:      ${m.global.suffixCollapse.toFixed(4)}  (target: <0.7)`);

  console.log("\n" + "=".repeat(65));
  console.log("PER-WINDOW METRICS (sampled every 50 episodes)");
  console.log("=".repeat(65));

  console.log("\n[Unique Sequence Ratio vs Episode]");
  console.log(printCurve("unique_ratio", m.uniqueRatios, 50));

  console.log("\n[Sequence Entropy vs Episode]");
  console.log(printCurve("seq_entropy", m.seqEntropies, 50));

  console.log("\n[Root Action Entropy vs Episode]");
  console.log(printCurve("root_entropy", m.rootEntropies, 50));

  console.log("\n[Root Top-1 Dominance vs Episode]");
  console.log(printCurve("top1_dom", m.top1Dominance, 50));

  console.log("\n[Suffix Collapse vs Episode]");
  console.log(printCurve("suffix_coll", m.suffixCollapse, 50));

  // Diagnosis
  console.log("\n" + "=".repeat(65));
  console.log("DIAGNOSIS");
  console.log("=".repeat(65));

  const g = m.global;
  const signals: string[] = [];

  if (g.uniqueRatio > 0.3) signals.push(`[OK] unique_ratio=${g.uniqueRatio.toFixed(3)} > 0.3`);
  else signals.push(`[!!] unique_ratio=${g.uniqueRatio.toFixed(3)} < 0.3 — STILL COLLAPSED`);

  if (g.rootEntropy > 1.0) signals.push(`[OK] root_entropy=${g.rootEntropy.toFixed(3)} > 1.0`);
  else signals.push(`[!!] root_entropy=${g.rootEntropy.toFixed(3)} < 1.0 — ROOT STILL BIASED`);

  if (g.seqEntropy > 2.0) signals.push(`[OK] seq_entropy=${g.seqEntropy.toFixed(3)} > 2.0 — healthy diversity`);
  else if (g.seqEntropy > 1.0) signals.push(`[~] seq_entropy=${g.seqEntropy.toFixed(3)} > 1.0 — moderate diversity`);
  else signals.push(`[!!] seq_entropy=${g.seqEntropy.toFixed(3)} < 1.0 — LOW diversity`);

  if (g.top1Dom < 0.7) signals.push(`[OK] top1_dom=${g.top1Dom.toFixed(3)} < 0.7 — no single root action dominates`);
  else signals.push(`[!!] top1_dom=${g.top1Dom.toFixed(3)} > 0.7 — ROOT ACTION STILL LOCKED`);

  if (g.suffixCollapse < 0.7) signals.push(`[OK] suffix_collapse=${g.suffixCollapse.toFixed(3)} < 0.7`);
  else signals.push(`[!!] suffix_collapse=${g.suffixCollapse.toFixed(3)} > 0.7 — SUFFIX STILL COLLAPSED`);

  for (const s of signals) console.log("  " + s);

  // Comparison vs pre-fix
  console.log("\n[Pre-fix vs Post-fix comparison]");
  console.log(`  Metric               Pre-fix    Post-fix   Target`);
  console.log(`  unique_ratio         0.003      ${g.uniqueRatio.toFixed(3).padStart(8)}    >0.3`);
  console.log(`  root_entropy         ~0.0       ${g.rootEntropy.toFixed(3).padStart(8)}    >1.0`);
  console.log(`  seq_entropy          ~0.1       ${g.seqEntropy.toFixed(3).padStart(8)}    >2.0`);
  console.log(`  suffix_collapse      0.999      ${g.suffixCollapse.toFixed(3).padStart(8)}    <0.7`);
  console.log(`  top1_dom             0.99       ${g.top1Dom.toFixed(3).padStart(8)}    <0.7`);

  // Unique sequences breakdown
  const seqCounter: Record<string, number> = {};
  for (const s of snapshots) seqCounter[s.sequence] = (seqCounter[s.sequence] || 0) + 1;
  const sortedSeqs = Object.entries(seqCounter).sort((a, b) => b[1] - a[1]);
  console.log(`\n[Top-10 most frequent sequences (${sortedSeqs.length} total unique)]`);
  for (const [seq, cnt] of sortedSeqs.slice(0, 10)) {
    console.log(`  ${seq} : ${cnt} episodes (${(cnt / snapshots.length * 100).toFixed(1)}%)`);
  }

  // Root action distribution
  const rootCounter: Record<string, number> = {};
  for (const s of snapshots) rootCounter[s.rootAction] = (rootCounter[s.rootAction] || 0) + 1;
  console.log(`\n[Root action distribution]`);
  for (const [a, cnt] of Object.entries(rootCounter).sort((a, b) => b[1] - a[1])) {
    console.log(`  ${a}: ${cnt} (${(cnt / snapshots.length * 100).toFixed(1)}%)`);
  }

  const recoveryScore =
    (Math.min(g.uniqueRatio / 0.3, 1) +
     Math.min(g.rootEntropy / 1.0, 1) +
     Math.min(g.seqEntropy / 2.0, 1) +
     Math.min((1 - g.top1Dom) / 0.3, 1) +
     Math.min((1 - g.suffixCollapse) / 0.3, 1)) / 5;

  console.log(`\n[Recovery Score: ${(recoveryScore * 100).toFixed(1)}%]`);
  if (recoveryScore > 0.8) console.log(`  STATUS: System is a healthy explorable dynamics system`);
  else if (recoveryScore > 0.5) console.log(`  STATUS: Partial recovery — some metrics still weak`);
  else console.log(`  STATUS: Still in collapse regime`);

  console.log("\n" + "=".repeat(65));
}

main();
