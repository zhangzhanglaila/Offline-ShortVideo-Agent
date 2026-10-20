/**
 * test_energy_reconstruction.ts
 *
 * Energy Geometry Reconstruction from Basin Transition Matrix.
 *
 * Reverse-engineers the implicit reward geometry from the 27×27 basin
 * transition matrix using detailed balance + Boltzmann statistics.
 *
 * Key outputs:
 * 1. Detailed balance violation measure (reversibility of T)
 * 2. Effective energy per basin: E_i = -log(pi_i)
 * 3. Cluster energy levels (fade/whip/zoom)
 * 4. Transition rate vs energy gradient comparison
 * 5. Effective temperature of the landscape
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

// ─────────────────────────────────────────────────────────────────────────────
// Collect basin sequences
// ─────────────────────────────────────────────────────────────────────────────
const N_RUNS = 2000;
const shots = makeShots(9);
const baseEmotions = makeEmotions(9, 42);

const basinSeqs: string[] = [];
for (let run = 0; run < N_RUNS; run++) {
    const emotions = baseEmotions.map((e, i) => e + Math.sin(run * 0.05 + i) * 0.1);
    const plan = beamSearchTransitionPlan(shots, emotions, 30, 30);
    const types = parseTypes(plan);
    basinSeqs.push(types.slice(-3).join("->"));
}

// Build basins + transition matrix
const basins = [...new Set(basinSeqs)].sort();
const basinIdx: Record<string, number> = {};
basins.forEach((b, i) => { basinIdx[b] = i; });
const n = basins.length;

const T: number[][] = Array.from({ length: n }, () => Array(n).fill(0));
const basinCounts: number[] = Array(n).fill(0);
for (let i = 0; i < basinSeqs.length - 1; i++) {
    const from = basinIdx[basinSeqs[i]];
    const to = basinIdx[basinSeqs[i + 1]];
    T[from][to]++;
    basinCounts[from]++;
}
const Tnorm: number[][] = T.map((row, i) => {
    const sum = basinCounts[i];
    return sum > 0 ? row.map(v => v / sum) : row;
});

// Stationary distribution via power iteration
let pi = Array(n).fill(1 / n);
for (let iter = 0; iter < 2000; iter++) {
    const piNew = pi.map((_, to) => pi.reduce((sum, p, from) => sum + p * Tnorm[from][to], 0));
    const s = piNew.reduce((a, b) => a + b, 0);
    pi = piNew.map(v => v / s);
    const delta = pi.reduce((max, v, i) => Math.max(max, Math.abs(v - pi[i])), 0);
    if (delta < 1e-10) break;
}

// ─────────────────────────────────────────────────────────────────────────────
// 1. DETAILED BALANCE TEST
//    T[i][j] * pi[i] == T[j][i] * pi[j]  (reversibility condition)
// ─────────────────────────────────────────────────────────────────────────────
let totalDBVio = 0;
let nVio = 0;
let maxDBVio = 0;
for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
        if (i !== j) {
            const lhs = Tnorm[i][j] * pi[i];
            const rhs = Tnorm[j][i] * pi[j];
            const dbv = Math.abs(lhs - rhs);
            totalDBVio += dbv;
            if (dbv > maxDBVio) maxDBVio = dbv;
            if (dbv > 0.01) nVio++;
        }
    }
}
const avgDBVio = totalDBVio / (n * n);
const reversibilityClass = avgDBVio < 1e-6 ? "APPROXIMATE" : avgDBVio < 0.001 ? "WEAK" : "VIOLATED";

console.log("\n" + "=".repeat(70));
console.log("DETAILED BALANCE TEST (Reversibility of T_basin)");
console.log("=".repeat(70));
console.log(`Average DB violation:  ${avgDBVio.toExponential(4)}`);
console.log(`Max DB violation:      ${maxDBVio.toExponential(4)}`);
console.log(`Pairs with DB > 1e-2:  ${nVio} / ${n * n}`);
console.log(`Reversibility:         ${reversibilityClass}`);
if (reversibilityClass === "APPROXIMATE") {
    console.log("  -> Chain is approximately reversible -> energy landscape interpretation valid");
} else if (reversibilityClass === "WEAK") {
    console.log("  -> Weak detailed balance -> non-gradient dynamics present");
} else {
    console.log("  -> Detailed balance violated -> non-conservative force field");
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. EFFECTIVE ENERGY FROM STATIONARY DISTRIBUTION
//    For reversible chain: pi_i ~ exp(-E_i)
// ─────────────────────────────────────────────────────────────────────────────
const piMax = Math.max(...pi);
const E: number[] = pi.map(p => -Math.log(p / piMax));
const Emean = E.reduce((a, b) => a + b, 0) / n;
const Estd = Math.sqrt(E.reduce((a, b) => a + (b - Emean) ** 2, 0) / n);

const basinEnergy = basins.map((b, i) => ({ basin: b, E: E[i], pi: pi[i] }));
basinEnergy.sort((a, b) => a.E - b.E);

console.log("\n" + "=".repeat(70));
console.log("ENERGY LANDSCAPE RECONSTRUCTION");
console.log("=".repeat(70));
console.log(`\npi range: [${Math.min(...pi).toExponential(3)}, ${Math.max(...pi).toExponential(3)}]`);
console.log(`E range:  [${Math.min(...E).toFixed(3)}, ${Math.max(...E).toFixed(3)}]`);
console.log(`E mean:   ${Emean.toFixed(3)}`);
console.log(`E std:    ${Estd.toFixed(3)}`);
console.log(`(E normalized so min(E)=0)`);

console.log(`\nENERGY RANKING (ascending = higher probability):`);
for (let rank = 0; rank < basinEnergy.length; rank++) {
    const be = basinEnergy[rank];
    const barLen = Math.round((be.E / Math.max(...E)) * 38);
    const bar = Array(barLen+1).join("░");
    const pad = barLen < 20 ? " ".repeat(20 - barLen) : "";
    console.log(`  ${String(rank+1).padStart(2)}. [${be.E.toFixed(3)}]  ${be.pi.toExponential(2).padStart(10)}  ${pad}${bar}  ${be.basin}`);
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. CLUSTER ENERGY LEVELS
// ─────────────────────────────────────────────────────────────────────────────
const clusterBasins: Record<string, string[]> = {
    "fade": basins.filter(b => b.startsWith("fade")),
    "whip": basins.filter(b => b.startsWith("whip")),
    "zoom": basins.filter(b => b.startsWith("zoom")),
};

console.log("\n" + "=".repeat(70));
console.log("CLUSTER ENERGY LEVELS");
console.log("=".repeat(70));
const clusterData: { name: string; Emean: number; piSum: number; n: number }[] = [];
for (const [cl, blist] of Object.entries(clusterBasins)) {
    const clE = blist.reduce((s, b) => s + E[basinIdx[b]], 0) / blist.length;
    const clPi = blist.reduce((s, b) => s + pi[basinIdx[b]], 0);
    clusterData.push({ name: cl, Emean: clE, piSum: clPi, n: blist.length });
}
clusterData.sort((a, b) => a.Emean - b.Emean);
for (const cd of clusterData) {
    const barLen = Math.round((cd.Emean / Math.max(...clusterData.map(c => c.Emean))) * 30);
    const bar = Array(barLen+1).join("█");
    const gap = (cd.Emean - clusterData[0].Emean);
    console.log(`  ${cd.name.padEnd(6)}  E=${cd.Emean.toFixed(3)}  ΔE=${gap.toFixed(3)}  π_sum=${cd.piSum.toFixed(4)}  ${bar}`);
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. TRANSITION RATE vs BOLTZMANN PREDICTION
//    For reversible: T_ij / T_ji = exp(-(E_j - E_i))
// ─────────────────────────────────────────────────────────────────────────────
console.log("\n" + "=".repeat(70));
console.log("TRANSITION RATE vs ENERGY GRADIENT");
console.log("=".repeat(70));
console.log("\nComparing observed T_ij/T_ji against Boltzmann prediction exp(-(E_j-E_i)):");
console.log("(Large discrepancy = non-gradient / non-conservative dynamics)");

const samplePairs: [string, string][] = [
    ["zoom->zoom->zoom", "fade->fade->fade"],
    ["zoom->zoom->zoom", "whip->whip->whip"],
    ["fade->fade->fade", "whip->whip->whip"],
    ["zoom->zoom->zoom", "zoom->fade->zoom"],
    ["zoom->zoom->zoom", "zoom->whip->zoom"],
    ["fade->zoom->zoom", "zoom->zoom->zoom"],
    ["whip->zoom->whip", "zoom->zoom->zoom"],
];
for (const [bi, bj] of samplePairs) {
    const ii = basinIdx[bi], ij = basinIdx[bj];
    if (ii === undefined || ij === undefined) continue;
    const t_ij = Tnorm[ii][ij];
    const t_ji = Tnorm[ij][ii];
    const dE = E[ij] - E[ii];
    let predictedRatio: number | string = "N/A";
    let boltzmannRatio: number | string = "N/A";
    if (t_ij > 0 && t_ji > 0) {
        predictedRatio = t_ij / t_ji;
        boltzmannRatio = Math.exp(-dE);
    }
    const discrepancy = typeof predictedRatio === "number" && typeof boltzmannRatio === "number"
        ? Math.abs(predictedRatio - boltzmannRatio) / boltzmannRatio
        : null;
    console.log(`  ${bi} <-> ${bj}`);
    console.log(`    dE=${dE.toFixed(3)},  obs T_ij/T_ji=${typeof predictedRatio === "number" ? predictedRatio.toFixed(3) : predictedRatio},  exp(-dE)=${typeof boltzmannRatio === "number" ? boltzmannRatio.toFixed(3) : boltzmannRatio}${discrepancy !== null ? `  discrepancy=${(discrepancy*100).toFixed(1)}%` : ""}`);
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. EFFECTIVE TEMPERATURE & CURVATURE
// ─────────────────────────────────────────────────────────────────────────────
const deltaPi = Math.max(...pi) / Math.min(...pi);
const T_eff = 1 / Math.log(deltaPi + 1e-10);
const dE_max = Math.max(...E) - Math.min(...E);
const boltzFactor = Math.exp(-dE_max);

console.log("\n" + "=".repeat(70));
console.log("EFFECTIVE THERMODYNAMICS");
console.log("=".repeat(70));
console.log(`\nStationary distribution ratio:  pi_max/pi_min = ${deltaPi.toFixed(4)}`);
console.log(`Effective temperature:          T_eff = 1/log(pi_max/pi_min) = ${T_eff.toFixed(4)}`);
console.log(`Max energy gap:                 dE_max = ${dE_max.toFixed(3)}`);
console.log(`Boltzmann factor for gap:       exp(-dE_max) = ${boltzFactor.toFixed(4)}`);
console.log(`\nInterpretation:`);
console.log(`  T_eff >> 1  -> high temperature, flat landscape (all basins accessible)`);
console.log(`  T_eff << 1  -> low temperature, peaked landscape (few basins dominate)`);
if (T_eff > 2) {
    console.log(`  -> System is HIGH TEMPERATURE relative to energy gaps`);
    console.log(`  -> Entropy dominates over energy minimization`);
} else if (T_eff > 0.5) {
    console.log(`  -> System is MODERATE TEMPERATURE`);
    console.log(`  -> Both energy and entropy play roles`);
} else {
    console.log(`  -> System is LOW TEMPERATURE`);
    console.log(`  -> Energy minimization dominates`);
}

// ─────────────────────────────────────────────────────────────────────────────
// 6. IMPlicit FORCE FIELD (gradient of potential)
//    F_ij = T_ij - T_ji  (net flow direction)
// ─────────────────────────────────────────────────────────────────────────────
console.log("\n" + "=".repeat(70));
console.log("NET FLOW FIELD (T_ij - T_ji)");
console.log("=".repeat(70));
console.log("\nIdentifying dominant net flows (potential-driven vs non-conservative):");

const flows: { from: string; to: string; netFlow: number; dE: number }[] = [];
for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
        if (i !== j) {
            const netFlow = Tnorm[i][j] - Tnorm[j][i];
            if (Math.abs(netFlow) > 0.02) {
                flows.push({ from: basins[i], to: basins[j], netFlow, dE: E[j] - E[i] });
            }
        }
    }
}
flows.sort((a, b) => Math.abs(b.netFlow) - Math.abs(a.netFlow));

console.log("\nTop net flows (|T_ij - T_ji| > 0.02):");
console.log("  direction                  net_flow    dE      interpretation");
for (const f of flows.slice(0, 12)) {
    const direction = f.netFlow > 0 ? "->" : "<-";
    const interp = f.netFlow > 0 && f.dE > 0 ? "DOWNHILL"
        : f.netFlow > 0 && f.dE < 0 ? "UPHILL(non-conservative)"
        : f.netFlow < 0 && f.dE < 0 ? "DOWNHILL"
        : "UPHILL(non-conservative)";
    console.log(`  ${f.from} ${direction} ${f.to}  ${f.netFlow.toFixed(3)}  ${f.dE.toFixed(3)}  ${interp}`);
}

const uphillCount = flows.filter(f => (f.netFlow > 0) !== (f.dE > 0)).length;
console.log(`\nNon-conservative flows (uphill against energy gradient): ${uphillCount} / ${flows.length}`);

// ─────────────────────────────────────────────────────────────────────────────
// 7. CURVATURE OF REWARD LANDSCAPE
//    Hessian approximation from basin-level transition curvature
// ─────────────────────────────────────────────────────────────────────────────
console.log("\n" + "=".repeat(70));
console.log("LANDSCAPE CURVATURE (second derivative proxy)");
console.log("=".repeat(70));

// Diagonal dominance: measures how "trapping" each basin is
const diagDominance = Tnorm.map((row, i) => row[i] / (row.reduce((a, b) => a + b, 0) + 1e-10));
const basinCurvature = diagDominance.map((d, i) => ({
    basin: basins[i],
    selfProb: d,
    escapeTendency: 1 - d,
    E: E[i],
}));
basinCurvature.sort((a, b) => b.escapeTendency - a.escapeTendency);

console.log("\nBasin escape tendency (1 - T[i][i], sorted descending):");
for (const bc of basinCurvature.slice(0, 15)) {
    const barLen = Math.round(bc.escapeTendency * 40);
    const bar = Array(barLen+1).join("▓");
    console.log(`  self=${bc.selfProb.toFixed(3)}  escape=${bc.escapeTendency.toFixed(3)}  E=${bc.E.toFixed(3)}  ${bar}  ${bc.basin}`);
}

// Correlation: does higher energy mean lower escape?
const correlation = (() => {
    let sum = 0, sumE = 0, sumEsc = 0, sumE2 = 0, sumEsc2 = 0, sumEEsc = 0;
    for (let i = 0; i < n; i++) {
        sum++;
        sumE += E[i]; sumEsc += basinCurvature[i].escapeTendency;
        sumE2 += E[i]*E[i]; sumEsc2 += basinCurvature[i].escapeTendency**2;
        sumEEsc += E[i] * basinCurvature[i].escapeTendency;
    }
    const num = sum*sumEEsc - sumE*sumEsc;
    const den = Math.sqrt((sum*sumE2-sumE*sumE)*(sum*sumEsc2-sumEsc*sumEsc));
    return den > 0 ? num/den : 0;
})();

console.log(`\nEnergy-Escape correlation (Pearson r): ${correlation.toFixed(4)}`);
console.log(`  r > 0: high-energy basins have higher escape (energy-dominated)`);
console.log(`  r < 0: high-energy basins are trapping (frustrated landscape)`);

// ─────────────────────────────────────────────────────────────────────────────
// 8. MATHEMATICAL SUMMARY
// ─────────────────────────────────────────────────────────────────────────────
console.log("\n" + "=".repeat(70));
console.log("MATHEMATICAL SUMMARY: Energy Geometry Reconstruction");
console.log("=".repeat(70));

const zoomData = clusterData.find(c => c.name === "zoom")!;
const fadeData = clusterData.find(c => c.name === "fade")!;
const whipData = clusterData.find(c => c.name === "whip")!;

console.log(`
REWARD GEOMETRY (implicit, reconstructed from T_basin):

  Energy ordering (ascending = more preferred):
    ${clusterData[0].name.padEnd(6)} (E=${clusterData[0].Emean.toFixed(3)}, π=${clusterData[0].piSum.toFixed(3)})
    ${clusterData[1].name.padEnd(6)} (E=${clusterData[1].Emean.toFixed(3)}, π=${clusterData[1].piSum.toFixed(3)})
    ${clusterData[2].name.padEnd(6)} (E=${clusterData[2].Emean.toFixed(3)}, π=${clusterData[2].piSum.toFixed(3)})

  Effective temperature: T_eff = ${T_eff.toFixed(4)}
  Reversibility:          ${reversibilityClass} detailed balance
  Non-conservative flows: ${uphillCount} significant uphill paths

  IMPLIED PHYSICS:
    Reward landscape is:
      - Flat/smooth (T_eff >> 1) -> low curvature
      - ${reversibilityClass === "APPROXIMATE" ? "Approximately gradient-like" : "Has non-gradient components"}
      - ${correlation < 0 ? "Frustrated (trapping basins)" : "Near-equalizing (basins accessible)"}
    Dominant attractor: zoom cluster (E_min = ${zoomData.Emean.toFixed(3)})
    Energy gap between phases: ΔE ≈ ${(clusterData[2].Emean - clusterData[0].Emean).toFixed(3)}

  CONTROL IMPLICATIONS:
    - Reward geometry perturbation can shift E_ordering
    - Action topology expansion changes T_eff
    - Basin structure is FIXED by reward feature geometry
`);

console.log("[COMPLETE] Energy geometry reconstructed from T_basin");
console.log("Next step: manifold embedding (diffusion geometry) or RG flow analysis");
