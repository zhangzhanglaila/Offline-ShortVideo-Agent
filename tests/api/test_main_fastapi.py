# -*- coding: utf-8 -*-
"""main_fastapi 应用装配测试：路由挂载 + 关键端点存在。"""
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _route_paths(app):
    return {getattr(r, "path", None) for r in app.routes}


def test_app_builds_and_mounts_key_routes():
    """create_app 挂载 topic 生成入口与视频管线端点。"""
    tmp = tempfile.mkdtemp()
    db_path = str(Path(tmp) / "test.db")
    # 预建空库，避免 create_app 触发的静态/DB 逻辑依赖真实数据
    sqlite3.connect(db_path).close()

    with patch("core.db_init.get_db_path", return_value=db_path):
        import main_fastapi
        app = main_fastapi.create_app()

    paths = _route_paths(app)
    # 统一入口
    assert "/api/topic/generate" in paths
    assert "/api/topic/generate/meta" in paths
    # 复用的视频管线
    assert "/api/ecom/videos" in paths
    assert "/api/ecom/videos/{video_id}/render" in paths
    # 健康检查
    assert "/api/health" in paths


def test_topic_video_router_included():
    """topic_video_api 已在 api.__all__ 中登记并被挂载。"""
    import api
    assert "topic_video_api" in api.__all__

    tmp = tempfile.mkdtemp()
    db_path = str(Path(tmp) / "test.db")
    sqlite3.connect(db_path).close()
    with patch("core.db_init.get_db_path", return_value=db_path):
        import main_fastapi
        app = main_fastapi.create_app()
    assert "topic_video_api" in app.state.included_routers
