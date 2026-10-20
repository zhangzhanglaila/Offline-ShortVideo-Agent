"""
Phase 3 视频合成Agent测试。

测试内容：
1. 场景图渲染器 (SceneImageRenderer) - PIL渲染，始终可用
2. 视频合成Agent - 用注入的fake composer测试编排逻辑
3. 素材选择逻辑
4. 降级路径（FFmpeg失败）
5. 真实FFmpeg端到端冒烟测试（无FFmpeg时跳过）
"""

import os
import asyncio
from pathlib import Path

import pytest
from PIL import Image

from core.models import (
    Message,
    ContentStructure,
    Scene,
    SceneType,
    MaterialAsset,
    SceneMaterialMap,
    create_task_message,
)
from core.compose.scene_image_renderer import SceneImageRenderer
from core.compose.ffmpeg_composer import FFmpegComposer
from core.agents.video_compose_agent import VideoComposeAgent


# 测试输出目录
TEST_DIR = Path("output/test_phase3")


def _setup():
    TEST_DIR.mkdir(parents=True, exist_ok=True)


# ========== Fake Composer（离线测试编排） ==========

class FakeComposer:
    """模拟FFmpegComposer，不实际调用FFmpeg。"""

    def __init__(self, should_succeed=True):
        self.should_succeed = should_succeed
        self.available = True
        self.compose_calls = []

    def compose(self, scenes, output_path, transition_duration=0.0, audio_path=None, transitions=None, bgm_path=None, bgm_volume=0.3):
        self.compose_calls.append({
            "scene_count": len(scenes),
            "output_path": output_path,
            "transition": transition_duration,
        })
        if self.should_succeed:
            # 创建一个假的输出文件
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"fake video")
            return True
        return False


def _make_content(scenes=None, style="minimal", source="llm"):
    if scenes is None:
        scenes = [
            Scene(1, SceneType.TITLE_CARD.value, "Redis为什么快", 3.0),
            Scene(2, SceneType.CONTENT.value, "内存存储读写极快", 12.0,
                  ["memory", "database"]),
            Scene(3, SceneType.CONTENT.value, "单线程无锁竞争", 12.0,
                  ["server"]),
            Scene(4, SceneType.CONCLUSION.value, "感谢观看", 3.0),
        ]
    return ContentStructure(
        title="Redis讲解", category="教育讲解", style=style,
        total_duration=30, scenes=scenes, source=source,
    )


def _make_material_map(with_files=True):
    """构建素材映射，可选生成真实图片文件。"""
    m = SceneMaterialMap()
    if with_files:
        # 为场景2、3生成真实测试图片
        for sid in (2, 3):
            img_path = str(TEST_DIR / f"material_{sid}.jpg")
            Image.new("RGB", (800, 600), (100 + sid * 20, 120, 180)).save(img_path)
            m.add(sid, MaterialAsset(
                asset_id=f"pexels_{sid}", scene_id=sid, source="pexels",
                local_path=img_path, width=800, height=600,
                quality_score=0.8, keywords=["test"],
            ))
    return m


def _make_message(content, material_map, output_path):
    return create_task_message(
        sender="coordinator", receiver="video_compose",
        task_type="compose_video",
        payload={
            "content": content.to_dict(),
            "materials": material_map.to_dict(),
            "output_path": output_path,
        },
    )


# ========== 测试1: 场景图渲染器 ==========

