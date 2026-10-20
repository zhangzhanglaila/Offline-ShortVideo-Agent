"""P4 — Incremental Media Rendering Tests.

Verifies the core incremental render behavior:
  - Only changed scenes are re-rendered
  - Cache hits reuse existing mp4
  - Composite hash changes only when scene hashes change
  - Concat result is cached by scene sequence
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.media.media_cache import MediaCache
from compiler.media.scene_renderer import SceneRenderer, IncrementalRenderStats
from compiler.media.concat_renderer import ConcatRenderer, _composite_hash
from compiler.thinking.canonicalize import content_hash


# ── Helpers ──────────────────────────────────────────────────────────

def _make_scene_ir(scene_id: str, text: str, duration: int = 150) -> dict:
    """Build a minimal scene IR for testing."""
    return {
        "content": {
            "scene_id": scene_id,
            "scene_type": "hook",
            "text": text,
            "duration_in_frames": duration,
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "theme": "dark",
            "audio_tracks": [],
            "elements": [],
        }
    }


def _mock_render_fn(content: dict, output_path: Path) -> Path:
    """Mock render function that writes a deterministic 'video' file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Write content hash as file content — deterministic
    h = content_hash(content)
    output_path.write_text(f"VIDEO:{h}", encoding="utf-8")
    return output_path


# ═══════════════════════════════════════════════════════════════════════
# Media Cache
# ═══════════════════════════════════════════════════════════════════════


class TestMediaCache:
    """Content-addressable media cache correctness."""

    def test_lookup_miss(self, tmp_path):
        cache = MediaCache(cache_dir=tmp_path / "cache")
        assert cache.lookup({"v": 1}) is None

    def test_store_and_lookup(self, tmp_path):
        cache = MediaCache(cache_dir=tmp_path / "cache")
        content = {"scene_id": "hook", "text": "hello"}

        # Create a fake media file
        media_file = tmp_path / "hook.mp4"
        media_file.write_bytes(b"fake video data")

        cache.store(content, media_file, scene_id="hook")
        cached = cache.lookup(content)

        assert cached is not None
        assert cached.exists()
        assert cached.read_bytes() == b"fake video data"

    def test_same_content_same_hash(self, tmp_path):
        cache = MediaCache(cache_dir=tmp_path / "cache")
        content = {"a": 1, "b": 2}

        media_file = tmp_path / "test.mp4"
        media_file.write_bytes(b"data")
        cache.store(content, media_file)

        # Same content, different dict instance
        assert cache.exists({"b": 2, "a": 1})

    def test_different_content_different_entry(self, tmp_path):
        cache = MediaCache(cache_dir=tmp_path / "cache")

        f1 = tmp_path / "a.mp4"
        f1.write_bytes(b"video A")
        cache.store({"v": 1}, f1, scene_id="a")

        f2 = tmp_path / "b.mp4"
        f2.write_bytes(b"video B")
        cache.store({"v": 2}, f2, scene_id="b")

        assert cache.lookup({"v": 1}).read_bytes() == b"video A"
        assert cache.lookup({"v": 2}).read_bytes() == b"video B"

    def test_persistence_across_instances(self, tmp_path):
        cache_dir = tmp_path / "cache"

        # First instance
        cache1 = MediaCache(cache_dir=cache_dir)
        f = tmp_path / "test.mp4"
        f.write_bytes(b"persisted")
        cache1.store({"key": "value"}, f)

        # Second instance
        cache2 = MediaCache(cache_dir=cache_dir)
        cached = cache2.lookup({"key": "value"})
        assert cached is not None
        assert cached.read_bytes() == b"persisted"


# ═══════════════════════════════════════════════════════════════════════
# Scene Renderer — Incremental Behavior
# ═══════════════════════════════════════════════════════════════════════


