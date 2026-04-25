"""
Intent Queue — Scene-bound Temporal Intent Scheduler.
Layer 3: scene-epoch-aware arbitration — NOT just time delay.
Intent is bound to the scene-epoch in which it was captured.
Prevents "emotional leakage across scene boundary".
"""

from dataclasses import dataclass
from typing import Optional, List
from engine.narrative.scene_fsm import NARRATIVE_ALLOWED


INTENT_TTL_MS = 2 * 400


@dataclass
class Intent:
    mode: str
    transition: Optional[str]
    scene_at_capture: str
    scene_epoch_id: int
    created_at: float  # ms timestamp


class IntentQueue:
    """
    FIFO queue of suppressed mode intents, bound to scene epochs.
    An intent is valid only while the scene epoch it was captured in is still active.
    """

    def __init__(self):
        self.queue: List[Intent] = []
        self.scene_epoch = 0

    def advance_epoch(self) -> None:
        """Call when a genuine scene transition occurs."""
        self.scene_epoch += 1

    def push(self, intent: Intent) -> None:
        """Enqueue a suppressed mode change intent."""
        self.queue.append(intent)

    def try_release(self, current_scene: str, current_epoch: int, now: float) -> Optional[Intent]:
        """
        Attempt to release the oldest valid intent.
        Valid = age OK + scene allowed + epoch matches.
        Returns the Intent if released, None if queue exhausted.
        """
        while self.queue:
            next_intent = self.queue[0]
            age_ok      = (now - next_intent.created_at) < INTENT_TTL_MS
            scene_ok    = current_scene in NARRATIVE_ALLOWED.get(next_intent.scene_at_capture, [])
            epoch_ok    = next_intent.scene_epoch_id == current_epoch

            if age_ok and scene_ok and epoch_ok:
                self.queue.pop(0)
                return next_intent
            else:
                # Stale or conflicting — discard
                self.queue.pop(0)
        return None

    def __len__(self) -> int:
        return len(self.queue)
