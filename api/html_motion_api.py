# -*- coding: utf-8 -*-
"""AI 动效 API：文本 LLM → HTML/CSS/SVG → 逐帧渲染 → MP4。

零图像 API 链路：一次文本补全 + 本地浏览器截图 + 本地 ffmpeg。
"""
import sys
import os
import json
import time
import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from services.html_motion import (
    DEMO_HTML,
    STYLES,
    generate_html,
    render_html_to_video,
    sanitize_html,
)

router = APIRouter()

OUTPUT_ROOT = Path(config.OUTPUT_DIR) / "html_motion"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


class GenerateRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=200)
    style: str = Field("minimal")
    duration: float = Field(5, ge=2, le=10)
    fps: int = Field(24, ge=8, le=30)
    width: int = Field(1080, ge=480, le=2160)
    height: int = Field(1920, ge=480, le=2160)


class RenderRequest(BaseModel):
    html: str = Field(..., min_length=20, max_length=200_000)
    duration: float = Field(5, ge=2, le=10)
    fps: int = Field(24, ge=8, le=30)
    width: int = Field(1080, ge=480, le=2160)
    height: int = Field(1920, ge=480, le=2160)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _static_url(path: str | Path) -> str:
    normalized = str(path).replace("\\", "/")
    output_dir = str(config.OUTPUT_DIR).replace("\\", "/")
    if normalized.startswith(output_dir):
        return "/static/output/" + normalized[len(output_dir):].lstrip("/")
    index = normalized.find("output/")
    if index >= 0:
        return "/static/output/" + normalized[index + len("output/"):]
    return normalized


async def _stream_job(work) -> StreamingResponse:
    """把异步任务包装成 SSE：progress / done / error。"""
    queue: asyncio.Queue = asyncio.Queue()

    def progress(msg: str, pct: float = 0.0):
        queue.put_nowait({
            "type": "progress",
            "msg": msg,
            "progress": round(max(0.0, min(1.0, float(pct))), 3),
        })

    async def runner():
        try:
            data = await work(progress)
            queue.put_nowait({"type": "done", **data})
        except Exception as exc:
            queue.put_nowait({"type": "error", "error": str(exc)})
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(runner())

    async def events():
        yield _sse({"type": "progress", "msg": "任务已开始", "progress": 0.0})
        while True:
            item = await queue.get()
            if item is None:
                break
            yield _sse(item)
        await task

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _job_dir() -> Path:
    job_id = f"job_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    path = OUTPUT_ROOT / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.get("/api/html-motion/presets")
async def html_motion_presets():
    """风格预设 + 内置示例（示例不调用任何 API）。"""
    return JSONResponse({
        "styles": [
            {"id": key, "name": value["name"], "hint": value["hint"]}
            for key, value in STYLES.items()
        ],
        "demo_html": DEMO_HTML,
        "defaults": {"style": "minimal", "duration": 5, "fps": 24},
    })


@router.post("/api/html-motion/generate")
async def html_motion_generate(req: GenerateRequest):
    """用文本 LLM 生成动效页面并渲染成视频（SSE）。"""

    async def work(progress):
        progress("正在调用大模型生成 HTML...", 0.05)
        generated = await asyncio.to_thread(
            generate_html,
            req.topic,
            req.style,
            req.duration,
            req.width,
            req.height,
        )
        progress("HTML 已生成，开始逐帧渲染...", 0.15)

        job_dir = _job_dir()
        render = await render_html_to_video(
            generated["html"],
            job_dir,
            width=req.width,
            height=req.height,
            fps=req.fps,
            duration=req.duration,
            on_progress=progress,
        )
        return {
            "job_id": job_dir.name,
            "video_url": _static_url(render["video_path"]),
            "video_path": render["video_path"],
            "html": generated["html"],
            "frames": render["frames"],
            "animations": render["animations"],
            "has_animation": generated["has_animation"],
            "elapsed": render["elapsed"],
        }

    return await _stream_job(work)


@router.post("/api/html-motion/render")
async def html_motion_render(req: RenderRequest):
    """把已有 HTML 渲染成视频（用户可改完 HTML 再渲，不消耗 LLM）。"""

    async def work(progress):
        html = sanitize_html(req.html)
        progress("HTML 已就绪，开始逐帧渲染...", 0.1)

        job_dir = _job_dir()
        render = await render_html_to_video(
            html,
            job_dir,
            width=req.width,
            height=req.height,
            fps=req.fps,
            duration=req.duration,
            on_progress=progress,
        )
        return {
            "job_id": job_dir.name,
            "video_url": _static_url(render["video_path"]),
            "video_path": render["video_path"],
            "html": html,
            "frames": render["frames"],
            "animations": render["animations"],
            "elapsed": render["elapsed"],
        }

    return await _stream_job(work)