class TestSceneRendererIncremental:
    """Core incremental rendering: only changed scenes are rendered."""

    def test_first_render_is_cache_miss(self, tmp_path):
        renderer = SceneRenderer(
            cache=MediaCache(cache_dir=tmp_path / "cache"),
            render_fn=_mock_render_fn,
        )
        ir = _make_scene_ir("hook", "What is Redis?")

        result = renderer.render_scene(ir, output_dir=tmp_path / "render")

        assert not result.cached
        assert result.video_path.exists()
        assert result.error is None

    def test_second_render_is_cache_hit(self, tmp_path):
        cache = MediaCache(cache_dir=tmp_path / "cache")
        renderer = SceneRenderer(cache=cache, render_fn=_mock_render_fn)
        ir = _make_scene_ir("hook", "What is Redis?")

        result1 = renderer.render_scene(ir, output_dir=tmp_path / "render")
        result2 = renderer.render_scene(ir, output_dir=tmp_path / "render")

        assert not result1.cached
        assert result2.cached
        assert result1.content_hash == result2.content_hash

    def test_content_change_triggers_rerender(self, tmp_path):
        cache = MediaCache(cache_dir=tmp_path / "cache")
        renderer = SceneRenderer(cache=cache, render_fn=_mock_render_fn)
        output_dir = tmp_path / "render"

        ir1 = _make_scene_ir("hook", "Original text")
        ir2 = _make_scene_ir("hook", "Modified text")

        result1 = renderer.render_scene(ir1, output_dir=output_dir)
        result2 = renderer.render_scene(ir2, output_dir=output_dir)

        assert not result1.cached
        assert not result2.cached  # Different content → re-render
        assert result1.content_hash != result2.content_hash

    def test_batch_render_only_changes_recompute(self, tmp_path):
        """The critical test: render 3 scenes, change 1, only 1 re-renders."""
        cache = MediaCache(cache_dir=tmp_path / "cache")
        renderer = SceneRenderer(cache=cache, render_fn=_mock_render_fn)
        output_dir = tmp_path / "render"

        scenes = [
            _make_scene_ir("hook", "Hook text"),
            _make_scene_ir("graph", "Graph explanation"),
            _make_scene_ir("cards", "Summary cards"),
        ]

        # First render: all 3 are cache misses
        results1, stats1 = renderer.render_scenes(scenes, output_dir=output_dir)
        assert stats1.cache_hits == 0
        assert stats1.renders == 3

        # Change only scene 2 (graph)
        scenes[1] = _make_scene_ir("graph", "MODIFIED graph explanation")

        # Second render: 2 cache hits, 1 re-render
        results2, stats2 = renderer.render_scenes(scenes, output_dir=output_dir)
        assert stats2.cache_hits == 2  # hook and cards cached
        assert stats2.renders == 1     # only graph re-rendered

        # Verify which scenes were cached
        assert results2[0].cached   # hook
        assert not results2[1].cached  # graph (changed)
        assert results2[2].cached   # cards

    def test_scene_hashes_independent(self, tmp_path):
        """Changing scene A doesn't affect scene B's hash."""
        cache = MediaCache(cache_dir=tmp_path / "cache")
        renderer = SceneRenderer(cache=cache, render_fn=_mock_render_fn)

        ir_a = _make_scene_ir("hook", "text A")
        ir_b = _make_scene_ir("graph", "text B")

        result_a1 = renderer.render_scene(ir_a, output_dir=tmp_path / "r1")
        result_b1 = renderer.render_scene(ir_b, output_dir=tmp_path / "r1")

        # Change A
        ir_a2 = _make_scene_ir("hook", "text A MODIFIED")
        result_a2 = renderer.render_scene(ir_a2, output_dir=tmp_path / "r2")
        result_b2 = renderer.render_scene(ir_b, output_dir=tmp_path / "r2")

        assert result_a1.content_hash != result_a2.content_hash
        assert result_b1.content_hash == result_b2.content_hash
        assert result_b2.cached  # B reused from cache


# ═══════════════════════════════════════════════════════════════════════
# Concat Renderer
# ═══════════════════════════════════════════════════════════════════════


