# -*- coding: utf-8 -*-
"""AI 生视频 API。

对外暴露两类能力：
  - GET  /api/ai/video/models      供应商 / 模型 / 画幅 / 分辨率
  - POST /api/ai/generate-video    SSE 进度流（提交 → 生成 → 下载 → 完成）

底层复用 `services/ai_video/` 的生成器（DashScope 通义万相 / 本地 ComfyUI），
两者都实现 `generate()` 异步生成器，逐个产出 `VideoGenerationProgress`。
"""
import sys
import os
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

router = APIRouter()

SIZES = ["9:16", "16:9", "1:1"]
RESOLUTIONS = ["720P", "1080P"]


# ---------- 供应商 ----------

def _provider_factories() -> dict:
    """可用的生成器工厂（按环境变量决定）。"""
    factories = {}

    dashscope_key = os.getenv("DASHSCOPE_API_KEY", "")
    if dashscope_key:
        def _dashscope(key=dashscope_key):
            from services.ai_video.dashscope_generator import DashScopeVideoGenerator
            return DashScopeVideoGenerator(api_key=key)
        factories["dashscope"] = _dashscope

    def _comfyui():
        from services.ai_video.comfyui_generator import ComfyUIVideoGenerator
        return ComfyUIVideoGenerator()
    factories["comfyui"] = _comfyui

    return factories


def _static_url(path: str) -> str:
    """把 output/ 下的本地路径转成 /static/output/... URL。"""
    if not path:
        return ""
    normalized = str(path).replace("\\", "/")
    output_dir = str(config.OUTPUT_DIR).replace("\\", "/")
    if normalized.startswith(output_dir):
        return "/static/output/" + normalized[len(output_dir):].lstrip("/")
    idx = normalized.find("output/")
    if idx >= 0:
        return "/static/output/" + normalized[idx + len("output/"):]
    return normalized


# ---------- 数据模型 ----------

class GenerateVideoRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="提示词")
    provider: str = Field("dashscope", description="供应商（dashscope / comfyui）")
    model: Optional[str] = Field(None, description="模型名，留空用默认")
    size: str = Field("9:16", description="画幅 9:16 / 16:9 / 1:1")
    resolution: str = Field("1080P", description="分辨率 720P / 1080P")
    duration: int = Field(5, ge=2, le=15, description="时长（秒）")
    image_path: Optional[str] = Field(None, description="图生视频的输入图片路径")


# ---------- 路由 ----------

@router.get("/api/ai/video/models")
async def video_models():
    """列出可用供应商、模型与可选参数。"""
    providers, models, errors = [], {}, {}
    for name, factory in _provider_factories().items():
        try:
            generator = factory()
            providers.append(name)
            models[name] = generator.get_supported_models()
        except Exception as exc:  # 供应商初始化失败不影响其它供应商
            errors[name] = str(exc)

    return JSONResponse({
        "providers": providers,
        "models": models,
        "sizes": SIZES,
        "resolutions": RESOLUTIONS,
        "defaults": {
            "provider": providers[0] if providers else "",
            "model": (models.get(providers[0]) or [""])[0] if providers else "",
            "size": "9:16",
            "resolution": "1080P",
            "duration": 5,
        },
        "errors": errors,
    })


@router.post("/api/ai/video/estimate-cost")
async def estimate_video_cost(req: GenerateVideoRequest):
    """估算生视频成本（元）。"""
    factories = _provider_factories()
    factory = factories.get(req.provider)
    if factory is None:
        raise HTTPException(status_code=400, detail=f"不支持的供应商: {req.provider}")

    from services.ai_video.base import (
        VideoGenerationRequest, VideoProvider, VideoResolution, VideoSize,
    )

    try:
        request = VideoGenerationRequest(
            prompt=req.prompt,
            provider=VideoProvider(req.provider),
            model=req.model,
            size=VideoSize(req.size),
            resolution=VideoResolution(req.resolution),
            duration=req.duration,
            image_path=req.image_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        generator = factory()
        cost = float(generator.estimate_cost(request))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"成本估算失败: {exc}")

    return JSONResponse({"estimated_cost": round(cost, 2), "currency": "CNY"})


@router.post("/api/ai/generate-video")
async def generate_video(req: GenerateVideoRequest):
    """生成视频，以 SSE 推送进度；成功事件里带本地视频 URL。"""
    factories = _provider_factories()
    factory = factories.get(req.provider)
    if factory is None:
        raise HTTPException(status_code=400, detail=f"不支持的供应商: {req.provider}")

    from services.ai_video.base import (
        VideoGenerationRequest, VideoProvider, VideoResolution, VideoSize,
    )

    try:
        video_request = VideoGenerationRequest(
            prompt=req.prompt,
            provider=VideoProvider(req.provider),
            model=req.model or None,
            size=VideoSize(req.size),
            resolution=VideoResolution(req.resolution),
            duration=req.duration,
            image_path=req.image_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    async def event_stream():
        generator = None
        try:
            yield _sse({"type": "progress", "status": "submitting", "progress": 0.0,
                        "msg": "正在提交任务..."})
            generator = factory()
            async for progress in generator.generate(video_request):
                status = getattr(progress.status, "value", str(progress.status))
                payload = {
                    "type": "progress",
                    "status": status,
                    "progress": round(float(progress.progress or 0.0), 3),
                    "task_id": progress.task_id,
                }
                if progress.video_path:
                    payload["video_path"] = progress.video_path
                    payload["video_url"] = _static_url(progress.video_path)
                if progress.video_url:
                    payload["remote_url"] = progress.video_url
                if progress.error:
                    payload["error"] = progress.error

                if status in ("succeeded", "failed", "cancelled"):
                    payload["type"] = "done" if status == "succeeded" else "error"
                    if status != "succeeded" and "error" not in payload:
                        payload["error"] = "生成失败"
                yield _sse(payload)
                if payload["type"] in ("done", "error"):
                    return
        except Exception as exc:
            yield _sse({"type": "error", "error": str(exc)})
        finally:
            if generator is not None and hasattr(generator, "close"):
                try:
                    await generator.close()
                except Exception:
                    pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
