"""Tests for Worker Server — HTTP endpoints for render workers."""

import time
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

from compiler.runtime.worker_server import create_worker_router, WorkerState, RenderRequest


@pytest.fixture
def app():
    """Create a test FastAPI app with a worker router."""
    fast_app = FastAPI()
    render_log = []

    def mock_render(payload):
        render_log.append(payload)
        time.sleep(0.01)
        return f"/tmp/rendered_{payload.get('scene_id', 'x')}.mp4"

    router, state = create_worker_router(
        worker_id="test-worker-1",
        capacity=2,
        render_fn=mock_render,
    )
    fast_app.include_router(router)
    fast_app.state.render_log = render_log
    fast_app.state.worker_state = state
    return fast_app


@pytest.fixture
def client(app):
    return TestClient(app)


class TestWorkerServer:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["worker_id"] == "test-worker-1"
        assert data["status"] == "ok"
        assert data["uptime"] >= 0

    def test_status(self, client, app):
        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["worker_id"] == "test-worker-1"
        assert data["capacity"] == 2
        assert data["active_tasks"] == 0
        assert data["completed_count"] == 0

    def test_render_success(self, client, app):
        resp = client.post("/render", json={
            "scene_id": "scene_1",
            "content_hash": "hash_abc",
            "scene_ir": {"text": "Hello"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["scene_id"] == "scene_1"
        assert data["output_path"] == "/tmp/rendered_scene_1.mp4"
        assert data["duration"] > 0
        assert data["worker_id"] == "test-worker-1"

        # Verify render was called
        assert len(app.state.render_log) == 1
        assert app.state.render_log[0]["scene_id"] == "scene_1"

    def test_render_tracks_state(self, client, app):
        client.post("/render", json={"scene_id": "s1", "content_hash": "h1"})

        state = app.state.worker_state
        assert state.completed_count == 1

        resp = client.get("/status")
        assert resp.json()["completed_count"] == 1

    def test_render_concurrent(self, client, app):
        """Submit multiple renders within capacity."""
        import threading
        results = []

        def render(scene_id):
            resp = client.post("/render", json={
                "scene_id": scene_id,
                "content_hash": f"h_{scene_id}",
            })
            results.append(resp.json())

        threads = [threading.Thread(target=render, args=(f"s{i}",)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r["success"] for r in results)
        assert len(results) == 2

    def test_render_at_capacity(self):
        """Reject when at capacity."""
        import threading

        started = threading.Event()
        proceed = threading.Event()

        def blocking_render(payload):
            started.set()
            proceed.wait(timeout=5)
            return "/tmp/done.mp4"

        fast_app = FastAPI()
        router, state = create_worker_router(
            worker_id="cap-worker",
            capacity=2,
            render_fn=blocking_render,
        )
        fast_app.include_router(router)
        c = TestClient(fast_app)

        # Fill capacity (2)
        results = []
        def do_render():
            r = c.post("/render", json={
                "scene_id": "slow",
                "content_hash": "slow_h",
                "timeout": 5.0,
            })
            results.append(r)

        threads = [threading.Thread(target=do_render) for _ in range(2)]
        for t in threads:
            t.start()
        started.wait(timeout=3)

        # This should be rejected (503)
        resp = c.post("/render", json={
            "scene_id": "overflow",
            "content_hash": "overflow_h",
        })
        assert resp.status_code == 503
        assert "capacity" in resp.json()["detail"].lower()

        proceed.set()
        for t in threads:
            t.join(timeout=5)

    def test_render_with_settings(self, client, app):
        resp = client.post("/render", json={
            "scene_id": "s1",
            "content_hash": "h1",
            "settings": {"fps": 30, "width": 1080},
        })
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_render_failure(self, client):
        """Test render function that raises."""
        fast_app = FastAPI()

        def failing_render(payload):
            raise RuntimeError("ffmpeg crashed")

        router, state = create_worker_router(
            worker_id="fail-worker",
            capacity=4,
            render_fn=failing_render,
        )
        fast_app.include_router(router)
        c = TestClient(fast_app)

        resp = c.post("/render", json={
            "scene_id": "bad",
            "content_hash": "bad_h",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert "ffmpeg crashed" in data["error"]

    def test_cancel_task(self, client, app):
        """Test task cancellation."""
        # First, start a render
        import threading

        started = threading.Event()
        proceed = threading.Event()

        def blocking_render(payload):
            started.set()
            proceed.wait(timeout=5)
            return "/tmp/done.mp4"

        fast_app = FastAPI()
        router, state = create_worker_router(
            worker_id="cancel-worker",
            capacity=4,
            render_fn=blocking_render,
        )
        fast_app.include_router(router)
        c = TestClient(fast_app)

        # Start render in background
        result = [None]
        def do_render():
            result[0] = c.post("/render", json={
                "scene_id": "cancel_me",
                "content_hash": "cancel_h",
                "timeout": 1.0,
            })

        t = threading.Thread(target=do_render)
        t.start()
        started.wait(timeout=2)

        # Get the task ID from active tasks
        resp = c.get("/status")
        active = resp.json()["active_task_ids"]
        assert len(active) > 0

        # Cancel it
        resp = c.post(f"/cancel/{active[0]}")
        assert resp.status_code == 200
        assert resp.json()["cancelled"] is True

        proceed.set()
        t.join(timeout=3)

    def test_cancel_nonexistent(self, client):
        resp = client.post("/cancel/nonexistent_task")
        assert resp.status_code == 404

    def test_render_request_model(self):
        req = RenderRequest(scene_id="s1", content_hash="h1")
        assert req.task_id  # auto-generated
        assert req.timeout == 120.0

    def test_render_request_custom_timeout(self):
        req = RenderRequest(scene_id="s1", content_hash="h1", timeout=30.0)
        assert req.timeout == 30.0

    def test_worker_state_thread_safety(self):
        """WorkerState is thread-safe."""
        state = WorkerState("stress", capacity=10)
        errors = []

        def work(n):
            try:
                for i in range(50):
                    tid = f"t_{n}_{i}"
                    state.accept_task(tid)
                    if i % 2 == 0:
                        state.complete_task(tid)
                    else:
                        state.fail_task(tid)
            except Exception as e:
                errors.append(e)

        import threading
        threads = [threading.Thread(target=work, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert state.completed_count + state.failed_count == 200
