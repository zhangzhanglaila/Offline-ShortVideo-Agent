import React, {useMemo} from "react";
import {
	AbsoluteFill,
	Easing,
	interpolate,
	useCurrentFrame,
} from "remotion";
import {FONT_FAMILY} from "./constants";
import type {GraphSceneData, GraphShot, GraphStep, GraphTimelineEvent} from "./types";
import {
	DataPulse,
	EdgeFlow,
	NodeReveal,
	resolveAnimationState,
	resolveFromTimelineLegacy,
	resolveMissEffectNodeIds,
} from "./animations";

const clamp01 = (value: number) => Math.max(0, Math.min(1, value));
type GraphBeat = GraphStep | GraphTimelineEvent;
type NodeTier = "hero" | "secondary" | "other";

// ─── v22.5: Numeric camera state for shot interpolation ─────────────
const SHOT_BLEND = 15; // frames to blend between shots

interface CameraState {
	scale: number;
	tx: number;
	ty: number;
}

function cameraToCSS(c: CameraState): string {
	if (Math.abs(c.scale - 1) < 0.001 && Math.abs(c.tx) < 0.5 && Math.abs(c.ty) < 0.5) return "none";
	return `scale(${c.scale.toFixed(4)}) translate(${c.tx.toFixed(2)}px, ${c.ty.toFixed(2)}px)`;
}

function lerpCamera(a: CameraState, b: CameraState, t: number): CameraState {
	const s = (t: number) => t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t; // ease in-out
	const e = s(t);
	return {
		scale: a.scale + (b.scale - a.scale) * e,
		tx: a.tx + (b.tx - a.tx) * e,
		ty: a.ty + (b.ty - a.ty) * e,
	};
}

function resolveCameraValues(
	shot: GraphShot | undefined,
	shotProgress: number,
	nodes: Map<string, {x: number; y: number; width: number; height: number}>,
	width: number,
	height: number,
): CameraState {
	const identity: CameraState = {scale: 1, tx: 0, ty: 0};
	if (!shot || shot.camera === "static") return identity;

	let cx = width / 2;
	let cy = height / 2;
	const targets = shot.targetIds
		.map((id) => nodes.get(id))
		.filter(Boolean) as Array<{x: number; y: number; width: number; height: number}>;
	if (targets.length > 0) {
		const minX = Math.min(...targets.map((n) => n.x));
		const maxX = Math.max(...targets.map((n) => n.x + n.width));
		const minY = Math.min(...targets.map((n) => n.y));
		const maxY = Math.max(...targets.map((n) => n.y + n.height));
		cx = (minX + maxX) / 2;
		cy = (minY + maxY) / 2;
	}

	const p = shotProgress;
	const ease = (t: number) => t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;

	switch (shot.camera) {
		case "zoom-in": {
			const s = 1 + ease(p) * 0.18;
			return {scale: s, tx: (width / 2 - cx) * 0.08, ty: (height / 2 - cy) * 0.08};
		}
		case "push-in": {
			const s = 1 + ease(p) * 0.24;
			return {scale: s, tx: 0, ty: 0};
		}
		case "pull-out": {
			const s = Math.max(0.88, 1 - ease(p) * 0.12);
			return {scale: s, tx: 0, ty: 0};
		}
		case "pan": {
			const dx = (width / 2 - cx) * ease(p) * 0.15;
			const dy = (height / 2 - cy) * ease(p) * 0.15;
			return {scale: 1, tx: dx, ty: dy};
		}
		default:
			return identity;
	}
}

