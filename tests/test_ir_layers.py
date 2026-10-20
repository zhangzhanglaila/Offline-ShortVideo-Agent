"""Tests for IR Layer Hierarchy — four intermediate representations."""

import pytest
from compiler.thinking.ir_layers import (
    SemanticIR, TemporalIR, RenderIR, ExecutionIR,
    IRTransformer, DeterminismConfig, DeterminismChecker,
)
from compiler.thinking.state import VideoProjectState, ModuleState, ScriptSentence


@pytest.fixture
def semantic_ir():
    return SemanticIR(
        module_id="mod_00",
        topic="Redis为什么快",
        title="Introduction",
        sentences=[
            ScriptSentence(text="Redis是一个内存数据库", index=0),
            ScriptSentence(text="它使用单线程模型", index=1),
        ],
        hook_strategy="question",
        pacing="fast",
        key_concepts=["内存", "单线程"],
    )


def make_state():
    s = VideoProjectState(topic="Test")
    mod = ModuleState(title="Mod", index=0)
    mod.script.append(ScriptSentence(text="Hello", index=0))
    s.modules.append(mod)
    s.current_module_index = 0
    return s


class TestSemanticIR:
    def test_content_hash_deterministic(self, semantic_ir):
        h1 = semantic_ir.content_hash()
        h2 = semantic_ir.content_hash()
        assert h1 == h2

    def test_content_hash_changes_on_edit(self, semantic_ir):
        h1 = semantic_ir.content_hash()
        semantic_ir.sentences[0] = ScriptSentence(text="Changed", index=0)
        h2 = semantic_ir.content_hash()
        assert h1 != h2

    def test_empty_hash(self):
        ir = SemanticIR()
        assert len(ir.content_hash()) == 32


class TestTemporalIR:
    def test_timing_hash_empty(self):
        ir = TemporalIR()
        assert ir.timing_hash() == ""

    def test_timing_hash_with_timeline(self, semantic_ir):
        temporal = IRTransformer.semantic_to_temporal(semantic_ir)
        h = temporal.timing_hash()
        assert len(h) == 32


class TestRenderIR:
    def test_layout_hash(self):
        ir = RenderIR(layout={"elements": [{"id": "e1"}]})
        h = ir.layout_hash()
        assert len(h) == 32

    def test_layout_hash_deterministic(self):
        ir = RenderIR(layout={"a": 1, "b": 2})
        assert ir.layout_hash() == ir.layout_hash()


class TestExecutionIR:
    def test_default_values(self):
        ir = ExecutionIR()
        assert ir.patch_count == 0


class TestIRTransformer:
    def test_semantic_to_temporal(self, semantic_ir):
        temporal = IRTransformer.semantic_to_temporal(semantic_ir, fps=30)
        assert temporal.module_id == "mod_00"
        assert temporal.fps == 30
        assert temporal.timeline is not None
        assert temporal.total_duration_frames > 0

    def test_audio_track_created(self, semantic_ir):
        temporal = IRTransformer.semantic_to_temporal(semantic_ir)
        audio_tracks = [t for t in temporal.timeline.tracks if t.track_type == "audio"]
        assert len(audio_tracks) == 1
        assert len(audio_tracks[0].clips) == 2

    def test_subtitle_synced_to_audio(self, semantic_ir):
        temporal = IRTransformer.semantic_to_temporal(semantic_ir)
        audio_track = next(t for t in temporal.timeline.tracks if t.track_type == "audio")
        sub_track = next(t for t in temporal.timeline.tracks if t.track_type == "subtitle")
        for a_clip, s_clip in zip(audio_track.clips, sub_track.clips):
            assert a_clip.start == s_clip.start
            assert a_clip.duration == s_clip.duration

    def test_temporal_to_render(self, semantic_ir):
        temporal = IRTransformer.semantic_to_temporal(semantic_ir)
        render = IRTransformer.temporal_to_render(temporal)
        assert render.module_id == "mod_00"
        assert render.fps == 30
        assert "elements" in render.layout

    def test_temporal_to_render_empty_timeline(self):
        temporal = TemporalIR(module_id="m")
        render = IRTransformer.temporal_to_render(temporal)
        assert render.module_id == "m"

    def test_state_to_semantic(self):
        state = make_state()
        mod = state.get_current_module()
        sem = IRTransformer.state_to_semantic(state, mod.id)
        assert sem.topic == "Test"
        assert len(sem.sentences) == 1

    def test_state_to_semantic_missing_module(self):
        state = VideoProjectState(topic="Test")
        sem = IRTransformer.state_to_semantic(state, "nonexistent")
        assert sem.module_id == "nonexistent"


class TestDeterminismConfig:
    def test_defaults(self):
        cfg = DeterminismConfig()
        assert cfg.topological_sort == "bfs"
        assert cfg.cache_enabled is True


class TestDeterminismChecker:
    def test_deterministic_transform(self, semantic_ir):
        def transform(ir):
            temporal = IRTransformer.semantic_to_temporal(ir)
            # Use content hash instead of timing hash (which includes timestamps)
            return temporal.timeline.tracks[0].clips[0].text

        assert DeterminismChecker.check_transform(semantic_ir, transform) is True
