"""Non-blocking hold-tier ticks. Tests pass their own player."""

from __future__ import annotations

import threading
from typing import Callable


Player = Callable[[int, int], None]


class AudioCue:
    def __init__(self, player: Player, enabled: bool = True):
        self.player = player
        self.enabled = enabled

    def play(self, hz: int, duration_ms: int = 40) -> None:
        if not self.enabled or hz <= 0 or duration_ms <= 0:
            return
        self.player(int(hz), int(duration_ms))


def winsound_player(hz: int, duration_ms: int) -> None:
    """Play a tick off the hook thread. Missing audio hardware is ignored."""

    def _run() -> None:
        try:
            import winsound

            winsound.Beep(int(hz), int(duration_ms))
        except Exception:
            return

    threading.Thread(target=_run, name="keyware-tick", daemon=True).start()