class TestSceneImageRenderer:
    """测试场景图渲染（PIL，始终可用）。"""

    def test_render_title_card(self):
        """测试标题卡渲染。"""
        _setup()
        renderer = SceneImageRenderer(size=(540, 960))  # 小尺寸加速
        out = str(TEST_DIR / "title.png")
        ok = renderer.render_scene("title_card", "Redis为什么快", out)
        assert ok
        assert Path(out).exists()
        img = Image.open(out)
        assert img.size == (540, 960)
        print("✅ 标题卡渲染正常")

    def test_render_content_with_material(self):
        """测试内容场景渲染（含素材背景+字幕）。"""
        _setup()
        # 准备素材图
        mat_path = str(TEST_DIR / "mat.jpg")
        Image.new("RGB", (800, 600), (50, 100, 150)).save(mat_path)

        renderer = SceneImageRenderer(size=(540, 960))
        out = str(TEST_DIR / "content.png")
        ok = renderer.render_scene("content", "内存存储读写极快", out, mat_path)
        assert ok
        assert Path(out).exists()
        print("✅ 内容场景渲染正常（素材+字幕）")

    def test_render_content_without_material(self):
        """测试无素材时用渐变背景。"""
        _setup()
        renderer = SceneImageRenderer(size=(540, 960))
        out = str(TEST_DIR / "content_no_mat.png")
        ok = renderer.render_scene("content", "无素材降级渐变背景", out, None)
        assert ok
        assert Path(out).exists()
        print("✅ 无素材渐变背景降级正常")

    def test_text_wrapping(self):
        """测试长文本换行不溢出。"""
        _setup()
        renderer = SceneImageRenderer(size=(540, 960))
        long_text = "这是一段非常长的讲解文字用来测试自动换行功能是否能正常工作不会溢出画面边界"
        out = str(TEST_DIR / "long.png")
        ok = renderer.render_scene("content", long_text, out, None)
        assert ok
        print("✅ 长文本换行正常")

    def test_different_styles(self):
        """测试不同风格配色。"""
        _setup()
        styles = [
            {"colors": {"background": "#000000", "primary": "#FFFFFF",
                        "accent": "#FF0000"}},
            {"colors": {"background": "#FFFFFF", "primary": "#000000",
                        "accent": "#00FF00"}},
        ]
        for i, style in enumerate(styles):
            renderer = SceneImageRenderer(style=style, size=(540, 960))
            out = str(TEST_DIR / f"style_{i}.png")
            assert renderer.render_scene("title_card", f"风格{i}", out)
        print("✅ 多风格配色正常")


# ========== 测试2: 合成Agent编排（Fake composer） ==========

class TestComposeOrchestration:
    """测试合成Agent的编排逻辑。"""

    def test_compose_success(self):
        """测试成功合成流程。"""
        async def _test():
            _setup()
            fake = FakeComposer(should_succeed=True)
            agent = VideoComposeAgent(size=(540, 960), composer=fake)

            content = _make_content()
            materials = _make_material_map(with_files=True)
            out = str(TEST_DIR / "video_success.mp4")
            msg = _make_message(content, materials, out)

            result = await agent.execute(msg)
            assert result.status == "success"

            data = result.result
            assert data["success"] is True
            assert data["scenes_rendered"] == 4
            assert data["video_path"] == out
            assert Path(out).exists()

            # 验证composer被调用，且传入4个场景
            assert len(fake.compose_calls) == 1
            assert fake.compose_calls[0]["scene_count"] == 4
            print(f"✅ 合成编排成功: 质量={data['quality_score']}")

        asyncio.run(_test())

    def test_material_selection(self):
        """测试素材选择：内容场景用真实素材，文字场景不用。"""
        async def _test():
            _setup()
            fake = FakeComposer(should_succeed=True)
            agent = VideoComposeAgent(size=(540, 960), composer=fake)

            content = _make_content()
            materials = _make_material_map(with_files=True)

            # 直接测试_pick_material
            title_scene = content.scenes[0]  # title_card
            content_scene = content.scenes[1]  # content with material

            assert agent._pick_material(title_scene, materials) is None
            picked = agent._pick_material(content_scene, materials)
            # _pick_material现返回MaterialAsset(D5)
            assert picked is not None
            assert Path(picked.local_path).exists()
            print("✅ 素材选择逻辑正常")

        asyncio.run(_test())

    def test_placeholder_material_not_used(self):
        """测试占位符素材不被选为背景。"""
        async def _test():
            agent = VideoComposeAgent(size=(540, 960), composer=FakeComposer())
            m = SceneMaterialMap()
            m.add(2, MaterialAsset(
                asset_id="placeholder_2", scene_id=2,
                source="placeholder", is_placeholder=True,
            ))
            scene = Scene(2, SceneType.CONTENT.value, "内容", 12.0)
            # 占位符不应被选中（返回None → 渲染器用渐变）
            assert agent._pick_material(scene, m) is None
            print("✅ 占位符素材正确跳过")

        asyncio.run(_test())

    def test_compose_degraded(self):
        """测试FFmpeg失败时降级为图文模式。"""
        async def _test():
            _setup()
            fake = FakeComposer(should_succeed=False)  # 合成失败
            agent = VideoComposeAgent(size=(540, 960), composer=fake)

            content = _make_content()
            materials = _make_material_map(with_files=True)
            out = str(TEST_DIR / "video_degraded.mp4")
            msg = _make_message(content, materials, out)

            result = await agent.execute(msg)
            # 降级仍返回success消息，但data标记degraded
            assert result.status == "success"
            data = result.result
            assert data["success"] is False
            assert data["degraded"] is True
            assert data["scenes_dir"] is not None
            # 场景图应已渲染保留
            assert Path(data["scenes_dir"]).exists()
            print("✅ FFmpeg失败降级图文模式正常")

        asyncio.run(_test())


