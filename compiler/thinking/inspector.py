"""Runtime Inspector — CLI viewer for the reactive runtime.

Reads state from ArtifactGraph, RuntimeGraph, Scheduler, and EventBus
to produce human-readable runtime diagnostics.

Usage:
    from compiler.thinking.inspector import inspect_all
    inspect_all(adapter, scheduler)

CLI:
    python main.py --topic "Redis" --inspect
"""

from __future__ import annotations

import time
from typing import Any, Optional

from compiler.thinking.artifacts import ArtifactGraph, ArtifactStatus, ArtifactType, VideoArtifact
from compiler.thinking.runtime_graph import RuntimeGraph, NodeStatus
from compiler.thinking.scheduler import Scheduler
from compiler.thinking.event_bus import EventBus, get_event_bus


# ── ANSI colors for terminal output ──
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RED = "\033[91m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_BLUE = "\033[94m"
_CYAN = "\033[96m"
_GRAY = "\033[90m"

_STATUS_COLORS = {
    "fresh": _GREEN,
    "stale": _YELLOW,
    "computing": _CYAN,
    "failed": _RED,
    "cached": _BLUE,
    "tts_sentence": _CYAN,
    "scene_ir": _BLUE,
    "scene_video": _GREEN,
    "pending": _GRAY,
    "done": _GREEN,
    "invalidated": _YELLOW,
    "skipped": _DIM,
}


def _colorize(text: str, color: str) -> str:
    return f"{color}{text}{_RESET}"


def _status_badge(status: str) -> str:
    color = _STATUS_COLORS.get(status, _GRAY)
    return _colorize(f"[{status.upper()}]", color)


# ============================================================
# DAG Tree Viewer
# ============================================================

def print_dag_tree(graph: ArtifactGraph, title: str = "Artifact DAG"):
    """Print a tree representation of the artifact dependency graph."""
    print(f"\n{_BOLD}{_CYAN}{'─' * 50}{_RESET}")
    print(f"{_BOLD}{_CYAN}  {title}{_RESET}")
    print(f"{_BOLD}{_CYAN}{'─' * 50}{_RESET}")

    # Find root artifacts (no upstream)
    roots = []
    for art in graph._artifacts.values():
        if not art.depends_on:
            roots.append(art)

    if not roots:
        print(f"  {_DIM}(empty graph){_RESET}")
        return

    visited: set[str] = set()

    def _print_tree(art: VideoArtifact, prefix: str, is_last: bool):
        if art.id in visited:
            connector = "└── " if is_last else "├── "
            print(f"  {prefix}{connector}{_colorize(art.type.value, _BOLD)} {_DIM}(→ {art.id[:16]}...){_RESET}")
            return
        visited.add(art.id)

        status = art.status.value
        badge = _status_badge(status)
        version = _colorize(f"v{art.version}", _DIM)
        hash_short = _colorize(art.content_hash[:8], _DIM)

        connector = "└── " if is_last else "├── "
        line = f"  {prefix}{connector}{_colorize(art.type.value, _BOLD)} {badge} {version} {hash_short}"

        # Add metadata hints
        meta_hints = []
        if art.metadata.get("topic"):
            meta_hints.append(f"topic={art.metadata['topic'][:20]}")
        if art.metadata.get("stale_reason"):
            meta_hints.append(_colorize(f"reason={art.metadata['stale_reason']}", _YELLOW))
        if meta_hints:
            line += f" {_DIM}({', '.join(meta_hints)}){_RESET}"

        print(line)

        # Print downstream children
        downstream = graph.get_downstream(art.id)
        child_prefix = prefix + ("    " if is_last else "│   ")
        for i, child in enumerate(downstream):
            _print_tree(child, child_prefix, i == len(downstream) - 1)

    for i, root in enumerate(roots):
        _print_tree(root, "", i == len(roots) - 1)


# ============================================================
# Artifact State Table
# ============================================================

def print_artifact_table(graph: ArtifactGraph):
    """Print a table of all artifacts with their states."""
    print(f"\n{_BOLD}{_BLUE}{'─' * 70}{_RESET}")
    print(f"{_BOLD}{_BLUE}  Artifact State Table{_RESET}")
    print(f"{_BOLD}{_BLUE}{'─' * 70}{_RESET}")
    print(f"  {_BOLD}{'Type':<18} {'Status':<14} {'Ver':>4} {'Hash':<10} {'Deps':<20}{_RESET}")
    print(f"  {'─' * 66}")

    for art in graph._artifacts.values():
        status = art.status.value
        badge = _status_badge(status)
        deps = ", ".join(d[:12] for d in art.depends_on) or "—"
        hash_short = art.content_hash[:8] if art.content_hash else "—"

        print(f"  {art.type.value:<18} {badge:<23} v{art.version:<3} {hash_short:<10} {deps:<20}")

    summary = graph.summary()
    print(f"\n  {_DIM}Total: {summary['total_artifacts']} artifacts, "
          f"{summary['stale_count']} stale{_RESET}")


# ============================================================
# Invalidation Trace
# ============================================================

def print_invalidation_trace(graph: ArtifactGraph, source_id: str = ""):
    """Print the invalidation chain from a source artifact."""
    print(f"\n{_BOLD}{_YELLOW}{'─' * 50}{_RESET}")
    print(f"{_BOLD}{_YELLOW}  Invalidation Trace{_RESET}")
    print(f"{_BOLD}{_YELLOW}{'─' * 50}{_RESET}")

    stale = graph.get_stale()
    if not stale:
        print(f"  {_GREEN}No stale artifacts — runtime is clean.{_RESET}")
        return

    print(f"  {_YELLOW}{len(stale)} stale artifact(s):{_RESET}\n")

    for art in stale:
        reason = art.metadata.get("stale_reason", "unknown")
        print(f"  {_colorize(art.type.value, _BOLD)} {_status_badge(art.status.value)}")
        print(f"    {_DIM}reason: {reason}{_RESET}")

        # Show what this invalidates downstream
        downstream = graph.get_transitive_downstream(art.id)
        if downstream:
            chain = " → ".join(d.type.value for d in downstream)
            print(f"    {_DIM}downstream: {chain}{_RESET}")
        print()

    # Show recompute order
    order = graph.get_recompute_order()
    if order:
        order_str = " → ".join(a.type.value for a in order)
        print(f"  {_BOLD}Recompute order:{_RESET} {order_str}")


# ============================================================
# Runtime Graph Viewer
# ============================================================

def print_runtime_graph(graph: RuntimeGraph):
    """Print the runtime graph nodes and their states."""
    print(f"\n{_BOLD}{_MAGENTA}{'─' * 50}{_RESET}")
    print(f"{_BOLD}{_MAGENTA}  Runtime Graph{_RESET}")
    print(f"{_BOLD}{_MAGENTA}{'─' * 50}{_RESET}")

    for node in graph.topological_order():
        status = node.status.value
        badge = _status_badge(status)
        deps = ", ".join(node.depends_on) or "—"
        duration = ""
        if node.duration > 0:
            duration = _colorize(f" ({node.duration:.2f}s)", _DIM)
        error = ""
        if node.error:
            error = _colorize(f" ERROR: {node.error}", _RED)

        print(f"  {_colorize(node.id, _BOLD)} {badge}{duration}{error}")
        if deps != "—":
            print(f"    {_DIM}depends on: {deps}{_RESET}")


# ============================================================
# Scheduler Metrics
# ============================================================

def print_scheduler_metrics(scheduler: Scheduler):
    """Print scheduler cache stats and execution plan."""
    print(f"\n{_BOLD}{_GREEN}{'─' * 50}{_RESET}")
    print(f"{_BOLD}{_GREEN}  Scheduler Metrics{_RESET}")
    print(f"{_BOLD}{_GREEN}{'─' * 50}{_RESET}")

    status = scheduler.get_status()
    cache = status.get("cache", {})

    print(f"  Cache entries:  {cache.get('entries', 0)}")
    print(f"  Cache hits:     {_colorize(str(cache.get('hits', 0)), _GREEN)}")
    print(f"  Cache misses:   {_colorize(str(cache.get('misses', 0)), _YELLOW)}")
    print(f"  Hit rate:       {_colorize(cache.get('hit_rate', '0%'), _GREEN)}")

    # Execution plan
    plan = scheduler.get_plan()
    pending = [p for p in plan if p["will_recompute"]]
    if pending:
        print(f"\n  {_BOLD}Pending recompute:{_RESET}")
        for p in pending:
            deps = ", ".join(p["depends_on"]) or "—"
            print(f"    {_colorize(p['node_id'], _BOLD)} ({p['name']}) {_status_badge(p['status'])}")
            print(f"      {_DIM}depends on: {deps}{_RESET}")
    else:
        print(f"\n  {_GREEN}All nodes up to date.{_RESET}")


# ============================================================
# Event Log
# ============================================================

def print_event_log(bus: EventBus, limit: int = 15):
    """Print recent runtime events."""
    print(f"\n{_BOLD}{_GRAY}{'─' * 50}{_RESET}")
    print(f"{_BOLD}{_GRAY}  Recent Events (last {limit}){_RESET}")
    print(f"{_BOLD}{_GRAY}{'─' * 50}{_RESET}")

    events = bus.get_log(limit=limit)
    if not events:
        print(f"  {_DIM}No events recorded.{_RESET}")
        return

    for evt in events:
        ts = time.strftime("%H:%M:%S", time.localtime(evt.timestamp))
        source = _colorize(evt.source or "?", _CYAN)
        etype = _colorize(evt.type, _BOLD)

        data_str = ""
        if isinstance(evt.data, dict):
            parts = [f"{k}={v}" for k, v in list(evt.data.items())[:3]]
            data_str = f" {_DIM}{', '.join(parts)}{_RESET}"
        elif evt.data:
            data_str = f" {_DIM}{str(evt.data)[:60]}{_RESET}"

        print(f"  {_DIM}{ts}{_RESET} {source} {etype}{data_str}")


# ============================================================
# Performance Profile
# ============================================================

def print_performance_profile(scheduler: Scheduler):
    """Print per-node execution time breakdown."""
    print(f"\n{_BOLD}{_CYAN}{'─' * 50}{_RESET}")
    print(f"{_BOLD}{_CYAN}  Performance Profile{_RESET}")
    print(f"{_BOLD}{_CYAN}{'─' * 50}{_RESET}")

    timings: list[tuple[str, float]] = []
    for node in scheduler.graph.nodes.values():
        if node.duration > 0:
            timings.append((node.id, node.duration))

    if not timings:
        print(f"  {_DIM}No timing data yet.{_RESET}")
        return

    timings.sort(key=lambda x: x[1], reverse=True)
    max_dur = timings[0][1] if timings else 1

    print(f"  {_BOLD}{'Node':<20} {'Duration':>8}  {'Bar'}{_RESET}")
    print(f"  {'─' * 46}")

    for node_id, dur in timings:
        bar_len = max(1, int(dur / max_dur * 20))
        bar = _colorize("█" * bar_len, _CYAN)
        dur_str = _colorize(f"{dur:.3f}s", _BOLD)
        print(f"  {node_id:<20} {dur_str:>8}  {bar}")

    total = sum(d for _, d in timings)
    print(f"\n  {_DIM}Total: {total:.3f}s across {len(timings)} nodes{_RESET}")

    # Retry stats
    if hasattr(scheduler, 'max_retries'):
        print(f"  {_DIM}Retry config: max={scheduler.max_retries}, base_delay={scheduler.base_delay}s{_RESET}")


# ============================================================
# Combined Inspector
# ============================================================

def inspect_all(
    artifact_graph: Optional[ArtifactGraph] = None,
    runtime_graph: Optional[RuntimeGraph] = None,
    scheduler: Optional[Scheduler] = None,
    event_bus: Optional[EventBus] = None,
    title: str = "Runtime Inspector",
):
    """Print a complete runtime inspection."""
    print(f"\n{_BOLD}{'═' * 50}{_RESET}")
    print(f"{_BOLD}  {title}{_RESET}")
    print(f"{_BOLD}{'═' * 50}{_RESET}")

    if artifact_graph:
        print_dag_tree(artifact_graph)
        print_artifact_table(artifact_graph)
        print_invalidation_trace(artifact_graph)

    if runtime_graph:
        print_runtime_graph(runtime_graph)

    if scheduler:
        print_scheduler_metrics(scheduler)
        print_performance_profile(scheduler)

    if event_bus:
        print_event_log(event_bus)

    print(f"\n{_BOLD}{'═' * 50}{_RESET}")
    print(f"{_BOLD}  End of Inspection{_RESET}")
    print(f"{_BOLD}{'═' * 50}{_RESET}\n")


# Missing ANSI code — _MAGENTA
_MAGENTA = "\033[95m"
