"""Tests for PipelineDAG — pipeline execution tracking."""

import time

import pytest
from runtime.dag import PipelineDAG, NodeStatus


class TestPipelineDAG:
    @pytest.fixture
    def dag(self):
        return PipelineDAG("test_pipeline")

    def test_add_node(self, dag):
        node = dag.add_node("script", "ScriptPass")
        assert node.id == "script"
        assert node.name == "ScriptPass"
        assert node.status == NodeStatus.PENDING

    def test_add_node_with_deps(self, dag):
        dag.add_node("script", "ScriptPass")
        dag.add_node("tts", "TTSPass", depends_on=["script"])
        assert dag.nodes["tts"].depends_on == ["script"]

    def test_start_node(self, dag):
        dag.add_node("tts", "TTSPass")
        dag.start("tts")
        assert dag.nodes["tts"].status == NodeStatus.RUNNING
        assert dag.nodes["tts"].started_at is not None

    def test_complete_node(self, dag):
        dag.add_node("tts", "TTSPass")
        dag.start("tts")
        # Windows 计时精度约 15ms：sleep(0.01) 可能被舍入成 0，
        # 这里留足余量，只验证"确实记录到了耗时"。
        time.sleep(0.05)
        dag.complete("tts", cache_hit=True)
        assert dag.nodes["tts"].status == NodeStatus.DONE
        assert dag.nodes["tts"].cache_hit is True
        assert dag.nodes["tts"].duration is not None
        assert dag.nodes["tts"].duration > 0.02

    def test_complete_with_outputs(self, dag):
        dag.add_node("tts", "TTSPass")
        dag.start("tts")
        dag.complete("tts", outputs={"audio_path": "/tmp/a.mp3"})
        assert dag.nodes["tts"].outputs["audio_path"] == "/tmp/a.mp3"

    def test_fail_node(self, dag):
        dag.add_node("tts", "TTSPass")
        dag.start("tts")
        dag.fail("tts", "OOM error")
        assert dag.nodes["tts"].status == NodeStatus.ERROR
        assert dag.nodes["tts"].error == "OOM error"

    def test_skip_node(self, dag):
        dag.add_node("asset", "AssetPass")
        dag.skip("asset", "no assets needed")
        assert dag.nodes["asset"].status == NodeStatus.SKIPPED

    def test_get_dependencies(self, dag):
        dag.add_node("script", "ScriptPass")
        dag.add_node("tts", "TTSPass", depends_on=["script"])
        deps = dag.get_dependencies("tts")
        assert len(deps) == 1
        assert deps[0].id == "script"

    def test_get_dependents(self, dag):
        dag.add_node("script", "ScriptPass")
        dag.add_node("tts", "TTSPass", depends_on=["script"])
        dag.add_node("timeline", "TimelinePass", depends_on=["script", "tts"])
        dependents = dag.get_dependents("script")
        assert len(dependents) == 2

    def test_ready_nodes_all_pending(self, dag):
        dag.add_node("script", "ScriptPass")
        dag.add_node("tts", "TTSPass", depends_on=["script"])
        dag.add_node("asset", "AssetPass")

        ready = dag.ready_nodes()
        ready_ids = {n.id for n in ready}
        assert "script" in ready_ids
        assert "asset" in ready_ids
        assert "tts" not in ready_ids  # depends on script

    def test_ready_nodes_after_complete(self, dag):
        dag.add_node("script", "ScriptPass")
        dag.add_node("tts", "TTSPass", depends_on=["script"])
        dag.start("script")
        dag.complete("script")

        ready = dag.ready_nodes()
        assert len(ready) == 1
        assert ready[0].id == "tts"

    def test_is_complete(self, dag):
        dag.add_node("a", "PassA")
        dag.add_node("b", "PassB")
        assert not dag.is_complete()

        dag.start("a")
        dag.complete("a")
        assert not dag.is_complete()

        dag.start("b")
        dag.complete("b")
        assert dag.is_complete()

    def test_is_complete_with_error(self, dag):
        dag.add_node("a", "PassA")
        dag.start("a")
        dag.fail("a", "boom")
        assert dag.is_complete()  # error counts as "done" for completion

    def test_to_dict(self, dag):
        dag.add_node("script", "ScriptPass", inputs={"topic": "Redis"})
        dag.add_node("tts", "TTSPass", depends_on=["script"])
        dag.start("script")
        dag.complete("script", cache_hit=True)

        d = dag.to_dict()
        assert d["name"] == "test_pipeline"
        assert "nodes" in d
        assert "edges" in d
        assert len(d["edges"]) == 1
        assert d["edges"][0] == {"from": "script", "to": "tts"}
        assert d["is_complete"] is False
        assert d["stats"]["cache_hits"] == 1

    def test_stats(self, dag):
        dag.add_node("a", "PassA")
        dag.add_node("b", "PassB")
        dag.add_node("c", "PassC")
        dag.start("a")
        dag.complete("a", cache_hit=True)
        dag.start("b")
        dag.fail("b", "err")
        # c still pending

        stats = dag.stats()
        assert stats.get("done", 0) == 1
        assert stats.get("error", 0) == 1
        assert stats.get("pending", 0) == 1
        assert stats.get("cache_hits", 0) == 1

    def test_critical_path_linear(self, dag):
        dag.add_node("a", "PassA")
        dag.add_node("b", "PassB", depends_on=["a"])
        dag.add_node("c", "PassC", depends_on=["b"])

        # Set durations
        dag.start("a"); dag.complete("a"); dag.nodes["a"].duration = 0.1
        dag.start("b"); dag.complete("b"); dag.nodes["b"].duration = 0.5
        dag.start("c"); dag.complete("c"); dag.nodes["c"].duration = 0.2

        path = dag.critical_path()
        assert path == ["a", "b", "c"]

    def test_critical_path_branching(self, dag):
        dag.add_node("a", "PassA")
        dag.add_node("b", "PassB", depends_on=["a"])
        dag.add_node("c", "PassC", depends_on=["a"])
        dag.add_node("d", "PassD", depends_on=["b", "c"])

        dag.start("a"); dag.complete("a"); dag.nodes["a"].duration = 0.1
        dag.start("b"); dag.complete("b"); dag.nodes["b"].duration = 0.3
        dag.start("c"); dag.complete("c"); dag.nodes["c"].duration = 0.8
        dag.start("d"); dag.complete("d"); dag.nodes["d"].duration = 0.1

        path = dag.critical_path()
        # Critical path should go through c (longer)
        assert "c" in path
        assert path == ["a", "c", "d"]

    def test_nonexistent_node(self, dag):
        dag.start("ghost")  # Should not raise
        dag.complete("ghost")
        dag.fail("ghost", "err")
        assert dag.get_node("ghost") is None
        assert dag.get_dependencies("ghost") == []

    def test_pipeline_with_full_lifecycle(self, dag):
        """Simulate a full pipeline execution."""
        dag.add_node("script", "ScriptPass", inputs={"topic": "Redis"})
        dag.add_node("tts", "TTSPass", depends_on=["script"])
        dag.add_node("scene", "ScenePass", depends_on=["script"])
        dag.add_node("timeline", "TimelinePass", depends_on=["tts", "scene"])
        dag.add_node("render", "RenderPass", depends_on=["timeline"])

        # Script
        dag.start("script")
        dag.complete("script", outputs={"text": "Redis为什么这么快"})

        # TTS and Scene can run in parallel
        ready = dag.ready_nodes()
        ready_ids = {n.id for n in ready}
        assert ready_ids == {"tts", "scene"}

        dag.start("tts")
        dag.start("scene")
        dag.complete("tts", cache_hit=False, outputs={"audio": "/tmp/a.mp3"})
        dag.complete("scene", outputs={"shots": []})

        # Timeline
        ready = dag.ready_nodes()
        assert len(ready) == 1 and ready[0].id == "timeline"
        dag.start("timeline")
        dag.complete("timeline", outputs={"tracks": []})

        # Render
        dag.start("render")
        dag.complete("render", cache_hit=True)

        assert dag.is_complete()
        d = dag.to_dict()
        assert d["is_complete"] is True
        assert d["stats"]["cache_hits"] == 1
