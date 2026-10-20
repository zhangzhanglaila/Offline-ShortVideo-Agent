"""End-to-end persistence tests — save, close, restore cycle.

Tests the full lifecycle:
  1. Create session with tracks + undo/redo history
  2. Save to disk
  3. Simulate "close" (new SessionStore instance)
  4. Load and verify all state restored
  5. Continue editing and save again
  6. Verify version increments and conflict detection
"""

import pytest
from compiler.backend.session_store import SessionStore, SaveConflictError


@pytest.fixture
def store(tmp_path):
    return SessionStore(sessions_dir=tmp_path / "sessions")


@pytest.fixture
def sample_tracks():
    return [
        {"track_id": "v1", "track_type": "video", "layer": 0, "start_frame": 0, "end_frame": 90, "scene_id": "hook", "content": {"scene_type": "hook", "text": "Hello"}},
        {"track_id": "v2", "track_type": "video", "layer": 0, "start_frame": 90, "end_frame": 200, "scene_id": "graph", "content": {"scene_type": "graph", "text": "World"}},
        {"track_id": "s1", "track_type": "subtitle", "layer": 1, "start_frame": 0, "end_frame": 90, "scene_id": "hook", "content": {"text": "Hello", "word_timings": [{"word": "Hello", "start": 0.0, "end": 0.5}]}},
        {"track_id": "a1", "track_type": "audio", "layer": 2, "start_frame": 0, "end_frame": 90, "scene_id": "hook", "content": {"audio_path": "/tmp/hook.mp3"}},
    ]


@pytest.fixture
def sample_undo():
    return [
        {"tracks": [{"track_id": "v1", "start_frame": 10}], "description": "Move hook"},
        {"tracks": [{"track_id": "v1", "start_frame": 20}], "description": "Move hook again"},
    ]


@pytest.fixture
def sample_redo():
    return [
        {"tracks": [{"track_id": "v1", "start_frame": 0}], "description": "undo"},
    ]


