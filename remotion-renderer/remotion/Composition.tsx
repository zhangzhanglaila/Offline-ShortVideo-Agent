/**
 * Composition - Remotion component entry
 */
import React from "react";
import {VideoScene} from "./VideoScene";
import {resolveVideoLayout} from "./layoutUtils";
import {videoLayoutSchema, type VideoLayoutSchemaProps} from "./layoutSchema";
import type {VideoLayout} from "./types";

export type {VideoLayout};
export {videoLayoutSchema};

export type VideoProps = VideoLayoutSchemaProps;

export const VideoComposition: React.FC<VideoProps> = (props) => {
	const layout = resolveVideoLayout(
		props as unknown as VideoLayout & {video?: VideoLayout},
	);
	return <VideoScene layout={layout} />;
};
