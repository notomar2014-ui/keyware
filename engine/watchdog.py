"""Pure decisions for the hook watchdog.

The hook thread itself never sleeps, and it never reinstalls the hook.
A timer thread calls ``decide_hook`` and then starts or stops the listener.
"""

from __future__ import annotations

_SECURE = frozenset({"lock", "sleep", "uac"})
_RETURN = frozenset({"unlock", "resume"})


def decide_hook(
    should_run: bool,
    running: bool,
    session_event: str | None,
    failures: int,
    *,
    suspended: bool = False,
) -> str:
    """Return ``reinstall``, ``stop``, ``pause``, or ``none``.

    Lock, sleep, and the UAC secure desktop stop the hook. Unlock and resume
    put it back when the user still wants Keyware active. A dead hook is
    reinstalled. After three failed reinstalls the decision is ``pause`` so
    the keyboard is left alone.
    """
    if session_event in _SECURE:
        return "stop"
    if session_event in _RETURN:
        if not should_run:
            return "none"
        if failures >= 3:
            return "pause"
        return "reinstall"
    if suspended:
        return "none"
    if should_run and not running:
        if failures >= 3:
            return "pause"
        return "reinstall"
    return "none"
