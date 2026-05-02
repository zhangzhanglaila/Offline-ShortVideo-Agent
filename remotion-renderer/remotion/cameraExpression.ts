import {Easing, interpolate, spring} from "remotion";
import type {Shot} from "./types";

type CameraExpressionInput = {
	shot: Shot;
	frame: number;
	fps: number;
	width: number;
	height: number;
	cameraOverride: string;
};

const CINEMATIC_EASING = Easing.bezier(0.42, 0, 0.58, 1);

const clamp01 = (value: number) => Math.max(0, Math.min(1, value));

const getMoveProgress = (frame: number, duration: number) => {
	return interpolate(frame, [0, Math.max(duration - 1, 1)], [0, 1], {
		easing: CINEMATIC_EASING,
		extrapolateLeft: "clamp",
		extrapolateRight: "clamp",
	});
};

export const getCameraShotTransform = ({
	shot,
	frame,
	fps,
	width,
	height,
	cameraOverride,
}: CameraExpressionInput): {shotTransform: string; emotionTransform: string} => {
	const {cropX = 0, cropY = 0, cropW = 1, cropH = 1, camera} = shot;
	const localFrame = Math.max(0, frame - shot.start);
	const progress = getMoveProgress(localFrame, shot.duration);

	// Source style:
	// - spring() stagger / damping from brand/src/ScalingLogo.tsx
	// - rotate interpolate from brand/src/Brand/Recorder.tsx
	// - easing window from brand/src/Compose/WhatIsRemotion.tsx
	const settle = clamp01(
		spring({
			frame: localFrame,
			fps,
			config: {
				mass: 2,
				damping: 200,
			},
			durationInFrames: Math.min(25, Math.max(shot.duration, 1)),
			durationRestThreshold: 0.0001,
		}),
	);

	const baseScaleX = 1 / cropW;
	const baseScaleY = 1 / cropH;
	const baseTranslateX = -(cropX * width) * baseScaleX;
	const baseTranslateY = -(cropY * height) * baseScaleY;

	let motionX = 0;
	let motionY = 0;
	let motionScale = 1;
	let motionRotate = 0;

	switch (camera) {
		case "push-in":
			motionScale = interpolate(progress, [0, 1], [1, 1.14]);
			motionRotate = interpolate(settle, [0, 1], [-Math.PI / 96, 0]);
			break;
		case "pull-out":
			motionScale = interpolate(progress, [0, 1], [1.12, 1]);
			motionRotate = interpolate(settle, [0, 1], [Math.PI / 128, 0]);
			break;
		case "pan-left":
			motionScale = 1.08;
			motionX = interpolate(progress, [0, 1], [42, -42]);
			motionRotate = interpolate(settle, [0, 1], [Math.PI / 96, 0]);
			break;
		case "pan-right":
			motionScale = 1.08;
			motionX = interpolate(progress, [0, 1], [-42, 42]);
			motionRotate = interpolate(settle, [0, 1], [-Math.PI / 96, 0]);
			break;
		case "tilt-up":
			motionScale = 1.06;
			motionY = interpolate(progress, [0, 1], [36, -36]);
			motionRotate = interpolate(settle, [0, 1], [Math.PI / 160, 0]);
			break;
		case "tilt-down":
			motionScale = 1.06;
			motionY = interpolate(progress, [0, 1], [-36, 36]);
			motionRotate = interpolate(settle, [0, 1], [-Math.PI / 160, 0]);
			break;
		case "shake":
			motionScale = 1.08;
			motionX = Math.sin(frame * 0.9) * 10;
			motionY = Math.cos(frame * 1.1) * 8;
			motionRotate = Math.sin(frame * 0.12) * 0.02;
			break;
		case "static":
		default:
			motionScale = interpolate(progress, [0, 1], [1.02, 1.05]);
			motionY = Math.sin(frame * 0.02) * 4;
			motionRotate = interpolate(settle, [0, 1], [Math.PI / 180, 0]);
			break;
	}

	const scaleX = baseScaleX * motionScale;
	const scaleY = baseScaleY * motionScale;
	const translateX = baseTranslateX + motionX;
	const translateY = baseTranslateY + motionY;

	const shotTransform = `translate(${translateX}px, ${translateY}px) scale(${scaleX}, ${scaleY}) rotate(${motionRotate}rad)`;

	let emotionTransform = "";
	if (cameraOverride === "shake") {
		emotionTransform = `translate(${Math.sin(frame * 3.1) * 6}px, ${Math.cos(frame * 2.7) * 6}px)`;
	} else if (cameraOverride === "pulse") {
		const pulse = 1 + Math.sin(frame * 0.05) * 0.02;
		emotionTransform = `scale(${pulse})`;
	} else if (cameraOverride === "slow-zoom") {
		const slow = 1 + Math.sin(frame * 0.015) * 0.01;
		emotionTransform = `scale(${slow})`;
	}

	return {shotTransform, emotionTransform};
};
