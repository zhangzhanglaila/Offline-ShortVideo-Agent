"""Tests for Production Hardening — Retry, Crash Recovery, Render Logger."""

import time
import threading
from unittest.mock import patch

import pytest

from runtime.retry import retry, RetryPolicy, RetryContext
from runtime.crash_recovery import CrashRecovery
from runtime.render_logger import RenderLogger


# ── Retry Tests ──


class TestRetryPolicy:
    def test_default_policy(self):
        p = RetryPolicy()
        assert p.max_attempts == 3
        assert p.base_delay == 1.0
        assert p.backoff_factor == 2.0

    def test_delay_exponential(self):
        p = RetryPolicy(base_delay=1.0, backoff_factor=2.0, jitter=0)
        assert p.delay_for_attempt(0) == 1.0
        assert p.delay_for_attempt(1) == 2.0
        assert p.delay_for_attempt(2) == 4.0

    def test_delay_max_cap(self):
        p = RetryPolicy(base_delay=10.0, backoff_factor=10.0, max_delay=15.0, jitter=0)
        assert p.delay_for_attempt(0) == 10.0
        assert p.delay_for_attempt(1) == 15.0  # capped
        assert p.delay_for_attempt(2) == 15.0

    def test_delay_jitter(self):
        p = RetryPolicy(base_delay=1.0, jitter=0.1)
        delays = [p.delay_for_attempt(0) for _ in range(100)]
        # All delays should be near 1.0 but with some variance
        assert all(0.85 <= d <= 1.15 for d in delays)
        assert len(set(delays)) > 1  # Not all identical due to jitter


class TestRetryDecorator:
    def test_succeeds_first_try(self):
        call_count = 0

        @retry(RetryPolicy(max_attempts=3, base_delay=0.01))
        def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        assert succeed() == "ok"
        assert call_count == 1

    def test_retries_on_failure(self):
        call_count = 0

        @retry(RetryPolicy(max_attempts=3, base_delay=0.01))
        def fail_twice():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("not yet")
            return "done"

        assert fail_twice() == "done"
        assert call_count == 3

    def test_raises_after_max_attempts(self):
        call_count = 0

        @retry(RetryPolicy(max_attempts=3, base_delay=0.01))
        def always_fail():
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"fail {call_count}")

        with pytest.raises(RuntimeError, match="fail 3"):
            always_fail()
        assert call_count == 3

    def test_retryable_exceptions(self):
        call_count = 0

        @retry(RetryPolicy(max_attempts=3, base_delay=0.01, retryable_exceptions=(ValueError,)))
        def type_error():
            nonlocal call_count
            call_count += 1
            raise TypeError("wrong type")

        with pytest.raises(TypeError):
            type_error()
        assert call_count == 1  # Not retried (TypeError not in retryable_exceptions)

    def test_on_retry_callback(self):
        retry_ctxs = []

        def on_retry(ctx: RetryContext):
            retry_ctxs.append(ctx.attempt)

        @retry(RetryPolicy(max_attempts=3, base_delay=0.01), on_retry=on_retry)
        def fail_twice():
            if len(retry_ctxs) < 2:
                raise ValueError("not yet")
            return "ok"

        fail_twice()
        assert retry_ctxs == [0, 1]

    def test_preserves_function_name(self):
        @retry(RetryPolicy(max_attempts=1))
        def my_function():
            pass

        assert my_function.__name__ == "my_function"


class TestRetryAsync:
    def test_async_succeeds(self):
        import asyncio

        @retry(RetryPolicy(max_attempts=3, base_delay=0.01))
        async def async_ok():
            return "async_ok"

        result = asyncio.run(async_ok())
        assert result == "async_ok"

    def test_async_retries(self):
        import asyncio
        call_count = 0

        @retry(RetryPolicy(max_attempts=3, base_delay=0.01))
        async def async_fail_twice():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("not yet")
            return "done"

        result = asyncio.run(async_fail_twice())
        assert result == "done"
        assert call_count == 3


# ── Crash Recovery Tests ──


