/**
 * Root - Remotion composition registration
 */
import React, {useCallback} from "react";
import {Composition, type CalculateMetadataFunction} from "remotion";
import {VideoComposition, type VideoProps, videoLayoutSchema} from "./Composition";
import {getLayoutDurationInFrames, resolveVideoLayout} from "./layoutUtils";
import type {VideoLayout} from "./types";

export const RemotionRoot: React.FC = () => {
	const calculateMetadata = useCallback<CalculateMetadataFunction<VideoProps>>(
		async ({props}) => {
			const layout = resolveVideoLayout(
				props as unknown as VideoLayout & {video?: VideoLayout},
			);
			return {
				durationInFrames: getLayoutDurationInFrames(layout),
				fps: layout.fps ?? 30,
			};
		},
		[],
	);

	return (
		<>
			<Composition
				id="VideoFlow"
				component={VideoComposition}
				durationInFrames={300}
				fps={30}
				width={1080}
				height={1920}
				schema={videoLayoutSchema}
				calculateMetadata={calculateMetadata}
				defaultProps={{
					width: 1080,
					height: 1920,
					fps: 30,
					durationInFrames: 300,
					background: "#0A0E14",
					elements: [],
					audioTracks: [],
					shots: [],
				}}
			/>
		</>
	);
};
