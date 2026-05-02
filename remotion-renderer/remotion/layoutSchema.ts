import {z} from "zod";
import {
	getLayoutDurationInFrames,
	getLayoutElementEnd,
	getLayoutShotEnd,
	resolveVideoLayout,
} from "./layoutUtils";
import type {VideoLayout} from "./types";

const baseElementSchema = z.object({
	id: z.string(),
	start: z.number().int().nonnegative(),
	duration: z.number().int().positive().default(150),
	zIndex: z.number().default(0),
	animation: z
		.object({
			enter: z
				.enum([
					"fade",
					"slide-up",
					"slide-down",
					"zoom-in",
					"zoom-out",
					"bounce-in",
					"blur-in",
				])
				.optional(),
			exit: z
				.enum(["fade", "slide-up", "slide-down", "zoom-out", "blur-out"])
				.optional(),
			duration: z.number().int().positive().optional(),
		})
		.optional(),
});

const textElementSchema = baseElementSchema.extend({
	type: z.literal("text"),
	text: z.string(),
	x: z.number(),
	y: z.number(),
	fontSize: z.number().positive(),
	color: z.string(),
	fontWeight: z.number().optional(),
	textAlign: z.enum(["left", "center", "right"]).optional(),
});

const imageElementSchema = baseElementSchema.extend({
	type: z.literal("image"),
	src: z.string(),
	x: z.number(),
	y: z.number(),
	width: z.number().positive(),
	height: z.number().positive(),
	borderRadius: z.number().optional(),
	objectFit: z.enum(["cover", "contain", "fill"]).optional(),
});

const stickerElementSchema = baseElementSchema.extend({
	type: z.literal("sticker"),
	emoji: z.string(),
	x: z.number(),
	y: z.number(),
	size: z.number().positive(),
});

const backgroundElementSchema = baseElementSchema.extend({
	type: z.literal("background"),
	color: z.string().optional(),
	gradient: z.string().optional(),
	image: z.string().optional(),
});

const shapeElementSchema = baseElementSchema.extend({
	type: z.literal("shape"),
	shape: z.enum(["rect", "circle", "line"]),
	x: z.number(),
	y: z.number(),
	width: z.number().positive(),
	height: z.number().positive(),
	color: z.string(),
	fillColor: z.string().optional(),
	borderRadius: z.number().optional(),
	rotation: z.number().optional(),
});

const elementSchema = z.discriminatedUnion("type", [
	textElementSchema,
	imageElementSchema,
	stickerElementSchema,
	backgroundElementSchema,
	shapeElementSchema,
]);

const shotObjectAnimationSchema = z.object({
	type: z.enum(["move", "zoom", "float", "sweep"]),
	from: z.tuple([z.number(), z.number()]).optional(),
	to: z.tuple([z.number(), z.number()]).optional(),
	fromScale: z.number().positive().optional(),
	toScale: z.number().positive().optional(),
	amplitude: z.number().nonnegative().optional(),
	speed: z.number().positive().optional(),
});

const shotObjectSchema = z.object({
	id: z.string(),
	type: z.enum(["image", "fx", "shape"]),
	z: z.number(),
	src: z.string().optional(),
	x: z.number().optional(),
	y: z.number().optional(),
	width: z.number().positive().optional(),
	height: z.number().positive().optional(),
	opacity: z.number().min(0).max(1).optional(),
	blur: z.number().min(0).optional(),
	scale: z.number().positive().optional(),
	color: z.string().optional(),
	gradient: z.string().optional(),
	borderRadius: z.number().min(0).optional(),
	blendMode: z.enum(["normal", "screen", "overlay", "soft-light", "multiply"]).optional(),
	objectFit: z.enum(["cover", "contain", "fill"]).optional(),
	shape: z.enum(["rect", "circle"]).optional(),
	effect: z.enum(["light-sweep", "foreground-occlusion", "glow-orb", "vignette"]).optional(),
	animation: shotObjectAnimationSchema.optional(),
});

const shotInteractionSchema = z.object({
	sourceId: z.string(),
	targetId: z.string(),
	type: z.enum(["link-opacity", "proximity-scale"]),
	inputRange: z.tuple([z.number(), z.number()]).optional(),
	outputRange: z.tuple([z.number(), z.number()]).optional(),
	distance: z.number().positive().optional(),
});

