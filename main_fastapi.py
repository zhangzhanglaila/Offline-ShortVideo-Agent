# -*- coding: utf-8 -*-
"""
主 FastAPI 应用 - 挂载所有 api/ 路由，服务前端 (vite 代理 /api、/static → :5001)。

统一化重构后：主题驱动生成入口 /api/generate 由 api/topic_video_api.py 提供，
视频管线端点 /api/ecom/videos/* 由 api/ecom_api.py 提供（命名遗留，已与电商无关）。

运行:
    python main_fastapi.py            # 默认 0.0.0.0:5001
    uvicorn main_fastapi:app --port 5001 --reload
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import config
import api


def _init_database():
    """确保数据库表结构就绪（含 ecom_videos 的 topic/category 迁移列）。"""
    try:
        from core.db_init import init_topics_db
        conn = init_topics_db()
        try:
            conn.close()
        except Exception:
            pass
    except Exception as e:  # pragma: no cover - 启动期容错
        print(f"[main_fastapi] 数据库初始化警告: {e}")


def create_app() -> FastAPI:
    """构建并返回 FastAPI 应用（挂载全部 api 路由 + 静态文件）。"""
    app = FastAPI(title="AI 短视频生成系统", version="2.1")

    # 跨域：默认只放行本机前端。同源部署（main_fastapi 直接托管 web/index.html）
    # 不需要 CORS；只有把前端跑在别的端口/域名时才需要。
    #   CORS_ALLOW_ORIGINS="http://127.0.0.1:5173"  追加来源（逗号分隔）
    #   CORS_ALLOW_ORIGINS="*"                      全放开（仅限本机调试）
    _origins = os.environ.get(
        "CORS_ALLOW_ORIGINS",
        "http://127.0.0.1:5001,http://localhost:5001",
    ).strip()
    allow_origins = (
        ["*"] if _origins == "*"
        else [o.strip() for o in _origins.split(",") if o.strip()]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 挂载 api 包下所有带 router 的模块
    included = []
    for mod_name in getattr(api, "__all__", []):
        module = getattr(api, mod_name, None)
        router = getattr(module, "router", None)
        if router is not None:
            app.include_router(router)
            included.append(mod_name)
    app.state.included_routers = included

    # 静态文件：/static/output/... → output/...（生成的视频）
    output_dir = Path(config.OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static/output", StaticFiles(directory=str(output_dir)), name="static-output")

    # 静态文件：/static/materials/... → assets/素材池_待剪辑/（用户上传的素材）
    material_dir = Path(config.MATERIAL_DIR)
    material_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static/materials", StaticFiles(directory=str(material_dir)), name="static-materials")

    # 静态文件：/static/thumbnails/... → assets/thumbnails/（视频缩略图）
    thumbnails_dir = Path(config.THUMBNAILS_DIR)
    thumbnails_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static/thumbnails", StaticFiles(directory=str(thumbnails_dir)), name="static-thumbnails")

    # 静态文件：/static/bgm/... → assets/bgm/（背景音乐）
    bgm_dir = Path(config.BGM_DIR)
    bgm_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static/bgm", StaticFiles(directory=str(bgm_dir)), name="static-bgm")

    @app.get("/api/health")
    async def health():
        return {"status": "ok", "routers": included}

    # 前端：api/web/index.html 作为 SPA fallback，所有非 /api/* /static/* 请求返回它
    web_dir = Path(__file__).parent / "api" / "web"
    if web_dir.is_dir():
        @app.get("/", include_in_schema=False)
        async def web_root():
            return FileResponse(str(web_dir / "index.html"))

        @app.get("/{path:path}", include_in_schema=False)
        async def web_fallback(path: str):
            # /api/* /static/* 已被精确路由匹配；未匹配的必须显式 404，
            # 否则会返回 200 + {"detail":"Not Found"}，前端无法识别失败。
            if path.startswith("api/") or path.startswith("static/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            return FileResponse(str(web_dir / "index.html"))

    return app


_init_database()
app = create_app()


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "5001"))
    uvicorn.run("main_fastapi:app", host="0.0.0.0", port=port, log_level="info")
