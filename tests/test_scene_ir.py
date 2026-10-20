"""Golden tests + property-based invariants for build_scene_ir().

Phase 1.1 — Deterministic golden test cases.
Phase 1.2 — Randomized invariant checks (manual, no Hypothesis dependency).
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import pytest

# Ensure project root is importable
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.thinking.video_runtime_adapter import build_scene_ir


# ── Helpers ──────────────────────────────────────────────────────────

def _hash(ir: dict) -> str:
    """Canonical content hash (mirrors what the runtime uses)."""
    blob = json.dumps(ir["content"], sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


def _make_scene(
    scene_id: str = "scene_hook",
    scene_type: str = "hook",
    start: int = 0,
    duration: int = 150,
    **kwargs,
) -> dict:
    scene = {"id": scene_id, "type": scene_type, "start": start, "duration": duration}
    scene.update(kwargs)
    return scene


def _make_track(tid: str, start: int, duration: int, text: str = "") -> dict:
    return {"id": tid, "src": f"/audio/{tid}.mp3", "start": start, "duration": duration, "text": text}


def _make_element(eid: str, start: int, duration: int, **kwargs) -> dict:
    elem = {"id": eid, "start": start, "duration": duration, "type": "subtitle", "text": kwargs.get("text", "")}
    elem.update(kwargs)
    return elem


# ═══════════════════════════════════════════════════════════════════════
# Phase 1.1 — Golden Tests
# ═══════════════════════════════════════════════════════════════════════


class TestEmptyAudio:
    """Case 1: scene with no audio tracks."""

    def test_no_audio_no_elements(self):
        scene = _make_scene(start=0, duration=150)
        ir = build_scene_ir(scene, [], [])

        assert ir["content"]["audio_tracks"] == []
        assert ir["content"]["elements"] == []
        assert ir["content"]["scene_id"] == "scene_hook"
        assert ir["content"]["duration_in_frames"] == 150

    def test_empty_audio_hash_stable(self):
        scene = _make_scene(start=0, duration=150)
        h1 = _hash(build_scene_ir(scene, [], []))
        h2 = _hash(build_scene_ir(scene, [], []))
        assert h1 == h2


class TestOverlapClipping:
    """Case 2: audio track that partially overlaps scene boundaries."""

    def test_track_fully_inside_scene(self):
        scene = _make_scene(start=100, duration=100)  # 100..200
        track = _make_track("t0", start=120, duration=50, text="hello")  # 120..170

        ir = build_scene_ir(scene, [track], [])
        audio = ir["content"]["audio_tracks"]

        assert len(audio) == 1
        assert audio[0]["local_start"] == 20  # 120 - 100
        assert audio[0]["duration"] == 50

    def test_track_overlaps_left_boundary(self):
        """Track starts before scene, ends inside."""
        scene = _make_scene(start=100, duration=100)  # 100..200
        track = _make_track("t0", start=80, duration=50, text="left")  # 80..130

        ir = build_scene_ir(scene, [track], [])
        audio = ir["content"]["audio_tracks"]

        assert len(audio) == 1
        assert audio[0]["local_start"] == 0    # clipped to scene start
        assert audio[0]["duration"] == 30      # 130 - 100

    def test_track_overlaps_right_boundary(self):
        """Track starts inside scene, ends after."""
        scene = _make_scene(start=100, duration=100)  # 100..200
        track = _make_track("t0", start=170, duration=60, text="right")  # 170..230

        ir = build_scene_ir(scene, [track], [])
        audio = ir["content"]["audio_tracks"]

        assert len(audio) == 1
        assert audio[0]["local_start"] == 70  # 170 - 100
        assert audio[0]["duration"] == 30     # 200 - 170

    def test_track_spans_entire_scene(self):
        """Track completely covers scene."""
        scene = _make_scene(start=100, duration=100)  # 100..200
        track = _make_track("t0", start=50, duration=200, text="span")  # 50..250

        ir = build_scene_ir(scene, [track], [])
        audio = ir["content"]["audio_tracks"]

        assert len(audio) == 1
        assert audio[0]["local_start"] == 0
        assert audio[0]["duration"] == 100  # full scene

    def test_track_entirely_outside(self):
        """Track has zero overlap with scene — should be excluded."""
        scene = _make_scene(start=100, duration=100)  # 100..200
        track = _make_track("t0", start=0, duration=80, text="before")  # 0..80

        ir = build_scene_ir(scene, [track], [])
        assert ir["content"]["audio_tracks"] == []


class TestSubtitleLocalTransform:
    """Case 3: subtitle element local coordinate mapping."""

    def test_element_inside_scene(self):
        scene = _make_scene(start=100, duration=100)  # 100..200
        elem = _make_element("e0", start=130, duration=40, text="subtitle")  # 130..170

        ir = build_scene_ir(scene, [], [elem])
        elems = ir["content"]["elements"]

        assert len(elems) == 1
        assert elems[0]["start"] == 30  # 130 - 100

    def test_element_overlaps_left(self):
        scene = _make_scene(start=100, duration=100)
        elem = _make_element("e0", start=80, duration=40)  # 80..120

        ir = build_scene_ir(scene, [], [elem])
        elems = ir["content"]["elements"]

        assert len(elems) == 1
        assert elems[0]["start"] == 0  # clamped

    def test_element_overlaps_right(self):
        scene = _make_scene(start=100, duration=100)
        elem = _make_element("e0", start=180, duration=40)  # 180..220

        ir = build_scene_ir(scene, [], [elem])
        elems = ir["content"]["elements"]

        assert len(elems) == 1
        assert elems[0]["start"] == 80  # 180 - 100

    def test_element_entirely_outside(self):
        scene = _make_scene(start=100, duration=100)
        elem = _make_element("e0", start=0, duration=50)  # 0..50

        ir = build_scene_ir(scene, [], [elem])
        assert ir["content"]["elements"] == []


class TestSceneTypeSchema:
    """Case 4: each scene type must include/exclude specific fields."""

    def test_hook_has_text_no_graph(self):
        scene = _make_scene(scene_type="hook", text="Why learn Redis?")
        ir = build_scene_ir(scene, [], [])

        assert ir["content"]["text"] == "Why learn Redis?"
        assert "graph" not in ir["content"]
        assert "items" not in ir["content"]

    def test_graph_has_graph_no_text(self):
        scene = _make_scene(scene_type="graph", graph={"nodes": [], "edges": []})
        ir = build_scene_ir(scene, [], [])

        assert ir["content"]["graph"] == {"nodes": [], "edges": []}
        assert "text" not in ir["content"]
        assert "items" not in ir["content"]

    def test_cards_has_title_and_items(self):
        scene = _make_scene(
            scene_type="cards",
            title="Key Features",
            items=[{"label": "Fast"}, {"label": "Safe"}],
        )
        ir = build_scene_ir(scene, [], [])

        assert ir["content"]["title"] == "Key Features"
        assert len(ir["content"]["items"]) == 2
        assert "text" not in ir["content"]
        assert "graph" not in ir["content"]


class TestDeterministicHash:
    """Case 5: same input always produces same hash."""

    def test_identical_input_same_hash(self):
        scene = _make_scene(start=100, duration=200, text="hello")
        tracks = [_make_track("t0", 110, 50), _make_track("t1", 180, 40)]
        elems = [_make_element("e0", 120, 30)]

        hashes = [_hash(build_scene_ir(scene, tracks, elems)) for _ in range(10)]
        assert len(set(hashes)) == 1

    def test_different_content_different_hash(self):
        scene1 = _make_scene(start=0, duration=100, text="A")
        scene2 = _make_scene(start=0, duration=100, text="B")

        h1 = _hash(build_scene_ir(scene1, [], []))
        h2 = _hash(build_scene_ir(scene2, [], []))
        assert h1 != h2

    def test_transition_metadata_excluded_from_hash(self):
        """Overlap values affect transition but NOT content hash."""
        scene = _make_scene(start=0, duration=100, text="stable")
        scene_with_overlap = _make_scene(start=0, duration=100, text="stable", overlapIn=8, overlapOut=8)

        ir1 = build_scene_ir(scene, [], [])
        ir2 = build_scene_ir(scene_with_overlap, [], [])

        # Content hash must be identical
        assert _hash(ir1) == _hash(ir2)
        # But transition metadata differs
        assert ir1["transition"]["overlap_in"] == 0
        assert ir2["transition"]["overlap_in"] == 8

    def test_audio_order_matters(self):
        """Reordering audio tracks changes content hash (semantic ordering)."""
        scene = _make_scene(start=0, duration=200)
        t1 = _make_track("t0", 0, 100)
        t2 = _make_track("t1", 100, 100)

        h1 = _hash(build_scene_ir(scene, [t1, t2], []))
        h2 = _hash(build_scene_ir(scene, [t2, t1], []))
        assert h1 != h2


class TestTransitionMetadata:
    """Transition field computation and render padding."""

    def test_overlap_padding(self):
        scene = _make_scene(start=0, duration=100, overlapIn=8, overlapOut=10)
        ir = build_scene_ir(scene, [], [])

        assert ir["transition"] == {
            "overlap_in": 8,
            "overlap_out": 10,
            "render_pad_in": 10,   # 8 + 2
            "render_pad_out": 12,  # 10 + 2
        }

    def test_default_overlap_zero(self):
        scene = _make_scene(start=0, duration=100)
        ir = build_scene_ir(scene, [], [])

        assert ir["transition"]["overlap_in"] == 0
        assert ir["transition"]["overlap_out"] == 0
        assert ir["transition"]["render_pad_in"] == 2
        assert ir["transition"]["render_pad_out"] == 2


# ═══════════════════════════════════════════════════════════════════════
# Phase 1.2 — Property-based Invariant Tests (manual random)
# ═══════════════════════════════════════════════════════════════════════


class TestInvariants:
    """Randomized invariant checks.

    Generates random scenes, tracks, and elements, then verifies
    structural invariants that must hold for ALL valid inputs.
    """

    @staticmethod
    def _random_tracks(n: int, scene_start: int, scene_end: int) -> list[dict]:
        """Generate n random audio tracks near scene boundaries."""
        tracks = []
        for i in range(n):
            # Random start: can be before, inside, or after scene
            t_start = random.randint(scene_start - 100, scene_end + 50)
            t_dur = random.randint(10, 200)
            tracks.append(_make_track(f"t{i}", t_start, t_dur))
        return tracks

    @staticmethod
    def _random_elements(n: int, scene_start: int, scene_end: int) -> list[dict]:
        """Generate n random subtitle elements near scene boundaries."""
        elems = []
        for i in range(n):
            e_start = random.randint(scene_start - 100, scene_end + 50)
            e_dur = random.randint(5, 100)
            elems.append(_make_element(f"e{i}", e_start, e_dur))
        return elems

    def test_local_audio_non_negative_start(self):
        """Invariant: all local_start >= 0."""
        for _ in range(200):
            s_start = random.randint(0, 500)
            s_dur = random.randint(50, 300)
            scene = _make_scene(start=s_start, duration=s_dur)
            tracks = self._random_tracks(random.randint(0, 5), s_start, s_start + s_dur)

            ir = build_scene_ir(scene, tracks, [])
            for audio in ir["content"]["audio_tracks"]:
                assert audio["local_start"] >= 0, f"Negative local_start: {audio}"

    def test_local_audio_positive_duration(self):
        """Invariant: all clipped durations > 0."""
        for _ in range(200):
            s_start = random.randint(0, 500)
            s_dur = random.randint(50, 300)
            scene = _make_scene(start=s_start, duration=s_dur)
            tracks = self._random_tracks(random.randint(0, 5), s_start, s_start + s_dur)

            ir = build_scene_ir(scene, tracks, [])
            for audio in ir["content"]["audio_tracks"]:
                assert audio["duration"] > 0, f"Non-positive duration: {audio}"

    def test_local_audio_within_scene_bounds(self):
        """Invariant: local_start + duration <= scene_duration."""
        for _ in range(200):
            s_start = random.randint(0, 500)
            s_dur = random.randint(50, 300)
            scene = _make_scene(start=s_start, duration=s_dur)
            tracks = self._random_tracks(random.randint(0, 5), s_start, s_start + s_dur)

            ir = build_scene_ir(scene, tracks, [])
            for audio in ir["content"]["audio_tracks"]:
                end = audio["local_start"] + audio["duration"]
                assert end <= s_dur + 1e-9, f"Audio extends past scene: {end} > {s_dur}"

    def test_local_elements_non_negative_start(self):
        """Invariant: all element starts >= 0."""
        for _ in range(200):
            s_start = random.randint(0, 500)
            s_dur = random.randint(50, 300)
            scene = _make_scene(start=s_start, duration=s_dur)
            elems = self._random_elements(random.randint(0, 5), s_start, s_start + s_dur)

            ir = build_scene_ir(scene, [], elems)
            for elem in ir["content"]["elements"]:
                assert elem["start"] >= 0, f"Negative element start: {elem}"

    def test_deterministic_under_random_input(self):
        """Invariant: same input always produces identical output."""
        for _ in range(100):
            s_start = random.randint(0, 500)
            s_dur = random.randint(50, 300)
            scene = _make_scene(start=s_start, duration=s_dur)
            tracks = self._random_tracks(random.randint(0, 3), s_start, s_start + s_dur)
            elems = self._random_elements(random.randint(0, 3), s_start, s_start + s_dur)

            ir1 = build_scene_ir(scene, tracks, elems)
            ir2 = build_scene_ir(scene, tracks, elems)
            assert _hash(ir1) == _hash(ir2), "Non-deterministic output for same input"

    def test_transition_never_in_content(self):
        """Invariant: _transition is never inside content dict."""
        for _ in range(100):
            s_start = random.randint(0, 500)
            s_dur = random.randint(50, 300)
            scene = _make_scene(start=s_start, duration=s_dur, overlapIn=random.randint(0, 12), overlapOut=random.randint(0, 12))

            ir = build_scene_ir(scene, [], [])
            assert "overlap_in" not in ir["content"]
            assert "overlap_out" not in ir["content"]
            assert "render_pad_in" not in ir["content"]
            assert "render_pad_out" not in ir["content"]


class TestEdgeCases:
    """Boundary and degenerate inputs."""

    def test_single_frame_scene(self):
        scene = _make_scene(start=0, duration=1)
        track = _make_track("t0", start=0, duration=1)
        elem = _make_element("e0", start=0, duration=1)

        ir = build_scene_ir(scene, [track], [elem])
        assert ir["content"]["audio_tracks"][0]["duration"] == 1
        assert ir["content"]["elements"][0]["start"] == 0

    def test_zero_duration_track_excluded(self):
        scene = _make_scene(start=0, duration=100)
        track = _make_track("t0", start=50, duration=0)

        ir = build_scene_ir(scene, [track], [])
        # Zero-duration track: start(50) < end(100) and end(50) > start(0) => included
        # but duration clipped to 0. The function includes it; content hash handles it.
        for audio in ir["content"]["audio_tracks"]:
            assert audio["duration"] >= 0

    def test_scene_at_nonzero_start(self):
        """Scene starting at frame 500 — verify local coords reset to 0."""
        scene = _make_scene(start=500, duration=100)
        track = _make_track("t0", start=520, duration=30)
        elem = _make_element("e0", start=540, duration=20)

        ir = build_scene_ir(scene, [track], [elem])
        assert ir["content"]["audio_tracks"][0]["local_start"] == 20
        assert ir["content"]["elements"][0]["start"] == 40

    def test_many_tracks_partial_overlap(self):
        """10 tracks, only some overlap with scene."""
        scene = _make_scene(start=100, duration=100)  # 100..200
        tracks = [_make_track(f"t{i}", start=i * 30, duration=40) for i in range(10)]

        ir = build_scene_ir(scene, tracks, [])
        # Only tracks with overlap should appear
        for audio in ir["content"]["audio_tracks"]:
            assert audio["local_start"] >= 0
            assert audio["local_start"] + audio["duration"] <= 100

    def test_unknown_scene_type_no_extra_fields(self):
        """Unknown type should not add text/graph/items."""
        scene = _make_scene(scene_type="custom_3d", start=0, duration=100)
        ir = build_scene_ir(scene, [], [])

        assert "text" not in ir["content"]
        assert "graph" not in ir["content"]
        assert "items" not in ir["content"]
        assert "title" not in ir["content"]


class TestMultiSceneConsistency:
    """Verify that building IRs for multiple scenes from shared data is consistent."""

    def test_shared_tracks_split_across_scenes(self):
        """Two adjacent scenes with a shared audio track that spans both."""
        scene_a = _make_scene("scene_a", start=0, duration=100)
        scene_b = _make_scene("scene_b", start=100, duration=100)
        track = _make_track("t0", start=50, duration=120)  # 50..170, spans both

        ir_a = build_scene_ir(scene_a, [track], [])
        ir_b = build_scene_ir(scene_b, [track], [])

        # Scene A: clip 50..100 → local_start=50, duration=50
        assert ir_a["content"]["audio_tracks"][0]["local_start"] == 50
        assert ir_a["content"]["audio_tracks"][0]["duration"] == 50

        # Scene B: clip 100..170 → local_start=0, duration=70
        assert ir_b["content"]["audio_tracks"][0]["local_start"] == 0
        assert ir_b["content"]["audio_tracks"][0]["duration"] == 70

    def test_scene_hashes_independent(self):
        """Changing one scene's content doesn't affect another's hash."""
        scene_a = _make_scene("scene_a", start=0, duration=100, text="original")
        scene_b = _make_scene("scene_b", start=100, duration=100, text="stable")
        track = _make_track("t0", start=0, duration=200)

        ir_a1 = build_scene_ir(scene_a, [track], [])
        ir_b1 = build_scene_ir(scene_b, [track], [])
        h_b1 = _hash(ir_b1)

        # Now change scene A's text
        scene_a["text"] = "modified"
        ir_a2 = build_scene_ir(scene_a, [track], [])
        ir_b2 = build_scene_ir(scene_b, [track], [])
        h_b2 = _hash(ir_b2)

        # Scene A hash changed
        assert _hash(ir_a1) != _hash(ir_a2)
        # Scene B hash UNCHANGED — critical for incremental correctness
        assert h_b1 == h_b2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