const audioTrackSchema = z.object({
	id: z.string(),
	src: z.string(),
	start: z.number().int().nonnegative(),
	duration: z.number().int().positive(),
	text: z.string().optional(),
});

const graphNodeSchema = z.object({
	id: z.string(),
	label: z.string(),
	role: z.string().optional(),
	group: z.string().optional(),
	x: z.number(),
	y: z.number(),
	width: z.number().positive(),
	height: z.number().positive(),
	color: z.string().optional(),
	fill: z.string().optional(),
});

const graphEdgeSchema = z.object({
	id: z.string(),
	from: z.string(),
	to: z.string(),
	label: z.string().optional(),
	kind: z.string().optional(),
	color: z.string().optional(),
	points: z.tuple([z.number(), z.number(), z.number(), z.number()]).optional(),
});

const graphStepSchema = z.object({
	id: z.string(),
	caption: z.string().optional(),
	nodeIds: z.array(z.string()),
	edgeIds: z.array(z.string()),
	start: z.number().int().nonnegative(),
	duration: z.number().int().positive(),
});

const graphTimelineEventSchema = z.object({
	id: z.string(),
	time: z.number().int().nonnegative().optional(),
	action: z.enum(["highlight_node", "highlight_edge", "highlight_path", "pulse"]),
	text: z.string().optional(),
	nodeIds: z.array(z.string()),
	edgeIds: z.array(z.string()),
	start: z.number().int().nonnegative(),
	duration: z.number().int().positive(),
});

const animationPlanStepSchema = z.object({
	id: z.string(),
	action: z.enum(["reveal", "flow", "highlight", "pulse", "camera_pan", "miss_effect"]),
	start: z.number().int().nonnegative(),
	duration: z.number().int().positive(),
	nodeIds: z.array(z.string()).default([]),
	edgeIds: z.array(z.string()).default([]),
	text: z.string().optional(),
	intensity: z.number().min(0).max(1).optional(),
	cameraFrom: z.string().optional(),
	cameraTo: z.string().optional(),
});

const animationPlanSchema = z.object({
	version: z.literal(1),
	steps: z.array(animationPlanStepSchema).min(1),
	nodeTiers: z.object({
		hero: z.string(),
		secondary: z.array(z.string()),
		others: z.array(z.string()),
	}).optional(),
});

const graphSceneSchema = z.object({
	scene_type: z.literal("graph"),
	title: z.string(),
	summary: z.string().optional(),
	nodes: z.array(graphNodeSchema).min(2),
	edges: z.array(graphEdgeSchema).min(1),
	steps: z.array(graphStepSchema).min(1),
	timeline: z.array(graphTimelineEventSchema).optional(),
	animation_plan: animationPlanSchema.optional(),
});

const shotSchema = z.object({
	start: z.number().int().nonnegative(),
	duration: z.number().int().positive(),
	src: z.string().optional(),
	camera: z
		.enum([
			"push-in",
			"pan-left",
			"pan-right",
			"pull-out",
			"tilt-up",
			"tilt-down",
			"static",
			"shake",
		])
		.optional(),
	cropX: z.number().optional(),
	cropY: z.number().optional(),
	cropW: z.number().positive().optional(),
	cropH: z.number().positive().optional(),
	opacity: z.number().min(0).max(1).optional(),
	objects: z.array(shotObjectSchema).optional(),
	interactions: z.array(shotInteractionSchema).optional(),
	_meta: z.record(z.string(), z.unknown()).optional(),
}).superRefine((shot, ctx) => {
	if (!shot.src && (!shot.objects || shot.objects.length === 0)) {
		ctx.addIssue({
			code: z.ZodIssueCode.custom,
			message: "shot must provide either src or objects",
			path: ["src"],
		});
	}
});

const sceneSchema = z.object({
	start: z.number().nonnegative(),
	end: z.number().nonnegative(),
	type: z.enum(["hook", "explain", "cta"]),
	emotionalCurve: z.array(z.number().min(0).max(1)).min(1),
	pacingCurve: z.array(z.number().min(0).max(1)).min(1),
	visualStyle: z.enum(["cinematic", "bold", "minimalist", "tech", "warm"]),
});

