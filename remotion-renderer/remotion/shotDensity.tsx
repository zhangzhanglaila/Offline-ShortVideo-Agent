import React from "react";
import {AbsoluteFill, Img, interpolate} from "remotion";
import type {Shot, ShotInteraction, ShotObject} from "./types";

const hashString = (value: string) => {
	let hash = 0;
	for (let index = 0; index < value.length; index++) {
		hash = (hash * 31 + value.charCodeAt(index)) | 0;
	}
	return Math.abs(hash);
};

const clamp = (value: number, minimum: number, maximum: number) => {
	return Math.max(minimum, Math.min(maximum, value));
};

const combineTransforms = (...transforms: Array<string | undefined>) => {
	return transforms.filter((value) => value && value.trim().length > 0).join(" ").trim();
};

const mapRange = (value: number, input: [number, number], output: [number, number]) => {
	return interpolate(value, input, output, {
		extrapolateLeft: "clamp",
		extrapolateRight: "clamp",
	});
};

type ShotObjectState = {
	x: number;
	y: number;
	width: number;
	height: number;
	scale: number;
	opacity: number;
	blur: number;
};

const resolveObjectState = ({
	object,
	localFrame,
	shotDuration,
	canvasWidth,
	canvasHeight,
}: {
	object: ShotObject;
	localFrame: number;
	shotDuration: number;
	canvasWidth: number;
	canvasHeight: number;
}): ShotObjectState => {
	const frameRange: [number, number] = [0, Math.max(shotDuration, 1)];
	const defaultWidth = object.width ?? canvasWidth;
	const defaultHeight = object.height ?? canvasHeight;
	const baseScale = object.scale ?? 1;

	let x = object.x ?? 0;
	let y = object.y ?? 0;
	let scale = baseScale;

	switch (object.animation?.type) {
		case "move": {
			const from = object.animation.from ?? [x, y];
			const to = object.animation.to ?? [x, y];
			x = mapRange(localFrame, frameRange, [from[0], to[0]]);
			y = mapRange(localFrame, frameRange, [from[1], to[1]]);
			scale = mapRange(localFrame, frameRange, [
				object.animation.fromScale ?? baseScale,
				object.animation.toScale ?? baseScale,
			]);
			break;
		}
		case "zoom": {
			scale = mapRange(localFrame, frameRange, [
				object.animation.fromScale ?? baseScale,
				object.animation.toScale ?? baseScale,
			]);
			break;
		}
		case "float": {
			const amplitude = object.animation.amplitude ?? 18;
			const speed = object.animation.speed ?? 0.045;
			x += Math.sin(localFrame * speed) * amplitude;
			y += Math.cos(localFrame * speed * 0.8) * amplitude * 0.65;
			scale = baseScale + Math.sin(localFrame * speed * 0.45) * 0.025;
			break;
		}
		case "sweep": {
			const from = object.animation.from ?? [-defaultWidth, y];
			const to = object.animation.to ?? [canvasWidth, y];
			x = mapRange(localFrame, frameRange, [from[0], to[0]]);
			y = mapRange(localFrame, frameRange, [from[1], to[1]]);
			break;
		}
		default:
			break;
	}

	return {
		x,
		y,
		width: defaultWidth,
		height: defaultHeight,
		scale,
		opacity: clamp(object.opacity ?? 1, 0, 1),
		blur: Math.max(0, object.blur ?? 0),
	};
};

const applyInteraction = (
	states: Record<string, ShotObjectState>,
	interaction: ShotInteraction,
) => {
	const source = states[interaction.sourceId];
	const target = states[interaction.targetId];
	if (!source || !target) {
		return;
	}

	if (interaction.type === "link-opacity") {
		const input = interaction.inputRange ?? [0, 400];
		const output = interaction.outputRange ?? [0.2, 1];
		target.opacity *= mapRange(source.x, input, output);
		return;
	}

	if (interaction.type === "proximity-scale") {
		const output = interaction.outputRange ?? [1, 1.2];
		const activeDistance = interaction.distance ?? 50;
		const xDistance = Math.abs(source.x - target.x);
		target.scale *= mapRange(
			clamp(xDistance, 0, activeDistance),
			[0, activeDistance],
			[output[1], output[0]],
		);
	}
};

const resolveInteractionStates = ({
	shot,
	localFrame,
	canvasWidth,
	canvasHeight,
}: {
	shot: Shot;
	localFrame: number;
	canvasWidth: number;
	canvasHeight: number;
}) => {
	const states = Object.fromEntries(
		(shot.objects ?? []).map((object) => [
			object.id,
			resolveObjectState({
				object,
				localFrame,
				shotDuration: shot.duration,
				canvasWidth,
				canvasHeight,
			}),
		]),
	);

	for (const interaction of shot.interactions ?? []) {
		applyInteraction(states, interaction);
	}

	if (!shot.interactions?.length) {
		const charState = states.char ?? states.subject;
		const lightState = states.light;
		if (charState && lightState) {
			lightState.opacity *= mapRange(charState.x, [80, 460], [0.18, 0.95]);
		}
	}

	return states;
};

