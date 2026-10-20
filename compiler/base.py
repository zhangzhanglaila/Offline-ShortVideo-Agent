"""Compiler Pass — Base classes for IR-to-IR transformations.

A CompilerPass transforms one IR level into the next. Each pass:
  - Is a pure function (deterministic for same input)
  - Returns a PassResult (output + metadata)
  - Is cacheable by input content hash
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from compiler.thinking.canonicalize import content_hash

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass
class PassResult:
    """Result of a compiler pass execution."""
    output: Any                     # The output IR
    input_hash: str                 # Content hash of the input
    output_hash: str                # Content hash of the output
    pass_name: str                  # Name of the pass
    duration: float = 0.0           # Execution time in seconds
    cached: bool = False            # Whether output was from cache
    metadata: dict[str, Any] = field(default_factory=dict)


class CompilerPass(Generic[InputT, OutputT]):
    """Base class for compiler passes.

    Subclasses implement `transform()` — the actual IR-to-IR logic.
    The base class handles timing, hashing, and caching.

    Usage:
        pass_ = IntentToNarrativePass()
        result = pass_.run(intent_ir)
        narrative = result.output
    """

    name: str = "base_pass"

    def run(self, input_ir: InputT, *, use_cache: bool = True) -> PassResult:
        """Execute the pass with timing and hashing."""
        input_hash = self._input_hash(input_ir)
        start = time.time()
        output = self.transform(input_ir)
        duration = time.time() - start
        output_hash = content_hash(output.canonical() if hasattr(output, 'canonical') else output)

        return PassResult(
            output=output,
            input_hash=input_hash,
            output_hash=output_hash,
            pass_name=self.name,
            duration=duration,
        )

    def transform(self, input_ir: InputT) -> OutputT:
        """The actual transformation logic. Subclasses must implement."""
        raise NotImplementedError

    def _input_hash(self, input_ir: InputT) -> str:
        """Compute content hash of the input."""
        if hasattr(input_ir, 'content_hash'):
            return input_ir.content_hash()
        return content_hash(input_ir)


class PassPipeline:
    """Ordered sequence of compiler passes.

    Usage:
        pipeline = PassPipeline([
            IntentToNarrativePass(),
            NarrativeToScenePass(),
        ])
        results = pipeline.run(intent_ir)
        # results[-1].output is the final IR
    """

    def __init__(self, passes: list[CompilerPass] | None = None):
        self.passes: list[CompilerPass] = passes or []

    def add(self, pass_: CompilerPass) -> PassPipeline:
        self.passes.append(pass_)
        return self

    def run(self, input_ir: Any) -> list[PassResult]:
        """Run all passes in sequence. Returns list of results."""
        results = []
        current = input_ir

        for pass_ in self.passes:
            result = pass_.run(current)
            results.append(result)
            current = result.output

        return results

    @property
    def pass_names(self) -> list[str]:
        return [p.name for p in self.passes]
