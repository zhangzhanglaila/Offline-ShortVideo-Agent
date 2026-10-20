"""P7.5 — Render IR Tests.

Verifies:
  - RenderInput construction
  - RenderCommand construction and validation
  - RenderIR construction and DAG validation
  - DAG queries (root, leaf)
  - Canonical form and content hashing
  - Edge cases
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from compiler.ir.render_ir import (
    RenderIR, RenderCommand, RenderInput,
    RenderBackend, CommandType,
)


# ═══════════════════════════════════════════════════════════════════════
# RenderInput
# ═══════════════════════════════════════════════════════════════════════


class TestRenderInput:
    """RenderInput construction."""

    def test_basic_input(self):
        inp = RenderInput(path="/tmp/v0.mp4", content_hash="abc123")
        assert inp.path == "/tmp/v0.mp4"
        assert inp.content_hash == "abc123"

    def test_frozen(self):
        inp = RenderInput(path="/tmp/v0.mp4", content_hash="abc123")
        with pytest.raises(AttributeError):
            inp.path = "changed"  # type: ignore

    def test_to_dict(self):
        inp = RenderInput(path="/tmp/v0.mp4", content_hash="abc123", track_id="v0")
        d = inp.to_dict()
        assert d["path"] == "/tmp/v0.mp4"
        assert d["track_id"] == "v0"


# ═══════════════════════════════════════════════════════════════════════
# RenderCommand
# ═══════════════════════════════════════════════════════════════════════


class TestRenderCommand:
    """RenderCommand construction and validation."""

    def test_basic_command(self):
        cmd = RenderCommand(
            command_id="render_0",
            command_type=CommandType.COMPOSITE,
            backend=RenderBackend.FFMPEG,
            inputs=(RenderInput(path="/tmp/in.mp4", content_hash="abc"),),
            output_path="/tmp/out.mp4",
        )
        assert cmd.command_id == "render_0"
        assert len(cmd.inputs) == 1

    def test_frozen(self):
        cmd = RenderCommand(
            command_id="c0", command_type=CommandType.FILTER,
            backend=RenderBackend.FFMPEG,
            inputs=(), output_path="/tmp/out.mp4",
        )
        with pytest.raises(AttributeError):
            cmd.command_id = "changed"  # type: ignore

    def test_empty_id_rejected(self):
        with pytest.raises(ValueError, match="command_id"):
            RenderCommand(
                command_id="", command_type=CommandType.FILTER,
                backend=RenderBackend.FFMPEG,
                inputs=(), output_path="/tmp/out.mp4",
            )

    def test_empty_output_rejected(self):
        with pytest.raises(ValueError, match="output_path"):
            RenderCommand(
                command_id="c0", command_type=CommandType.FILTER,
                backend=RenderBackend.FFMPEG,
                inputs=(), output_path="",
            )

    def test_input_hashes(self):
        cmd = RenderCommand(
            command_id="c0", command_type=CommandType.COMPOSITE,
            backend=RenderBackend.FFMPEG,
            inputs=(
                RenderInput(path="/a.mp4", content_hash="hash_a"),
                RenderInput(path="/b.mp4", content_hash="hash_b"),
            ),
            output_path="/out.mp4",
        )
        assert cmd.input_hashes == ["hash_a", "hash_b"]

    def test_depends_on(self):
        cmd = RenderCommand(
            command_id="c1", command_type=CommandType.CONCAT,
            backend=RenderBackend.FFMPEG,
            inputs=(), output_path="/out.mp4",
            depends_on=("c0",),
        )
        assert cmd.depends_on == ("c0",)


# ═══════════════════════════════════════════════════════════════════════
# RenderIR
# ═══════════════════════════════════════════════════════════════════════


class TestRenderIRConstruction:
    """RenderIR construction and DAG validation."""

    def _make_render_ir(self) -> RenderIR:
        return RenderIR(
            commands=(
                RenderCommand(
                    command_id="render_hook",
                    command_type=CommandType.COMPOSITE,
                    backend=RenderBackend.FFMPEG,
                    inputs=(RenderInput(path="/hook.mp4", content_hash="h1"),),
                    output_path="/tmp/hook_out.mp4",
                ),
                RenderCommand(
                    command_id="render_graph",
                    command_type=CommandType.COMPOSITE,
                    backend=RenderBackend.FFMPEG,
                    inputs=(RenderInput(path="/graph.mp4", content_hash="h2"),),
                    output_path="/tmp/graph_out.mp4",
                ),
                RenderCommand(
                    command_id="concat",
                    command_type=CommandType.CONCAT,
                    backend=RenderBackend.FFMPEG,
                    inputs=(
                        RenderInput(path="/tmp/hook_out.mp4", content_hash="h1"),
                        RenderInput(path="/tmp/graph_out.mp4", content_hash="h2"),
                    ),
                    output_path="/tmp/final.mp4",
                    depends_on=("render_hook", "render_graph"),
                ),
            ),
            final_output="/tmp/final.mp4",
        )

    def test_basic_construction(self):
        rir = self._make_render_ir()
        assert rir.command_count == 3
        assert rir.final_output == "/tmp/final.mp4"

    def test_frozen(self):
        rir = self._make_render_ir()
        with pytest.raises(AttributeError):
            rir.final_output = "changed"  # type: ignore

    def test_empty_commands_rejected(self):
        with pytest.raises(ValueError, match="commands"):
            RenderIR(commands=(), final_output="/tmp/out.mp4")

    def test_unknown_dependency_rejected(self):
        with pytest.raises(ValueError, match="unknown command"):
            RenderIR(
                commands=(
                    RenderCommand(
                        command_id="c0", command_type=CommandType.FILTER,
                        backend=RenderBackend.FFMPEG,
                        inputs=(), output_path="/tmp/out.mp4",
                        depends_on=("nonexistent",),
                    ),
                ),
                final_output="/tmp/out.mp4",
            )


class TestDAGQueries:
    """Root and leaf command queries."""

    def test_root_commands(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="a", command_type=CommandType.FILTER,
                    backend=RenderBackend.FFMPEG, inputs=(),
                    output_path="/a.mp4",
                ),
                RenderCommand(
                    command_id="b", command_type=CommandType.FILTER,
                    backend=RenderBackend.FFMPEG, inputs=(),
                    output_path="/b.mp4",
                ),
                RenderCommand(
                    command_id="c", command_type=CommandType.CONCAT,
                    backend=RenderBackend.FFMPEG, inputs=(),
                    output_path="/c.mp4", depends_on=("a", "b"),
                ),
            ),
            final_output="/c.mp4",
        )
        roots = rir.root_commands()
        assert len(roots) == 2
        assert {r.command_id for r in roots} == {"a", "b"}

    def test_leaf_commands(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="a", command_type=CommandType.FILTER,
                    backend=RenderBackend.FFMPEG, inputs=(),
                    output_path="/a.mp4",
                ),
                RenderCommand(
                    command_id="b", command_type=CommandType.FILTER,
                    backend=RenderBackend.FFMPEG, inputs=(),
                    output_path="/b.mp4",
                ),
                RenderCommand(
                    command_id="c", command_type=CommandType.CONCAT,
                    backend=RenderBackend.FFMPEG, inputs=(),
                    output_path="/c.mp4", depends_on=("a", "b"),
                ),
            ),
            final_output="/c.mp4",
        )
        leaves = rir.leaf_commands()
        assert len(leaves) == 1
        assert leaves[0].command_id == "c"

    def test_command_by_id(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="x", command_type=CommandType.FILTER,
                    backend=RenderBackend.FFMPEG, inputs=(),
                    output_path="/x.mp4",
                ),
            ),
            final_output="/x.mp4",
        )
        assert rir.command_by_id("x") is not None
        assert rir.command_by_id("nonexistent") is None


# ═══════════════════════════════════════════════════════════════════════
# Canonical Form & Hashing
# ═══════════════════════════════════════════════════════════════════════


class TestCanonicalization:
    """Canonical form and content hashing."""

    def test_deterministic_hash(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.FILTER,
                    backend=RenderBackend.FFMPEG, inputs=(),
                    output_path="/out.mp4",
                ),
            ),
            final_output="/out.mp4",
        )
        hashes = [rir.content_hash() for _ in range(100)]
        assert len(set(hashes)) == 1

    def test_different_commands_different_hash(self):
        rir1 = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.FILTER,
                    backend=RenderBackend.FFMPEG, inputs=(),
                    output_path="/out.mp4", params={"scale": "720"},
                ),
            ),
            final_output="/out.mp4",
        )
        rir2 = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.FILTER,
                    backend=RenderBackend.FFMPEG, inputs=(),
                    output_path="/out.mp4", params={"scale": "1080"},
                ),
            ),
            final_output="/out.mp4",
        )
        assert rir1.content_hash() != rir2.content_hash()

    def test_same_content_same_hash(self):
        rir1 = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.COPY,
                    backend=RenderBackend.COPY, inputs=(),
                    output_path="/out.mp4",
                ),
            ),
            final_output="/out.mp4",
        )
        rir2 = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.COPY,
                    backend=RenderBackend.COPY, inputs=(),
                    output_path="/out.mp4",
                ),
            ),
            final_output="/out.mp4",
        )
        assert rir1.content_hash() == rir2.content_hash()


# ═══════════════════════════════════════════════════════════════════════
# Edge Cases
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Boundary conditions."""

    def test_single_command(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.COPY,
                    backend=RenderBackend.COPY, inputs=(),
                    output_path="/out.mp4",
                ),
            ),
            final_output="/out.mp4",
        )
        assert rir.command_count == 1

    def test_all_backends(self):
        for backend in RenderBackend:
            rir = RenderIR(
                commands=(
                    RenderCommand(
                        command_id="c0", command_type=CommandType.FILTER,
                        backend=backend, inputs=(),
                        output_path="/out.mp4",
                    ),
                ),
                final_output="/out.mp4",
                backend=backend,
            )
            assert rir.backend == backend

    def test_all_command_types(self):
        for ct in CommandType:
            rir = RenderIR(
                commands=(
                    RenderCommand(
                        command_id="c0", command_type=ct,
                        backend=RenderBackend.FFMPEG, inputs=(),
                        output_path="/out.mp4",
                    ),
                ),
                final_output="/out.mp4",
            )
            assert rir.commands[0].command_type == ct

    def test_summary(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.FILTER,
                    backend=RenderBackend.FFMPEG, inputs=(),
                    output_path="/out.mp4",
                ),
            ),
            final_output="/out.mp4",
        )
        s = rir.summary()
        assert "1 commands" in s
        assert "ffmpeg" in s

    def test_command_graph(self):
        rir = RenderIR(
            commands=(
                RenderCommand(
                    command_id="c0", command_type=CommandType.FILTER,
                    backend=RenderBackend.FFMPEG, inputs=(),
                    output_path="/out.mp4",
                ),
            ),
            final_output="/out.mp4",
        )
        g = rir.command_graph()
        assert "c0" in g
        assert "filter" in g


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
