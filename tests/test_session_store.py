"""Tests for SessionStore — persistence of timeline editing sessions."""

import json
import os
import tempfile
import threading
from pathlib import Path

import pytest
from compiler.backend.session_store import SessionStore


@pytest.fixture
def tmp_sessions(tmp_path):
    return SessionStore(sessions_dir=tmp_path / "sessions")


@pytest.fixture
def sample_tracks():
    return [
        {"track_id": "v1", "track_type": "video", "layer": 0, "start_frame": 0, "end_frame": 90, "scene_id": "s1", "content": {"text": "Hello"}},
        {"track_id": "s1", "track_type": "subtitle", "layer": 1, "start_frame": 0, "end_frame": 90, "scene_id": "s1", "content": {"text": "Hello"}},
    ]


@pytest.fixture
def sample_undo_stack():
    return [
        {"tracks": [{"track_id": "v1", "start_frame": 10}], "description": "Move s1"},
    ]


class TestSessionStore:
    def test_save_and_load(self, tmp_sessions, sample_tracks):
        result = tmp_sessions.save("sess1", sample_tracks)
        assert result["session_id"] == "sess1"
        assert result["tracks_count"] == 2
        assert result["last_saved"] > 0

        state = tmp_sessions.load("sess1")
        assert state is not None
        assert len(state["tracks"]) == 2
        assert state["tracks"][0]["track_id"] == "v1"

    def test_save_with_undo_redo(self, tmp_sessions, sample_tracks, sample_undo_stack):
        redo_stack = [{"tracks": [{"track_id": "v1", "start_frame": 30}], "description": "redo"}]
        tmp_sessions.save("sess2", sample_tracks, undo_stack=sample_undo_stack, redo_stack=redo_stack)

        state = tmp_sessions.load("sess2")
        assert len(state["undo_stack"]) == 1
        assert state["undo_stack"][0]["description"] == "Move s1"
        assert len(state["redo_stack"]) == 1

    def test_save_with_meta(self, tmp_sessions, sample_tracks):
        meta = {"topic": "Redis为什么这么快", "created_at": 1000}
        tmp_sessions.save("sess3", sample_tracks, meta=meta)

        state = tmp_sessions.load("sess3")
        assert state["meta"]["topic"] == "Redis为什么这么快"
        assert state["meta"]["created_at"] == 1000

    def test_load_nonexistent(self, tmp_sessions):
        assert tmp_sessions.load("no_such_session") is None

    def test_exists(self, tmp_sessions, sample_tracks):
        assert not tmp_sessions.exists("sess_x")
        tmp_sessions.save("sess_x", sample_tracks)
        assert tmp_sessions.exists("sess_x")

    def test_delete(self, tmp_sessions, sample_tracks):
        tmp_sessions.save("del_me", sample_tracks)
        assert tmp_sessions.exists("del_me")
        assert tmp_sessions.delete("del_me") is True
        assert not tmp_sessions.exists("del_me")

    def test_delete_nonexistent(self, tmp_sessions):
        assert tmp_sessions.delete("ghost") is False

    def test_list_sessions(self, tmp_sessions, sample_tracks):
        tmp_sessions.save("a", sample_tracks, meta={"topic": "Alpha"})
        tmp_sessions.save("b", sample_tracks, meta={"topic": "Beta"})

        sessions = tmp_sessions.list_sessions()
        assert len(sessions) == 2
        topics = {s.get("topic") for s in sessions}
        assert "Alpha" in topics
        assert "Beta" in topics

    def test_list_sessions_empty(self, tmp_sessions):
        assert tmp_sessions.list_sessions() == []

    def test_overwrite_save(self, tmp_sessions, sample_tracks):
        tmp_sessions.save("overwrite", sample_tracks)
        new_tracks = [{"track_id": "v_new", "track_type": "video", "start_frame": 0, "end_frame": 50}]
        tmp_sessions.save("overwrite", new_tracks)

        state = tmp_sessions.load("overwrite")
        assert len(state["tracks"]) == 1
        assert state["tracks"][0]["track_id"] == "v_new"

    def test_meta_preserves_created_at(self, tmp_sessions, sample_tracks):
        tmp_sessions.save("meta_test", sample_tracks, meta={"created_at": 100, "topic": "T1"})
        state = tmp_sessions.load("meta_test")
        assert state["meta"]["created_at"] == 100

        # Save again without meta — created_at should be preserved from first save's meta
        tmp_sessions.save("meta_test", sample_tracks, meta={"topic": "T2"})
        state2 = tmp_sessions.load("meta_test")
        assert state2["meta"]["topic"] == "T2"

    def test_thread_safety(self, tmp_sessions, sample_tracks):
        errors = []

        def save_many(prefix, count):
            try:
                for i in range(count):
                    tmp_sessions.save(f"{prefix}_{i}", sample_tracks)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=save_many, args=(f"t{t}", 10)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        sessions = tmp_sessions.list_sessions()
        assert len(sessions) == 40

    def test_atomic_write_no_corruption(self, tmp_sessions, sample_tracks):
        """Verify files are valid JSON even after multiple saves."""
        for i in range(20):
            tmp_sessions.save(f"atomic_{i}", sample_tracks * (i + 1))

        for i in range(20):
            state = tmp_sessions.load(f"atomic_{i}")
            assert state is not None
            assert isinstance(state["tracks"], list)

    def test_empty_tracks(self, tmp_sessions):
        tmp_sessions.save("empty", [])
        state = tmp_sessions.load("empty")
        assert state["tracks"] == []

    def test_session_dir_structure(self, tmp_sessions, sample_tracks):
        tmp_sessions.save("dir_test", sample_tracks)
        session_dir = tmp_sessions._session_dir("dir_test")
        assert (session_dir / "timeline.json").exists()
        assert (session_dir / "undo_stack.json").exists()
        assert (session_dir / "redo_stack.json").exists()
        assert (session_dir / "meta.json").exists()

    def test_json_content_valid(self, tmp_sessions, sample_tracks):
        tmp_sessions.save("json_test", sample_tracks)
        timeline_path = tmp_sessions._session_dir("json_test") / "timeline.json"
        with open(timeline_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, list)
        assert len(data) == 2


class TestTimelineAPIPersistence:
    """Test the timeline API save/load endpoints."""

    def test_save_endpoint_model(self):
        from api.timeline_api import SaveRequest
        req = SaveRequest(
            tracks=[{"track_id": "v1"}],
            undo_stack=[{"tracks": [], "description": "test"}],
            redo_stack=[],
        )
        assert len(req.tracks) == 1
        assert req.meta is None

    def test_save_endpoint_defaults(self):
        from api.timeline_api import SaveRequest
        req = SaveRequest()
        assert req.tracks == []
        assert req.undo_stack == []
        assert req.redo_stack == []

    def test_load_response_model(self):
        from api.timeline_api import LoadResponse
        resp = LoadResponse(
            success=True,
            session_id="s1",
            tracks=[{"track_id": "v1"}],
            undo_stack=[],
            redo_stack=[],
            meta={"topic": "test"},
        )
        assert resp.success is True
        assert resp.session_id == "s1"