class TestConcatRenderer:
    """Composition caching and composite hash behavior."""

    def test_composite_hash_deterministic(self):
        h1 = _composite_hash(["a", "b", "c"], [8, 8])
        h2 = _composite_hash(["a", "b", "c"], [8, 8])
        assert h1 == h2

    def test_composite_hash_changes_with_scene_hash(self):
        h1 = _composite_hash(["a", "b", "c"], [8, 8])
        h2 = _composite_hash(["a", "X", "c"], [8, 8])
        assert h1 != h2

    def test_composite_hash_changes_with_overlap(self):
        h1 = _composite_hash(["a", "b"], [8, 8])
        h2 = _composite_hash(["a", "b"], [12, 8])
        assert h1 != h2

    def test_single_scene_copy(self, tmp_path):
        concat = ConcatRenderer(cache_dir=tmp_path / "concat")
        scene = tmp_path / "scene.mp4"
        scene.write_bytes(b"single scene video")

        output = tmp_path / "output.mp4"
        result = concat.compose(
            scene_paths=[scene],
            scene_hashes=["hash1"],
            overlaps=[],
            output_path=output,
        )

        assert result.output_path.exists()
        assert not result.cached  # First time
        assert result.error is None

    def test_single_scene_cached(self, tmp_path):
        concat = ConcatRenderer(cache_dir=tmp_path / "concat")
        scene = tmp_path / "scene.mp4"
        scene.write_bytes(b"single scene video")

        output1 = tmp_path / "out1.mp4"
        output2 = tmp_path / "out2.mp4"

        concat.compose([scene], ["hash1"], [], output1)
        result2 = concat.compose([scene], ["hash1"], [], output2)

        assert result2.cached

    def test_compose_result_caches_by_scene_sequence(self, tmp_path):
        """Single-scene compose (copy mode) — same hash → cached."""
        concat = ConcatRenderer(cache_dir=tmp_path / "concat")
        s1 = tmp_path / "s1.mp4"
        s1.write_bytes(b"scene 1")

        out1 = tmp_path / "out1.mp4"
        out2 = tmp_path / "out2.mp4"

        # First compose (single scene → copy)
        r1 = concat.compose([s1], ["h1"], [], out1)
        # Same scene sequence → cached
        r2 = concat.compose([s1], ["h1"], [], out2)

        assert not r1.cached
        assert r2.cached


# ═══════════════════════════════════════════════════════════════════════
# End-to-End Incremental Flow
# ═══════════════════════════════════════════════════════════════════════


class TestEndToEndIncremental:
    """Full incremental render flow: scene render → compose → verify."""

    def test_incremental_full_flow(self, tmp_path):
        """Render 3 scenes, change 1, recompose — only 1 scene re-rendered."""
        cache = MediaCache(cache_dir=tmp_path / "cache")
        renderer = SceneRenderer(cache=cache, render_fn=_mock_render_fn)
        concat = ConcatRenderer(cache_dir=tmp_path / "concat")
        output_dir = tmp_path / "render"

        # Initial scenes
        scenes = [
            _make_scene_ir("hook", "Hook text", 150),
            _make_scene_ir("graph", "Graph text", 300),
            _make_scene_ir("cards", "Cards text", 100),
        ]

        # Render all scenes
        results, stats = renderer.render_scenes(scenes, output_dir=output_dir)
        assert stats.renders == 3
        assert stats.cache_hits == 0

        scene_paths = [r.video_path for r in results]
        scene_hashes = [r.content_hash for r in results]

        # Compose
        output1 = tmp_path / "final1.mp4"
        c1 = concat.compose(scene_paths, scene_hashes, [8, 8], output1)

        # Now change only the graph scene
        scenes[1] = _make_scene_ir("graph", "MODIFIED graph text", 300)
        results2, stats2 = renderer.render_scenes(scenes, output_dir=output_dir)

        assert stats2.cache_hits == 2  # hook + cards
        assert stats2.renders == 1     # only graph

        scene_paths2 = [r.video_path for r in results2]
        scene_hashes2 = [r.content_hash for r in results2]

        # Verify graph hash changed, others unchanged
        assert scene_hashes2[0] == scene_hashes[0]  # hook same
        assert scene_hashes2[1] != scene_hashes[1]  # graph changed
        assert scene_hashes2[2] == scene_hashes[2]  # cards same

        # Compose again — different scene sequence → re-compose
        output2 = tmp_path / "final2.mp4"
        c2 = concat.compose(scene_paths2, scene_hashes2, [8, 8], output2)

        assert not c1.cached  # First compose
        assert c1.composite_hash != c2.composite_hash  # Different scenes

    def test_no_change_full_reuse(self, tmp_path):
        """If nothing changes, scene renders are all cached."""
        cache = MediaCache(cache_dir=tmp_path / "cache")
        renderer = SceneRenderer(cache=cache, render_fn=_mock_render_fn)
        output_dir = tmp_path / "render"

        scenes = [
            _make_scene_ir("hook", "Hook"),
            _make_scene_ir("graph", "Graph"),
        ]

        # First render
        r1, s1 = renderer.render_scenes(scenes, output_dir=output_dir)
        assert s1.renders == 2
        assert s1.cache_hits == 0

        # Second render — everything cached
        r2, s2 = renderer.render_scenes(scenes, output_dir=output_dir)
        assert s2.cache_hits == 2
        assert s2.renders == 0

        # Hashes identical
        assert [r.content_hash for r in r1] == [r.content_hash for r in r2]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