const renderFxContent = ({
	object,
	state,
	canvasWidth,
	canvasHeight,
	seed,
}: {
	object: ShotObject;
	state: ShotObjectState;
	canvasWidth: number;
	canvasHeight: number;
	seed: number;
}) => {
	const effect = object.effect
		?? (object.id.includes("light")
			? "light-sweep"
			: object.id.includes("fg") || object.id.includes("foreground")
				? "foreground-occlusion"
				: "glow-orb");

	if (effect === "foreground-occlusion") {
		return (
			<div
				style={{
					width: "100%",
					height: "100%",
					background: object.gradient
						?? `linear-gradient(120deg, rgba(0,0,0,0.72), ${object.color ?? "rgba(255,255,255,0.06)"} 55%, transparent 100%)`,
					borderRadius: object.borderRadius ?? 999,
					filter: `blur(${Math.max(8, state.blur)}px)`,
				}}
			/>
		);
	}

	if (effect === "vignette") {
		return (
			<div
				style={{
					width: canvasWidth,
					height: canvasHeight,
					boxShadow: "inset 0 0 220px rgba(0,0,0,0.35)",
				}}
			/>
		);
	}

	if (effect === "glow-orb") {
		return (
			<div
				style={{
					width: "100%",
					height: "100%",
					background: object.gradient
						?? `radial-gradient(circle at 50% 50%, ${object.color ?? "rgba(255,255,255,0.32)"} 0%, transparent 68%)`,
					filter: `blur(${Math.max(16, state.blur)}px)`,
				}}
			/>
		);
	}

	const sweepWidth = Math.max(state.width, canvasWidth * 0.28);
	return (
		<div
			style={{
				width: sweepWidth,
				height: canvasHeight,
				background: object.gradient
					?? `linear-gradient(${94 + (seed % 18)}deg, transparent 18%, ${object.color ?? "rgba(255,255,255,0.55)"} 48%, transparent 78%)`,
				filter: `blur(${Math.max(14, state.blur)}px)`,
			}}
		/>
	);
};

const ObjectLayer: React.FC<{
	object: ShotObject;
	state: ShotObjectState;
	canvasWidth: number;
	canvasHeight: number;
	seed: number;
}> = ({object, state, canvasWidth, canvasHeight, seed}) => {
	const width = state.width;
	const height = state.height;

	return (
		<AbsoluteFill
			style={{
				left: state.x,
				top: state.y,
				right: "auto",
				bottom: "auto",
				width,
				height,
				overflow: "hidden",
				opacity: state.opacity,
				zIndex: object.z,
				transform: `translate3d(0, 0, 0) scale(${state.scale})`,
				transformOrigin: "center center",
				mixBlendMode: object.blendMode ?? "normal",
				borderRadius: object.borderRadius ?? 0,
				filter: state.blur > 0 ? `blur(${state.blur}px)` : "none",
			}}
		>
			{object.type === "image" && object.src ? (
				<Img
					src={object.src}
					style={{
						width: "100%",
						height: "100%",
						objectFit: object.objectFit ?? "cover",
						borderRadius: object.borderRadius ?? 0,
					}}
				/>
			) : null}
			{object.type === "shape" ? (
				<div
					style={{
						width: "100%",
						height: "100%",
						borderRadius: object.shape === "circle" ? "50%" : object.borderRadius ?? 24,
						background: object.gradient ?? object.color ?? "rgba(255,255,255,0.12)",
					}}
				/>
			) : null}
			{object.type === "fx"
				? renderFxContent({object, state, canvasWidth, canvasHeight, seed})
				: null}
		</AbsoluteFill>
	);
};

type ShotDensityStackProps = {
	shot: Shot;
	frame: number;
	opacity: number;
	transform: string;
	width: number;
	height: number;
	zIndex: number;
};

