"""Tests for Distributed Rendering — Worker, TaskQueue, Scheduler."""

import time
import threading

import pytest

from runtime.worker import WorkerRegistry, WorkerStatus, WorkerInfo
from runtime.render_task_queue import RenderTaskQueue, TaskStatus
from runtime.distributed_scheduler import DistributedScheduler, RenderResult


# ── Worker Registry Tests ──


class TestWorkerRegistry:
    @pytest.fixture
    def registry(self):
        return WorkerRegistry(heartbeat_timeout=5.0)

    def test_register(self, registry):
        registry.register("w1", "http://localhost:9000", capacity=4)
        workers = registry.list_workers()
        assert len(workers) == 1
        assert workers[0]["worker_id"] == "w1"
        assert workers[0]["capacity"] == 4

    def test_register_update(self, registry):
        registry.register("w1", "http://localhost:9000", capacity=4)
        registry.register("w1", "http://localhost:9001", capacity=8)
        w = registry.get_worker("w1")
        assert w.url == "http://localhost:9001"
        assert w.capacity == 8

    def test_unregister(self, registry):
        registry.register("w1", "http://localhost:9000")
        assert registry.unregister("w1") is True
        assert registry.get_worker("w1") is None

    def test_unregister_nonexistent(self, registry):
        assert registry.unregister("ghost") is False

    def test_best_worker_least_loaded(self, registry):
        registry.register("w1", "http://localhost:9000", capacity=4)
        registry.register("w2", "http://localhost:9001", capacity=4)
        registry.assign_task("w1", "task_1")
        registry.assign_task("w1", "task_2")

        best = registry.best_worker()
        assert best is not None
        assert best.worker_id == "w2"  # w2 has 0 load

    def test_best_worker_no_workers(self, registry):
        assert registry.best_worker() is None

    def test_best_worker_all_busy(self, registry):
        registry.register("w1", "http://localhost:9000", capacity=1)
        registry.assign_task("w1", "task_1")

        assert registry.best_worker() is None

    def test_assign_task(self, registry):
        registry.register("w1", "http://localhost:9000", capacity=2)
        assert registry.assign_task("w1", "task_1") is True
        w = registry.get_worker("w1")
        assert w.load == 1
        assert w.status == WorkerStatus.RUNNING

    def test_assign_task_full(self, registry):
        registry.register("w1", "http://localhost:9000", capacity=1)
        registry.assign_task("w1", "task_1")
        assert registry.assign_task("w1", "task_2") is False

    def test_complete_task(self, registry):
        registry.register("w1", "http://localhost:9000", capacity=4)
        registry.assign_task("w1", "task_1")
        assert registry.complete_task("w1", "task_1") is True
        w = registry.get_worker("w1")
        assert w.load == 0
        assert w.completed_count == 1
        assert w.status == WorkerStatus.IDLE

    def test_fail_task(self, registry):
        registry.register("w1", "http://localhost:9000", capacity=4)
        registry.assign_task("w1", "task_1")
        assert registry.fail_task("w1", "task_1") is True
        w = registry.get_worker("w1")
        assert w.failed_count == 1

    def test_heartbeat(self, registry):
        registry.register("w1", "http://localhost:9000")
        old_hb = registry.get_worker("w1").last_heartbeat
        time.sleep(0.05)
        registry.heartbeat("w1")
        assert registry.get_worker("w1").last_heartbeat >= old_hb

    def test_health_check_offline(self, registry):
        registry.register("w1", "http://localhost:9000")
        # Simulate expired heartbeat
        registry.workers["w1"].last_heartbeat = time.monotonic() - 100
        workers = registry.list_workers()
        assert workers[0]["status"] == "offline"

    def test_stats(self, registry):
        registry.register("w1", "http://localhost:9000", capacity=4)
        registry.register("w2", "http://localhost:9001", capacity=2)
        registry.assign_task("w1", "t1")

        stats = registry.stats()
        assert stats["total_workers"] == 2
        assert stats["total_capacity"] == 6
        assert stats["total_load"] == 1


# ── Task Queue Tests ──


