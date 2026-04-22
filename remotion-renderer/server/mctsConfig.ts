/**
 * mctsConfig — server-side (π, E, J) control parameters for MCTS
 *
 * 这些参数在 server 端被 agentOrchestrator 读取，
 * 在 collectRewardData() 调用 beamSearchTransitionPlan 之前注入。
 *
 * 为什么不放在 VideoScene.tsx 里：
 *   VideoScene.tsx 是 TSX/Remotion 组件，在 Remotion bundle 端运行。
 *   而 MCTS 计算在 Node.js server 端执行。
 *   所以这个 config 放在 server 层，通过函数参数传递到 VideoScene 的纯函数。
 */

/** E_bias: reward 权重乘子（默认 1.0） */
let _E_bias = 1.0;
/** Pi_temp: softmax 温度（默认 1.0）*/
let _Pi_temp = 1.0;
/** J_noise: Dirichlet 噪声强度（默认 0.25）*/
let _J_noise = 0.25;
/** SIMULATION_COUNT: MCTS rollouts（不推荐改，见 memory feedback）*/
let _SIMULATION_COUNT: number | undefined = undefined;

export const mctsConfig = {
  get E_bias() { return _E_bias; },
  get Pi_temp() { return _Pi_temp; },
  get J_noise() { return _J_noise; },
  get SIMULATION_COUNT() { return _SIMULATION_COUNT; },

  set(patch: { E_bias?: number; Pi_temp?: number; J_noise?: number; SIMULATION_COUNT?: number }) {
    if (patch.E_bias !== undefined) _E_bias = patch.E_bias;
    if (patch.Pi_temp !== undefined) _Pi_temp = patch.Pi_temp;
    if (patch.J_noise !== undefined) _J_noise = patch.J_noise;
    if (patch.SIMULATION_COUNT !== undefined) _SIMULATION_COUNT = patch.SIMULATION_COUNT;
    console.info(`[mctsConfig] E_bias=${_E_bias} Pi_temp=${_Pi_temp} J_noise=${_J_noise} SIM_COUNT=${_SIMULATION_COUNT ?? "default"}`);
  },

  reset() {
    _E_bias = 1.0;
    _Pi_temp = 1.0;
    _J_noise = 0.25;
    _SIMULATION_COUNT = undefined;
    console.info("[mctsConfig] reset");
  },

  /** 读取当前快照 */
  snapshot() {
    return { E_bias: _E_bias, Pi_temp: _Pi_temp, J_noise: _J_noise, SIMULATION_COUNT: _SIMULATION_COUNT };
  },
};
