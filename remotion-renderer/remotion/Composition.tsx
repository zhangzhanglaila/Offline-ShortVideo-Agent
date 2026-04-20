/**
 * Composition - Remotion 组件入口
 *
 * PRODUCTION PIPELINE:
 *   Root → VideoComposition → VideoScene → directorEval → types.ts
 *
 * 仅保留 VideoLayout 相关代码，TimelineLayout 已废弃。
 */
import React from "react";
import { z } from "zod";
import { VideoScene } from "./VideoScene";
import type { VideoLayout } from "./types";

export type { VideoLayout };

// 通用元素 Schema
const baseElementSchema = z.object({
  id: z.string(),
  start: z.number(),
  duration: z.number().default(150),
  zIndex: z.number().default(0),
  animation: z.object({
    enter: z.enum(["fade", "slide-up", "slide-down", "zoom-in", "zoom-out", "bounce-in", "blur-in"]).optional(),
    exit: z.enum(["fade", "slide-up", "slide-down", "zoom-out", "blur-out"]).optional(),
    duration: z.number().optional(),
  }).optional(),
});

const textElementSchema = baseElementSchema.extend({
  type: z.literal("text"),
  text: z.string(),
  x: z.number(),
  y: z.number(),
  fontSize: z.number(),
  color: z.string(),
  fontWeight: z.number().optional(),
  textAlign: z.enum(["left", "center", "right"]).optional(),
});

const imageElementSchema = baseElementSchema.extend({
  type: z.literal("image"),
  src: z.string(),
  x: z.number(),
  y: z.number(),
  width: z.number(),
  height: z.number(),
  borderRadius: z.number().optional(),
  objectFit: z.enum(["cover", "contain", "fill"]).optional(),
});

const stickerElementSchema = baseElementSchema.extend({
  type: z.literal("sticker"),
  emoji: z.string(),
  x: z.number(),
  y: z.number(),
  size: z.number(),
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
  width: z.number(),
  height: z.number(),
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

// VideoLayout Schema（新版）
export const videoLayoutSchema = z.object({
  width: z.number(),
  height: z.number(),
  fps: z.number().default(30),
  background: z.string().optional(),
  elements: z.array(elementSchema),
});

export type VideoProps = z.infer<typeof videoLayoutSchema>;

// VideoScene 入口（唯一 production 路径）
export const VideoComposition: React.FC<VideoProps> = (props) => {
  return <VideoScene layout={props} />;
};
