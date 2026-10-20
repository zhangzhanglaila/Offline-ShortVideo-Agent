"""P9 — Full Compiler Pipeline Tests.

Verifies the complete IR pipeline:
  Intent IR → Narrative IR → Scene IRs → Timeline IR → Render IR

Tests:
  - Narrative → Scene: beat types map to correct visual grammar
  - Narrative → Scene: long text auto-splits
  - Scene → Timeline: frame allocation, track packing, overlaps
  - Timeline → Render: command generation, concat structure
  - End-to-end: Intent → Render IR (full pipeline)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.ir.intent_ir import IntentIR, Tone
from compiler.ir.narrative_ir import (
    NarrativeIR, Beat, BeatType, TransitionType,
    HookBeat, ProblemBeat, RevealBeat, CTABeat,
)
from compiler.ir.timeline_ir import TimelineIR, Track, TrackType, Transition, TransitionEffect
from compiler.ir.render_ir import RenderIR, CommandType
from compiler.pass_intent_to_narrative import IntentToNarrativePass
from compiler.pass_narrative_to_scene import (
    NarrativeToScenePass, SceneIR, _split_text,
)
from compiler.pass_scene_to_timeline import SceneToTimelinePass
from compiler.pass_timeline_to_render import TimelineToRenderPass
from compiler.base import PassPipeline


# ═══════════════════════════════════════════════════════════════════════
# Text Splitting
# ═══════════════════════════════════════════════════════════════════════


class TestTextSplitting:
    """Long text auto-split at sentence boundaries."""

    def test_short_text_unchanged(self):
        chunks = _split_text("Hello world", 60)
        assert chunks == ["Hello world"]

    def test_long_text_splits(self):
        text = "这是一段很长的文字。" * 10  # ~100 chars
        chunks = _split_text(text, 30)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 30 + 5  # some tolerance for split point

    def test_split_preserves_content(self):
        text = "第一句。第二句。第三句。"
        chunks = _split_text(text, 10)
        reconstructed = "".join(chunks)
        # All original characters should be present
        for char in text:
            if char not in " \n":
                assert char in reconstructed


# ═══════════════════════════════════════════════════════════════════════
# Narrative → Scene
# ═══════════════════════════════════════════════════════════════════════


class TestNarrativeToScene:
    """Narrative → Scene pass behavior."""

    def test_produces_scene_list(self):
        narrative = NarrativeIR(beats=(
            HookBeat("What is Redis?"),
            ProblemBeat("Slow queries"),
            RevealBeat("In-memory"),
            CTABeat("Follow"),
        ))
        pass_ = NarrativeToScenePass()
        result = pass_.run(narrative)
        scenes = result.output
        assert isinstance(scenes, list)
        assert len(scenes) >= 4

    def test_hook_beat_maps_to_hook_scene(self):
        narrative = NarrativeIR(beats=(
            HookBeat("What is Redis?"),
            CTABeat("Follow"),
        ))
        pass_ = NarrativeToScenePass()
        scenes = pass_.run(narrative).output
        assert scenes[0].scene_type == "hook"
        assert scenes[0].camera_motion == "zoom_in"

    def test_reveal_beat_maps_to_reveal_scene(self):
        narrative = NarrativeIR(beats=(
            HookBeat("Question"),
            RevealBeat("Answer"),
            CTABeat("Follow"),
        ))
        pass_ = NarrativeToScenePass()
        scenes = pass_.run(narrative).output
        reveal = [s for s in scenes if s.scene_type == "reveal"]
        assert len(reveal) == 1

    def test_cta_beat_maps_to_cta_scene(self):
        narrative = NarrativeIR(beats=(
            HookBeat("Question"),
            CTABeat("Follow"),
        ))
        pass_ = NarrativeToScenePass()
        scenes = pass_.run(narrative).output
        assert scenes[-1].scene_type == "cta"

    def test_deterministic(self):
        narrative = NarrativeIR(beats=(
            HookBeat("What is Redis?"),
            ProblemBeat("Slow queries"),
            RevealBeat("In-memory"),
            CTABeat("Follow"),
        ))
        pass_ = NarrativeToScenePass()
        r1 = pass_.run(narrative)
        r2 = pass_.run(narrative)
        assert r1.output_hash == r2.output_hash

    def test_different_narrative_different_scenes(self):
        n1 = NarrativeIR(beats=(HookBeat("A"), CTABeat("B")))
        n2 = NarrativeIR(beats=(HookBeat("X"), CTABeat("Y")))
        pass_ = NarrativeToScenePass()
        s1 = pass_.run(n1).output
        s2 = pass_.run(n2).output
        assert s1[0].content_hash() != s2[0].content_hash()

    def test_scene_has_valid_duration(self):
        narrative = NarrativeIR(beats=(
            HookBeat("What is Redis?"),
            CTABeat("Follow"),
        ))
        pass_ = NarrativeToScenePass()
        scenes = pass_.run(narrative).output
        for scene in scenes:
            assert scene.duration_in_frames > 0

    def test_long_beat_splits(self):
        long_text = "这是一个很长的句子。" * 20
        narrative = NarrativeIR(beats=(
            HookBeat(long_text),
            CTABeat("Follow"),
        ))
        pass_ = NarrativeToScenePass(max_chars_per_scene=30)
        scenes = pass_.run(narrative).output
        hook_scenes = [s for s in scenes if s.scene_type == "hook"]
        assert len(hook_scenes) > 1


# ═══════════════════════════════════════════════════════════════════════
# Scene → Timeline
# ═══════════════════════════════════════════════════════════════════════


class TestSceneToTimeline:
    """Scene → Timeline pass behavior."""

    def _make_scenes(self) -> list[SceneIR]:
        return [
            SceneIR(
                scene_id="scene_0", scene_type="hook",
                text="What is Redis?", duration_in_frames=150,
                camera_motion="zoom_in", transition_after="fade",
            ),
            SceneIR(
                scene_id="scene_1", scene_type="graph",
                text="Slow queries", duration_in_frames=200,
                camera_motion="push_in", transition_after="fade",
            ),
            SceneIR(
                scene_id="scene_2", scene_type="cta",
                text="Follow", duration_in_frames=90,
                camera_motion="static", transition_after="cut",
            ),
        ]

    def test_produces_timeline(self):
        scenes = self._make_scenes()
        pass_ = SceneToTimelinePass()
        result = pass_.run(scenes)
        tl = result.output
        assert isinstance(tl, TimelineIR)

    def test_video_tracks_created(self):
        scenes = self._make_scenes()
        pass_ = SceneToTimelinePass()
        tl = pass_.run(scenes).output
        video_tracks = tl.tracks_of_type(TrackType.VIDEO)
        assert len(video_tracks) == 3

    def test_subtitle_tracks_created(self):
        scenes = self._make_scenes()
        pass_ = SceneToTimelinePass()
        tl = pass_.run(scenes).output
        sub_tracks = tl.tracks_of_type(TrackType.SUBTITLE)
        assert len(sub_tracks) == 3

    def test_audio_tracks_created(self):
        scenes = self._make_scenes()
        pass_ = SceneToTimelinePass()
        tl = pass_.run(scenes).output
        audio_tracks = tl.tracks_of_type(TrackType.AUDIO)
        assert len(audio_tracks) == 3

    def test_tracks_are_sequential(self):
        scenes = self._make_scenes()
        pass_ = SceneToTimelinePass()
        tl = pass_.run(scenes).output
        video_tracks = sorted(
            tl.tracks_of_type(TrackType.VIDEO),
            key=lambda t: t.start_frame,
        )
        # Each track starts after (or overlapping with) the previous
        for i in range(1, len(video_tracks)):
            assert video_tracks[i].start_frame <= video_tracks[i - 1].end_frame

    def test_transitions_created(self):
        scenes = self._make_scenes()
        pass_ = SceneToTimelinePass()
        tl = pass_.run(scenes).output
        # Should have transitions between scenes with "fade" transition
        assert len(tl.transitions) >= 1

    def test_duration_is_correct(self):
        scenes = self._make_scenes()
        pass_ = SceneToTimelinePass()
        tl = pass_.run(scenes).output
        # Total duration should be sum of scene durations minus overlaps
        total_scene_frames = sum(s.duration_in_frames for s in scenes)
        overlap_frames = sum(t.duration_frames for t in tl.transitions)
        expected = total_scene_frames - overlap_frames
        assert tl.duration_frames == expected

    def test_deterministic(self):
        scenes = self._make_scenes()
        pass_ = SceneToTimelinePass()
        r1 = pass_.run(scenes)
        r2 = pass_.run(scenes)
        assert r1.output_hash == r2.output_hash

    def test_empty_scenes_produces_valid_timeline(self):
        pass_ = SceneToTimelinePass()
        result = pass_.run([])
        tl = result.output
        assert tl.track_count >= 1

    def test_single_scene_no_transitions(self):
        scenes = [SceneIR(
            scene_id="s0", scene_type="hook",
            text="Hello", duration_in_frames=150,
        )]
        pass_ = SceneToTimelinePass()
        tl = pass_.run(scenes).output
        assert len(tl.transitions) == 0


# ═══════════════════════════════════════════════════════════════════════
# Timeline → Render
# ═══════════════════════════════════════════════════════════════════════


class TestTimelineToRender:
    """Timeline → Render pass behavior."""

    def test_produces_render_ir(self):
        tl = TimelineIR(
            tracks=(
                Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=150,
                       content={"scene_id": "s0", "text": "Hello"}),
                Track(track_id="v1", track_type=TrackType.VIDEO, layer=0, start_frame=142, end_frame=350,
                       content={"scene_id": "s1", "text": "World"}),
                Track(track_id="s0", track_type=TrackType.SUBTITLE, layer=1, start_frame=0, end_frame=150,
                       content={"text": "Hello"}),
                Track(track_id="s1", track_type=TrackType.SUBTITLE, layer=1, start_frame=142, end_frame=350,
                       content={"text": "World"}),
            ),
            transitions=(
                Transition(from_track="v0", to_track="v1", duration_frames=8),
            ),
        )
        pass_ = TimelineToRenderPass()
        result = pass_.run(tl)
        rir = result.output
        assert isinstance(rir, RenderIR)

    def test_multi_scene_has_concat(self):
        tl = TimelineIR(
            tracks=(
                Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=150,
                       content={"scene_id": "s0", "text": "A"}),
                Track(track_id="v1", track_type=TrackType.VIDEO, layer=0, start_frame=142, end_frame=350,
                       content={"scene_id": "s1", "text": "B"}),
            ),
            transitions=(
                Transition(from_track="v0", to_track="v1", duration_frames=8),
            ),
        )
        pass_ = TimelineToRenderPass()
        rir = pass_.run(tl).output
        concat_cmds = [c for c in rir.commands if c.command_type == CommandType.CONCAT]
        assert len(concat_cmds) == 1

    def test_single_scene_uses_copy(self):
        tl = TimelineIR(
            tracks=(
                Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=150,
                       content={"scene_id": "s0", "text": "A"}),
            ),
        )
        pass_ = TimelineToRenderPass()
        rir = pass_.run(tl).output
        copy_cmds = [c for c in rir.commands if c.command_type == CommandType.COPY]
        assert len(copy_cmds) == 1

    def test_subtitle_generates_composite(self):
        tl = TimelineIR(
            tracks=(
                Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=150,
                       content={"scene_id": "s0", "text": "A"}),
                Track(track_id="sub0", track_type=TrackType.SUBTITLE, layer=1, start_frame=0, end_frame=150,
                       content={"scene_id": "s0", "text": "A"}),
            ),
        )
        pass_ = TimelineToRenderPass()
        rir = pass_.run(tl).output
        composite_cmds = [c for c in rir.commands if c.command_type == CommandType.COMPOSITE]
        assert len(composite_cmds) >= 1

    def test_deterministic(self):
        tl = TimelineIR(
            tracks=(
                Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=150,
                       content={"scene_id": "s0", "text": "A"}),
            ),
        )
        pass_ = TimelineToRenderPass()
        r1 = pass_.run(tl)
        r2 = pass_.run(tl)
        assert r1.output_hash == r2.output_hash


# ═══════════════════════════════════════════════════════════════════════
# End-to-End Pipeline
# ═══════════════════════════════════════════════════════════════════════


class TestEndToEndPipeline:
    """Full pipeline: Intent → Render IR."""

    def test_full_pipeline(self):
        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45.0, audience="beginners",
        )

        pipeline = PassPipeline([
            IntentToNarrativePass(),
            NarrativeToScenePass(),
            SceneToTimelinePass(),
            TimelineToRenderPass(),
        ])

        results = pipeline.run(intent)
        assert len(results) == 4

        narrative = results[0].output
        scenes = results[1].output
        timeline = results[2].output
        render_ir = results[3].output

        assert isinstance(narrative, NarrativeIR)
        assert isinstance(scenes, list)
        assert isinstance(timeline, TimelineIR)
        assert isinstance(render_ir, RenderIR)

        # Narrative has beats
        assert narrative.beat_count >= 3

        # Scenes match beats
        assert len(scenes) >= narrative.beat_count

        # Timeline has tracks
        assert timeline.track_count >= len(scenes)

        # Render IR has commands
        assert render_ir.command_count >= 1

    def test_pipeline_deterministic(self):
        intent = IntentIR(
            topic="Redis", tone=Tone.DRAMATIC,
            target_duration=45.0, audience="beginners",
        )

        pipeline = PassPipeline([
            IntentToNarrativePass(),
            NarrativeToScenePass(),
            SceneToTimelinePass(),
            TimelineToRenderPass(),
        ])

        r1 = pipeline.run(intent)
        r2 = pipeline.run(intent)

        # All output hashes should match
        for a, b in zip(r1, r2):
            assert a.output_hash == b.output_hash

    def test_pipeline_with_different_intents(self):
        i1 = IntentIR(topic="Redis", tone=Tone.DRAMATIC, target_duration=45, audience="beginners")
        i2 = IntentIR(topic="MongoDB", tone=Tone.EDUCATIONAL, target_duration=30, audience="experts")

        pipeline = PassPipeline([
            IntentToNarrativePass(),
            NarrativeToScenePass(),
            SceneToTimelinePass(),
            TimelineToRenderPass(),
        ])

        r1 = pipeline.run(i1)
        r2 = pipeline.run(i2)

        # Final render IRs should differ
        assert r1[-1].output_hash != r2[-1].output_hash

    def test_pipeline_pass_names(self):
        pipeline = PassPipeline([
            IntentToNarrativePass(),
            NarrativeToScenePass(),
            SceneToTimelinePass(),
            TimelineToRenderPass(),
        ])
        assert pipeline.pass_names == [
            "intent_to_narrative",
            "narrative_to_scene",
            "scene_to_timeline",
            "timeline_to_render",
        ]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
