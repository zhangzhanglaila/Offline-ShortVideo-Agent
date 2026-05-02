import {Easing, interpolate, spring} from "remotion";
import type {ElementAnimation} from "./types";

type LayerAnimationInput = {
	frame: number;
	fps: number;
	start: number;
	duration: number;
	animation?: ElementAnimation;
};

const ENTRANCE_DAMPING = 200;
const ENTRANCE_DISTANCE = 40;

const clamp01 = (value: number) => Math.max(0, Math.min(1, value));

const joinTransforms = (...values: Array<string | null | undefined>) =>
	values.filter(Boolean).join(" ").trim();

export const getLayerAnimationStyle = ({
	frame,
	fps,
	start,
	duration,
	animation,
}: LayerAnimationInput) => {
	const localFrame = frame - start;
	const isVisible = localFrame >= 0 && localFrame <= duration;
	if (!isVisible) {
		return {opacity: 0, transform: "", filter: "none", isVisible: false};
	}

	const animationFrames = Math.min(animation?.duration ?? 18, Math.max(duration, 1));
	const enterProgress = clamp01(
		spring({
			frame: localFrame,
			fps,
			config: {damping: ENTRANCE_DAMPING},
			durationInFrames: animationFrames,
			durationRestThreshold: 0.001,
		}),
	);

	const exitFrame = localFrame - Math.max(duration - animationFrames, 0);
	const exitProgress =
		exitFrame <= 0
			? 0
			: interpolate(exitFrame, [0, animationFrames], [0, 1], {
					easing: Easing.bezier(0.42, 0, 0.58, 1),
					extrapolateLeft: "clamp",
					extrapolateRight: "clamp",
				});

	const enterType = animation?.enter ?? "fade";
	const exitType = animation?.exit ?? "fade";

	let opacity = localFrame < animationFrames ? enterProgress : 1;
	let enterTransform = "";
	let exitTransform = "";
	let filter = "none";

	// Source style:
	// - heading/card spring entrances from brand/src/RulesEnumeration/RulesEnumeration.tsx
	// - scaling pattern from brand/src/ScalingLogo.tsx
	switch (enterType) {
		case "slide-up":
			enterTransform = `translateY(${(1 - enterProgress) * ENTRANCE_DISTANCE}px)`;
			break;
		case "slide-down":
			enterTransform = `translateY(${(enterProgress - 1) * ENTRANCE_DISTANCE}px)`;
			break;
		case "zoom-in":
			enterTransform = `scale(${interpolate(enterProgress, [0, 1], [0.86, 1])})`;
			break;
		case "zoom-out":
			enterTransform = `scale(${interpolate(enterProgress, [0, 1], [1.16, 1])})`;
			break;
		case "bounce-in":
			enterTransform = `scale(${interpolate(enterProgress, [0, 1], [0.8, 1])})`;
			break;
		case "blur-in":
			filter = `blur(${(1 - enterProgress) * 12}px)`;
			break;
		default:
			break;
	}

	if (exitProgress > 0) {
		opacity = 1 - exitProgress;
		switch (exitType) {
			case "slide-up":
				exitTransform = `translateY(${-exitProgress * ENTRANCE_DISTANCE}px)`;
				break;
			case "slide-down":
				exitTransform = `translateY(${exitProgress * ENTRANCE_DISTANCE}px)`;
				break;
			case "zoom-out":
				exitTransform = `scale(${interpolate(exitProgress, [0, 1], [1, 0.88])})`;
				break;
			case "blur-out":
				filter = `blur(${exitProgress * 12}px)`;
				break;
			default:
				break;
		}
	}

	if (exitProgress === 0 && enterProgress >= 1) {
		const breathe = 1 + Math.sin(localFrame * 0.04) * 0.01;
		enterTransform = joinTransforms(enterTransform, `scale(${breathe})`);
	}

	return {
		opacity: clamp01(opacity),
		transform: joinTransforms(enterTransform, exitTransform),
		filter,
		isVisible: true,
	};
};
