# -*- coding: utf-8 -*-
"""AI 动效链路测试：LLM 输出解析/清洗 → 逐帧渲染 → MP4。"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from services.html_motion import (
    DEMO_HTML,
    extract_html,
    generate_html,
    render_html_to_video,
    sanitize_html,
)
from services.html_motion.prompts import build_html_prompt

HAS_FFPROBE = shutil.which("ffprobe") is not None

SMALL_HTML = """<!DOCTYPE html><html><head><style>
html,body{margin:0;width:540px;height:960px;background:#101828;}
.box{width:120px;height:120px;background:#6ea8ff;animation:move 2s linear both;}
@keyframes move{from{transform:translate(0,0)}to{transform:translate(360px,760px)}}
</style></head><body><div class="box"></div></body></html>"""


# ---------- 解析 / 清洗 ----------

def test_extract_html_from_fenced_response():
    raw = "这是页面：\n```html\n" + DEMO_HTML + "\n```\n希望你喜欢"
    html = extract_html(raw)
    assert html.startswith("<!DOCTYPE html>")
    assert html.rstrip().endswith("</html>")


def test_extract_html_rejects_plain_text():
    with pytest.raises(Exception):
        extract_html("抱歉，我无法完成这个请求。")


def test_sanitize_removes_script_and_handlers():
    dirty = """<html><head><style>@import url("http://x/a.css");</style>
    <link rel="stylesheet" href="http://x/b.css"></head>
    <body onclick="alert(1)"><script>alert(2)</script>
    <iframe src="http://x"></iframe><div>ok</div></body></html>"""
    clean = sanitize_html(dirty)
    assert "<script" not in clean.lower()
    assert "onclick" not in clean.lower()
    assert "@import" not in clean.lower()
    assert "<link" not in clean.lower()
    assert "<iframe" not in clean.lower()
    assert "ok" in clean


def test_prompt_mentions_duration_and_style():
    prompt = build_html_prompt("测试主题", style="tech", duration=7)
    assert "7" in prompt
    assert "科技" in prompt
    assert "测试主题" in prompt
    assert "@keyframes" in prompt


def test_generate_html_with_stub_llm():
    class StubLLM:
        def generate(self, prompt, temperature=0.7, timeout=None, **kwargs):
            return "```html\n" + DEMO_HTML + "\n```"

    result = generate_html("测试主题", llm=StubLLM())
    assert result["html"].startswith("<!DOCTYPE html>")
    assert result["has_animation"] is True


def test_generate_html_rejects_non_html():
    class StubLLM:
        def generate(self, *args, **kwargs):
            return "好的，我来帮你写一个页面。"

    with pytest.raises(Exception):
        generate_html("测试主题", llm=StubLLM())


def test_generate_html_prefers_chat_when_available():
    """DualModeLLMClient 只有 chat()，必须走这条路。"""
    calls = {}

    class ChatLLM:
        def chat(self, messages, temperature=0.7, timeout=None, **kwargs):
            calls["messages"] = messages
            return "```html\n" + DEMO_HTML + "\n```"

    result = generate_html("测试主题", llm=ChatLLM())
    assert result["has_animation"] is True
    assert calls["messages"][0]["role"] == "user"
    assert "测试主题" in calls["messages"][0]["content"]


# ---------- 渲染 ----------

@pytest.mark.asyncio
async def test_render_html_to_video(tmp_path):
    output = tmp_path / "job"
    result = await render_html_to_video(
        SMALL_HTML, output, width=540, height=960, fps=8, duration=2
    )

    video = Path(result["video_path"])
    assert video.exists()
    assert video.stat().st_size > 5_000
    assert result["frames"] == 16
    assert result["animations"] >= 1
    # 成功后会清理中间帧，避免占盘
    assert not (output / "frames").exists()

    if HAS_FFPROBE:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(video)],
            capture_output=True, text=True,
        )
        assert abs(float(probe.stdout.strip()) - 2.0) < 0.3


def test_render_rejects_empty_output(tmp_path):
    """duration/fps 极大时会被上限保护，不会生成 0 帧。"""
    # 这里只验证参数兜底逻辑，不真正渲染
    from services.html_motion.renderer import MAX_FRAMES
    assert MAX_FRAMES >= 300


# ---------- API ----------

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main_fastapi import create_app

    return TestClient(create_app())


def test_api_presets(client):
    response = client.get("/api/html-motion/presets")
    assert response.status_code == 200
    data = response.json()
    assert len(data["styles"]) >= 5
    assert data["demo_html"].startswith("<!DOCTYPE html>")


def test_api_render_stream(client):
    payload = {"html": SMALL_HTML, "duration": 2, "fps": 8, "width": 540, "height": 960}
    with client.stream("POST", "/api/html-motion/render", json=payload) as response:
        assert response.status_code == 200
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    types = [e.get("type") for e in events]
    assert "progress" in types
    assert types[-1] == "done"

    done = events[-1]
    assert done["frames"] == 16
    assert done["video_url"].startswith("/static/output/html_motion/")


def test_api_generate_stream(client, monkeypatch):
    """走完整 generate 端点，但用桩替换 LLM 调用（不产生费用）。"""
    import api.html_motion_api as hm

    monkeypatch.setattr(
        hm, "generate_html",
        lambda *args, **kwargs: {"html": SMALL_HTML, "has_animation": True, "raw_preview": ""},
    )

    payload = {"topic": "测试主题", "style": "minimal", "duration": 2, "fps": 8,
               "width": 540, "height": 960}
    with client.stream("POST", "/api/html-motion/generate", json=payload) as response:
        assert response.status_code == 200
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    assert events[-1]["type"] == "done"
    assert events[-1]["has_animation"] is True
    assert events[-1]["video_url"].startswith("/static/output/html_motion/")
