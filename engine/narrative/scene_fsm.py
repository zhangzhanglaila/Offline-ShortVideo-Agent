"""
Scene FSM — Narrative grouping with persistence and direction graph.
Scene is a STATE MACHINE, not a stateless classifier.
The director tells a story with a beginning, middle, and end.
"""

from dataclasses import dataclass
from typing import Optional, List


MIN_SCENE_DURATION = {
    'intro':      600,
    'buildup':    500,
    'climax':     300,
    'release':    800,
    'idle':       400,
    'focus-arc':  400,
    'normal':     200,
}

# Narrative direction graph: which scene transitions are allowed
NARRATIVE_ALLOWED = {
    'intro':      ['buildup', 'normal'],
    'buildup':    ['climax', 'focus-arc', 'normal'],
    'climax':     ['release', 'normal'],
    'release':    ['idle', 'normal', 'buildup'],
    'idle':       ['buildup', 'normal'],
    'focus-arc':  ['climax', 'release', 'normal'],
    'normal':     ['buildup', 'focus-arc', 'idle', 'intro', 'climax'],
}


@dataclass
class SceneResult:
    scene: str
    from_scene: Optional[str]
    epoch: int


def derive_scene(tags: List[str], ctx: dict, current_scene: str) -> str:
    """
    Derive candidate next scene from tags + context + current scene.
    Implements narrative inertia — scenes resist premature exit.
    """
    gap_prev      = ctx.get('gapPrev', 0)
    parallel_size = ctx.get('parallelBurstSize', 0)
    is_retry_storm = 'retry-storm' in tags
    is_cascade    = 'cascade' in tags
    is_critical   = 'critical' in tags
    is_first      = ctx.get('isFirstEvent', False)

    # Climax inertia: ride the peak until clear release signal
    if current_scene == 'climax':
        if gap_prev > 0.4:
            return 'release'
        return 'climax'

    # Buildup inertia: hold tension until it fully resolves
    if current_scene == 'buildup':
        if is_retry_storm or is_cascade:
            return 'climax'
        if parallel_size > 4:
            return 'buildup'
        if gap_prev > 0.5:
            return 'release'
        return 'buildup'

    # Focus-arc inertia: sustain attention
    if current_scene == 'focus-arc':
        if gap_prev > 0.45:
            return 'release'
        if is_retry_storm:
            return 'climax'
        return 'focus-arc'

    # Release inertia: don't spike back up after wind-down
    if current_scene == 'release':
        if is_retry_storm:
            return 'climax'
        if parallel_size > 3:
            return 'buildup'
        if gap_prev > 0.5:
            return 'idle'
        return 'release'

    # Idle: wait for genuine activity
    if current_scene == 'idle':
        if parallel_size > 2:
            return 'buildup'
        return 'idle'

    # Intro: establishes early
    if current_scene == 'intro':
        if is_retry_storm or is_cascade:
            return 'climax'
        if parallel_size > 2:
            return 'buildup'
        if is_critical:
            return 'focus-arc'
        return 'intro'

    # Normal (free state): open to any transition
    if is_retry_storm or (is_cascade and 'failed' in tags):
        return 'climax'
    if parallel_size > 3:
        return 'buildup'
    if is_critical:
        return 'focus-arc'
    if gap_prev > 0.3:
        return 'release'
    if parallel_size == 0 and gap_prev > 0.5:
        return 'idle'
    if is_first:
        return 'intro'
    return 'normal'


class SceneResolver:
    """
    Stateful scene resolver with time-locking and narrative direction constraints.
    Each SceneResolver instance tracks one narrative timeline.
    """

    def __init__(self):
        self.current_scene = 'normal'
        self.scene_since   = 0.0   # ms timestamp when entered current scene
        self.epoch         = 0    # scene epoch counter

    def resolve(self, tags: List[str], ctx: dict, now: float = 0.0) -> SceneResult:
        """
        Resolve next scene given current tags/context.
        Returns SceneResult with scene, fromScene, and epoch (incremented on switch).
        """
        candidate = derive_scene(tags, ctx, self.current_scene)

        if candidate == self.current_scene:
            return SceneResult(scene=self.current_scene, from_scene=None, epoch=self.epoch)

        # Narrative direction constraint
        allowed = NARRATIVE_ALLOWED.get(self.current_scene, ['normal'])
        if candidate not in allowed:
            return SceneResult(scene=self.current_scene, from_scene=None, epoch=self.epoch)

        # Time lock: enforce minimum scene duration
        elapsed = now - self.scene_since
        if elapsed < MIN_SCENE_DURATION.get(self.current_scene, 200):
            return SceneResult(scene=self.current_scene, from_scene=None, epoch=self.epoch)

        # Confirmed transition
        from_scene   = self.current_scene
        self.current_scene = candidate
        self.scene_since   = now
        self.epoch        += 1

        return SceneResult(scene=self.current_scene, from_scene=from_scene, epoch=self.epoch)
