import {interpolate} from "remotion";
import type {GraphNode} from "../types";

const clamp01 = (value: number) => Math.max(0, Math.min(1, value));

interface CameraPanProps {
	fromNodeId: string;
	toNodeId: string;
	nodes: Map<string, GraphNode>;
	frame: number;
	start: number;
	duration: number;
	width: number;
	height: number;
}

export const computeCameraTransform = ({
	fromNodeId,
	toNodeId,
	nodes,
	frame,
	start,
	duration,
	width,
	height,
}: CameraPanProps): string => {
	const from = nodes.get(fromNodeId);
	const to = nodes.get(toNodeId);
	if (!from || !to) return "";

	const progress = clamp01((frame - start) / Math.max(1, duration));
	const fromCx = from.x + from.width / 2;
	const fromCy = from.y + from.height / 2;
	const toCx = to.x + to.width / 2;
	const toCy = to.y + to.height / 2;

	const dx = interpolate(progress, [0, 1], [0, -(toCx - width / 2) + (fromCx - width / 2)]);
	const dy = interpolate(progress, [0, 1], [0, -(toCy - height / 2) + (fromCy - height / 2)]);
	const scale = interpolate(progress, [0, 0.3, 0.7, 1], [1, 1.12, 1.12, 1]);

	return `translate(${dx}px, ${dy}px) scale(${scale})`;
};
