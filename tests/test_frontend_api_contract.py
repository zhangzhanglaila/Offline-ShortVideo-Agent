"""前端 ↔ API 契约测试。

`api/web/index.html` 是当前唯一前端，它用 `API + '/path'` 拼接调用后端。
历史上曾出现「前端调用的端点没有挂载」的问题（topic_pipeline_api 未登记到
api.__all__，导致首页一键生成与步进式流水线全部落空）。本测试做回归守护：
扫描前端源码里出现的所有 /api 路径，逐个断言已在 main_fastapi 中注册。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "api" / "web" / "index.html"

_QUOTED = re.compile(r"'([^']*)'")
_PARAM = re.compile(r"\{[^}]+\}")

# 主流程关键端点：任何一条缺失都会让 UI 走不通
CRITICAL_ENDPOINTS = [
    "/api/topic/generate",
    "/api/topic/generate/stream",
    "/api/topic/videos/{video_id}/script",
    "/api/topic/videos/{video_id}/tts",
    "/api/topic/videos/{video_id}/render",
    "/api/topic/videos/{video_id}/retry-render",
    "/api/topic/videos/{video_id}/status",
    "/api/ecom/videos/{video_id}/script",
    "/api/ecom/videos/{video_id}/tts",
    "/api/ecom/videos/{video_id}/render",
]


def _normalize(path: str) -> str:
    """把路由参数统一成 {}，便于和前端拼接出的路径比较。"""
    return _PARAM.sub("{}", path.rstrip("/"))


def _extract_frontend_paths(html: str) -> set[str]:
    """从 `API + '...'` 拼接表达式中提取路径模板。

    支持三种写法：
        API + '/works'
        API + '/topic/videos/' + id + '/status'
        API + '/topics?limit=20&offset=' + offset
    """
    paths: set[str] = set()

    for raw_line in html.splitlines():
        if "API +" not in raw_line:
            continue
        rest = raw_line.split("API +", 1)[1]

        # 只取以 / 开头的字面量作为路径片段，过滤掉 'POST'、'DELETE' 之类
        segments = [
            (m.start(), m.end(), m.group(1))
            for m in _QUOTED.finditer(rest)
            if m.group(1).startswith("/")
        ]
        if not segments:
            continue

        path = segments[0][2]
        has_query = "?" in path
        if has_query:
            path = path.split("?", 1)[0]

        last_end = segments[0][1]
        for start, end, value in segments[1:]:
            gap = rest[last_end:start]
            if "+" in gap and not has_query:
                path += "{}"
            if "?" in value:
                value = value.split("?", 1)[0]
                has_query = True
            path += value
            last_end = end

        # 末段后面还有拼接（形如 API + '/agent/session/' + sessionId）
        # 前端常量 var API = '/api'，所以拼接出的片段要补上前缀
        path = "/api" + path

        if not has_query and "+" in rest[last_end:]:
            path += "{}"

        if path.startswith("/api/"):
            paths.add(_normalize(path))

    return paths


def _app_route_paths() -> set[str]:
    from main_fastapi import create_app

    return {_normalize(getattr(route, "path", "")) for route in create_app().routes}


def test_frontend_has_api_calls():
    """确保解析逻辑没被前端写法变化悄悄绕过。"""
    paths = _extract_frontend_paths(INDEX.read_text(encoding="utf-8"))
    assert len(paths) >= 15, f"只解析到 {len(paths)} 个接口，解析规则可能已失效：{sorted(paths)}"


def test_every_frontend_call_is_registered():
    """前端调用的每个端点都必须已挂载，否则会在运行时 404。"""
    registered = _app_route_paths()
    called = _extract_frontend_paths(INDEX.read_text(encoding="utf-8"))
    missing = sorted(p for p in called if p not in registered)
    assert not missing, "前端调用了未注册的端点：" + ", ".join(missing)


def test_critical_pipeline_endpoints_mounted():
    """主流程关键端点显式守护。"""
    registered = _app_route_paths()
    missing = [p for p in CRITICAL_ENDPOINTS if _normalize(p) not in registered]
    assert not missing, "缺少关键端点：" + ", ".join(missing)


def test_unmatched_api_returns_404():
    """未注册的 /api/* 必须返回 404，而不是 200 + {"detail": "Not Found"}。"""
    from fastapi.testclient import TestClient

    from main_fastapi import create_app

    client = TestClient(create_app())
    response = client.get("/api/definitely-not-a-real-endpoint")
    assert response.status_code == 404
