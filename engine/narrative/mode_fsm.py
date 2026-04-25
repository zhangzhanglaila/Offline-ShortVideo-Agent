"""
Mode FSM — Director Mode (discrete style decisions).
The mode is the PRIMARY control: it overrides base decisions with intent.
Persistence (hysteresis) prevents flicker via minimum duration lock.
"""

from dataclasses import dataclass
from typing import Optional


MIN_MODE_DURATION = {
    'chaos':  200,
    'burst':  300,
    'focus':  400,
    'linger': 600,
    'normal': 100,
}


def apply_scene_bias(candidate: str, scene: str) -> str:
    """
    Scene reshapes mode preference — narrative context biases shot selection.
    Applied AFTER candidate derivation, BEFORE time-lock check.
    """
    if scene == 'climax':
        if candidate in ('chaos', 'burst'):
            return 'chaos'
        if candidate == 'linger':
            return 'focus'
        return candidate
    if scene == 'buildup':
        if candidate in ('normal', 'linger'):
            return 'burst'
        return candidate
    if scene == 'focus-arc':
        if candidate in ('chaos', 'linger', 'normal'):
            return 'focus'
        return candidate
    if scene == 'release':
        if candidate in ('burst', 'focus', 'chaos'):
            return 'linger'
        return candidate
    if scene == 'idle':
        return 'linger'
    if scene == 'intro':
        if candidate == 'chaos':
            return 'normal'
        if candidate == 'focus':
            return 'burst'
        return candidate
    return candidate


def get_transition(from_mode: str, to_mode: str, scene: str) -> Optional[str]:
    """Compute transition type between modes, with scene bias."""
    if from_mode == to_mode:
        return None

    t: Optional[str] = None
    if to_mode == 'chaos':
        t = 'jump-cut'
    elif from_mode != 'chaos' and to_mode == 'burst':
        t = 'wide-cut'
    elif to_mode == 'linger':
        t = 'ease-out'
    elif from_mode == 'burst' and to_mode == 'focus':
        t = 'snap-in'
    elif from_mode == 'focus' and to_mode == 'burst':
        t = 'release-cut'
    else:
        t = 'cut'

    # Scene bias on transition type
    if scene == 'climax' and t in ('cut', 'wide-cut'):
        t = 'jump-cut'
    if scene == 'release' and t in ('cut', 'snap-in'):
        t = 'ease-out'
    if scene == 'intro' and t == 'cut':
        t = 'wide-cut'
    if scene == 'focus-arc' and t == 'cut':
        t = 'snap-in'
    if scene == 'idle' and t == 'cut':
        t = 'ease-out'

    return t


def derive_mode(tags: list, ctx: dict, scene: str) -> str:
    """
    Derive base mode candidate from content tags + context.
    Pure derivation — no history, no time lock.
    """
    parallel_size = ctx.get('parallelBurstSize', 0)
    is_retry_storm = 'retry-storm' in tags
    is_critical   = 'critical' in tags
    gap_prev      = ctx.get('gapPrev', 0)

    if is_retry_storm:
        return 'chaos'
    if parallel_size > 3:
        return 'burst'
    if is_critical:
        return 'focus'
    if gap_prev > 0.3:
        return 'linger'
    return 'normal'


@dataclass
class ModeResult:
    mode: str
    transition: Optional[str]
    from_mode: Optional[str]
    scene: str


class ModeResolver:
    """
    Stateful mode resolver with time-locking and scene bias.
    """

    def __init__(self):
        self.current_mode = 'normal'
        self.mode_since   = 0.0   # ms timestamp when entered

    def resolve(self, tags: list, ctx: dict, scene: str, now: float = 0.0) -> ModeResult:
        """
        Resolve mode given tags/context/scene.
        Returns ModeResult with mode, transition, fromMode, scene.
        """
        candidate = derive_mode(tags, ctx, scene)
        candidate = apply_scene_bias(candidate, scene)

        if candidate == self.current_mode:
            return ModeResult(mode=self.current_mode, transition=None, from_mode=None, scene=scene)

        elapsed = now - self.mode_since
        if elapsed < MIN_MODE_DURATION.get(self.current_mode, 100):
            return ModeResult(mode=self.current_mode, transition=None, from_mode=None, scene=scene)

        from_mode  = self.current_mode
        transition = get_transition(from_mode, candidate, scene)
        self.current_mode = candidate
        self.mode_since   = now

        return ModeResult(mode=self.current_mode, transition=transition, from_mode=from_mode, scene=scene)
