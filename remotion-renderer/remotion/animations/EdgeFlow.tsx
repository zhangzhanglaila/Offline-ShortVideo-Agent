import React from "react";
import {Easing, interpolate} from "remotion";
import {FONT_FAMILY} from "../constants";
import type {GraphEdge, GraphNode} from "../types";

const clamp01 = (value: number) => Math.max(0, Math.min(1, value));

const pathForEdge = (edge: GraphEdge, nodes: Map<string, GraphNode>) => {
	const points = edge.points;
	if (points) {
		const [x1, y1, x2, y2] = points;
		const dx = x2 - x1;
		const curve = Math.max(90, Math.min(190, Math.abs(dx) * 0.45 + 80));
		return `M ${x1} ${y1} C ${x1} ${y1 + curve}, ${x2} ${y2 - curve}, ${x2} ${y2}`;
	}

	const source = nodes.get(edge.from);
	const target = nodes.get(edge.to);
	if (!source || !target) {
		return "M 0 0 L 0 0";
	}
	const x1 = source.x + source.width / 2;
	const y1 = source.y + source.height / 2;
	const x2 = target.x + target.width / 2;
	const y2 = target.y + target.height / 2;
	const curve = Math.max(90, Math.min(190, Math.abs(x2 - x1) * 0.45 + 80));
	return `M ${x1} ${y1} C ${x1} ${y1 + curve}, ${x2} ${y2 - curve}, ${x2} ${y2}`;
};

const labelPos = (edge: GraphEdge, nodes: Map<string, GraphNode>) => {
	if (edge.points) {
		return {
			x: (edge.points[0] + edge.points[2]) / 2,
			y: (edge.points[1] + edge.points[3]) / 2 - 14,
		};
	}
	const source = nodes.get(edge.from);
	const target = nodes.get(edge.to);
	if (source && target) {
		return {
			x: (source.x + source.width / 2 + target.x + target.width / 2) / 2,
			y: (source.y + source.height / 2 + target.y + target.height / 2) / 2 - 14,
		};
	}
	return {x: 0, y: 0};
};

interface EdgeFlowProps {
	edge: GraphEdge;
	nodes: Map<string, GraphNode>;
	index: number;
	active: boolean;
	intensity: number;
	frame: number;
}

export const EdgeFlow: React.FC<EdgeFlowProps> = ({
	edge,
	nodes,
	index,
	active,
	intensity,
	frame,
}) => {
	const path = pathForEdge(edge, nodes);
	const intro = clamp01(
		interpolate(frame, [18 + index * 8, 58 + index * 8], [0, 1], {
			easing: Easing.out(Easing.cubic),
			extrapolateLeft: "clamp",
			extrapolateRight: "clamp",
		}),
	);
	// P4.4: Global unified flow speed — slight active boost for visual hierarchy
	const FLOW_BASE = 0.52;
	const flowSpeed = FLOW_BASE * (active ? 1.25 : 1.0);
	const flow = ((frame * flowSpeed + index * 73) % 220) / 220;
	const color = edge.color ?? "#9bb7ff";

	const lp = labelPos(edge, nodes);

	return (
		<g>
			<path
				d={path}
				fill="none"
				stroke="rgba(130,155,190,0.24)"
				strokeWidth={5}
				strokeLinecap="round"
				strokeDasharray="1000"
				strokeDashoffset={1000 - intro * 1000}
			/>
			<path
				d={path}
				fill="none"
				stroke={color}
				strokeWidth={active ? 5 : 3}
				strokeLinecap="round"
				strokeDasharray="1000"
				strokeDashoffset={1000 - intro * 1000}
				opacity={active ? 0.98 : 0.42}
				filter={active ? "url(#graphGlow)" : undefined}
			/>
			{intro > 0 ? (
				<path
					d={path}
					fill="none"
					stroke={color}
					strokeWidth={active ? 9 : 6}
					strokeLinecap="round"
					strokeDasharray="34 186"
					strokeDashoffset={-flow * 220}
					opacity={((active ? 0.95 : 0.32)) * intro}
				/>
			) : null}
			{edge.label && intro > 0 ? (
				<text
					x={lp.x}
					y={lp.y}
					fill={active ? "#f8fbff" : "rgba(222,235,255,0.62)"}
					fontFamily={FONT_FAMILY}
					fontSize={18}
					fontWeight={650}
					textAnchor="middle"
					paintOrder="stroke"
					stroke="#070b10"
					strokeWidth={5}
					opacity={intro}
				>
					{edge.label}
				</text>
			) : null}
		</g>
	);
};
