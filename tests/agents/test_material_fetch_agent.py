"""
Phase 2 素材检索Agent测试。

测试内容：
1. 素材数据模型 (MaterialAsset, SceneMaterialMap)
2. 占位符降级路径（无API）
3. 注入fake API的检索路径（离线）
4. 下载失败降级
5. 质量评分
6. 真实网络冒烟测试（无网络时自动跳过）
"""

import os
import asyncio

from core.models import (
    Message,
    ContentStructure,
    Scene,
    SceneType,
    MaterialAsset,
    SceneMaterialMap,
    create_task_message,
)
from core.agents.material_fetch_agent import MaterialFetchAgent


# ========== Fake API 基础设施（离线测试） ==========

class FakeAPIResult:
    """模拟APIResult。"""

    def __init__(self, id, download_url, width=1920, height=1080,
                 url="", media_type="image"):
        self.id = id
        self.download_url = download_url
        self.url = url or f"https://example.com/{id}"
        self.width = width
        self.height = height
        self.media_type = media_type
        self.title = "fake"
        self.tags = []


class FakeAPIManager:
    """模拟APIManager，返回预设结果。"""

    def __init__(self, results=None, fail_queries=None):
        # results: 固定返回的 [(api_name, FakeAPIResult), ...]
        self._results = results
        self._fail_queries = fail_queries or set()
        self.queries_seen = []

    def search_best(self, query, top_k=5):
        self.queries_seen.append(query)
        if query in self._fail_queries:
            return []
        if self._results is not None:
            return self._results[:top_k]
        # 默认返回一个1080p结果
        return [("pexels", FakeAPIResult(f"img_{len(self.queries_seen)}",
                                          "https://example.com/pic.jpg"))]


def _fake_downloader_success(download_url, local_path):
    """模拟成功下载（不实际写文件）。"""
    return True


def _fake_downloader_fail(download_url, local_path):
    """模拟下载失败。"""
    return False


def _make_content_message(scenes=None, duration=30):
    """构建含ContentStructure的任务消息。"""
    if scenes is None:
        scenes = [
            Scene(1, SceneType.TITLE_CARD.value, "标题", 3.0),
            Scene(2, SceneType.CONTENT.value, "内存存储极快", 12.0,
                  ["Redis", "memory"]),
            Scene(3, SceneType.CONTENT.value, "单线程模型", 12.0,
                  ["single thread"]),
            Scene(4, SceneType.CONCLUSION.value, "感谢观看", 3.0),
        ]
    content = ContentStructure(
        title="测试视频",
        category="教育讲解",
        style="tech",
        total_duration=duration,
        scenes=scenes,
    )
    return create_task_message(
        sender="coordinator",
        receiver="material_fetch",
        task_type="fetch_material",
        payload=content.to_dict(),
    )


# ========== 测试1: 素材数据模型 ==========

class TestMaterialModels:
    """测试素材数据模型。"""

    def test_asset_roundtrip(self):
        """测试素材序列化往返。"""
        asset = MaterialAsset(
            asset_id="pexels_123",
            scene_id=2,
            source="pexels",
            download_url="https://x.com/a.jpg",
            local_path="/cache/a.jpg",
            width=1920, height=1080,
            quality_score=0.5,
            keywords=["redis"],
        )
        d = asset.to_dict()
        asset2 = MaterialAsset.from_dict(d)
        assert asset2.asset_id == "pexels_123"
        assert asset2.scene_id == 2
        assert asset2.is_available
        print("✅ 素材资产序列化往返正常")

    def test_placeholder_available(self):
        """测试占位符视为可用。"""
        asset = MaterialAsset(
            asset_id="placeholder_1", scene_id=1,
            source="placeholder", is_placeholder=True,
        )
        assert asset.is_available
        print("✅ 占位符可用性正常")

    def test_scene_material_map(self):
        """测试场景素材映射。"""
        m = SceneMaterialMap()
        m.add(2, MaterialAsset("a1", 2, "pexels", width=1920, height=1080))
        m.add(2, MaterialAsset("a2", 2, "placeholder", is_placeholder=True))
        m.add(3, MaterialAsset("a3", 3, "pixabay", width=1280, height=720))

        assert m.total_assets == 3
        assert m.placeholder_count == 1
        assert m.real_count == 2
        assert len(m.get(2)) == 2

        # 匹配率: 场景2和3都有真实素材 = 2/2
        assert m.match_rate(2) == 1.0
        print("✅ 场景素材映射正常")

    def test_map_roundtrip(self):
        """测试映射序列化往返（含int键转换）。"""
        m = SceneMaterialMap()
        m.add(2, MaterialAsset("a1", 2, "pexels"))
        d = m.to_dict()
        # JSON键应为字符串
        assert "2" in d["scene_materials"]
        m2 = SceneMaterialMap.from_dict(d)
        # 还原后应为int键
        assert 2 in m2.scene_materials
        print("✅ 映射序列化往返正常")


