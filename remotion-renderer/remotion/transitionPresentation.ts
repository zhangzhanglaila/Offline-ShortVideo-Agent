import type {CSSProperties} from "react";
import {interpolate} from "remotion";
import type {Shot} from "./types";

export type SlideDirection =
	| "from-left"
	| "from-top"
	| "from-right"
	| "from-bottom";

export type PresentationDirection = "entering" | "exiting";
export type TransitionPresentationKind = "fade" | "slide";

type PresentationInput = {
	kind: TransitionPresentationKind;
	presentationDirection: PresentationDirection;
	presentationProgress: number;
	direction: SlideDirection;
};

const epsilon = 0.01;

export const getLinearTransitionProgress = ({
	frame,
	durationInFrames,
}: {
	frame: number;
	durationInFrames: number;
}) => {
	return interpolate(frame, [0, durationInFrames], [0, 1], {
		extrapolateLeft: "clamp",
		extrapolateRight: "clamp",
	});
};

const getFadeStyle = ({
	presentationDirection,
	presentationProgress,
}: PresentationInput): CSSProperties => {
	const isEntering = presentationDirection === "entering";
	return {
		opacity: isEntering ? presentationProgress : 1 - presentationProgress,
	};
};

const getSlideStyle = ({
	presentationDirection,
	presentationProgress,
	direction,
}: PresentationInput): CSSProperties => {
	const presentationProgressWithEpsilonCorrection =
		presentationProgress === 1
			? presentationProgress * 100
			: presentationProgress * 100 - epsilon;

	if (presentationDirection === "exiting") {
		switch (direction) {
			case "from-left":
				return {
					transform: `translateX(${presentationProgressWithEpsilonCorrection}%)`,
				};
			case "from-right":
				return {
					transform: `translateX(${-presentationProgress * 100}%)`,
				};
			case "from-top":
				return {
					transform: `translateY(${presentationProgressWithEpsilonCorrection}%)`,
				};
			case "from-bottom":
				return {
					transform: `translateY(${-presentationProgress * 100}%)`,
				};
			default:
				return {};
		}
	}

	switch (direction) {
		case "from-left":
			return {
				transform: `translateX(${-100 + presentationProgress * 100}%)`,
			};
		case "from-right":
			return {
				transform: `translateX(${100 - presentationProgressWithEpsilonCorrection}%)`,
			};
		case "from-top":
			return {
				transform: `translateY(${-100 + presentationProgress * 100}%)`,
			};
		case "from-bottom":
			return {
				transform: `translateY(${100 - presentationProgressWithEpsilonCorrection}%)`,
			};
		default:
			return {};
	}
};

export const getPresentationStyle = (input: PresentationInput): CSSProperties => {
	// Source copied from remotionOriginal/packages/transitions:
	// - src/presentations/fade.tsx
	// - src/presentations/slide.tsx
	return input.kind === "fade" ? getFadeStyle(input) : getSlideStyle(input);
};

const readMetaValue = (shot: Shot | null, key: string): string => {
	const value = shot?._meta?.[key];
	return typeof value === "string" ? value.toLowerCase() : "";
};

const resolveSlideDirection = (next: Shot | null): SlideDirection => {
	switch (next?.camera) {
		case "pan-left":
			return "from-right";
		case "pan-right":
			return "from-left";
		case "tilt-up":
			return "from-bottom";
		case "tilt-down":
			return "from-top";
		default:
			return "from-right";
	}
};

export const resolveTransitionPresentation = (
	current: Shot,
	next: Shot | null,
): {kind: TransitionPresentationKind; direction: SlideDirection} => {
	const transition = `${readMetaValue(current, "transition")} ${readMetaValue(current, "sceneTransition")}`;
	if (transition.includes("fade") || transition.includes("silence-hold")) {
		return {kind: "fade", direction: "from-right"};
	}

	return {
		kind: "slide",
		direction: resolveSlideDirection(next),
	};
};