# ========== 测试3: 异常处理 ==========

class TestErrorHandling:
    """测试异常处理。"""

    def test_missing_content(self):
        """测试缺少content字段。"""
        async def _test():
            agent = VideoComposeAgent(composer=FakeComposer())
            msg = create_task_message(
                sender="c", receiver="video_compose",
                task_type="compose_video", payload={"materials": {}},
            )
            result = await agent.execute(msg)
            assert result.status == "failed"
            print("✅ 缺少content正确拒绝")

        asyncio.run(_test())

    def test_no_materials_all_text(self):
        """测试无素材映射时（全用渐变/文字卡）。"""
        async def _test():
            _setup()
            fake = FakeComposer(should_succeed=True)
            agent = VideoComposeAgent(size=(540, 960), composer=fake)

            content = _make_content()
            out = str(TEST_DIR / "video_no_mat.mp4")
            # 不传materials
            msg = create_task_message(
                sender="c", receiver="video_compose",
                task_type="compose_video",
                payload={"content": content.to_dict(), "output_path": out},
            )
            result = await agent.execute(msg)
            assert result.status == "success"
            data = result.result
            # 内容场景无素材 → 匹配率0，但仍能合成
            assert data["scenes_rendered"] == 4
            print("✅ 无素材映射仍能合成（渐变背景）")

        asyncio.run(_test())


# ========== 测试4: 真实FFmpeg端到端冒烟 ==========

class TestRealFFmpeg:
    """真实FFmpeg端到端测试（无FFmpeg时跳过）。"""

    def test_real_compose_smoke(self):
        """真实合成一个短视频。"""
        composer = FFmpegComposer(size=(540, 960), fps=15)
        if not composer.available:
            print("⏭️  跳过: FFmpeg不可用")
            return

        async def _test():
            _setup()
            agent = VideoComposeAgent(
                size=(540, 960), fps=15, transition_duration=0.3,
            )
            # 短时长加速测试
            scenes = [
                Scene(1, SceneType.TITLE_CARD.value, "测试标题", 1.0),
                Scene(2, SceneType.CONTENT.value, "内容场景配字幕", 1.5,
                      ["test"]),
                Scene(3, SceneType.CONCLUSION.value, "结尾", 1.0),
            ]
            content = _make_content(scenes=scenes)
            materials = _make_material_map(with_files=True)
            out = str(TEST_DIR / "real_video.mp4")
            msg = _make_message(content, materials, out)

            result = await agent.execute(msg)
            assert result.status == "success"
            data = result.result
            assert data["success"] is True
            assert Path(out).exists()

            # 验证视频时长接近预期（3.5s，转场会略微缩短）
            actual = composer.probe_duration(out)
            if actual:
                print(f"✅ 真实合成成功: {out}, 时长{actual:.1f}s")
                assert 2.0 < actual < 4.5, f"时长异常: {actual}"
            else:
                print(f"✅ 真实合成成功: {out}")

        asyncio.run(_test())


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])


@pytest.mark.asyncio
async def test_build_scene_spec_async_with_template(tmp_path):
    """scene.template 非空时调用 render_template_frame"""
    from core.models.content import Scene, ContentStructure
    from core.agents.video_compose_agent import VideoComposeAgent
    from PIL import Image
    import core.compose.scene_image_renderer as sir

    # Mock render_template_frame to avoid actually launching Chromium
    calls = []
    original = sir.render_template_frame

    async def mock(template_name, context, output_path, fallback_func=None, renderer=None):
        calls.append({"template": template_name, "context": context, "output": output_path})
        Image.new("RGB", (1080, 1920), "#abcdef").save(output_path)
        return output_path

    sir.render_template_frame = mock
    try:
        scene = Scene(
            scene_id=1,
            scene_type="content",
            text="讲解Python异步",
            duration=5.0,
            template="image_default",
            keywords=["python"],
        )
        agent = VideoComposeAgent(size=(1080, 1920))
        style_dict = {"bg_gradient": ["#1a1a2e", "#16213e"], "text_color": "#ffffff"}
        spec = await agent._build_scene_spec_async(
            scene, 0, {}, tmp_path,
            title="Python异步编程",
            style_dict=style_dict,
        )

        assert spec is not None, "Template path should produce a spec"
        assert len(calls) == 1, f"Expected 1 call, got {len(calls)}"
        assert calls[0]["template"] == "image_default"
        assert calls[0]["context"]["title"] == "Python异步编程"
        assert calls[0]["context"]["text"] == "讲解Python异步"
        assert Path(spec.background_path).exists()
    finally:
        sir.render_template_frame = original
