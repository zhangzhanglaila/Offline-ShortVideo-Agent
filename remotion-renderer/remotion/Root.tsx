/**
 * Root - Remotion Composition 注册入口
 *
 * PRODUCTION PIPELINE (Live Path):
 *   Root.tsx → VideoComposition → VideoScene → directorEval → types.ts
 *
 * 所有其他旧 Composition 已删除（TimelineScene / CinematicScene / TestScene 等）
 *
 * @see VideoScene.tsx  — 三层语义渲染引擎（emotion / emphasis / phrase）
 * @see directorEval.ts — 每帧状态机（emotionEffect / cameraOverride）
 */
import React from "react";
import { Composition } from "remotion";
import "./fonts.css";
import { VideoComposition, videoLayoutSchema } from "./Composition";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      {/* V6+ 视频级元素系统（VideoScene）— 唯一 production 路径 */}
      <Composition
        id="VideoFlow"
        component={VideoComposition}
        durationInFrames={300}
        fps={30}
        width={1080}
        height={1920}
        schema={videoLayoutSchema}
        defaultProps={{
          width: 1080,
          height: 1920,
          fps: 30,
          background: "#0A0E14",
          elements: [],
        }}
      />
    </>
  );
};