export const GraphScene: React.FC<{graph: GraphSceneData; width: number; height: number}> = ({
	graph,
	width,
	height,
}) => {
	const frame = useCurrentFrame();
	const nodes = useMemo(
		() => new Map(graph.nodes.map((node) => [node.id, node])),
		[graph.nodes],
	);

	const nodeTier = useMemo(() => {
		const tiers = new Map<string, NodeTier>();
		const nt = graph.animation_plan?.nodeTiers;
		if (nt) {
			tiers.set(nt.hero, "hero");
			for (const nid of nt.secondary) tiers.set(nid, "secondary");
			for (const nid of nt.others) tiers.set(nid, "other");
		}
		return tiers;
	}, [graph.animation_plan]);

	// ── v22.5: Shot with interpolation ──
	const shots = graph.shots ?? [];
	const currentShotIdx = useMemo(() => {
		const idx = shots.findIndex(
			(s) => frame >= s.start && frame < s.start + s.duration,
		);
		return idx >= 0 ? idx : shots.length - 1;
	}, [shots, frame]);

	const currentShot = shots[currentShotIdx] ?? shots[shots.length - 1];
	const prevShot = currentShotIdx > 0 ? shots[currentShotIdx - 1] : undefined;

	const shotLocalFrame = currentShot
		? frame - currentShot.start
		: 0;
	const isShotBlending = prevShot != null && shotLocalFrame < SHOT_BLEND;
	const shotBlendProgress = isShotBlending
		? clamp01(shotLocalFrame / SHOT_BLEND)
		: 0;

	const shotProgress = currentShot
		? clamp01((frame - currentShot.start) / Math.max(1, currentShot.duration))
		: 0;

	// ── Focus set (current shot targets) ──
	const focusNodeIds = useMemo(() => {
		if (!currentShot) return new Set<string>();
		if (currentShot.focus === "overview" || currentShot.focus === "group") {
			return new Set(currentShot.targetIds);
		}
		if (currentShot.focus === "node") {
			return new Set(currentShot.targetIds);
		}
		if (currentShot.focus === "edge") {
			const eids = new Set(currentShot.targetIds);
			const connected = new Set<string>();
			for (const e of graph.edges) {
				if (eids.has(e.id)) {
					connected.add(e.from);
					connected.add(e.to);
				}
			}
			return connected;
		}
		return new Set<string>();
	}, [currentShot, graph.edges]);

	// Previous focus set for exit animation
	const prevFocusNodeIds = useMemo(() => {
		if (!prevShot) return new Set<string>();
		if (prevShot.focus === "overview" || prevShot.focus === "group") {
			return new Set(prevShot.targetIds);
		}
		if (prevShot.focus === "node") {
			return new Set(prevShot.targetIds);
		}
		if (prevShot.focus === "edge") {
			const eids = new Set(prevShot.targetIds);
			const connected = new Set<string>();
			for (const e of graph.edges) {
				if (eids.has(e.id)) {
					connected.add(e.from);
					connected.add(e.to);
				}
			}
			return connected;
		}
		return new Set<string>();
	}, [prevShot, graph.edges]);

	// ── P3.2: Emphasis boost from LLM director ──
	const emphasisSet = useMemo(() => {
		const keywords = (graph as any)._emphasis as string[] | undefined;
		if (!keywords || keywords.length === 0) return new Set<string>();
		const lower = keywords.map((k: string) => k.toLowerCase());
		return new Set(graph.nodes
			.filter((n) => lower.some((k: string) => n.label.toLowerCase().includes(k) || n.id.toLowerCase().includes(k)))
			.map((n) => n.id),
		);
	}, [graph]);

	// ── P5: Intent-driven behavior profile ──
	const intentProfile = useMemo(() => {
		const i = currentShot?.intent || '';
		// Flow intents → edges animate stronger, nodes more subtle
		if (i === 'flow' || i === 'show_flow' || i === 'trace_path' || i === 'focus_edge') {
			return {edgeMulti: 1.6, nodeGlowBoost: 0, spreadEmphasis: false, description: 'flow'};
		}
		// Focus intents → target node gets dramatic push, edges subdued
		if (i === 'focus' || i === 'focus_node' || i === 'push_into' || i === 'spotlight' || i === 'highlight_result' || i === 'emphasize') {
			return {edgeMulti: 0.5, nodeGlowBoost: 0.25, spreadEmphasis: false, description: 'focus'};
		}
		// Introduce/expand intents → show everything, gentle camera, all nodes emphasized
		if (i === 'introduce' || i === 'introduce_node' || i === 'expand_view' || i === 'reveal_all') {
			return {edgeMulti: 0.7, nodeGlowBoost: 0.1, spreadEmphasis: true, description: 'introduce'};
		}
		// Summary/hold intents → static, calm, fade emphasis
		if (i === 'summary' || i === 'summarize' || i === 'hold_frame') {
			return {edgeMulti: 0.4, nodeGlowBoost: 0, spreadEmphasis: true, description: 'summary'};
		}
		// Pulse/ripple intents → dramatic single-node emphasis
		if (i === 'pulse' || i === 'ripple' || i === 'pause') {
			return {edgeMulti: 0.3, nodeGlowBoost: 0.4, spreadEmphasis: false, description: 'pulse'};
		}
		return {edgeMulti: 1.0, nodeGlowBoost: 0, spreadEmphasis: false, description: 'default'};
	}, [currentShot]);

	// ── Animated focus intensity per node (no flicker) ──
	const focusIntensity = useMemo(() => {
		const map = new Map<string, number>();
		for (const node of graph.nodes) {
			const wasFocus = prevFocusNodeIds.has(node.id);
			const isFocus = focusNodeIds.has(node.id);
			if (wasFocus && isFocus) {
				map.set(node.id, 1.0);
			} else if (wasFocus && !isFocus) {
				// Exiting focus: ramp down from 1.0 to 0.45
				map.set(node.id, isShotBlending
					? interpolate(shotBlendProgress, [0, 1], [1.0, 0.45], {extrapolateRight: "clamp"})
					: 0.45);
			} else if (!wasFocus && isFocus) {
				// Entering focus: ramp up from 0.45 to 1.0
				map.set(node.id, isShotBlending
					? interpolate(shotBlendProgress, [0, 1], [0.45, 1.0], {extrapolateRight: "clamp"})
					: 1.0);
			} else {
				map.set(node.id, 0.45);
			}
		}
		return map;
	}, [graph.nodes, prevFocusNodeIds, focusNodeIds, isShotBlending, shotBlendProgress]);

	// ── Interpolated camera transform ──
	const shotCamera = useMemo(() => {
		const curr = resolveCameraValues(currentShot, shotProgress, nodes, width, height);
		if (!isShotBlending || !prevShot) return cameraToCSS(curr);
		const prev = resolveCameraValues(prevShot, 1.0, nodes, width, height);
		const blended = lerpCamera(prev, curr, shotBlendProgress);
		return cameraToCSS(blended);
	}, [currentShot, prevShot, shotProgress, isShotBlending, shotBlendProgress, nodes, width, height]);

	// 3-tier fallback: animation_plan → timeline → steps
	const animState = useMemo(() => {
		if (graph.animation_plan?.steps?.length) {
			const state = resolveAnimationState(
				graph.animation_plan,
				frame,
				nodes,
				new Map(graph.edges.map((e) => [e.id, e])),
				width,
				height,
			);
			const missNodeIds = resolveMissEffectNodeIds(graph.animation_plan, frame);
			return {...state, missNodeIds};
		}
		const beats: GraphBeat[] =
			graph.timeline?.length ? graph.timeline : graph.steps;
		return {
			...resolveFromTimelineLegacy(beats, frame),
			missNodeIds: new Set<string>(),
		};
	}, [graph, frame, nodes, width, height]);

	const beats: GraphBeat[] =
		graph.timeline?.length ? graph.timeline : graph.steps;
	const activeBeat = useMemo(() => {
		return (
			beats.find(
				(beat) =>
					frame >= beat.start && frame < beat.start + beat.duration,
			) ?? beats[beats.length - 1]
		);
	}, [beats, frame]);

	const beatProgress = activeBeat
		? clamp01((frame - activeBeat.start) / Math.max(1, activeBeat.duration))
		: 0;

	const titleY = interpolate(frame, [0, 30], [92, 72], {
		easing: Easing.out(Easing.cubic),
		extrapolateLeft: "clamp",
		extrapolateRight: "clamp",
	});
	const titleOpacity = interpolate(frame, [0, 24], [0, 1], {
		extrapolateLeft: "clamp",
		extrapolateRight: "clamp",
	});

	const cameraTransform = [animState.cameraTransform, shotCamera]
		.filter((t) => t && t !== "none")
		.join(" ");

	return (
		<AbsoluteFill
			style={{
				width,
				height,
				overflow: "hidden",
				background:
					"radial-gradient(circle at 50% 18%, rgba(98,217,255,0.16), transparent 30%), linear-gradient(180deg, #071018 0%, #070b10 58%, #091018 100%)",
				fontFamily: FONT_FAMILY,
			}}
		>
				<div
					style={{
						position: "absolute",
						left: 0,
						right: 0,
						top: titleY,
						opacity: titleOpacity,
						textAlign: "center",
						color: "#f8fbff",
						fontSize: 54,
						fontWeight: 820,
						textShadow: "0 0 28px rgba(98,217,255,0.35)",
						zIndex: 10,
					}}
				>
					{graph.title}
				</div>
				{graph.summary ? (
					<div
						style={{
							position: "absolute",
							left: 120,
							right: 120,
							top: 150,
							opacity: titleOpacity * 0.78,
							textAlign: "center",
							color: "rgba(221,235,255,0.68)",
							fontSize: 24,
							lineHeight: 1.35,
							zIndex: 10,
						}}
					>
						{graph.summary}
					</div>
				) : null}
				{activeBeat && "text" in activeBeat && activeBeat.text ? (
					<div
						style={{
							position: "absolute",
							left: 140,
							right: 140,
							top: 232,
							opacity: interpolate(beatProgress, [0, 0.12, 0.86, 1], [0, 1, 1, 0], {
								extrapolateLeft: "clamp",
								extrapolateRight: "clamp",
							}),
							transform: `translateY(${interpolate(beatProgress, [0, 0.18], [18, 0], {
								easing: Easing.out(Easing.cubic),
								extrapolateLeft: "clamp",
								extrapolateRight: "clamp",
							})}px)`,
							color: "#dbeafe",
							fontSize: 28,
							fontWeight: 680,
							textAlign: "center",
							lineHeight: 1.35,
							zIndex: 10,
							textShadow: "0 2px 18px rgba(0,0,0,0.65)",
						}}
					>
						{activeBeat.text}
					</div>
				) : null}
				<svg
					width={width}
					height={height}
					viewBox={`0 0 ${width} ${height}`}
					style={{
						position: "absolute",
						inset: 0,
						zIndex: 2,
						transform: cameraTransform,
						transformOrigin: "center center",
					}}
				>
					<defs>
						<filter id="graphGlow" x="-50%" y="-50%" width="200%" height="200%">
							<feGaussianBlur stdDeviation="6" result="coloredBlur" />
							<feMerge>
								<feMergeNode in="coloredBlur" />
								<feMergeNode in="SourceGraphic" />
							</feMerge>
						</filter>
					</defs>
					{graph.edges.map((edge, index) => {
						const isFocus = focusNodeIds.has(edge.from) || focusNodeIds.has(edge.to);
						const baseIntensity = animState.glowIntensity;
						const edgeIntensity = (isFocus
							? baseIntensity * 1.3
							: baseIntensity * 0.55) * intentProfile.edgeMulti;
						return (
							<EdgeFlow
								key={edge.id}
								edge={edge}
								nodes={nodes}
								index={index}
								active={animState.activeEdgeIds.has(edge.id)}
								intensity={edgeIntensity}
								frame={frame}
							/>
						);
					})}
				</svg>
				{graph.nodes.map((node, index) => {
					const nodeFocus = focusIntensity.get(node.id) ?? 0.45;
					return (
						<NodeReveal
							key={node.id}
							node={node}
							index={index}
							active={animState.activeNodeIds.has(node.id)}
							visible={animState.visibleNodeIds.has(node.id)}
							intensity={(animState.glowIntensity + intentProfile.nodeGlowBoost) * nodeFocus * (emphasisSet.has(node.id) ? 1.6 : 1) * (intentProfile.spreadEmphasis && !emphasisSet.has(node.id) ? 1.15 : 1)}
							frame={frame}
							missEffect={"missNodeIds" in animState && animState.missNodeIds.has(node.id)}
							emphasized={emphasisSet.has(node.id)}
							tier={nodeTier.get(node.id) ?? "other"}
						/>
					);
				})}
				{Array.from(animState.pulseTargets.entries()).map(([nodeId, progress]) => {
					const node = nodes.get(nodeId);
					if (!node) return null;
					return (
						<DataPulse
							key={`pulse-${nodeId}`}
							node={node}
							progress={progress}
							intensity={animState.glowIntensity}
						/>
					);
				})}
				{graph._debug ? (
					<div
						style={{
							position: 'absolute',
							left: 12,
							bottom: 12,
							background: 'rgba(0,0,0,0.72)',
							color: '#0f0',
							fontSize: 13,
							fontFamily: 'monospace',
							padding: '6px 10px',
							borderRadius: 4,
							zIndex: 99,
							lineHeight: 1.5,
							pointerEvents: 'none',
						}}
					>
						<div>shot {currentShotIdx + 1}/{shots.length}</div>
						{currentShot ? (
							<>
								<div>intent: {currentShot.intent || '—'}</div>
								<div>camera: {currentShot.camera}</div>
								<div>focus: {currentShot.focus}</div>
								<div>targets: {currentShot.targetIds.join(', ')}</div>
								<div>intent: {currentShot?.intent || "—"} ({intentProfile.description})</div>
								<div>pace: {graph._pace || '—'}</div>
							</>
						) : null}
					</div>
				) : null}
				</AbsoluteFill>
			);
		};
