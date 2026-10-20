"""P10.3 + P11.3 — Asset Store and Retriever Tests.

Verifies:
  - AssetStore: CAS, dedup, metadata, atomic writes, thread safety
  - AssetRetriever: search, fetch, caching with mock backend
  - AssetPass: NarrativeIR → asset search results
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.backend.asset_store import AssetStore
from core.ai.asset_retriever import (
    AssetResult, AssetRetriever, SearchBackend, LocalSearchBackend,
)
from compiler.pass_asset import AssetPass, _extract_keywords, _extract_mood
from compiler.ir.narrative_ir import (
    NarrativeIR, Beat, BeatType, TransitionType,
    HookBeat, ProblemBeat, RevealBeat, CTABeat,
)


# ═══════════════════════════════════════════════════════════════════════
# Asset Store
# ═══════════════════════════════════════════════════════════════════════


class TestAssetStore:
    """Content-addressable file storage."""

    def _make_file(self, tmp_path: Path, name: str, content: bytes) -> Path:
        p = tmp_path / name
        p.write_bytes(content)
        return p

    def test_put_and_get(self, tmp_path):
        store = AssetStore(tmp_path / "store")
        f = self._make_file(tmp_path, "test.txt", b"hello world")
        h = store.put(f)
        assert isinstance(h, str)
        assert len(h) == 16
        assert store.exists(h)
        assert store.get(h).exists()

    def test_dedup_same_content(self, tmp_path):
        store = AssetStore(tmp_path / "store")
        f1 = self._make_file(tmp_path, "a.txt", b"same content")
        f2 = self._make_file(tmp_path, "b.txt", b"same content")
        h1 = store.put(f1)
        h2 = store.put(f2)
        assert h1 == h2
        assert store.stats()["entries"] == 1

    def test_different_content_different_hash(self, tmp_path):
        store = AssetStore(tmp_path / "store")
        f1 = self._make_file(tmp_path, "a.txt", b"content A")
        f2 = self._make_file(tmp_path, "b.txt", b"content B")
        h1 = store.put(f1)
        h2 = store.put(f2)
        assert h1 != h2
        assert store.stats()["entries"] == 2

    def test_metadata_stored(self, tmp_path):
        store = AssetStore(tmp_path / "store")
        f = self._make_file(tmp_path, "photo.jpg", b"\xff\xd8\xff\xe0")
        h = store.put(f, metadata={"tags": ["nature"], "width": 1920})
        meta = store.get_metadata(h)
        assert meta["tags"] == ["nature"]
        assert meta["width"] == 1920

    def test_metadata_merged_on_dedup(self, tmp_path):
        store = AssetStore(tmp_path / "store")
        f1 = self._make_file(tmp_path, "a.txt", b"same")
        f2 = self._make_file(tmp_path, "b.txt", b"same")
        store.put(f1, metadata={"tags": ["a"]})
        h2 = store.put(f2, metadata={"license": "cc0"})
        meta = store.get_metadata(h2)
        assert "a" in meta["tags"]
        assert meta["license"] == "cc0"

    def test_asset_type_stored(self, tmp_path):
        store = AssetStore(tmp_path / "store")
        f = self._make_file(tmp_path, "clip.mp4", b"\x00\x00\x00\x1c")
        h = store.put(f, asset_type="video")
        assets = store.list_assets("video")
        assert len(assets) == 1
        assert assets[0]["asset_type"] == "video"

    def test_delete(self, tmp_path):
        store = AssetStore(tmp_path / "store")
        f = self._make_file(tmp_path, "tmp.bin", b"data")
        h = store.put(f)
        assert store.exists(h)
        assert store.delete(h)
        assert not store.exists(h)

    def test_delete_nonexistent(self, tmp_path):
        store = AssetStore(tmp_path / "store")
        assert not store.delete("nonexistent")

    def test_get_nonexistent(self, tmp_path):
        store = AssetStore(tmp_path / "store")
        assert store.get("nonexistent") is None

    def test_file_not_found_raises(self, tmp_path):
        store = AssetStore(tmp_path / "store")
        with pytest.raises(FileNotFoundError):
            store.put(Path("does_not_exist.txt"))

    def test_two_level_directory(self, tmp_path):
        store = AssetStore(tmp_path / "store")
        f = self._make_file(tmp_path, "test.bin", b"data")
        h = store.put(f)
        # First two chars of hash should be a subdirectory
        sub = store.store_dir / h[:2]
        assert sub.is_dir()

    def test_manifest_persists(self, tmp_path):
        store_dir = tmp_path / "store"
        store = AssetStore(store_dir)
        f = self._make_file(tmp_path, "test.bin", b"persist")
        h = store.put(f)

        # Create new store instance (simulates restart)
        store2 = AssetStore(store_dir)
        assert store2.exists(h)
        assert store2.get_metadata(h) is not None

    def test_stats(self, tmp_path):
        store = AssetStore(tmp_path / "store")
        f1 = self._make_file(tmp_path, "a.bin", b"aaaa")
        f2 = self._make_file(tmp_path, "b.bin", b"bbbbbb")
        store.put(f1)
        store.put(f2)
        stats = store.stats()
        assert stats["entries"] == 2
        assert stats["total_size_mb"] >= 0

    def test_list_all(self, tmp_path):
        store = AssetStore(tmp_path / "store")
        store.put(self._make_file(tmp_path, "a.jpg", b"\xff\xd8"), asset_type="image")
        store.put(self._make_file(tmp_path, "b.mp4", b"\x00\x00"), asset_type="video")
        store.put(self._make_file(tmp_path, "c.mp3", b"\xff\xfb"), asset_type="audio")
        assert len(store.list_assets()) == 3
        assert len(store.list_assets("image")) == 1

    def test_clear(self, tmp_path):
        store = AssetStore(tmp_path / "store")
        store.put(self._make_file(tmp_path, "a.bin", b"a"))
        store.put(self._make_file(tmp_path, "b.bin", b"b"))
        assert store.stats()["entries"] == 2
        store.clear()
        assert store.stats()["entries"] == 0

    def test_thread_safety(self, tmp_path):
        """Multiple threads putting different files concurrently."""
        store = AssetStore(tmp_path / "store")
        results = []
        errors = []

        def put_file(i):
            try:
                f = self._make_file(tmp_path, f"t{i}.bin", f"content_{i}".encode())
                h = store.put(f)
                results.append(h)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=put_file, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10
        assert len(set(results)) == 10  # All unique
        assert store.stats()["entries"] == 10


# ═══════════════════════════════════════════════════════════════════════
# Local Search Backend
# ═══════════════════════════════════════════════════════════════════════


class TestLocalSearchBackend:
    """Search local directory for assets."""

    def _setup_library(self, tmp_path: Path):
        lib = tmp_path / "library"
        lib.mkdir()
        (lib / "redis_diagram.png").write_bytes(b"\x89PNG")
        (lib / "database_schema.jpg").write_bytes(b"\xff\xd8")
        (lib / "memory_cache.webp").write_bytes(b"RIFF")
        (lib / "server_video.mp4").write_bytes(b"\x00\x00")
        (lib / "background_music.mp3").write_bytes(b"\xff\xfb")
        # Non-matching
        (lib / "random.txt").write_bytes(b"text")
        return lib

    def test_search_images(self, tmp_path):
        lib = self._setup_library(tmp_path)
        backend = LocalSearchBackend(lib)
        results = asyncio.run(backend.search(["redis"], "image", count=5))
        assert len(results) >= 1
        assert any("redis" in r["source_path"].lower() for r in results)

    def test_search_videos(self, tmp_path):
        lib = self._setup_library(tmp_path)
        backend = LocalSearchBackend(lib)
        results = asyncio.run(backend.search(["server"], "video", count=5))
        assert len(results) >= 1

    def test_search_audio(self, tmp_path):
        lib = self._setup_library(tmp_path)
        backend = LocalSearchBackend(lib)
        results = asyncio.run(backend.search(["background"], "audio", count=5))
        assert len(results) >= 1

    def test_no_match(self, tmp_path):
        lib = self._setup_library(tmp_path)
        backend = LocalSearchBackend(lib)
        results = asyncio.run(backend.search(["nonexistent"], "image", count=5))
        assert len(results) == 0

    def test_count_limit(self, tmp_path):
        lib = self._setup_library(tmp_path)
        backend = LocalSearchBackend(lib)
        results = asyncio.run(backend.search(["redis", "database", "memory"], "image", count=1))
        assert len(results) <= 1

    def test_empty_library(self, tmp_path):
        lib = tmp_path / "empty"
        lib.mkdir()
        backend = LocalSearchBackend(lib)
        results = asyncio.run(backend.search(["anything"], "image", count=5))
        assert len(results) == 0

    def test_nonexistent_library(self, tmp_path):
        backend = LocalSearchBackend(tmp_path / "nope")
        results = asyncio.run(backend.search(["test"], "image", count=5))
        assert len(results) == 0


# ═══════════════════════════════════════════════════════════════════════
# Asset Retriever
# ═══════════════════════════════════════════════════════════════════════


class MockSearchBackend(SearchBackend):
    """Mock backend returning pre-defined results."""

    def __init__(self, results: list[dict]):
        self.results = results

    async def search(self, keywords, asset_type, count=5, **kwargs):
        return self.results[:count]


class TestAssetRetriever:
    """Search + download + cache with mock backend."""

    def test_search_and_store_local(self, tmp_path):
        """Local file gets stored in CAS."""
        store = AssetStore(tmp_path / "store")
        # Create a real file
        src = tmp_path / "source.jpg"
        src.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)

        backend = MockSearchBackend([{
            "source_path": str(src),
            "metadata": {"tags": ["test"]},
        }])
        retriever = AssetRetriever(store=store, backend=backend)

        results = asyncio.run(retriever.search_images(["test"], count=1))
        assert len(results) == 1
        assert results[0].asset_type == "image"
        assert results[0].source == "local"
        assert store.exists(results[0].asset_id)

    def test_dedup_across_searches(self, tmp_path):
        """Same file searched twice → only one store entry."""
        store = AssetStore(tmp_path / "store")
        src = tmp_path / "photo.jpg"
        src.write_bytes(b"same image data")

        backend = MockSearchBackend([{
            "source_path": str(src),
            "metadata": {"tags": ["photo"]},
        }])
        retriever = AssetRetriever(store=store, backend=backend)

        r1 = asyncio.run(retriever.search_images(["photo"], count=1))
        r2 = asyncio.run(retriever.search_images(["photo"], count=1))
        assert r1[0].asset_id == r2[0].asset_id
        assert store.stats()["entries"] == 1

    def test_asset_result_to_dict(self, tmp_path):
        store = AssetStore(tmp_path / "store")
        src = tmp_path / "img.png"
        src.write_bytes(b"\x89PNG")

        backend = MockSearchBackend([{
            "source_path": str(src),
            "metadata": {"width": 1080, "height": 1920},
        }])
        retriever = AssetRetriever(store=store, backend=backend)

        results = asyncio.run(retriever.search_images(["img"], count=1))
        d = results[0].to_dict()
        assert "asset_id" in d
        assert "path" in d
        assert d["metadata"]["width"] == 1080

    def test_empty_search(self, tmp_path):
        store = AssetStore(tmp_path / "store")
        backend = MockSearchBackend([])
        retriever = AssetRetriever(store=store, backend=backend)
        results = asyncio.run(retriever.search_images(["nothing"], count=5))
        assert len(results) == 0


# ═══════════════════════════════════════════════════════════════════════
# Keyword Extraction
# ═══════════════════════════════════════════════════════════════════════


class TestKeywordExtraction:
    """Extract keywords and mood from narrative."""

    def _make_narrative(self) -> NarrativeIR:
        return NarrativeIR(
            beats=(
                HookBeat(text="Redis为什么这么快？", duration=2.0, intensity=0.9),
                ProblemBeat(text="很多人不了解数据库原理", duration=3.0, intensity=0.6),
                RevealBeat(text="答案是内存加单线程", duration=4.0, intensity=1.0),
                CTABeat(text="关注学习更多", duration=1.0, intensity=0.4),
            ),
            title="Redis深度解析",
        )

    def test_topic_in_keywords(self):
        n = self._make_narrative()
        kw = _extract_keywords(n, "Redis")
        assert "redis" in kw

    def test_beat_type_in_keywords(self):
        n = self._make_narrative()
        kw = _extract_keywords(n, "Redis")
        assert "hook" in kw
        assert "reveal" in kw

    def test_text_in_keywords(self):
        n = self._make_narrative()
        kw = _extract_keywords(n, "Redis")
        # Beat text is included as whole tokens (Chinese has no word boundaries)
        assert any("数据库" in k or "内存" in k for k in kw)

    def test_mood_dramatic(self):
        n = self._make_narrative()
        mood = _extract_mood(n)
        assert mood == "dramatic"  # avg intensity ~0.725

    def test_mood_calm(self):
        n = NarrativeIR(
            beats=(
                HookBeat(text="hi", duration=2.0, intensity=0.2),
                CTABeat(text="bye", duration=1.0, intensity=0.2),
            ),
            title="Calm",
        )
        mood = _extract_mood(n)
        assert mood == "calm"


# ═══════════════════════════════════════════════════════════════════════
# Asset Pass
# ═══════════════════════════════════════════════════════════════════════


class TestAssetPass:
    """NarrativeIR → ASSET content dict."""

    def _make_narrative(self) -> NarrativeIR:
        return NarrativeIR(
            beats=(
                HookBeat(text="Redis快的秘密", duration=2.0, intensity=0.9),
                RevealBeat(text="答案是内存", duration=4.0, intensity=0.8),
                CTABeat(text="关注学习更多", duration=1.0, intensity=0.4),
            ),
            title="Redis解析",
        )

    def _make_retriever(self, tmp_path: Path) -> AssetRetriever:
        store = AssetStore(tmp_path / "store")
        # Create mock source files
        for name in ["redis.jpg", "memory.mp4", "bgm.mp3"]:
            (tmp_path / name).write_bytes(b"\x00" * 100)

        backend = MockSearchBackend([
            {"source_path": str(tmp_path / "redis.jpg"), "metadata": {"tags": ["redis"]}},
            {"source_path": str(tmp_path / "memory.mp4"), "metadata": {"tags": ["memory"]}},
            {"source_path": str(tmp_path / "bgm.mp3"), "metadata": {"tags": ["bgm"]}},
        ])
        return AssetRetriever(store=store, backend=backend)

    def test_basic_output(self, tmp_path):
        retriever = self._make_retriever(tmp_path)
        pass_ = AssetPass(retriever, images_per_beat=2, videos_per_beat=1, music_count=1)
        result = pass_.run(self._make_narrative(), topic="Redis")

        assert "images" in result
        assert "videos" in result
        assert "music" in result
        assert "keywords" in result
        assert "mood" in result

    def test_keywords_include_topic(self, tmp_path):
        retriever = self._make_retriever(tmp_path)
        pass_ = AssetPass(retriever)
        result = pass_.run(self._make_narrative(), topic="Redis")
        assert "redis" in result["keywords"]

    def test_mood_in_result(self, tmp_path):
        retriever = self._make_retriever(tmp_path)
        pass_ = AssetPass(retriever)
        result = pass_.run(self._make_narrative(), topic="Redis")
        assert result["mood"] == "dramatic"

    def test_asset_structure(self, tmp_path):
        retriever = self._make_retriever(tmp_path)
        pass_ = AssetPass(retriever, images_per_beat=1, videos_per_beat=0, music_count=0)
        result = pass_.run(self._make_narrative(), topic="Redis")

        if result["images"]:
            img = result["images"][0]
            assert "asset_id" in img
            assert "path" in img
            assert img["asset_type"] == "image"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
