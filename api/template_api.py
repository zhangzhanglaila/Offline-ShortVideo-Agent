# -*- coding: utf-8 -*-
"""
模板API路由 - 模板列表、筛选、预览
"""
import sys
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.template.registry import TemplateRegistry
from services.template.renderer import TemplateRenderer

import config

router = APIRouter()

# 全局模板注册表实例（懒加载）
_registry: Optional[TemplateRegistry] = None
_renderer: Optional[TemplateRenderer] = None


def get_registry() -> TemplateRegistry:
    """获取模板注册表实例（单例）"""
    global _registry
    if _registry is None:
        _registry = TemplateRegistry(Path("templates"))
        _registry.scan()
    return _registry


def get_renderer() -> TemplateRenderer:
    """获取模板渲染器实例（单例）"""
    global _renderer
    if _renderer is None:
        _renderer = TemplateRenderer(Path("templates"))
    return _renderer


# ---------- 数据模型 ----------


class TemplateInfoResponse(BaseModel):
    """模板信息响应"""
    name: str
    aspect_ratio: str
    width: int
    height: int
    template_type: str  # image / static / video / asset
    style: str
    parameters: List[str]
    path: str


class TemplateListResponse(BaseModel):
    """模板列表响应"""
    templates: List[TemplateInfoResponse]
    total: int
    filters: Dict[str, Any]


class TemplatePreviewRequest(BaseModel):
    """模板预览请求"""
    template_name: str
    context: Dict[str, Any] = {}


# ---------- API 路由 ----------


@router.get("/api/templates", response_model=TemplateListResponse)
async def list_templates(
    aspect_ratio: Optional[str] = None,
    template_type: Optional[str] = None,
    style: Optional[str] = None,
    search: Optional[str] = None,
):
    """
    获取模板列表

    参数:
        aspect_ratio: 画幅筛选 (如 "1080x1920", "1920x1080")
        template_type: 类型筛选 (image / static / video / asset)
        style: 风格筛选
        search: 关键词搜索（匹配模板名）
    """
    registry = get_registry()
    templates = list(registry._cache.values())

    # 应用筛选
    if aspect_ratio:
        templates = [t for t in templates if t.aspect_ratio == aspect_ratio]

    if template_type:
        templates = [t for t in templates if t.template_type == template_type]

    if style:
        templates = [t for t in templates if t.style == style]

    if search:
        search_lower = search.lower()
        templates = [t for t in templates if search_lower in t.name.lower()]

    # 转换为响应格式
    response_data = [
        TemplateInfoResponse(
            name=t.name,
            aspect_ratio=t.aspect_ratio,
            width=t.width,
            height=t.height,
            template_type=t.template_type,
            style=t.style,
            parameters=t.parameters,
            path=str(t.path),
        )
        for t in templates
    ]

    # 获取可用的筛选值
    all_templates = list(registry._cache.values())
    filters = {
        "aspect_ratios": sorted(set(t.aspect_ratio for t in all_templates)),
        "template_types": sorted(set(t.template_type for t in all_templates)),
        "styles": sorted(set(t.style for t in all_templates)),
    }

    return TemplateListResponse(
        templates=response_data,
        total=len(response_data),
        filters=filters,
    )


@router.get("/api/templates/{name}", response_model=TemplateInfoResponse)
async def get_template_info(name: str):
    """
    获取单个模板详细信息
    """
    try:
        registry = get_registry()
        template = registry.get(name)
        return TemplateInfoResponse(
            name=template.name,
            aspect_ratio=template.aspect_ratio,
            width=template.width,
            height=template.height,
            template_type=template.template_type,
            style=template.style,
            parameters=template.parameters,
            path=str(template.path),
        )
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Template not found: {name}")


@router.post("/api/templates/preview")
async def preview_template(request: TemplatePreviewRequest):
    """
    生成模板预览图（使用默认参数）
    """
    try:
        renderer = get_renderer()

        # 默认预览上下文
        default_context = {
            "title": "模板预览",
            "text": "这是预览文本内容",
            "author": "AI Assistant",
            "describe": "模板预览",
            "brand": "Offline-ShortVideo-Agent",
            "image": "",
        }
        context = {**default_context, **request.context}

        # 生成预览图（写入 output/previews，由 /static/output 挂载对外提供）
        preview_dir = Path(config.OUTPUT_DIR) / "previews"
        preview_dir.mkdir(parents=True, exist_ok=True)
        output_path = preview_dir / f"{request.template_name}_preview.png"

        await renderer.render(
            request.template_name,
            context,
            output_path,
        )

        return {
            "success": True,
            "preview_url": f"/static/output/previews/{request.template_name}_preview.png",
            "output_path": str(output_path),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Preview generation failed: {str(e)}")


@router.get("/api/templates/filters")
async def get_template_filters():
    """
    获取所有可用的筛选值（用于前端筛选器）
    """
    registry = get_registry()
    templates = list(registry._cache.values())

    return {
        "aspect_ratios": sorted(set(t.aspect_ratio for t in templates)),
        "template_types": sorted(set(t.template_type for t in templates)),
        "styles": sorted(set(t.style for t in templates)),
        "total_templates": len(templates),
    }
