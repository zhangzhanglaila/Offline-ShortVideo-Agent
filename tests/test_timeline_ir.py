"""P7.4 — Timeline IR Tests.

Verifies:
  - Track construction and validation
  - Timeline construction and structural validation
  - Frame-accurate duration computation
  - Layer queries and temporal queries
  - Transition validation
  - Canonical form and content hashing
  - Edge cases
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.ir.timeline_ir import (
    TimelineIR, Track, TrackType, Transition, TransitionEffect,
)


# ═══════════════════════════════════════════════════════════════════════
# Track Construction
# ═══════════════════════════════════════════════════════════════════════


class TestTrackConstruction:
    """Track dataclass behavior."""

    def test_basic_track(self):
        t = Track(
            track_id="v0", track_type=TrackType.VIDEO,
            layer=0, start_frame=0, end_frame=150,
        )
        assert t.track_id == "v0"
        assert t.duration_frames == 150

    def test_frozen(self):
        t = Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=10)
        with pytest.raises(AttributeError):
            t.start_frame = 5  # type: ignore

    def test_negative_start_rejected(self):
        with pytest.raises(ValueError, match="start_frame"):
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=-1, end_frame=10)

    def test_end_before_start_rejected(self):
        with pytest.raises(ValueError, match="end_frame"):
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=10, end_frame=5)

    def test_equal_start_end_rejected(self):
        with pytest.raises(ValueError, match="end_frame"):
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=10, end_frame=10)

    def test_negative_layer_rejected(self):
        with pytest.raises(ValueError, match="layer"):
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=-1, start_frame=0, end_frame=10)

    def test_single_frame_track(self):
        t = Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=1)
        assert t.duration_frames == 1


class TestTrackOverlap:
    """Track overlap detection."""

    def test_adjacent_no_overlap(self):
        a = Track(track_id="a", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=10)
        b = Track(track_id="b", track_type=TrackType.VIDEO, layer=0, start_frame=10, end_frame=20)
        assert not a.overlaps(b)
        assert a.overlap_frames(b) == 0

    def test_partial_overlap(self):
        a = Track(track_id="a", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=15)
        b = Track(track_id="b", track_type=TrackType.VIDEO, layer=0, start_frame=10, end_frame=20)
        assert a.overlaps(b)
        assert a.overlap_frames(b) == 5

    def test_full_containment(self):
        a = Track(track_id="a", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=30)
        b = Track(track_id="b", track_type=TrackType.VIDEO, layer=0, start_frame=5, end_frame=15)
        assert a.overlaps(b)
        assert a.overlap_frames(b) == 10

    def test_no_overlap_separated(self):
        a = Track(track_id="a", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=10)
        b = Track(track_id="b", track_type=TrackType.VIDEO, layer=0, start_frame=20, end_frame=30)
        assert not a.overlaps(b)
        assert a.overlap_frames(b) == 0


# ═══════════════════════════════════════════════════════════════════════
# Timeline Construction
# ═══════════════════════════════════════════════════════════════════════


class TestTimelineConstruction:
    """TimelineIR construction and validation."""

    def _make_timeline(self) -> TimelineIR:
        return TimelineIR(
            tracks=(
                Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=150),
                Track(track_id="a0", track_type=TrackType.AUDIO, layer=1, start_frame=0, end_frame=147),
                Track(track_id="s0", track_type=TrackType.SUBTITLE, layer=2, start_frame=0, end_frame=150),
            ),
            fps=30,
        )

    def test_basic_construction(self):
        tl = self._make_timeline()
        assert tl.track_count == 3
        assert tl.fps == 30

    def test_frozen(self):
        tl = self._make_timeline()
        with pytest.raises(AttributeError):
            tl.fps = 60  # type: ignore

    def test_empty_tracks_rejected(self):
        with pytest.raises(ValueError, match="tracks"):
            TimelineIR(tracks=())

    def test_zero_fps_rejected(self):
        with pytest.raises(ValueError, match="fps"):
            TimelineIR(tracks=(
                Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=10),
            ), fps=0)

    def test_duplicate_ids_rejected(self):
        with pytest.raises(ValueError, match="Duplicate"):
            TimelineIR(tracks=(
                Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=10),
                Track(track_id="v0", track_type=TrackType.AUDIO, layer=1, start_frame=0, end_frame=10),
            ))


# ═══════════════════════════════════════════════════════════════════════
# Duration & Queries
# ═══════════════════════════════════════════════════════════════════════


class TestDurationQueries:
    """Frame-accurate duration and query methods."""

    def test_duration_frames(self):
        tl = TimelineIR(tracks=(
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=150),
            Track(track_id="a0", track_type=TrackType.AUDIO, layer=1, start_frame=0, end_frame=200),
        ))
        assert tl.duration_frames == 200

    def test_duration_seconds(self):
        tl = TimelineIR(tracks=(
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=150),
        ), fps=30)
        assert abs(tl.duration_seconds - 5.0) < 1e-10

    def test_layer_count(self):
        tl = TimelineIR(tracks=(
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=10),
            Track(track_id="s0", track_type=TrackType.SUBTITLE, layer=3, start_frame=0, end_frame=10),
        ))
        assert tl.layer_count == 4

    def test_tracks_at_frame(self):
        tl = TimelineIR(tracks=(
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=100),
            Track(track_id="v1", track_type=TrackType.VIDEO, layer=0, start_frame=100, end_frame=200),
        ))
        at_50 = tl.tracks_at_frame(50)
        assert len(at_50) == 1
        assert at_50[0].track_id == "v0"

        at_150 = tl.tracks_at_frame(150)
        assert len(at_150) == 1
        assert at_150[0].track_id == "v1"

    def test_tracks_on_layer(self):
        tl = TimelineIR(tracks=(
            Track(track_id="a0", track_type=TrackType.AUDIO, layer=1, start_frame=0, end_frame=100),
            Track(track_id="a1", track_type=TrackType.AUDIO, layer=1, start_frame=100, end_frame=200),
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=200),
        ))
        audio = tl.tracks_on_layer(1)
        assert len(audio) == 2
        assert audio[0].track_id == "a0"
        assert audio[1].track_id == "a1"

    def test_tracks_of_type(self):
        tl = TimelineIR(tracks=(
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=100),
            Track(track_id="a0", track_type=TrackType.AUDIO, layer=1, start_frame=0, end_frame=100),
            Track(track_id="s0", track_type=TrackType.SUBTITLE, layer=2, start_frame=0, end_frame=100),
        ))
        assert len(tl.tracks_of_type(TrackType.VIDEO)) == 1
        assert len(tl.tracks_of_type(TrackType.AUDIO)) == 1
        assert len(tl.tracks_of_type(TrackType.SUBTITLE)) == 1
        assert len(tl.tracks_of_type(TrackType.EFFECT)) == 0


# ═══════════════════════════════════════════════════════════════════════
# Transitions
# ═══════════════════════════════════════════════════════════════════════


class TestTransitions:
    """Transition construction and validation."""

    def test_valid_transition(self):
        tl = TimelineIR(
            tracks=(
                Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=100),
                Track(track_id="v1", track_type=TrackType.VIDEO, layer=0, start_frame=92, end_frame=200),
            ),
            transitions=(
                Transition(from_track="v0", to_track="v1", duration_frames=8),
            ),
        )
        assert len(tl.transitions) == 1

    def test_unknown_from_track_rejected(self):
        with pytest.raises(ValueError, match="unknown track"):
            TimelineIR(
                tracks=(
                    Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=100),
                ),
                transitions=(
                    Transition(from_track="xxx", to_track="v0"),
                ),
            )

    def test_unknown_to_track_rejected(self):
        with pytest.raises(ValueError, match="unknown track"):
            TimelineIR(
                tracks=(
                    Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=100),
                ),
                transitions=(
                    Transition(from_track="v0", to_track="xxx"),
                ),
            )

    def test_zero_duration_transition_rejected(self):
        with pytest.raises(ValueError, match="duration_frames"):
            TimelineIR(
                tracks=(
                    Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=100),
                    Track(track_id="v1", track_type=TrackType.VIDEO, layer=0, start_frame=90, end_frame=200),
                ),
                transitions=(
                    Transition(from_track="v0", to_track="v1", duration_frames=0),
                ),
            )

    def test_transition_between(self):
        tl = TimelineIR(
            tracks=(
                Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=100),
                Track(track_id="v1", track_type=TrackType.VIDEO, layer=0, start_frame=92, end_frame=200),
            ),
            transitions=(
                Transition(
                    from_track="v0", to_track="v1",
                    effect=TransitionEffect.CROSSFADE, duration_frames=8,
                ),
            ),
        )
        tr = tl.transition_between("v0", "v1")
        assert tr is not None
        assert tr.effect == TransitionEffect.CROSSFADE

    def test_transition_between_none(self):
        tl = TimelineIR(tracks=(
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=100),
        ))
        assert tl.transition_between("v0", "v0") is None


# ═══════════════════════════════════════════════════════════════════════
# Canonical Form & Hashing
# ═══════════════════════════════════════════════════════════════════════


class TestCanonicalization:
    """Canonical form and content hashing."""

    def test_deterministic_hash(self):
        tl = TimelineIR(tracks=(
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=150),
        ))
        hashes = [tl.content_hash() for _ in range(100)]
        assert len(set(hashes)) == 1

    def test_different_tracks_different_hash(self):
        tl1 = TimelineIR(tracks=(
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=150),
        ))
        tl2 = TimelineIR(tracks=(
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=200),
        ))
        assert tl1.content_hash() != tl2.content_hash()

    def test_same_content_same_hash(self):
        tl1 = TimelineIR(tracks=(
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=150),
            Track(track_id="a0", track_type=TrackType.AUDIO, layer=1, start_frame=0, end_frame=147),
        ))
        tl2 = TimelineIR(tracks=(
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=150),
            Track(track_id="a0", track_type=TrackType.AUDIO, layer=1, start_frame=0, end_frame=147),
        ))
        assert tl1.content_hash() == tl2.content_hash()

    def test_transition_affects_hash(self):
        tracks = (
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=100),
            Track(track_id="v1", track_type=TrackType.VIDEO, layer=0, start_frame=92, end_frame=200),
        )
        tl1 = TimelineIR(tracks=tracks)
        tl2 = TimelineIR(tracks=tracks, transitions=(
            Transition(from_track="v0", to_track="v1", duration_frames=8),
        ))
        assert tl1.content_hash() != tl2.content_hash()


# ═══════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Boundary conditions."""

    def test_single_track(self):
        tl = TimelineIR(tracks=(
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=1),
        ))
        assert tl.duration_frames == 1
        assert tl.duration_seconds == 1.0 / 30

    def test_many_layers(self):
        tracks = tuple(
            Track(track_id=f"t{i}", track_type=TrackType.EFFECT, layer=i, start_frame=0, end_frame=10)
            for i in range(10)
        )
        tl = TimelineIR(tracks=tracks)
        assert tl.layer_count == 10

    def test_nonzero_start(self):
        tl = TimelineIR(tracks=(
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=100, end_frame=200),
        ))
        assert tl.duration_frames == 200

    def test_summary(self):
        tl = TimelineIR(tracks=(
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=150),
        ))
        s = tl.summary()
        assert "150" in s
        assert "1 tracks" in s

    def test_ascii_timeline(self):
        tl = TimelineIR(tracks=(
            Track(track_id="v0", track_type=TrackType.VIDEO, layer=0, start_frame=0, end_frame=100),
            Track(track_id="a0", track_type=TrackType.AUDIO, layer=1, start_frame=0, end_frame=100),
        ))
        ascii_tl = tl.ascii_timeline()
        assert "L0:" in ascii_tl
        assert "L1:" in ascii_tl


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
