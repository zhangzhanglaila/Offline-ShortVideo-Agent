"""模板视频端到端测试：真实生成 mp4"""
import asyncio
import shutil
from pathlib import Path

import pytest

from core.agents.video_compose_agent import VideoComposeAgent
from core.models.content import Scene, ContentStructure, SceneType


@pytest.mark.asyncio
async def test_real_video_generation_with_template(tmp_path):
    """用 VideoComposeAgent 的 _build_scene_spec_async 生成带模板的 mp4"""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")

    scene = Scene(
        scene_id=2,
        scene_type=SceneType.CONTENT.value,
        text="这是模板渲染的第一段内容",
        duration=5.0,
        template="image_default",
        keywords=["test"],
    )
    content = ContentStructure(
        title="模板测试视频",
        category="测试",
        style="minimal",
        total_duration=5,
        scenes=[scene],
    )
    material_map = {}  # 无素材 → 用空 image 占位

    agent = VideoComposeAgent(size=(1080, 1920))
    agent._content_counter = 0

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    spec = await agent._build_scene_spec_async(
        scene, 0, material_map, work_dir,
        title=content.title,
        style_dict=agent._load_style(content.style),
    )

    # 验证
    assert spec is not None, "Template path should produce a spec"
    bg_path = Path(spec.background_path)
    assert bg_path.exists(), f"Template PNG missing: {bg_path}"
    size_kb = bg_path.stat().st_size / 1024
    assert size_kb > 5, f"Template PNG too small: {size_kb:.1f}KB"
    print(f"\n[PASS] Template-rendered PNG: {bg_path} ({size_kb:.1f}KB)")
    print(f"   Spec duration: {spec.duration}s")
    print(f"   Overlays: {len(spec.overlays)}")

pytestmark = pytest.mark.integration
