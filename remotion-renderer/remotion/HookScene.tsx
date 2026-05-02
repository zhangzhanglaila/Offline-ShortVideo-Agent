import React from "react";
import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from "remotion";
import {FONT_FAMILY} from "./constants";

interface HookSceneProps {
	text: string;
	durationInFrames: number;
}

export const HookScene: React.FC<HookSceneProps> = ({text, durationInFrames}) => {
	const frame = useCurrentFrame();

	const fadeIn = interpolate(frame, [0, 15], [0, 1], {
		extrapolateLeft: "clamp",
		extrapolateRight: "clamp",
	});
	const fadeOut = interpolate(frame, [durationInFrames - 20, durationInFrames], [1, 0], {
		extrapolateLeft: "clamp",
		extrapolateRight: "clamp",
	});
	const scale = interpolate(frame, [0, 40], [1.08, 1], {
		easing: Easing.out(Easing.cubic),
		extrapolateLeft: "clamp",
		extrapolateRight: "clamp",
	});
	const bgPulse = 1 + Math.sin(frame * 0.06) * 0.04;

	return (
		<AbsoluteFill
			style={{
				background:
					"radial-gradient(circle at 50% 50%, rgba(98,217,255,0.18), transparent 50%), linear-gradient(180deg, #071018 0%, #070b10 100%)",
				fontFamily: FONT_FAMILY,
			}}
		>
			{/* Ambient glow behind text */}
			<div
				style={{
					position: "absolute",
					inset: 0,
					background: `radial-gradient(ellipse 60% 30% at 50% 45%, rgba(98,217,255,${0.12 * bgPulse}), transparent 70%)`,
				}}
			/>
			<div
				style={{
					position: "absolute",
					inset: 0,
					display: "flex",
					flexDirection: "column",
					alignItems: "center",
					justifyContent: "center",
					opacity: fadeIn * fadeOut,
					transform: `scale(${scale})`,
				}}
			>
				<div
					style={{
						color: "#f8fbff",
						fontSize: 68,
						fontWeight: 860,
						textAlign: "center",
						textShadow: "0 0 48px rgba(98,217,255,0.4), 0 4px 24px rgba(0,0,0,0.5)",
						maxWidth: 800,
						lineHeight: 1.2,
						padding: "0 40px",
					}}
				>
					{text}
				</div>
				<div
					style={{
						marginTop: 32,
						width: 120,
						height: 3,
						background: "linear-gradient(90deg, transparent, rgba(98,217,255,0.7), transparent)",
						borderRadius: 2,
					}}
				/>
			</div>
		</AbsoluteFill>
	);
};
