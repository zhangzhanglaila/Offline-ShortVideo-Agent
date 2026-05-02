import {interpolate} from "remotion";
import type {AnimationPlan, AnimationPlanStep, GraphEdge, GraphNode, GraphStep, GraphTimelineEvent} from "../types";
import type {AnimationState} from "./types";
import {computeCameraTransform} from "./CameraPan";

const clamp01 = (value: number) => Math.max(0, Math.min(1, value));

type GraphBeat = GraphStep | GraphTimelineEvent;

export function resolveAnimationState(
	plan: AnimationPlan,
	frame: number,
	nodes: Map<string, GraphNode>,
	edges: Map<string, GraphEdge>,
	width: number,
	height: number,
): AnimationState {
	const activeStep = plan.steps.find(
		(s) => frame >= s.start && frame < s.start + s.duration,
	);

	const activeNodeIds = new Set(activeStep?.nodeIds ?? []);
	const activeEdgeIds = new Set(activeStep?.edgeIds ?? []);

	// Cumulative visibility: nodes become visible once introduced and stay visible
	const visibleNodeIds = new Set<string>();
	for (const step of plan.steps) {
		if (step.start <= frame) {
			for (const nid of step.nodeIds) {
				visibleNodeIds.add(nid);
			}
		}
	}

	let cameraTransform = "";
	const panStep = plan.steps.find(
		(s) =>
			s.action === "camera_pan" &&
			frame >= s.start &&
			frame < s.start + s.duration,
	);
	if (panStep?.cameraFrom && panStep?.cameraTo) {
		cameraTransform = computeCameraTransform({
			fromNodeId: panStep.cameraFrom,
			toNodeId: panStep.cameraTo,
			nodes,
			frame,
			start: panStep.start,
			duration: panStep.duration,
			width,
			height,
		});
	}

	const pulseTargets = new Map<string, number>();
	for (const step of plan.steps) {
		if (
			step.action === "pulse" &&
			frame >= step.start &&
			frame < step.start + step.duration
		) {
			for (const nodeId of step.nodeIds) {
				pulseTargets.set(
					nodeId,
					(frame - step.start) / Math.max(1, step.duration),
				);
			}
		}
	}

	const glowIntensity = activeStep?.intensity ?? 0.7;

	return {activeNodeIds, activeEdgeIds, visibleNodeIds, glowIntensity, pulseTargets, cameraTransform};
}

export function resolveFromTimelineLegacy(
	beats: GraphBeat[],
	frame: number,
): AnimationState {
	const activeBeat =
		beats.find(
			(beat) =>
				frame >= beat.start && frame < beat.start + beat.duration,
		) ?? beats[beats.length - 1];

	const activeNodeIds = new Set(activeBeat?.nodeIds ?? []);
	const activeEdgeIds = new Set(activeBeat?.edgeIds ?? []);
	// Legacy mode: all nodes are always visible
	const visibleNodeIds = new Set(activeBeat ? activeBeat.nodeIds : []);

	return {
		activeNodeIds,
		activeEdgeIds,
		visibleNodeIds,
		glowIntensity: 0.7,
		pulseTargets: new Map(),
		cameraTransform: "",
	};
}

export function resolveMissEffectNodeIds(
	plan: AnimationPlan,
	frame: number,
): Set<string> {
	const missSteps = plan.steps.filter(
		(s) =>
			s.action === "miss_effect" &&
			frame >= s.start &&
			frame < s.start + s.duration,
	);
	return new Set(missSteps.flatMap((s) => s.nodeIds));
}
