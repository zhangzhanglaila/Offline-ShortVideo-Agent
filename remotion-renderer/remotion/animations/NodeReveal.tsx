import React from "react";
import {interpolate, spring, useVideoConfig} from "remotion";
import {FONT_FAMILY} from "../constants";
import type {GraphNode} from "../types";

type NodeTier = "hero" | "secondary" | "other";

interface NodeRevealProps {
	node: GraphNode;
	index: number;
	active: boolean;
	visible: boolean;
	intensity: number;
	frame: number;
	missEffect?: boolean;
	tier?: NodeTier;
	emphasized?: boolean;
}

const tierStyles: Record<NodeTier, {glowMulti: number; scaleBump: number; fontSize: number; zBase: number; opacityBase: number}> = {
	hero:      {glowMulti: 1.6, scaleBump: 1.04, fontSize: 30, zBase: 10, opacityBase: 1},
	secondary: {glowMulti: 1.0, scaleBump: 1.02, fontSize: 26, zBase: 6,  opacityBase: 0.92},
	other:     {glowMulti: 0.6, scaleBump: 1.0,  fontSize: 22, zBase: 4,  opacityBase: 0.78},
};

export const NodeReveal: React.FC<NodeRevealProps> = ({
	node,
	index,
	active,
	visible,
	intensity,
	frame,
	missEffect = false,
	tier = "secondary",
	emphasized = false,
}) => {
	const {fps} = useVideoConfig();
	if (!visible) return null;
	const ts = tierStyles[tier];
	const appear = spring({
		frame: frame - index * 6,
		fps,
		config: {damping: 16, stiffness: 95, mass: 0.9},
	});
	const pulse = active
		? 1 + Math.sin(frame * 0.13) * 0.025 * intensity * ts.glowMulti
		: 1;
	const activeGlow = active
		? 0.9 + Math.sin(frame * 0.09) * 0.1 * intensity * ts.glowMulti + (emphasized ? 0.35 : 0)
		: 0.2;
	const shakeX = missEffect ? Math.sin(frame * 0.5) * 8 : 0;
	const heroColor = tier === "hero" ? "#62d9ff" : (node.color ?? "#9bb7ff");
	const borderColor = missEffect
		? `rgba(255, 72, 72, ${0.6 + Math.sin(frame * 0.7) * 0.4})`
		: heroColor;

	return (
		<div
			style={{
				position: "absolute",
				left: node.x,
				top: node.y,
				width: node.width,
				height: node.height,
				border: tier === "hero"
					? `3px solid ${borderColor}`
					: `2px solid ${borderColor}`,
				borderRadius: 18,
				background: tier === "hero"
					? "rgba(98,217,255,0.16)"
					: (node.fill ?? "rgba(155,183,255,0.12)"),
				boxShadow: active
					? `0 0 ${32 * ts.glowMulti * (emphasized ? 1.5 : 1)}px ${heroColor}66, inset 0 0 28px rgba(255,255,255,0.06)`
					: "inset 0 0 22px rgba(255,255,255,0.04)",
				opacity: interpolate(appear, [0, 1], [0, ts.opacityBase]),
				transform: `translateX(${shakeX}px) translateY(${interpolate(appear, [0, 1], [34, 0])}px) scale(${interpolate(appear, [0, 1], [0.88, 1]) * pulse * ts.scaleBump * (emphasized ? 1.15 : 1)})`,
				transformOrigin: "center center",
				display: "flex",
				alignItems: "center",
				justifyContent: "center",
				padding: "0 18px",
				color: tier === "hero" ? "#f8fbff" : "#eef6ff",
				fontFamily: FONT_FAMILY,
				fontSize: ts.fontSize,
				fontWeight: tier === "hero" ? 820 : (tier === "secondary" ? 760 : 680),
				textAlign: "center",
				letterSpacing: 0,
				zIndex: active ? ts.zBase + 3 : ts.zBase,
			}}
		>
			<div>
				<div>{node.label}</div>
				{node.role ? (
					<div
						style={{
							marginTop: 8,
							fontSize: tier === "hero" ? 16 : (tier === "secondary" ? 15 : 13),
							fontWeight: 600,
							color: tier === "hero" ? "rgba(98,217,255,0.85)" : "rgba(238,246,255,0.58)",
							textTransform: "uppercase",
						}}
					>
						{node.role}
					</div>
				) : null}
			</div>
		</div>
	);
};
