import type {VideoLayout} from "./types";

type VideoLayoutInput = VideoLayout | {video?: VideoLayout};

export const looksLikeVideoLayout = (value: unknown): value is VideoLayout => {
	if (!value || typeof value !== "object") {
		return false;
	}

	const candidate = value as Partial<VideoLayout>;
	return (
		typeof candidate.width === "number" &&
		typeof candidate.height === "number" &&
		Array.isArray(candidate.elements)
	);
};

export const resolveVideoLayout = (input: VideoLayoutInput): VideoLayout => {
	const wrapped = input as {video?: VideoLayout};
	if (looksLikeVideoLayout(wrapped.video)) {
		return wrapped.video;
	}

	return input as VideoLayout;
};

export const getLayoutElementEnd = (layout: VideoLayout): number => {
	return (layout.elements ?? []).reduce((max, element) => {
		return Math.max(max, element.start + element.duration);
	}, 0);
};

export const getLayoutShotEnd = (layout: VideoLayout): number => {
	return (layout.shots ?? []).reduce((max, shot) => {
		return Math.max(max, shot.start + shot.duration);
	}, 0);
};

export const getLayoutAudioEnd = (layout: VideoLayout): number => {
	return (layout.audioTracks ?? []).reduce((max, track) => {
		return Math.max(max, track.start + track.duration);
	}, 0);
};

export const getLayoutDurationInFrames = (input: VideoLayoutInput): number => {
	const layout = resolveVideoLayout(input);

	return Math.max(
		layout.durationInFrames ?? 0,
		getLayoutElementEnd(layout),
		getLayoutShotEnd(layout),
		getLayoutAudioEnd(layout),
		1,
	);
};
