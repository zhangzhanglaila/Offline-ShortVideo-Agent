/**
 * test_mcts_depth_profile.ts
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

function parseTypes(plan: Map<number, { type: string }>): string[] {
  return Array.from(plan.keys()).sort((a, b) => a - b).map(k => plan.get(k)!.type as string);
}

function entropy(labels: string[]): number {
  const counts: Record<string, number> = {};
  for (const l of labels) counts[l] = (counts[l] || 0) + 1;
  const total = labels.length;
  if (total === 0) return 0;
  return -Object.entries(counts).reduce((a, [, c]) => {
    const p = c / total;
    return a + (p > 0 ? p * Math.log2(p) : 0);
  }, 0);
}

function pad(n: number, w: number): string {
  return String(n).padStart(w, " ");
}

function main() {
  const fps = 30;

  // Test 1: depth scaling
  console.log("\n=== DEPTH SCALING (fixed emotions, sim=5) ===");
  for (const nShots of [3, 5, 7, 9]) {
    const emotions = Array.from({ length: nShots }, (_, i) => 0.3 + (i % 3) * 0.2);
    const shots = makeShots(nShots);
    const seqs: string[] = [];
    for (let run = 0; run < 30; run++) {
      const plan = beamSearchTransitionPlan(shots, emotions, fps, 5);
      seqs.push(parseTypes(plan).join("->"));
    }
    const unique = new Set(seqs).size;
    console.log("n=" + pad(nShots, 2) + ": unique=" + pad(unique, 2) + "/30  ratio=" + (unique / 30).toFixed(3));
    const counter: Record<string, number> = {};
    for (const s of seqs) counter[s] = (counter[s] || 0) + 1;
    for (const [seq, cnt] of Object.entries(counter).sort((a, b) => b[1] - a[1]).slice(0, 3)) {
      console.log("       " + seq + " : " + cnt);
    }
  }

  // Test 2: emotion variance
  console.log("\n=== EMOTION VARIANCE (n=9, sim=5) ===");
  const emotionConfigs = [
    { name: "constant_0.5", emos: Array(9).fill(0.5) },
    { name: "alternating_0.3_0.7", emos: Array.from({ length: 9 }, (_, i) => (i % 2 === 0 ? 0.3 : 0.7)) },
    { name: "sinusoidal", emos: Array.from({ length: 9 }, (_, i) => 0.3 + 0.4 * Math.abs(Math.sin(i * 0.8))) },
    { name: "random_0.25_0.75", emos: Array.from({ length: 9 }, () => 0.25 + Math.random() * 0.5) },
  ];
  const shots9 = makeShots(9);
  for (const cfg of emotionConfigs) {
    const seqs: string[] = [];
    for (let run = 0; run < 30; run++) {
      const plan = beamSearchTransitionPlan(shots9, cfg.emos, fps, 5);
      seqs.push(parseTypes(plan).join("->"));
    }
    const unique = new Set(seqs).size;
    console.log(cfg.name + ": unique=" + pad(unique, 2) + "/30  ratio=" + (unique / 30).toFixed(3));
  }

  // Test 3: sim count vs entropy
  console.log("\n=== SIM COUNT vs ENTROPY (n=9, random emotions, 50 runs) ===");
  console.log("sim   unique  mean_H   pos_H[0..8]");
  console.log("-".repeat(55));
  for (const sim of [5, 10, 20, 40, 80]) {
    const seqs: string[] = [];
    for (let run = 0; run < 50; run++) {
      const emos = Array.from({ length: 9 }, () => 0.25 + Math.random() * 0.5);
      const plan = beamSearchTransitionPlan(shots9, emos, fps, sim);
      seqs.push(parseTypes(plan).join("->"));
    }
    const unique = new Set(seqs).size;
    const posTypes: string[][] = Array.from({ length: 9 }, () => []);
    for (const seq of seqs) {
      const types = seq.split("->");
      types.forEach((t, i) => posTypes[i].push(t));
    }
    const entropies = posTypes.map(ts => entropy(ts));
    const meanH = entropies.reduce((a, b) => a + b, 0) / entropies.length;
    const posStr = "[" + entropies.map(h => h.toFixed(2)).join(",") + "]";
    console.log(pad(sim, 4) + "  " + pad(unique, 6) + "  " + meanH.toFixed(3) + "  " + posStr);
  }
}

main();