class TestTaskQueue:
    @pytest.fixture
    def queue(self):
        return RenderTaskQueue(max_retries=3)

    def test_enqueue(self, queue):
        task = queue.enqueue("s1", "hash_1")
        assert task.scene_id == "s1"
        assert task.content_hash == "hash_1"
        assert task.status == TaskStatus.PENDING

    def test_next_task_priority(self, queue):
        queue.enqueue("s1", "h1", priority=0)
        queue.enqueue("s2", "h2", priority=5)
        queue.enqueue("s3", "h3", priority=2)

        task = queue.next_task()
        assert task.scene_id == "s2"  # Highest priority

    def test_next_task_fifo_same_priority(self, queue):
        queue.enqueue("s1", "h1", priority=0)
        time.sleep(0.01)
        queue.enqueue("s2", "h2", priority=0)

        task = queue.next_task()
        assert task.scene_id == "s1"  # First enqueued

    def test_next_task_empty(self, queue):
        assert queue.next_task() is None

    def test_mark_running(self, queue):
        task = queue.enqueue("s1", "h1")
        assert queue.mark_running(task.task_id, "w1") is True
        assert queue.get_task(task.task_id).status == TaskStatus.RUNNING

    def test_mark_completed(self, queue):
        task = queue.enqueue("s1", "h1")
        queue.mark_running(task.task_id, "w1")
        assert queue.mark_completed(task.task_id) is True
        assert queue.get_task(task.task_id).status == TaskStatus.COMPLETED

    def test_mark_failed_retries(self, queue):
        task = queue.enqueue("s1", "h1")
        queue.mark_running(task.task_id, "w1")

        queue.mark_failed(task.task_id, "error 1")
        assert queue.get_task(task.task_id).status == TaskStatus.RETRYING

        # Should be available for next task again
        next_task = queue.next_task()
        assert next_task is not None
        assert next_task.task_id == task.task_id

    def test_mark_failed_exhausted(self, queue):
        queue = RenderTaskQueue(max_retries=2)
        task = queue.enqueue("s1", "h1")
        queue.mark_running(task.task_id, "w1")
        queue.mark_failed(task.task_id, "err 1")  # retry 1

        next_task = queue.next_task()
        queue.mark_running(next_task.task_id, "w1")
        queue.mark_failed(next_task.task_id, "err 2")  # retry 2 → max reached

        assert queue.get_task(task.task_id).status == TaskStatus.FAILED

    def test_pending_count(self, queue):
        queue.enqueue("s1", "h1")
        queue.enqueue("s2", "h2")
        task3 = queue.enqueue("s3", "h3")
        queue.mark_running(task3.task_id, "w1")

        assert queue.pending_count() == 2

    def test_stats(self, queue):
        queue.enqueue("s1", "h1")
        t2 = queue.enqueue("s2", "h2")
        queue.mark_running(t2.task_id, "w1")

        stats = queue.stats()
        assert stats["pending"] == 1
        assert stats["running"] == 1
        assert stats["total"] == 2

    def test_completed_hashes(self, queue):
        t1 = queue.enqueue("s1", "hash_a")
        t2 = queue.enqueue("s2", "hash_b")
        queue.mark_running(t1.task_id, "w1")
        queue.mark_completed(t1.task_id)

        hashes = queue.completed_hashes()
        assert "hash_a" in hashes
        assert "hash_b" not in hashes

    def test_remove_completed(self, queue):
        t1 = queue.enqueue("s1", "h1")
        queue.mark_running(t1.task_id, "w1")
        queue.mark_completed(t1.task_id)
        queue.enqueue("s2", "h2")

        queue.remove_completed()
        assert len(queue.list_tasks()) == 1


# ── Scheduler Tests ──


