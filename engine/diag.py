"""Install the keyboard hook, then remove it.

``python main.py --diag`` uses this. The hook is not left running.
Off Windows the hook cannot install; that is a successful diagnostic
because nothing was left running. Tests pass a fake factory.
"""

from __future__ import annotations

from typing import Callable

from engine.hook_engine import HookEngine, HookUnavailable


def run_diag(factory: Callable | None = None) -> int:
    """Return 0 when nothing is left running. Return 1 when the hook stays up."""
    hook = factory() if factory is not None else HookEngine(lambda *_args: False)
    started = False
    try:
        hook.start()
        started = True
    except HookUnavailable:
        print("The keyboard hook is only available on Windows. Nothing was left running.")
        return 0
    except Exception as exc:
        print(f"The keyboard hook did not install: {exc}")
        _stop(hook)
        return 1
    if not _stop(hook):
        print("The keyboard hook did not stop.")
        return 1
    running = False
    try:
        running = bool(hook.is_running())
    except Exception:
        running = True
    if running:
        print("The keyboard hook was still running after the diagnostic.")
        return 1
    if started:
        print("The keyboard hook installed and was removed. Nothing left running.")
    return 0


def _stop(hook) -> bool:
    try:
        hook.stop()
        return True
    except Exception:
        return False
