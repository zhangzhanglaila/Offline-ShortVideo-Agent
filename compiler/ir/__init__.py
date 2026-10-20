"""Multi-Level IR — Intermediate Representations for Media Compilation.

The IR pipeline:
    Intent IR → Narrative IR → Scene IR → Timeline IR → Render IR

Each level is:
  - Deterministically canonicalizable
  - Content-hashable
  - Validated (structural constraints)
  - Independently cacheable
"""

from compiler.ir.intent_ir import IntentIR, Tone, Platform, AspectRatio
from compiler.ir.narrative_ir import (
    NarrativeIR, Beat, BeatType,
    HookBeat, ProblemBeat, RevealBeat, CTABeat,
)
from compiler.ir.timeline_ir import (
    TimelineIR, Track, TrackType, Transition, TransitionEffect,
)
from compiler.ir.render_ir import (
    RenderIR, RenderCommand, RenderInput, RenderBackend, CommandType,
)