const emphasisPointSchema = z.object({
	at: z.tuple([z.number().nonnegative(), z.number().nonnegative()]),
	type: z.enum(["visual", "audio", "both"]),
	action: z.enum([
		"zoom-in",
		"flash",
		"pause",
		"slow-down",
		"subtitle-pulse",
		"voice-up",
	]),
});

const wordCueSchema = z.object({
	index: z.number().int().nonnegative(),
	word: z.string(),
	start: z.number().nonnegative(),
	end: z.number().nonnegative(),
});

const subtitleCueSchema = z.object({
	id: z.string(),
	start: z.number().nonnegative(),
	end: z.number().nonnegative(),
	words: z.array(wordCueSchema),
});

const emphasisPointWordSchema = z.object({
	wordIndices: z.array(z.number().int().nonnegative()),
	type: z.enum(["visual", "audio", "both"]),
	action: z.enum([
		"zoom-in",
		"flash",
		"pause",
		"slow-down",
		"subtitle-pulse",
		"voice-up",
	]),
});

const directorSchema = z.object({
	arc: z.enum(["hook-first", "problem-solution", "story", "viral"]),
	scenes: z.array(sceneSchema),
	emotionalCurve: z.array(z.number().min(0).max(1)),
	pacingCurve: z.array(z.number().min(0).max(1)),
	ttsVoice: z.enum(["male_deep", "female_energetic", "female_calm", "neutral"]),
	ttsSpeed: z.number().positive(),
	emphasisPoints: z.array(emphasisPointSchema),
	cameraStrategy: z.enum(["zoom-in-out", "pan", "static", "shake"]),
	subtitleCues: z.array(subtitleCueSchema),
	allWords: z.array(wordCueSchema),
	emphasisPointsWord: z.array(emphasisPointWordSchema),
});

const addIssue = (
	ctx: z.RefinementCtx,
	path: Array<string | number>,
	message: string,
) => {
	ctx.addIssue({
		code: z.ZodIssueCode.custom,
		path,
		message,
	});
};

const validateElements = (
	layout: VideoLayout,
	totalFrames: number,
	ctx: z.RefinementCtx,
) => {
	for (const [index, element] of (layout.elements ?? []).entries()) {
		const end = element.start + element.duration;
		if (element.start >= totalFrames) {
			addIssue(ctx, ["elements", index, "start"], "element start exceeds durationInFrames");
		}
		if (end > totalFrames) {
			addIssue(ctx, ["elements", index, "duration"], "element end exceeds durationInFrames");
		}
	}
};

const validateShots = (
	layout: VideoLayout,
	totalFrames: number,
	ctx: z.RefinementCtx,
) => {
	const shots = layout.shots ?? [];
	if (shots.length === 0) {
		return;
	}

	for (const [index, shot] of shots.entries()) {
		const end = shot.start + shot.duration;
		if (shot.start >= totalFrames) {
			addIssue(ctx, ["shots", index, "start"], "shot start exceeds durationInFrames");
		}
		if (end > totalFrames) {
			addIssue(ctx, ["shots", index, "duration"], "shot end exceeds durationInFrames");
		}
	}

	if (shots[0].start != 0) {
		addIssue(ctx, ["shots", 0, "start"], "first shot must start at frame 0");
	}

	for (let index = 1; index < shots.length; index++) {
		const previous = shots[index - 1];
		const current = shots[index];
		const expectedStart = previous.start + previous.duration;
		if (current.start !== expectedStart) {
			addIssue(ctx, ["shots", index, "start"], "shots must be continuous without overlap or gaps");
		}
	}

	const lastShot = shots[shots.length - 1];
	if (lastShot.start + lastShot.duration !== totalFrames) {
		addIssue(ctx, ["shots", shots.length - 1, "duration"], "shots must cover the full durationInFrames");
	}
};

