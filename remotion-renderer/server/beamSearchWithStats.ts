/**
 * beamSearchWithStats — server-only wrapper around beamSearchTransitionPlan
 *
 * Exposes the full (π, E, J) observation payload for UI feedback.
 *
 * This file is server-only (Node.js). It must NOT be imported by any
 * Remotion/TSX bundle code (VideoScene.tsx, VideoScene.react etc.)
 */
import { beamSearchTransitionPlan } from "../remotion/VideoScene.js";
import type { Shot } from "../remotion/types.js";
import type { MctsControlParams, BeamSearchStats } from "../remotion/VideoScene.js";

export interface BeamSearchResult {
  plan: Map<number, { shotIndex: number; type: string; microCutAt?: number; microCutIntensity?: number }>;
  stats: NormalizedBeamSearchStats;
}

/** Stats with π normalized to proper probabilities + entropy + E contributions */
export interface NormalizedBeamSearchStats {
  /** Root-level transition candidates with probability */
  rootChildren: Array<{
    type: string;
    /** Raw rule score (pre-softmax) */
    score: number;
    /** Softmax probability: exp(score) / Σ exp(score_i) */
    prob: number;
    visits: number;
    modelScore: number;
  }>;
  /** π entropy: -Σ p·log(p), measures style diversity */
  piEntropy: number;
  reward: {
    energy_alignment: number;
    entropy: number;
    pacing_smoothness: number;
    micro_cut_semantic: number;
    energy_transition_alignment: number;
    /** Per-feature weighted contribution to total score */
    contrib: {
      energy_alignment: number;
      entropy: number;
      pacing_smoothness: number;
      micro_cut_semantic: number;
      energy_transition_alignment: number;
    };
  };
  control: { E_bias: number; Pi_temp: number; J_noise: number; SIMULATION_COUNT?: number };
}

export function beamSearchWithStats(
  shots: Shot[],
  emotions: number[],
  fps: number,
  simCount: number | undefined,
  controlParams: MctsControlParams | undefined,
): BeamSearchResult {
  let capturedStats: BeamSearchStats | null = null;

  const plan = beamSearchTransitionPlan(
    shots,
    emotions,
    fps,
    simCount,
    controlParams,
    (stats) => { capturedStats = stats; },
  );

  if (!capturedStats) {
    return {
      plan,
      stats: {
        rootChildren: [],
        piEntropy: 0,
        reward: { energy_alignment: 0, entropy: 0, pacing_smoothness: 0, micro_cut_semantic: 0, energy_transition_alignment: 0, contrib: { energy_alignment: 0, entropy: 0, pacing_smoothness: 0, micro_cut_semantic: 0, energy_transition_alignment: 0 } },
        control: { E_bias: 1, Pi_temp: 1, J_noise: 0.25 },
      },
    };
  }

  // ── Normalize π to proper probability distribution ───────────────────────
  const rawStats: BeamSearchStats = capturedStats;
  const scores = rawStats.rootChildren.map(c => c.score);
  const exps = scores.map(s => Math.exp(Math.max(-50, Math.min(50, s)))); // clip for safety
  const expSum = exps.reduce((a, b) => a + b, 0) || 1;
  const probs = exps.map(e => e / expSum);

  // π entropy: -Σ p·log(p)
  const piEntropy = probs.reduce((s, p) => s - (p > 0 ? p * Math.log(p) : 0), 0);

  // ── E contribution = weight × feature ────────────────────────────────────
  // Weights from evaluateFullSequence L895-899
  const W = { energy_alignment: 0.10, entropy: 0.35, pacing_smoothness: 0.20, micro_cut_semantic: 0.10, energy_transition_alignment: 0.25 };
  const R = rawStats.reward;
  const contrib = {
    energy_alignment: W.energy_alignment * (R.energy_alignment ?? 0),
    entropy: W.entropy * (R.entropy ?? 0),
    pacing_smoothness: W.pacing_smoothness * (R.pacing_smoothness ?? 0),
    micro_cut_semantic: W.micro_cut_semantic * (R.micro_cut_semantic ?? 0),
    energy_transition_alignment: W.energy_transition_alignment * (R.energy_transition_alignment ?? 0),
  };

  return {
    plan,
    stats: {
      rootChildren: rawStats.rootChildren.map((c, i) => ({
        type: String(c.type),
        score: c.score,
        prob: probs[i],
        visits: c.visits,
        modelScore: c.modelScore,
      })),
      piEntropy,
      reward: { ...R, contrib },
      control: {
        E_bias: rawStats.control.E_bias ?? 1,
        Pi_temp: rawStats.control.Pi_temp ?? 1,
        J_noise: rawStats.control.J_noise ?? 0.25,
      },
    },
  };
}
