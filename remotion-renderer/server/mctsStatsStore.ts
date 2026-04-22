/**
 * mctsStatsStore — shared mutable store for latest MCTS stats
 *
 * Flow:
 *   collectRewardData (agentOrchestrator) → writes latest stats
 *   WebSocket handler (index.ts)          → reads + broadcasts to all clients
 *
 * Only the latest stats per session are kept (simple, sufficient for demo).
 */
export interface MctsStats {
  jobId: string;
  timestamp: number;
  rootChildren: Array<{ type: string; score: number; prob: number; visits: number; modelScore: number }>;
  piEntropy: number;
  selected: string;
  reward: {
    energy_alignment: number;
    entropy: number;
    pacing_smoothness: number;
    micro_cut_semantic: number;
    energy_transition_alignment: number;
    contrib: {
      energy_alignment: number;
      entropy: number;
      pacing_smoothness: number;
      micro_cut_semantic: number;
      energy_transition_alignment: number;
    };
  };
  control: { E_bias: number; Pi_temp: number; J_noise: number; SIMULATION_COUNT?: number; stylePreset?: string; intensity?: number };
}

let _latest: MctsStats | null = null;

export const mctsStatsStore = {
  get() { return _latest; },

  set(stats: MctsStats) {
    _latest = stats;
  },

  clear() {
    _latest = null;
  },
};
