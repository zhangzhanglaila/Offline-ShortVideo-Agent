import React from "react";
import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from "remotion";
import {FONT_FAMILY} from "./constants";

interface CardSceneProps {
	title: string;
	items: string[];
	durationInFrames: number;
}

const CARD_COLORS = [
	{gradient: "linear-gradient(135deg, rgba(98,217,255,0.18), rgba(56,168,255,0.08))", border: "rgba(98,217,255,0.45)"},
	{gradient: "linear-gradient(135deg, rgba(138,180,255,0.16), rgba(108,148,255,0.07))", border: "rgba(138,180,255,0.40)"},
	{gradient: "linear-gradient(135deg, rgba(98,255,200,0.14), rgba(56,224,180,0.06))", border: "rgba(98,255,200,0.38)"},
	{gradient: "linear-gradient(135deg, rgba(255,180,98,0.14), rgba(255,148,56,0.06))", border: "rgba(255,180,98,0.38)"},
];

export const CardScene: React.FC<CardSceneProps> = ({title, items, durationInFrames}) => {
	const frame = useCurrentFrame();
	const {fps} = useVideoConfig();

	const fadeIn = interpolate(frame, [0, 12], [0, 1], {
		extrapolateLeft: "clamp",
		extrapolateRight: "clamp",
	});
	const fadeOut = interpolate(frame, [durationInFrames - 18, durationInFrames], [1, 0], {
		extrapolateLeft: "clamp",
		extrapolateRight: "clamp",
	});

	const titleAppear = spring({
		frame: frame - 5,
		fps,
		config: {damping: 14, stiffness: 100, mass: 0.8},
	});

	return (
		<AbsoluteFill
			style={{
				background:
					"radial-gradient(circle at 50% 30%, rgba(98,217,255,0.12), transparent 40%), linear-gradient(180deg, #070b10 0%, #091018 100%)",
				fontFamily: FONT_FAMILY,
			}}
		>
			<div
				style={{
					position: "absolute",
					inset: 0,
					display: "flex",
					flexDirection: "column",
					alignItems: "center",
					justifyContent: "center",
					opacity: fadeIn * fadeOut,
					padding: "0 60px",
				}}
			>
				<div
					style={{
						color: "#f8fbff",
						fontSize: 44,
						fontWeight: 800,
						textAlign: "center",
						textShadow: "0 0 24px rgba(98,217,255,0.25)",
						marginBottom: 48,
						transform: `translateY(${interpolate(titleAppear, [0, 1], [-20, 0])}px)`,
						opacity: titleAppear,
					}}
				>
					{title}
				</div>
				<div style={{display: "flex", flexWrap: "wrap", justifyContent: "center", gap: 20, maxWidth: 900}}>
					{items.map((item, i) => {
						const cardSpring = spring({
							frame: frame - 15 - i * 10,
							fps,
							config: {damping: 15, stiffness: 90, mass: 0.85},
						});
						const colors = CARD_COLORS[i % CARD_COLORS.length];
						return (
							<div
								key={i}
								style={{
									width: 260,
									height: 140,
									borderRadius: 20,
									border: `2px solid ${colors.border}`,
									background: colors.gradient,
									display: "flex",
									alignItems: "center",
									justifyContent: "center",
									opacity: cardSpring,
									transform: `translateY(${interpolate(cardSpring, [0, 1], [36, 0])}px) scale(${interpolate(cardSpring, [0, 1], [0.92, 1])})`,
									boxShadow: `0 0 ${20 * cardSpring}px rgba(98,217,255,0.1)`,
								}}
							>
								<span
									style={{
										color: "#eef6ff",
										fontSize: 28,
										fontWeight: 700,
										textAlign: "center",
										padding: "0 16px",
									}}
								>
									{item}
								</span>
							</div>
						);
					})}
				</div>
			</div>
		</AbsoluteFill>
	);
};
