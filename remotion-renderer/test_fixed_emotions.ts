/**
 * test_fixed_emotions.ts
 *
 * Key diagnostic: with IDENTICAL emotion inputs, does MCTS still produce
 * the same plan every time (confirming deterministic collapse)?
 *
 * This isolates: "is the collapse due to similar inputs, or due to the search itself?"
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

function makeEmotions(n: number): number[] {
  // FIXED deterministic pattern
  return Array.from({ length: n }, (_, i) => 0.3 + (i % 3) * 0.2);
}

function parseTypes(plan: Map<number, { type: string }>): string[] {
  return Array.from(plan.keys()).sort((a, b) => a - b).map(k => plan.get(k)!.type as string);
}

function main() {
  const fps = 30;
  const shots = makeShots(9);
  const emotions = makeEmotions(9);

  console.log("Testing MCTS with FIXED identical inputs across 30 runs\n");
  console.log(`emotions: [${emotions.join(", ")}]`);
  console.log(`shots: ${shots.length}\n`);

  const sequences: string[] = [];
  for (let run = 0; run < 30; run++) {
    const plan = beamSearchTransitionPlan(shots, emotions, fps, 5);
    const types = parseTypes(plan);
    sequences.push(types.join("->"));
  }

  const unique = [...new Set(sequences)];
  console.log(`Unique sequences: ${unique.length} / 30`);
  console.log(`\nAll unique plans:`);
  for (const seq of unique) {
    const count = sequences.filter(s => s === seq).length;
    console.log(`  ${seq} : ${count} runs`);
  }

  // Now test with simCount=80
  console.log("\n--- sim_count=80 (30 runs, same emotions) ---");
  const sequences80: string[] = [];
  for (let run = 0; run < 30; run++) {
    const plan = beamSearchTransitionPlan(shots, emotions, fps, 80);
    const types = parseTypes(plan);
    sequences80.push(types.join("->"));
  }
  const unique80 = [...new Set(sequences80)];
  console.log(`Unique sequences: ${unique80.length} / 30`);
  for (const seq of unique80) {
    const count = sequences80.filter(s => s === seq).length;
    console.log(`  ${seq} : ${count} runs`);
  }
}

main();
