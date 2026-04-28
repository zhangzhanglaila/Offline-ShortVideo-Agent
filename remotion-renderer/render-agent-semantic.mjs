import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {renderMedia, selectComposition} from "@remotion/renderer";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, "..");
const layoutPath = process.argv[2]
	? path.resolve(process.argv[2])
	: path.join(rootDir, "output", "agent_what_is_layout.json");
const outputPath = process.argv[3]
	? path.resolve(process.argv[3])
	: path.join(rootDir, "output", "agent_what_is_semantic.mp4");
const serveUrl = path.join(__dirname, "build");

const layout = JSON.parse(fs.readFileSync(layoutPath, "utf8"));
const getLayoutDurationInFrames = (videoLayout) => {
	const elementEnd = (videoLayout.elements ?? []).reduce((max, element) => {
		return Math.max(max, (element.start ?? 0) + (element.duration ?? 0));
	}, 0);
	const shotEnd = (videoLayout.shots ?? []).reduce((max, shot) => {
		return Math.max(max, (shot.start ?? 0) + (shot.duration ?? 0));
	}, 0);
	return Math.max(videoLayout.durationInFrames ?? 0, elementEnd, shotEnd, 1);
};

console.log(`[render-agent] serveUrl=${serveUrl}`);
console.log(`[render-agent] layout=${layoutPath}`);
console.log(`[render-agent] output=${outputPath}`);

const composition = await selectComposition({
	serveUrl,
	id: "VideoFlow",
	inputProps: {video: layout},
	chromiumOptions: {gl: "swiftshader"},
});

if (!composition) {
	throw new Error('Composition "VideoFlow" not found');
}

const durationInFrames = getLayoutDurationInFrames(layout);
console.log(`[render-agent] duration=${durationInFrames} frames`);

await renderMedia({
	serveUrl,
	composition: {
		...composition,
		durationInFrames,
		fps: layout.fps ?? 30,
		props: {video: layout},
	},
	inputProps: {video: layout},
	codec: "h264",
	outputLocation: outputPath,
	chromiumOptions: {gl: "swiftshader"},
	onProgress: ({progress}) => {
		console.log(`[render-agent] progress=${(progress * 100).toFixed(1)}%`);
	},
});

console.log(`[render-agent] done=${outputPath}`);