export const ShotDensityStack: React.FC<ShotDensityStackProps> = ({
	shot,
	frame,
	opacity,
	transform,
	width,
	height,
	zIndex,
}) => {
	const objectKey = (shot.objects ?? []).map((object) => object.id).join("|");
	const seed = hashString(`${shot.src ?? "object-shot"}-${objectKey}-${shot.start}-${shot.duration}`);
	const localFrame = Math.max(0, frame - shot.start);
	const hasObjects = (shot.objects?.length ?? 0) > 0;

	const variationWindow = 15 + (seed % 16);
	const variationStep = Math.floor(localFrame / variationWindow);
	const variationValue = ((seed + variationStep * 13) % 17) / 16;
	const secondaryValue = ((seed + variationStep * 7) % 11) / 10;

	const mainScale = 1 + variationValue * (hasObjects ? 0.015 : 0.025);
	const brightness = 0.92 + variationValue * 0.16;
	const contrast = 1.02 + secondaryValue * 0.1;
	const saturate = 1.04 + variationValue * 0.18;
	const overlayOpacity = (hasObjects ? 0.08 : 0.14) + variationValue * 0.1;
	const grainOpacity = (hasObjects ? 0.04 : 0.08) + secondaryValue * 0.05;
	const noiseShiftX = ((variationStep * 17) + seed) % 120;
	const noiseShiftY = ((variationStep * 11) + seed) % 96;
	const sweepTravel = ((localFrame * 10) + seed) % (width * 2);
	const sweepAngle = (seed % 24) - 12;
	const bgHue = seed % 360;
	const objectStates = hasObjects
		? resolveInteractionStates({
			shot,
			localFrame,
			canvasWidth: width,
			canvasHeight: height,
		})
		: {};

	return (
		<div
			style={{
				position: "absolute",
				inset: 0,
				overflow: "hidden",
				opacity,
				zIndex,
			}}
		>
			<AbsoluteFill
				style={{
					background: `linear-gradient(${125 + (bgHue % 20)}deg, rgba(5,12,24,0.94), hsla(${bgHue}, 70%, 55%, 0.18), rgba(0,0,0,0.72))`,
				}}
			/>
			<AbsoluteFill
				style={{
					backgroundImage: [
						`radial-gradient(circle at ${22 + (seed % 46)}% ${18 + (seed % 38)}%, rgba(255,255,255,0.12), transparent 20%)`,
						`radial-gradient(circle at ${68 - (seed % 18)}% ${74 - (seed % 24)}%, rgba(255,180,120,0.10), transparent 24%)`,
					].join(","),
					filter: "blur(18px)",
					opacity: hasObjects ? 0.48 : 0.8,
				}}
			/>

			{hasObjects ? (
				<AbsoluteFill
					style={{
						transform: combineTransforms(transform, `scale(${mainScale})`),
						transformOrigin: "center center",
						filter: `brightness(${brightness}) contrast(${contrast}) saturate(${saturate})`,
					}}
				>
					{[...(shot.objects ?? [])]
						.sort((left, right) => left.z - right.z)
						.map((object) => {
							const state = objectStates[object.id];
							if (!state) {
								return null;
							}

							return (
								<ObjectLayer
									key={object.id}
									object={object}
									state={state}
									canvasWidth={width}
									canvasHeight={height}
									seed={seed}
								/>
							);
						})}
				</AbsoluteFill>
			) : shot.src ? (
				<AbsoluteFill
					style={{
						transform: combineTransforms(transform, `scale(${mainScale})`),
						transformOrigin: "center center",
						filter: `brightness(${brightness}) contrast(${contrast}) saturate(${saturate})`,
					}}
				>
					<Img
						src={shot.src}
						style={{
							width: "100%",
							height: "100%",
							objectFit: "cover",
						}}
					/>
				</AbsoluteFill>
			) : null}

			<AbsoluteFill
				style={{
					pointerEvents: "none",
					mixBlendMode: "overlay",
					opacity: overlayOpacity,
					backgroundImage: [
						`radial-gradient(circle at ${18 + (seed % 60)}% ${22 + (seed % 40)}%, rgba(255,255,255,0.16), transparent 18%)`,
						`radial-gradient(circle at ${72 - (seed % 20)}% ${70 - (seed % 35)}%, rgba(255,210,120,0.14), transparent 24%)`,
						`linear-gradient(${100 + sweepAngle}deg, transparent 34%, rgba(255,255,255,0.20) 50%, transparent 66%)`,
					].join(","),
					backgroundSize: `100% 100%, 100% 100%, ${Math.max(width * 0.34, 420)}px 100%`,
					backgroundRepeat: "no-repeat",
					backgroundPosition: `0 0, 0 0, ${sweepTravel - width}px 0`,
				}}
			/>
			<AbsoluteFill
				style={{
					pointerEvents: "none",
					opacity: grainOpacity,
					mixBlendMode: "soft-light",
					backgroundImage: [
						"repeating-linear-gradient(0deg, rgba(255,255,255,0.16) 0 1px, transparent 1px 3px)",
						"repeating-linear-gradient(90deg, rgba(0,0,0,0.22) 0 1px, transparent 1px 4px)",
						"repeating-linear-gradient(45deg, rgba(255,255,255,0.08) 0 2px, transparent 2px 6px)",
					].join(","),
					backgroundSize: "64px 64px, 96px 96px, 128px 128px",
					backgroundPosition: `${noiseShiftX}px ${noiseShiftY}px, ${-noiseShiftY}px ${noiseShiftX}px, ${noiseShiftY}px ${-noiseShiftX}px`,
				}}
			/>
			<AbsoluteFill
				style={{
					pointerEvents: "none",
					boxShadow: "inset 0 0 180px rgba(0,0,0,0.28)",
				}}
			/>
		</div>
	);
};