export const videoLayoutSchema = z
	.object({
		width: z.number().int().positive(),
		height: z.number().int().positive(),
		fps: z.number().int().positive().default(30),
		durationInFrames: z.number().int().positive().optional(),
		background: z.string().optional(),
		scene_type: z.literal("graph").optional(),
		graph: graphSceneSchema.optional(),
		nodes: z.array(graphNodeSchema).optional(),
		edges: z.array(graphEdgeSchema).optional(),
		elements: z.array(elementSchema),
		audioTracks: z.array(audioTrackSchema).optional(),
		shots: z.array(shotSchema).optional(),
		director: directorSchema.optional(),
	})
	.passthrough()
	.superRefine((input, ctx) => {
		const layout = resolveVideoLayout(input as VideoLayout & {video?: VideoLayout});
		const declaredDuration = layout.durationInFrames;
		const maxEnd = Math.max(getLayoutElementEnd(layout), getLayoutShotEnd(layout), 1);

		if (declaredDuration !== undefined && declaredDuration < maxEnd) {
			addIssue(ctx, ["durationInFrames"], "durationInFrames is shorter than the timeline end");
		}

		const totalFrames = declaredDuration ?? getLayoutDurationInFrames(layout);
		validateElements(layout, totalFrames, ctx);
		validateShots(layout, totalFrames, ctx);

		if (layout.director && layout.director.scenes.length === 0) {
			addIssue(ctx, ["director", "scenes"], "director.scenes cannot be empty when director is provided");
		}

		if (layout.scene_type === "graph") {
			if (!layout.graph) {
				addIssue(ctx, ["graph"], "graph layout requires graph data");
				return;
			}

			const nodeIds = new Set(layout.graph.nodes.map((node) => node.id));
			const edgeIds = new Set(layout.graph.edges.map((edge) => edge.id));
			for (const [index, edge] of layout.graph.edges.entries()) {
				if (!nodeIds.has(edge.from)) {
					addIssue(ctx, ["graph", "edges", index, "from"], "edge source node does not exist");
				}
				if (!nodeIds.has(edge.to)) {
					addIssue(ctx, ["graph", "edges", index, "to"], "edge target node does not exist");
				}
			}
			for (const [index, step] of layout.graph.steps.entries()) {
				for (const nodeId of step.nodeIds) {
					if (!nodeIds.has(nodeId)) {
						addIssue(ctx, ["graph", "steps", index, "nodeIds"], "step node does not exist");
					}
				}
				for (const edgeId of step.edgeIds) {
					if (!edgeIds.has(edgeId)) {
						addIssue(ctx, ["graph", "steps", index, "edgeIds"], "step edge does not exist");
					}
				}
			}
			for (const [index, event] of (layout.graph.timeline ?? []).entries()) {
				for (const nodeId of event.nodeIds) {
					if (!nodeIds.has(nodeId)) {
						addIssue(ctx, ["graph", "timeline", index, "nodeIds"], "timeline node does not exist");
					}
				}
				for (const edgeId of event.edgeIds) {
					if (!edgeIds.has(edgeId)) {
						addIssue(ctx, ["graph", "timeline", index, "edgeIds"], "timeline edge does not exist");
					}
				}
			}
			for (const [index, step] of (layout.graph.animation_plan?.steps ?? []).entries()) {
				for (const nodeId of step.nodeIds) {
					if (!nodeIds.has(nodeId)) {
						addIssue(ctx, ["graph", "animation_plan", "steps", index, "nodeIds"], "animation_plan step node does not exist");
					}
				}
				for (const edgeId of step.edgeIds) {
					if (!edgeIds.has(edgeId)) {
						addIssue(ctx, ["graph", "animation_plan", "steps", index, "edgeIds"], "animation_plan step edge does not exist");
					}
				}
				if (step.action === "camera_pan") {
					if (step.cameraFrom && !nodeIds.has(step.cameraFrom)) {
						addIssue(ctx, ["graph", "animation_plan", "steps", index, "cameraFrom"], "cameraFrom node does not exist");
					}
					if (step.cameraTo && !nodeIds.has(step.cameraTo)) {
						addIssue(ctx, ["graph", "animation_plan", "steps", index, "cameraTo"], "cameraTo node does not exist");
					}
				}
			}
		}
	});

export type VideoLayoutSchemaProps = z.input<typeof videoLayoutSchema>;
