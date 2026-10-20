"""Tests for Runtime Inspector — CLI viewer for reactive runtime."""

import pytest
from unittest.mock import MagicMock
from compiler.thinking.inspector import (
    print_dag_tree, print_artifact_table, print_invalidation_trace,
    print_runtime_graph, print_scheduler_metrics, print_event_log,
    print_performance_profile, inspect_all,
)
from compiler.thinking.event_bus import EventBus, Event
from compiler.thinking.artifacts import ArtifactGraph, ArtifactType, ArtifactStatus, VideoArtifact


@pytest.fixture
def graph():
    g = ArtifactGraph()
    a1 = g.create(artifact_type=ArtifactType.SCRIPT, artifact_id="a1")
    a1.content_hash = "abc123def"
    a2 = g.create(artifact_type=ArtifactType.SCENE_VIDEO, artifact_id="a2", depends_on=["a1"])
    a2.content_hash = "def456ghi"
    g.invalidate("a2", reason="upstream changed")
    return g


@pytest.fixture
def bus():
    b = EventBus()
    b.publish(Event(type="thinking", source="agent", data={"step": 1}))
    b.publish(Event(type="edit", source="user", data={"target": "s1"}))
    return b


class TestPrintFunctions:
    """All print functions should execute without errors."""

    def test_print_dag_tree(self, graph, capsys):
        print_dag_tree(graph)
        output = capsys.readouterr().out
        assert "script" in output.lower()
        assert "fresh" in output.lower()

    def test_print_dag_tree_empty(self, capsys):
        print_dag_tree(ArtifactGraph())
        output = capsys.readouterr().out
        assert "empty" in output.lower()

    def test_print_artifact_table(self, graph, capsys):
        print_artifact_table(graph)
        output = capsys.readouterr().out
        assert "script" in output.lower()
        assert "scene_video" in output.lower()

    def test_print_invalidation_trace_no_stale(self, capsys):
        g = ArtifactGraph()
        a1 = g.create(artifact_type=ArtifactType.SCRIPT, artifact_id="a1")
        a1.content_hash = "abc"
        print_invalidation_trace(g)
        output = capsys.readouterr().out
        assert "clean" in output.lower() or "No stale" in output

    def test_print_invalidation_trace_with_stale(self, graph, capsys):
        print_invalidation_trace(graph)
        output = capsys.readouterr().out
        assert "stale" in output.lower()

    def test_print_event_log(self, bus, capsys):
        print_event_log(bus)
        output = capsys.readouterr().out
        assert "thinking" in output

    def test_print_event_log_empty(self, capsys):
        print_event_log(EventBus())
        output = capsys.readouterr().out
        assert "No events" in output

    def test_inspect_all_with_all_args(self, graph, bus, capsys):
        # Mock scheduler and runtime graph
        scheduler = MagicMock()
        scheduler.get_status.return_value = {"cache": {"entries": 0, "hits": 0, "misses": 0, "hit_rate": "0%"}}
        scheduler.get_plan.return_value = []
        scheduler.graph = MagicMock()
        scheduler.graph.nodes = {}

        inspect_all(artifact_graph=graph, scheduler=scheduler, event_bus=bus)
        output = capsys.readouterr().out
        assert "Inspector" in output

    def test_inspect_all_empty(self, capsys):
        inspect_all()
        output = capsys.readouterr().out
        assert "Inspector" in output

    def test_print_runtime_graph(self, capsys):
        from compiler.thinking.runtime_graph import RuntimeGraph
        rg = RuntimeGraph()
        rg.add_node("n1", depends_on=[])
        rg.add_node("n2", depends_on=["n1"])
        print_runtime_graph(rg)
        output = capsys.readouterr().out
        assert "n1" in output

    def test_print_scheduler_metrics(self, capsys):
        scheduler = MagicMock()
        scheduler.get_status.return_value = {
            "cache": {"entries": 5, "hits": 3, "misses": 2, "hit_rate": "60%"},
        }
        scheduler.get_plan.return_value = []
        scheduler.graph = MagicMock()
        scheduler.graph.nodes = {}
        print_scheduler_metrics(scheduler)
        output = capsys.readouterr().out
        assert "60%" in output

    def test_print_performance_profile_no_data(self, capsys):
        scheduler = MagicMock()
        scheduler.graph = MagicMock()
        scheduler.graph.nodes = {}
        print_performance_profile(scheduler)
        output = capsys.readouterr().out
        assert "No timing" in output