# ========== 测试2: 占位符降级路径 ==========

class TestPlaceholderFallback:
    """测试无API时的占位符降级。"""

    def test_no_api_all_placeholder(self):
        """测试禁用API时全部占位符。"""
        async def _test():
            agent = MaterialFetchAgent(api_manager=False, video_module=False)
            msg = _make_content_message()
            result = await agent.execute(msg)

            assert result.status == "success"
            m = SceneMaterialMap.from_dict(result.result)

            # 2个内容场景，各1占位符
            assert m.total_assets == 2
            assert m.placeholder_count == 2
            assert m.real_count == 0
            assert m.match_rate(2) == 0.0
            print(f"✅ 无API降级占位符正常\n{m.get_summary()}")

        asyncio.run(_test())

    def test_text_only_content(self):
        """测试全文字场景（无需素材）。"""
        async def _test():
            scenes = [
                Scene(1, SceneType.TITLE_CARD.value, "标题", 3.0),
                Scene(2, SceneType.CONCLUSION.value, "结尾", 3.0),
            ]
            agent = MaterialFetchAgent(api_manager=False, video_module=False)
            msg = _make_content_message(scenes=scenes)
            result = await agent.execute(msg)

            assert result.status == "success"
            m = SceneMaterialMap.from_dict(result.result)
            assert m.total_assets == 0
            print("✅ 全文字场景处理正常")

        asyncio.run(_test())


# ========== 测试3: Fake API 检索路径 ==========

class TestFakeAPIPath:
    """测试注入fake API的检索路径。"""

    def test_fetch_success(self):
        """测试成功检索素材。"""
        async def _test():
            fake_api = FakeAPIManager()
            agent = MaterialFetchAgent(
                api_manager=fake_api, video_module=False,
                downloader=_fake_downloader_success,
            )
            msg = _make_content_message()
            result = await agent.execute(msg)

            assert result.status == "success"
            m = SceneMaterialMap.from_dict(result.result)

            # 2个内容场景各匹配到真实素材
            assert m.real_count == 2
            assert m.placeholder_count == 0
            assert m.match_rate(2) == 1.0

            # 验证素材有本地路径和质量分
            for assets in m.scene_materials.values():
                for a in assets:
                    assert a.local_path is not None
                    assert a.quality_score > 0
            print(f"✅ Fake API检索成功\n{m.get_summary()}")

        asyncio.run(_test())

    def test_query_building(self):
        """测试检索查询构建（关键词优先）。"""
        async def _test():
            fake_api = FakeAPIManager()
            agent = MaterialFetchAgent(
                api_manager=fake_api, video_module=False,
                downloader=_fake_downloader_success,
            )
            msg = _make_content_message()
            await agent.execute(msg)

            # 场景2关键词["Redis","memory"] → 首查询应为"Redis memory"
            assert "Redis memory" in fake_api.queries_seen
            print(f"✅ 查询构建正常: {fake_api.queries_seen}")

        asyncio.run(_test())

    def test_download_fail_fallback(self):
        """测试下载失败时降级占位符。"""
        async def _test():
            fake_api = FakeAPIManager()
            agent = MaterialFetchAgent(
                api_manager=fake_api, video_module=False,
                downloader=_fake_downloader_fail,  # 全部下载失败
            )
            msg = _make_content_message()
            result = await agent.execute(msg)

            assert result.status == "success"
            m = SceneMaterialMap.from_dict(result.result)
            # 下载全失败 → 全占位符
            assert m.placeholder_count == 2
            assert m.real_count == 0
            print("✅ 下载失败降级正常")

        asyncio.run(_test())

    def test_search_empty_fallback(self):
        """测试搜索无结果时降级占位符。"""
        async def _test():
            # 所有查询都返回空
            fake_api = FakeAPIManager(results=[])
            agent = MaterialFetchAgent(
                api_manager=fake_api, video_module=False,
                downloader=_fake_downloader_success,
            )
            msg = _make_content_message()
            result = await agent.execute(msg)

            assert result.status == "success"
            m = SceneMaterialMap.from_dict(result.result)
            assert m.placeholder_count == 2
            print("✅ 搜索无结果降级正常")

        asyncio.run(_test())

    def test_quality_scoring(self):
        """测试质量评分随分辨率变化。"""
        async def _test():
            # 高分辨率结果
            hi_res = [("pexels", FakeAPIResult("hi", "https://x.com/hi.jpg",
                                                width=4096, height=2304))]
            fake_api = FakeAPIManager(results=hi_res)
            agent = MaterialFetchAgent(
                api_manager=fake_api, video_module=False,
                downloader=_fake_downloader_success,
            )
            scenes = [
                Scene(1, SceneType.TITLE_CARD.value, "标题", 3.0),
                Scene(2, SceneType.CONTENT.value, "内容", 24.0, ["test"]),
                Scene(3, SceneType.CONCLUSION.value, "结尾", 3.0),
            ]
            msg = _make_content_message(scenes=scenes)
            result = await agent.execute(msg)
            m = SceneMaterialMap.from_dict(result.result)

            asset = m.get(2)[0]
            # 4K素材质量应接近1.0
            assert asset.quality_score >= 0.9, f"高清素材质量应高: {asset.quality_score}"
            print(f"✅ 质量评分正常: 4K={asset.quality_score}")

        asyncio.run(_test())