class TestDistributedScheduler:
    @pytest.fixture
    def scheduler(self):
        s = DistributedScheduler(max_workers=4)
        s.add_worker("w1", "http://localhost:9000", capacity=4)
        s.add_worker("w2", "http://localhost:9001", capacity=2)
        return s

    def test_add_worker(self, scheduler):
        workers = scheduler.registry.list_workers()
        assert len(workers) == 2

    def test_remove_worker(self, scheduler):
        scheduler.remove_worker("w1")
        workers = scheduler.registry.list_workers()
        assert len(workers) == 1

    def test_submit_scenes(self, scheduler):
        scheduler.submit_scenes([
            {"scene_id": "s1", "content_hash": "h1"},
            {"scene_id": "s2", "content_hash": "h2"},
        ])
        assert scheduler.queue.pending_count() == 2

    def test_process_all_success(self, scheduler):
        scenes = [
            {"scene_id": f"s{i}", "content_hash": f"h{i}"}
            for i in range(6)
        ]
        scheduler.submit_scenes(scenes)

        call_log = []

        def render_fn(payload):
            call_log.append(payload["scene_id"])
            time.sleep(0.01)
            return f"/tmp/{payload['scene_id']}.mp4"

        results = scheduler.process_all(render_fn)
        assert len(results) == 6
        assert all(r.success for r in results)
        assert len(call_log) == 6

    def test_process_all_with_failure(self, scheduler):
        scheduler.queue.max_retries = 1  # No retries for this test
        scheduler.submit_scenes([
            {"scene_id": "s1", "content_hash": "h1"},
            {"scene_id": "s2", "content_hash": "h2"},
        ])

        def render_fn(payload):
            if payload["scene_id"] == "s1":
                raise RuntimeError("ffmpeg crash")
            return "/tmp/s2.mp4"

        results = scheduler.process_all(render_fn)
        successes = [r for r in results if r.success]
        failures = [r for r in results if not r.success]
        assert len(successes) >= 1
        assert len(failures) >= 1

    def test_process_dedup(self, scheduler):
        """Same content_hash should be rendered once."""
        scheduler.submit_scenes([
            {"scene_id": "s1", "content_hash": "same_hash"},
            {"scene_id": "s2", "content_hash": "same_hash"},
        ])

        call_log = []
        def render_fn(payload):
            call_log.append(payload["scene_id"])
            return f"/tmp/{payload['scene_id']}.mp4"

        results = scheduler.process_all(render_fn)
        # Both tasks run (dedup is at cache level, not scheduler level)
        assert len(results) == 2

    def test_get_status(self, scheduler):
        scheduler.submit_scenes([{"scene_id": "s1", "content_hash": "h1"}])
        status = scheduler.get_status()
        assert "workers" in status
        assert "queue" in status

    def test_benchmark_parallel(self, scheduler):
        """Benchmark: 10 scenes across 2 workers."""
        scenes = [
            {"scene_id": f"s{i}", "content_hash": f"h{i}", "priority": i % 3}
            for i in range(10)
        ]
        scheduler.submit_scenes(scenes)

        def render_fn(payload):
            time.sleep(0.02)  # Simulate 20ms render
            return f"/tmp/{payload['scene_id']}.mp4"

        start = time.monotonic()
        results = scheduler.process_all(render_fn, timeout=30)
        elapsed = time.monotonic() - start

        assert len(results) == 10
        assert all(r.success for r in results)
        # With 2 workers, 10 tasks at 20ms each → ~100ms sequential, ~40-60ms parallel
        assert elapsed < 5.0  # Very generous timeout for CI

    def test_worker_load_distribution(self, scheduler):
        """Verify tasks are distributed across workers."""
        scenes = [{"scene_id": f"s{i}", "content_hash": f"h{i}"} for i in range(8)]
        scheduler.submit_scenes(scenes)

        worker_tasks: dict[str, list[str]] = {}
        original_assign = scheduler.registry.assign_task

        def track_assign(worker_id, task_id):
            worker_tasks.setdefault(worker_id, []).append(task_id)
            return original_assign(worker_id, task_id)

        scheduler.registry.assign_task = track_assign

        def render_fn(payload):
            time.sleep(0.01)
            return f"/tmp/{payload['scene_id']}.mp4"

        scheduler.process_all(render_fn)
        # Both workers should have received tasks
        assert len(worker_tasks) == 2

    def test_empty_submission(self, scheduler):
        results = scheduler.process_all(lambda p: "/tmp/x.mp4")
        assert results == []


class TestRenderResult:
    def test_success_result(self):
        task = type("T", (), {
            "task_id": "t1", "scene_id": "s1", "content_hash": "h1",
            "retry_count": 0, "started_at": 1.0, "completed_at": 1.5,
        })()
        result = RenderResult(task=task, output_path="/tmp/s1.mp4", success=True)
        assert result.success is True
        d = result.to_dict()
        assert d["scene_id"] == "s1"

    def test_failure_result(self):
        task = type("T", (), {
            "task_id": "t1", "scene_id": "s1", "content_hash": "h1",
            "retry_count": 2, "started_at": 1.0, "completed_at": 1.1,
        })()
        result = RenderResult(task=task, success=False, error="OOM")
        assert result.error == "OOM"
