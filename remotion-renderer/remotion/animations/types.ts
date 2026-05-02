import type {AnimationActionType, AnimationPlanStep} from "../types";

export interface AnimationState {
	activeNodeIds: Set<string>;
	activeEdgeIds: Set<string>;
	visibleNodeIds: Set<string>;
	glowIntensity: number;
	pulseTargets: Map<string, number>;
	cameraTransform: string;
}

export type {AnimationActionType, AnimationPlanStep};
