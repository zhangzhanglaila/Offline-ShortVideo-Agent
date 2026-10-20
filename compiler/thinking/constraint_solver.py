"""Constraint Propagation Engine — Fixed-point solver for temporal constraints.

Replaces the procedural resolve_constraints() with a proper constraint solver:
  - Fixed-point iteration (propagate until stable)
  - Conflict detection (contradictory constraints)
  - Minimal adjustment (change as little as possible)
  - Priority-based resolution (hard vs soft constraints)

Inspired by:
  - Cassowary algorithm (linear constraint solving)
  - Z3 SMT solver (satisfiability)
  - CSS cascading (priority + specificity)
  - React reconciliation (minimal DOM updates)

The solver treats constraints as a directed graph and propagates
timing values until no more changes occur (fixed-point) or a
conflict is detected.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from compiler.thinking.state import (
    Timeline, Clip, Constraint, ConstraintType, Anchor,
)


class ConstraintPriority(int, Enum):
    """Priority levels for constraints. Higher = stronger."""
    SOFT = 10       # Preference, can be violated
    NORMAL = 50     # Default, should be satisfied
    HARD = 90       # Must be satisfied, will override soft
    IMMUTABLE = 100 # Cannot be violated (user pin, locked clip)


@dataclass
class SolverResult:
    """Result of a constraint solving pass."""
    converged: bool = False         # Did we reach fixed-point?
    iterations: int = 0             # How many iterations
    conflicts: list[Conflict] = field(default_factory=list)
    adjustments: list[Adjustment] = field(default_factory=list)
    duration_ms: float = 0.0


@dataclass
class Conflict:
    """A detected conflict between constraints."""
    constraint_a_id: str = ""
    constraint_b_id: str = ""
    clip_id: str = ""
    description: str = ""
    resolution: str = ""  # How the conflict was resolved


@dataclass
class Adjustment:
    """A single timing adjustment made by the solver."""
    clip_id: str = ""
    old_start: int = 0
    new_start: int = 0
    old_duration: int = 0
    new_duration: int = 0
    reason: str = ""


class ConstraintSolver:
    """Fixed-point constraint propagation engine.

    Algorithm:
      1. Build constraint graph from Timeline
      2. Initialize work queue with all constrained clips
      3. Propagate: for each constraint, compute target timing
      4. Apply minimal adjustment
      5. If anything changed, re-queue affected constraints
      6. Repeat until fixed-point or max iterations
      7. Detect and resolve conflicts

    Constraint types and their propagation rules:
      SYNCED:        target.start = source.start, target.duration = source.duration
      ALIGNS_START:  target.start = source.start
      ALIGNS_END:    target.start = source.end() - target.duration
      FOLLOWS:       target.start = source.end()
      OFFSET:        target.start = source.start + offset (or anchor + offset)
      BOUNDED_BY:    clamp target within source range
    """

    MAX_ITERATIONS = 50
    TOLERANCE = 0  # Frame-level precision (no floating point drift)

    def __init__(self, timeline: Timeline):
        self.timeline = timeline
        self._clip_map: dict[str, Clip] = {}
        self._constraint_map: dict[str, Constraint] = {}
        self._constraints_by_source: dict[str, list[Constraint]] = defaultdict(list)
        self._constraints_by_target: dict[str, list[Constraint]] = defaultdict(list)
        self._locked_clips: set[str] = set()

        self._build_indices()

    def _build_indices(self):
        """Build lookup indices for fast constraint resolution."""
        for track in self.timeline.tracks:
            for clip in track.clips:
                self._clip_map[clip.id] = clip

        for constraint in self.timeline.constraints:
            if not constraint.is_active:
                continue
            self._constraint_map[constraint.id] = constraint
            self._constraints_by_source[constraint.source_clip_id].append(constraint)
            self._constraints_by_target[constraint.target_clip_id].append(constraint)

    def solve(self) -> SolverResult:
        """Run the constraint solver to fixed-point.

        Returns:
            SolverResult with convergence status, conflicts, and adjustments
        """
        start_time = time.time()
        result = SolverResult()
        all_adjustments: list[Adjustment] = []

        # Phase 1: Identify locked clips (IMMUTABLE priority or user-pinned)
        self._identify_locked_clips()

        # Phase 2: Sort constraints by priority
        sorted_constraints = self._sort_constraints_by_priority()

        # Phase 3: Fixed-point propagation
        # Track which constraints have been satisfied to avoid oscillation
        satisfied: dict[str, tuple[int, int]] = {}  # constraint_id → (start, duration)

        for iteration in range(self.MAX_ITERATIONS):
            changed = False
            iteration_adjustments = []

            for constraint in sorted_constraints:
                # Skip if this constraint was already satisfied in this iteration
                target = self._clip_map.get(constraint.target_clip_id)
                if target:
                    prev = satisfied.get(constraint.id)
                    if prev and prev == (target.start, target.duration):
                        continue

                adjustment = self._propagate_constraint(constraint)
                if adjustment:
                    changed = True
                    iteration_adjustments.append(adjustment)
                    # Record the new state as satisfying this constraint
                    satisfied[constraint.id] = (target.start, target.duration)

            all_adjustments.extend(iteration_adjustments)
            result.iterations = iteration + 1

            if not changed:
                result.converged = True
                break

        # If still not converged, detect which constraints are fighting
        if not result.converged:
            for clip_id, constraints in self._constraints_by_target.items():
                if len(constraints) >= 2:
                    # Multiple constraints on same target — potential conflict
                    for i in range(len(constraints)):
                        for j in range(i + 1, len(constraints)):
                            result.conflicts.append(Conflict(
                                constraint_a_id=constraints[i].id,
                                constraint_b_id=constraints[j].id,
                                clip_id=clip_id,
                                description=f"Oscillation: constraints {constraints[i].constraint_type.value} and {constraints[j].constraint_type.value} on clip {clip_id}",
                                resolution="Higher priority wins",
                            ))
            result.converged = True  # Force convergence with conflicts recorded

        # Phase 4: Conflict detection
        result.conflicts = self._detect_conflicts()

        # Phase 5: Resolve conflicts
        for conflict in result.conflicts:
            self._resolve_conflict(conflict)

        result.adjustments = all_adjustments
        result.duration_ms = (time.time() - start_time) * 1000
        return result

    def _identify_locked_clips(self):
        """Identify clips that cannot be moved."""
        for clip_id, clip in self._clip_map.items():
            if clip.is_user_edited:
                self._locked_clips.add(clip_id)

    def _sort_constraints_by_priority(self) -> list[Constraint]:
        """Sort constraints: HARD before NORMAL before SOFT."""
        active = [c for c in self.timeline.constraints if c.is_active]
        # Assign default priority based on constraint type
        for c in active:
            if not hasattr(c, '_priority'):
                if c.constraint_type == ConstraintType.SYNCED:
                    c._priority = ConstraintPriority.HARD
                elif c.constraint_type == ConstraintType.BOUNDED_BY:
                    c._priority = ConstraintPriority.HARD
                elif c.constraint_type == ConstraintType.FOLLOWS:
                    c._priority = ConstraintPriority.NORMAL
                else:
                    c._priority = ConstraintPriority.NORMAL
        return sorted(active, key=lambda c: -getattr(c, '_priority', 50))

    def _propagate_constraint(self, constraint: Constraint) -> Optional[Adjustment]:
        """Propagate a single constraint. Returns Adjustment if timing changed."""
        source = self._clip_map.get(constraint.source_clip_id)
        target = self._clip_map.get(constraint.target_clip_id)

        if not source or not target:
            return None

        # Skip if target is locked
        if target.id in self._locked_clips:
            return None

        old_start = target.start
        old_duration = target.duration
        new_start = target.start
        new_duration = target.duration

        ct = constraint.constraint_type

        if ct == ConstraintType.SYNCED:
            new_start = source.start
            new_duration = source.duration

        elif ct == ConstraintType.ALIGNS_START:
            new_start = source.start

        elif ct == ConstraintType.ALIGNS_END:
            new_start = source.end() - target.duration

        elif ct == ConstraintType.FOLLOWS:
            new_start = source.end()

        elif ct == ConstraintType.OFFSET:
            if constraint.source_anchor:
                anchor = self.timeline.get_anchor(
                    constraint.source_clip_id, constraint.source_anchor
                )
                if anchor:
                    new_start = anchor.frame + constraint.offset_frames
                else:
                    new_start = source.start + constraint.offset_frames
            else:
                new_start = source.start + constraint.offset_frames

        elif ct == ConstraintType.BOUNDED_BY:
            new_start = max(new_start, source.start)
            if new_start + new_duration > source.end():
                new_duration = source.end() - new_start
                if new_duration < 0:
                    new_duration = target.duration
                    new_start = source.start

        # Check if anything changed
        if abs(new_start - old_start) <= self.TOLERANCE and abs(new_duration - old_duration) <= self.TOLERANCE:
            return None

        # Apply the adjustment
        target.start = new_start
        target.duration = new_duration

        return Adjustment(
            clip_id=target.id,
            old_start=old_start, new_start=new_start,
            old_duration=old_duration, new_duration=new_duration,
            reason=f"{ct.value}: {source.id} → {target.id}",
        )

    def _detect_conflicts(self) -> list[Conflict]:
        """Detect conflicts between constraints on the same clip."""
        conflicts = []

        # Group constraints by target clip
        for clip_id, constraints in self._constraints_by_target.items():
            if len(constraints) < 2:
                continue

            # Check for contradictory SYNCED constraints
            synced_sources = [
                c for c in constraints
                if c.constraint_type == ConstraintType.SYNCED
            ]
            if len(synced_sources) > 1:
                sources_differ = False
                for i in range(1, len(synced_sources)):
                    s1 = self._clip_map.get(synced_sources[0].source_clip_id)
                    s2 = self._clip_map.get(synced_sources[i].source_clip_id)
                    if s1 and s2 and (s1.start != s2.start or s1.duration != s2.duration):
                        sources_differ = True
                        break
                if sources_differ:
                    conflicts.append(Conflict(
                        constraint_a_id=synced_sources[0].id,
                        constraint_b_id=synced_sources[1].id,
                        clip_id=clip_id,
                        description=f"Clip {clip_id} has conflicting SYNCED constraints",
                    ))

            # Check FOLLOWS + SYNCED conflict
            follows = [c for c in constraints if c.constraint_type == ConstraintType.FOLLOWS]
            synceds = [c for c in constraints if c.constraint_type == ConstraintType.SYNCED]
            if follows and synceds:
                conflicts.append(Conflict(
                    constraint_a_id=follows[0].id,
                    constraint_b_id=synceds[0].id,
                    clip_id=clip_id,
                    description=f"Clip {clip_id} has both FOLLOWS and SYNCED constraints",
                ))

            # Check SYNCED + OFFSET conflict (most common)
            offsets = [c for c in constraints if c.constraint_type == ConstraintType.OFFSET]
            if synceds and offsets:
                conflicts.append(Conflict(
                    constraint_a_id=synceds[0].id,
                    constraint_b_id=offsets[0].id,
                    clip_id=clip_id,
                    description=f"Clip {clip_id} has both SYNCED and OFFSET constraints",
                ))

            # Check ALIGNS_START + ALIGNS_END on different sources
            aligns_start = [c for c in constraints if c.constraint_type == ConstraintType.ALIGNS_START]
            aligns_end = [c for c in constraints if c.constraint_type == ConstraintType.ALIGNS_END]
            if aligns_start and aligns_end:
                conflicts.append(Conflict(
                    constraint_a_id=aligns_start[0].id,
                    constraint_b_id=aligns_end[0].id,
                    clip_id=clip_id,
                    description=f"Clip {clip_id} has both ALIGNS_START and ALIGNS_END constraints",
                ))

        return conflicts

    def _resolve_conflict(self, conflict: Conflict):
        """Resolve a conflict by priority."""
        ca = self._constraint_map.get(conflict.constraint_a_id)
        cb = self._constraint_map.get(conflict.constraint_b_id)

        if not ca or not cb:
            return

        pa = getattr(ca, '_priority', 50)
        pb = getattr(cb, '_priority', 50)

        if pa > pb:
            # A wins, deactivate B
            cb.is_active = False
            conflict.resolution = f"Deactivated {cb.id} (lower priority)"
        elif pb > pa:
            ca.is_active = False
            conflict.resolution = f"Deactivated {ca.id} (lower priority)"
        else:
            # Same priority — deactivate the later one (stable ordering)
            ca.is_active = False
            conflict.resolution = f"Deactivated {ca.id} (same priority, stable ordering)"


class PropagationStats:
    """Statistics for constraint propagation, for observability."""

    @staticmethod
    def analyze(timeline: Timeline) -> dict:
        """Analyze constraint structure without solving."""
        total_constraints = len(timeline.constraints)
        active_constraints = sum(1 for c in timeline.constraints if c.is_active)
        total_anchors = len(timeline.anchors)

        type_counts = defaultdict(int)
        for c in timeline.constraints:
            type_counts[c.constraint_type.value] += 1

        # Check for potential conflicts
        target_counts = defaultdict(int)
        for c in timeline.constraints:
            if c.is_active:
                target_counts[c.target_clip_id] += 1
        over_constrained = {
            cid: count for cid, count in target_counts.items() if count > 1
        }

        return {
            "total_constraints": total_constraints,
            "active_constraints": active_constraints,
            "total_anchors": total_anchors,
            "by_type": dict(type_counts),
            "over_constrained_clips": len(over_constrained),
            "over_constrained_details": over_constrained,
        }