class TestCrashRecovery:
    @pytest.fixture
    def recovery(self, tmp_path):
        return CrashRecovery(state_dir=tmp_path / "render_state")

    def test_mark_started(self, recovery):
        recovery.mark_started("scene_1", "hash_abc")
        assert "scene_1" in recovery.manifest
        assert recovery.manifest["scene_1"]["status"] == "running"
        assert recovery.manifest["scene_1"]["content_hash"] == "hash_abc"

    def test_mark_completed(self, recovery):
        recovery.mark_started("scene_1", "hash_abc")
        recovery.mark_completed("scene_1")
        assert recovery.manifest["scene_1"]["status"] == "completed"
        assert recovery.manifest["scene_1"]["completed_at"] is not None

    def test_mark_failed(self, recovery):
        recovery.mark_started("scene_1", "hash_abc")
        recovery.mark_failed("scene_1", "ffmpeg error")
        assert recovery.manifest["scene_1"]["status"] == "failed"
        assert recovery.manifest["scene_1"]["error"] == "ffmpeg error"

    def test_get_pending(self, recovery):
        recovery.mark_started("s1", "h1")
        recovery.mark_started("s2", "h2")
        recovery.mark_completed("s1")

        pending = recovery.get_pending()
        assert len(pending) == 1
        assert pending[0]["scene_id"] == "s2"

    def test_get_pending_empty(self, recovery):
        assert recovery.get_pending() == []

    def test_get_failed(self, recovery):
        recovery.mark_started("s1", "h1")
        recovery.mark_failed("s1", "OOM")
        failed = recovery.get_failed()
        assert len(failed) == 1
        assert failed[0]["error"] == "OOM"

    def test_get_completed(self, recovery):
        recovery.mark_started("s1", "h1")
        recovery.mark_completed("s1")
        completed = recovery.get_completed()
        assert len(completed) == 1

    def test_clear_completed(self, recovery):
        recovery.mark_started("s1", "h1")
        recovery.mark_started("s2", "h2")
        recovery.mark_completed("s1")
        recovery.clear_completed()

        assert "s1" not in recovery.manifest
        assert "s2" in recovery.manifest

    def test_reset_scene(self, recovery):
        recovery.mark_started("s1", "h1")
        recovery.reset("s1")
        assert "s1" not in recovery.manifest

    def test_stats(self, recovery):
        recovery.mark_started("s1", "h1")
        recovery.mark_started("s2", "h2")
        recovery.mark_completed("s1")
        recovery.mark_failed("s2", "err")

        stats = recovery.stats()
        assert stats.get("completed", 0) == 1
        assert stats.get("failed", 0) == 1

    def test_persistence_across_instances(self, tmp_path):
        state_dir = tmp_path / "state"
        r1 = CrashRecovery(state_dir=state_dir)
        r1.mark_started("s1", "h1")
        r1.mark_completed("s1")

        # New instance reads from disk
        r2 = CrashRecovery(state_dir=state_dir)
        assert r2.manifest["s1"]["status"] == "completed"

    def test_thread_safety(self, recovery):
        errors = []

        def mark_many(prefix, count):
            try:
                for i in range(count):
                    recovery.mark_started(f"{prefix}_{i}", f"hash_{i}")
                    recovery.mark_completed(f"{prefix}_{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=mark_many, args=(f"t{t}", 20)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(recovery.manifest) == 80

    def test_metadata(self, recovery):
        recovery.mark_started("s1", "h1", metadata={"fps": 30, "width": 1080})
        assert recovery.manifest["s1"]["fps"] == 30
        assert recovery.manifest["s1"]["width"] == 1080

    def test_crash_scenario(self, tmp_path):
        """Simulate crash: started but never completed."""
        state_dir = tmp_path / "state"
        r = CrashRecovery(state_dir=state_dir)
        r.mark_started("s1", "h1")
        r.mark_started("s2", "h2")
        r.mark_completed("s1")
        # s2 was "in progress" when crash happened

        # Restart: new instance
        r2 = CrashRecovery(state_dir=state_dir)
        pending = r2.get_pending()
        assert len(pending) == 1
        assert pending[0]["scene_id"] == "s2"
        assert pending[0]["content_hash"] == "h2"


# ── Render Logger Tests ──


class TestRenderLogger:
    def test_step_timing(self):
        rl = RenderLogger("test")
        with rl.step("tts", scene_id="s1"):
            time.sleep(0.05)

        assert len(rl.steps) == 1
        assert rl.steps[0].name == "tts"
        assert rl.steps[0].scene_id == "s1"
        # Windows 定时器精度约 15ms：sleep(0.05) 最少也有 34ms，
        # 断言放到 20ms 以内既验证了计时确实生效，又不会被时钟粒度打成偶发失败。
        assert rl.steps[0].duration >= 0.02

    def test_cache_hit_tracking(self):
        rl = RenderLogger("test")
        with rl.step("render", scene_id="s1") as ctx:
            ctx.cache_hit = True
        with rl.step("render", scene_id="s2") as ctx:
            ctx.cache_hit = False

        summary = rl.summary()
        assert summary["cache_hits"] == 1
        assert summary["cache_misses"] == 1

    def test_error_tracking(self):
        rl = RenderLogger("test")
        try:
            with rl.step("fail_step") as ctx:
                raise ValueError("boom")
        except ValueError:
            pass

        assert rl.steps[0].error == "boom"
        summary = rl.summary()
        assert summary["errors"] == 1

    def test_summary_structure(self):
        rl = RenderLogger("pipeline")
        with rl.step("a"):
            pass
        with rl.step("b"):
            pass

        s = rl.summary()
        assert s["pipeline"] == "pipeline"
        assert s["steps"] == 2
        assert s["total_duration"] >= 0
        assert len(s["details"]) == 2

    def test_reset(self):
        rl = RenderLogger("test")
        with rl.step("a"):
            pass
        assert len(rl.steps) == 1
        rl.reset()
        assert len(rl.steps) == 0

    def test_metadata_in_step(self):
        rl = RenderLogger("test")
        with rl.step("render", scene_id="s1") as ctx:
            ctx.metadata["frames"] = 90
            ctx.metadata["fps"] = 30

        assert rl.steps[0].metadata["frames"] == 90

    def test_multiple_steps_same_name(self):
        rl = RenderLogger("test")
        with rl.step("tts", scene_id="s1"):
            pass
        with rl.step("tts", scene_id="s2"):
            pass
        with rl.step("render", scene_id="s1"):
            pass

        summary = rl.summary()
        assert summary["steps"] == 3

    def test_log_summary(self, caplog):
        import logging
        with caplog.at_level(logging.INFO):
            rl = RenderLogger("test")
            with rl.step("a"):
                pass
            rl.log_summary()

        assert "Pipeline complete" in caplog.text

    def test_step_context_metadata(self):
        rl = RenderLogger("test")
        with rl.step("complex", scene_id="s1") as ctx:
            ctx.cache_hit = True
            ctx.metadata["output_path"] = "/tmp/out.mp4"
            ctx.metadata["size_bytes"] = 1024000

        step = rl.steps[0]
        assert step.cache_hit is True
        assert step.metadata["output_path"] == "/tmp/out.mp4"


class TestStressFaultInjection:
    """Simulate various failure scenarios."""

    def test_retry_with_intermittent_failure(self):
        """Test retry handles intermittent failures gracefully."""
        call_log = []

        @retry(RetryPolicy(max_attempts=5, base_delay=0.001))
        def intermittent():
            call_log.append(time.monotonic())
            if len(call_log) < 4:
                raise ConnectionError("network blip")
            return "ok"

        result = intermittent()
        assert result == "ok"
        assert len(call_log) == 4

    def test_recovery_after_all_failures(self):
        """Test recovery handles the case where all attempts fail."""
        recovery = CrashRecovery()
        recovery.mark_started("stress_1", "hash_1")
        recovery.mark_failed("stress_1", "all workers down")

        failed = recovery.get_failed()
        assert len(failed) == 1
        assert "all workers down" in failed[0]["error"]

    def test_logger_with_many_steps(self):
        """Test logger handles many concurrent steps."""
        rl = RenderLogger("stress")

        for i in range(100):
            with rl.step(f"step_{i}", scene_id=f"s{i}") as ctx:
                if i % 10 == 0:
                    ctx.cache_hit = True

        summary = rl.summary()
        assert summary["steps"] == 100
        assert summary["cache_hits"] == 10

    def test_recovery_manifest_integrity(self, tmp_path):
        """Test manifest survives many concurrent writes."""
        recovery = CrashRecovery(state_dir=tmp_path / "stress")

        def writer(prefix, count):
            for i in range(count):
                sid = f"{prefix}_{i}"
                recovery.mark_started(sid, f"hash_{i}")
                if i % 2 == 0:
                    recovery.mark_completed(sid)
                else:
                    recovery.mark_failed(sid, "error")

        threads = [threading.Thread(target=writer, args=(f"t{t}", 50)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Manifest should be valid JSON
        recovery2 = CrashRecovery(state_dir=tmp_path / "stress")
        assert isinstance(recovery2.manifest, dict)
        assert len(recovery2.manifest) == 200
