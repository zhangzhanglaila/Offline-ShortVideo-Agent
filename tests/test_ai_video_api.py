"""AI 生视频 API 测试：供应商发现 + 成本估算 + SSE 进度流（打桩生成器）。"""
import json

import pytest

import api.ai_video_api as av
from services.ai_video.base import VideoGenerationProgress, VideoTaskStatus


class _FakeGenerator:
    """不联网的生成器替身。"""

    def __init__(self, **_kwargs):
        pass

    def get_supported_models(self):
        return ["fake-t2v"]

    def estimate_cost(self, request):
        return 1.25

    async def generate(self, request):
        yield VideoGenerationProgress(task_id="t1", status=VideoTaskStatus.PENDING, progress=0.0)
        yield VideoGenerationProgress(
            task_id="t1",
            status=VideoTaskStatus.SUCCEEDED,
            progress=1.0,
            video_path="output/ai_cache/fake.mp4",
        )


@pytest.fixture()
def client(monkeypatch):
    # 用合法供应商名 dashscope 替换工厂，避免真实网络与密钥依赖
    monkeypatch.setattr(av, "_provider_factories", lambda: {"dashscope": _FakeGenerator})
    from fastapi.testclient import TestClient
    from main_fastapi import create_app

    return TestClient(create_app())


def test_video_models_lists_stubbed_provider(client):
    response = client.get("/api/ai/video/models")
    assert response.status_code == 200
    data = response.json()
    assert data["providers"] == ["dashscope"]
    assert data["models"]["dashscope"] == ["fake-t2v"]
    assert "9:16" in data["sizes"]
    assert "1080P" in data["resolutions"]


def test_estimate_cost(client):
    response = client.post("/api/ai/video/estimate-cost", json={
        "prompt": "霓虹街道", "provider": "dashscope", "duration": 5,
    })
    assert response.status_code == 200
    assert response.json()["estimated_cost"] == 1.25
    assert response.json()["currency"] == "CNY"


def test_generate_stream_emits_progress_and_done(client):
    payload = {"prompt": "霓虹街道", "provider": "dashscope", "size": "9:16", "duration": 5}
    with client.stream("POST", "/api/ai/generate-video", json=payload) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    types = [e.get("type") for e in events]
    assert "progress" in types
    assert types[-1] == "done"

    done = events[-1]
    assert done["video_url"] == "/static/output/ai_cache/fake.mp4"
    assert done["video_path"] == "output/ai_cache/fake.mp4"


def test_unknown_provider_rejected(client):
    response = client.post("/api/ai/generate-video", json={"prompt": "x", "provider": "nope"})
    assert response.status_code == 400