class TestE2EPersistence:
    def test_full_save_restore_cycle(self, store, sample_tracks, sample_undo, sample_redo):
        """Save → close → restore → verify all state."""
        # 1. Save
        result = store.save(
            "e2e_session",
            sample_tracks,
            undo_stack=sample_undo,
            redo_stack=sample_redo,
            meta={"topic": "Redis为什么这么快"},
        )
        assert result["version"] == 1

        # 2. Simulate close (new store instance from same dir)
        store2 = SessionStore(sessions_dir=store.sessions_dir)

        # 3. Load
        state = store2.load("e2e_session")
        assert state is not None

        # 4. Verify tracks
        assert len(state["tracks"]) == 4
        assert state["tracks"][0]["scene_id"] == "hook"
        assert state["tracks"][0]["content"]["text"] == "Hello"
        assert state["tracks"][2]["content"]["word_timings"][0]["word"] == "Hello"

        # 5. Verify undo/redo stacks
        assert len(state["undo_stack"]) == 2
        assert state["undo_stack"][0]["description"] == "Move hook"
        assert len(state["redo_stack"]) == 1

        # 6. Verify meta
        assert state["meta"]["topic"] == "Redis为什么这么快"
        assert state["meta"]["version"] == 1

    def test_edit_save_reload_cycle(self, store, sample_tracks):
        """Edit → save → reload → verify changes persisted."""
        store.save("cycle", sample_tracks, meta={"topic": "test"})

        # Edit: move scene
        modified = list(sample_tracks)
        modified[0] = {**modified[0], "start_frame": 10, "end_frame": 100}

        store.save("cycle", modified, meta={"topic": "test"})
        state = store.load("cycle")
        assert state["tracks"][0]["start_frame"] == 10
        assert state["tracks"][0]["end_frame"] == 100

    def test_undo_redo_roundtrip(self, store, sample_tracks):
        """Save undo history → restore → verify undo/redo are correct."""
        # Simulate: user edited 3 times, undid 1
        undo_stack = [
            {"tracks": [{"track_id": "v1", "start_frame": 0, "end_frame": 90}], "description": "Move v1"},
            {"tracks": [{"track_id": "v1", "start_frame": 10, "end_frame": 100}], "description": "Resize v1"},
        ]
        redo_stack = [
            {"tracks": [{"track_id": "v1", "start_frame": 15, "end_frame": 105}], "description": "redo"},
        ]

        store.save("undo_test", sample_tracks, undo_stack=undo_stack, redo_stack=redo_stack)

        # Restore
        state = store.load("undo_test")
        assert len(state["undo_stack"]) == 2
        assert state["undo_stack"][1]["description"] == "Resize v1"
        assert len(state["redo_stack"]) == 1
        assert state["redo_stack"][0]["description"] == "redo"

    def test_version_increments(self, store, sample_tracks):
        """Each save increments the version."""
        store.save("v_test", sample_tracks)
        store.save("v_test", sample_tracks)
        result = store.save("v_test", sample_tracks)
        assert result["version"] == 3

    def test_versioned_save_success(self, store, sample_tracks):
        """Versioned save succeeds when version matches."""
        result = store.save("vs_test", sample_tracks)
        assert result["version"] == 1

        result2 = store.save_versioned("vs_test", expected_version=1, tracks=sample_tracks)
        assert result2["version"] == 2

    def test_versioned_save_conflict(self, store, sample_tracks):
        """Versioned save raises on stale version."""
        store.save("conflict_test", sample_tracks)  # version 1
        store.save("conflict_test", sample_tracks)  # version 2

        with pytest.raises(SaveConflictError) as exc_info:
            store.save_versioned("conflict_test", expected_version=1, tracks=sample_tracks)
        assert exc_info.value.expected_version == 1
        assert exc_info.value.actual_version == 2

    def test_versioned_save_from_api_model(self):
        """Test the API request/response models."""
        from api.timeline_api import SaveRequest, SaveResponse, LoadResponse

        # Versioned save request
        req = SaveRequest(
            tracks=[{"track_id": "v1"}],
            expected_version=1,
        )
        assert req.expected_version == 1

        # Conflict response
        resp = SaveResponse(
            success=False,
            session_id="s1",
            conflict=True,
            error="Version mismatch",
            version=1,
            current_version=3,
        )
        assert resp.conflict is True
        assert resp.current_version == 3

        # Load response with version
        load_resp = LoadResponse(
            success=True,
            session_id="s1",
            tracks=[{"track_id": "v1"}],
            version=5,
        )
        assert load_resp.version == 5

    def test_multiple_sessions_independent(self, store, sample_tracks):
        """Different sessions have independent versions."""
        store.save("sess_a", sample_tracks)
        store.save("sess_a", sample_tracks)
        store.save("sess_b", sample_tracks)

        state_a = store.load("sess_a")
        state_b = store.load("sess_b")
        assert state_a["meta"]["version"] == 2
        assert state_b["meta"]["version"] == 1

    def test_concurrent_save_last_write_wins(self, store, sample_tracks):
        """Without versioning, last write wins."""
        import threading
        results = []

        def writer(tracks, idx):
            modified = [{**tracks[0], "start_frame": idx * 10}]
            r = store.save("concurrent", modified)
            results.append(r)

        threads = [
            threading.Thread(target=writer, args=(sample_tracks, i))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        state = store.load("concurrent")
        # Last write wins — version should be 5
        assert state["meta"]["version"] == 5

    def test_save_empty_session(self, store):
        """Save and restore an empty session."""
        store.save("empty", [], undo_stack=[], redo_stack=[])
        state = store.load("empty")
        assert state["tracks"] == []
        assert state["undo_stack"] == []
        assert state["redo_stack"] == []

    def test_restore_preserves_word_timings(self, store, sample_tracks):
        """Word timings in subtitle tracks survive save/restore."""
        store.save("wt_test", sample_tracks)
        state = store.load("wt_test")

        sub_track = next(t for t in state["tracks"] if t["track_type"] == "subtitle")
        timings = sub_track["content"]["word_timings"]
        assert len(timings) == 1
        assert timings[0]["word"] == "Hello"
        assert timings[0]["start"] == 0.0
        assert timings[0]["end"] == 0.5

    def test_restore_preserves_audio_path(self, store, sample_tracks):
        """Audio paths survive save/restore."""
        store.save("audio_test", sample_tracks)
        state = store.load("audio_test")

        audio_track = next(t for t in state["tracks"] if t["track_type"] == "audio")
        assert audio_track["content"]["audio_path"] == "/tmp/hook.mp3"
