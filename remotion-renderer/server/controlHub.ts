/**
 * ControlHub — (π, E, J) runtime control layer
 *
 * 理论映射：
 *   E_bias  → 能量地形 shaping（reward 权重乘子）
 *   Pi_temp → 静止分布温度（sampling entropy）
 *   J_noise → 探索通量（Dirichlet 噪声强度）
 *
 * 使用方式：
 *   import { controlHub } from "./controlHub";
 *   controlHub.set({ E_bias: 1.5, Pi_temp: 0.8, J_noise: 0.1 });
 */

export interface ControlParams {
  /** reward shaping 乘子（默认 1.0） */
  E_bias: number;
  /** softmax 温度（默认 1.0）*/
  Pi_temp: number;
  /** Dirichlet 噪声强度（默认 0.25）*/
  J_noise: number;
  /** MCTS simulation count（默认 undefined = 5）*/
  SIMULATION_COUNT?: number;
  /** Creator style preset — overrides E-derived rendering with a fixed style */
  stylePreset?: "tiktok_fast" | "cinematic" | "glitch_edit";
  /** Intensity slider 0~1 for style preset (default 0.7) */
  intensity?: number;
}

const DEFAULT_PARAMS: ControlParams = {
  E_bias: 1.0,
  Pi_temp: 1.0,
  J_noise: 0.25,
  SIMULATION_COUNT: undefined,
  stylePreset: undefined,
  intensity: 0.7,
};

class ControlHubClass {
  private params: ControlParams = { ...DEFAULT_PARAMS };
  private version = 0; // 递增用于观察是否生效

  get(): ControlParams & { version: number } {
    return { ...this.params, version: this.version };
  }

  set(patch: Partial<ControlParams>): void {
    Object.assign(this.params, patch);
    this.version++;
    console.info(`[ControlHub] updated: E_bias=${this.params.E_bias} Pi_temp=${this.params.Pi_temp} J_noise=${this.params.J_noise} style=${this.params.stylePreset ?? "auto"} intensity=${this.params.intensity} (v${this.version})`);
  }

  reset(): void {
    this.params = { ...DEFAULT_PARAMS };
    this.version++;
    console.info("[ControlHub] reset to defaults");
  }
}

/** 全局单例（Node.js 进程内共享） */
export const controlHub = new ControlHubClass();
