"""When to show the occasional "Support Keyware" note.

Rules: only when a donate link is set, on every even-numbered launch (2nd,
4th, 6th...), never on the first, and never again after "Don't ask again". State lives in state.json next to
config.json so the config file stays about bindings. Every function here is
pure or touches only that one small file, and none of them raise.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from engine.models import is_web_url

NUDGE_TITLE = "Enjoying Keyware?"
NUDGE_MESSAGE = (
    "Keyware is made by a solo developer, not a company. It's free, with no ads and no tracking, "
    "and donations are what keep it alive, free, and getting better. "
    "If Keyware saves you time, a small one-time or monthly donation would truly mean a lot. Thank you!"
)


@dataclass
class LaunchState:
    launches: int = 0
    first_launch: float = 0.0
    nudge_last: float = 0.0
    nudge_off: bool = False


def state_path(config_path: Path) -> Path:
    return Path(config_path).parent / "state.json"


def load_state(path: Path) -> LaunchState:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return LaunchState()
        return LaunchState(
            launches=max(0, int(raw.get("launches", 0) or 0)),
            first_launch=float(raw.get("first_launch", 0) or 0),
            nudge_last=float(raw.get("nudge_last", 0) or 0),
            nudge_off=bool(raw.get("nudge_off", False)),
        )
    except Exception:
        return LaunchState()


def save_state(path: Path, state: LaunchState) -> None:
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(asdict(state), indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
    except Exception:
        pass


def record_launch(path: Path, now: float | None = None) -> LaunchState:
    moment = time.time() if now is None else now
    state = load_state(path)
    state.launches += 1
    if not state.first_launch:
        state.first_launch = moment
    save_state(path, state)
    return state


def should_nudge(state: LaunchState, donate_url: str, now: float | None = None) -> bool:
    """Even-numbered launches only. Hidden entirely while donate_url is empty."""
    if state.nudge_off or not is_web_url(donate_url):
        return False
    return state.launches >= 2 and state.launches % 2 == 0


def mark_nudged(path: Path, state: LaunchState, now: float | None = None) -> None:
    state.nudge_last = time.time() if now is None else now
    save_state(path, state)


def turn_off(path: Path, state: LaunchState) -> None:
    state.nudge_off = True
    save_state(path, state)
