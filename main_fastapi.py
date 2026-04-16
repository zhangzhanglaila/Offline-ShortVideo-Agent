# -*- coding: utf-8 -*-
"""
Offline-ShortVideo-Agent FastAPI 主入口
企业级异步Web服务，支持自动API文档

启动方式: python main_fastapi.py
可选参数: --port <端口号> (默认 5001)
"""
import os
import sys
import argparse
import webbrowser
import threading
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent))

import config
config.ensure_dirs()

# 初始化数据库
from core.db_init import init_topics_db, insert_sample_topics


def init_database():
    """初始化数据库"""
    conn = init_topics_db()
    insert_sample_topics(conn)
    conn.close()
    print("[初始化] 选题数据库完成")


def check_port_in_use(port):
    """检查端口是否已被占用"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('127.0.0.1', port))
            return False
        except OSError:
            return True


def open_browser(port):
    """延迟打开浏览器"""
    def _open():
        import time
        time.sleep(1.5)
        webbrowser.open(f'http://127.0.0.1:{port}')
    threading.Thread(target=_open, daemon=True).start()


def create_app() -> "FastAPI":
    """创建并配置 FastAPI 应用"""
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse

    app = FastAPI(
        title="Offline-ShortVideo-Agent API",
        description="离线短视频Agent - 企业级API服务",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS 配置
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 挂载静态文件目录
    material_dir = str(config.MATERIAL_DIR)
    thumbnails_dir = str(config.THUMBNAILS_DIR)

    if Path(material_dir).exists():
        app.mount(
            "/static/materials",
            StaticFiles(directory=material_dir),
            name="materials"
        )

    if Path(thumbnails_dir).exists():
        app.mount(
            "/static/thumbnails",
            StaticFiles(directory=thumbnails_dir),
            name="thumbnails"
        )

    # 注册 API 路由
    from api import agent_api, generate_api, material_api, system_api, topic_api, work_api, tts_api, dual_mode_api

    app.include_router(agent_api.router, tags=["Agent"])
    app.include_router(generate_api.router, tags=["生成"])
    app.include_router(material_api.router, tags=["素材"])
    app.include_router(system_api.router, tags=["系统"])
    app.include_router(topic_api.router, tags=["选题"])
    app.include_router(work_api.router, tags=["作品"])
    app.include_router(tts_api.router, tags=["TTS配音"])
    app.include_router(dual_mode_api.router, tags=["双模式生成"])

    # 前端页面
    @app.get("/")
    async def read_index():
        """返回前端首页"""
        return FileResponse("web/index.html")

    @app.get("/favicon.ico")
    async def favicon():
        """Favicon"""
        ico_path = Path("web/favicon.ico")
        if ico_path.exists():
            return FileResponse(ico_path)
        return {"error": "Not found"}, 404

    return app


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="Offline-ShortVideo-Agent FastAPI 服务")
    parser.add_argument("--port", type=int, default=5001, help="服务端口 (默认: 5001)")
    parser.add_argument("--browser", action="store_true", help="启动后自动打开浏览器")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认: 127.0.0.1)")
    args = parser.parse_args()

    port = args.port

    # 检查端口
    if check_port_in_use(port):
        print(f"错误: 端口 {port} 已被占用")
        print(f"请使用 --port 参数指定其他端口，或关闭占用该端口的程序")
        return

    # 初始化数据库
    init_database()

    # 检查选题库
    from core.topics_module import TopicsModule
    topics = TopicsModule(
        enable_cache=config.CACHE_CONFIG.get("enabled", True),
        preload_count=config.CACHE_CONFIG.get("preload_count", 500)
    )
    stats = topics.get_statistics()
    print(f"[启动] 选题库: {stats['total']} 条")

    # 确保素材目录存在
    Path(config.MATERIAL_DIR).mkdir(parents=True, exist_ok=True)

    # 创建应用
    app = create_app()

    print("=" * 60)
    print("   Offline-ShortVideo-Agent FastAPI 服务")
    print(f"   访问地址: http://{args.host}:{port}")
    print(f"   API文档:  http://{args.host}:{port}/docs")
    print("=" * 60)

    # 自动打开浏览器（默认关闭，如需自动打开请用 --browser 参数）
    if args.browser:
        open_browser(port)

    # 启动服务
    import uvicorn
    uvicorn.run(
        app,
        host=args.host,
        port=port,
        reload=False,
        log_level="info"
    )


if __name__ == "__main__":
    main()