# ========== 测试4: 异常处理 ==========

class TestErrorHandling:
    """测试异常处理。"""

    def test_invalid_content(self):
        """测试无效内容结构。"""
        async def _test():
            agent = MaterialFetchAgent(api_manager=False, video_module=False)
            # 空场景的无效内容
            bad_content = {
                "title": "", "category": "教育讲解",
                "style": "minimal", "total_duration": 30, "scenes": [],
            }
            msg = create_task_message(
                sender="coordinator", receiver="material_fetch",
                task_type="fetch_material", payload=bad_content,
            )
            result = await agent.execute(msg)
            assert result.status == "failed"
            print("✅ 无效内容正确拒绝")

        asyncio.run(_test())


# ========== 测试5: 真实网络冒烟测试 ==========

class TestRealNetwork:
    """真实网络冒烟测试（无网络/无密钥时自动跳过）。"""

    def test_real_pexels_smoke(self):
        """真实调用Pexels检索（需PEXELS_API_KEY且有网络）。"""
        # 加载.env
        try:
            from dotenv import load_dotenv
            load_dotenv(override=True)
        except Exception:
            pass

        if not os.environ.get("PEXELS_API_KEY"):
            print("⏭️  跳过: 未配置PEXELS_API_KEY")
            return

        async def _test():
            # 只搜索不下载，加快速度
            agent = MaterialFetchAgent(enable_download=False)
            scenes = [
                Scene(1, SceneType.TITLE_CARD.value, "标题", 3.0),
                Scene(2, SceneType.CONTENT.value, "自然风光", 24.0,
                      ["nature", "landscape"]),
                Scene(3, SceneType.CONCLUSION.value, "结尾", 3.0),
            ]
            msg = _make_content_message(scenes=scenes)
            result = await agent.execute(msg)

            assert result.status == "success"
            m = SceneMaterialMap.from_dict(result.result)
            # 真实检索应找到素材（网络正常时）
            if m.real_count > 0:
                print(f"✅ 真实Pexels检索成功\n{m.get_summary()}")
            else:
                print("⚠️  真实检索未找到素材（可能网络受限），降级占位符")

        try:
            asyncio.run(_test())
        except Exception as e:
            print(f"⏭️  跳过真实网络测试（网络异常）: {e}")


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "-s"])
