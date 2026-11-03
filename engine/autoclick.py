"""Auto-click worker.

One daemon thread sleeps on an Event until a binding starts clicking. Clicks
are scheduled against a deadline (``next += interval``) so the rate stays
steady, and the loop never sleeps longer than 5 ms at a time so a stop
(pause, panic, Keyware off, key released, profile switch) lands within a few
milliseconds. The button is always released on the way out.

Only one auto-clicker runs at a time. Starting another stops the first.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from engine.models import Action

SLICE_SECONDS = 0.005
MAX_HOLD_SECONDS = 0.008


@dataclass
class ClickJob:
    binding_id: str
    key: Optional[str]
    action: Action
    started: float = 0.0
    next_at: float = 0.0
    deadline: Optional[float] = None
    clicks: int = 0
    stopped: threading.Event = field(default_factory=threading.Event)

    @property
    def while_held(self) -> bool:
        return self.action.click_mode == "hold"


class AutoClicker:
    def __init__(
        self,
        injector,
        *,
        guard: Callable[[ClickJob], bool] | None = None,
        log: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], None] = time.sleep,
        threaded: bool = True,
    ):
        self._injector = injector
        self._guard = guard
        self._log = log or (lambda _message: None)
        self._clock = clock
        self._sleep = sleep
        self._threaded = threaded
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._job: Optional[ClickJob] = None
        self._closed = False
        self._thread: Optional[threading.Thread] = None

    @property
    def running_binding(self) -> Optional[str]:
        job = self._job
        return job.binding_id if job is not None else None

    @property
    def job(self) -> Optional[ClickJob]:
        return self._job

    def start(self, binding_id: str, key: Optional[str], action: Action) -> None:
        if self._closed:
            return
        now = self._clock()
        job = ClickJob(binding_id=binding_id, key=key, action=action, started=now, next_at=now)
        if action.max_seconds:
            job.deadline = now + float(action.max_seconds)
        with self._lock:
            previous, self._job = self._job, job
        if previous is not None:
            previous.stopped.set()
        how = "toggle" if action.click_mode == "toggle" else "while held"
        self._log(f"Auto-click on: {action.button}, {int(action.cps)}/s, {how}.")
        if self._threaded:
            self._ensure_thread()
            self._wake.set()

    def toggle(self, binding_id: str, key: Optional[str], action: Action) -> bool:
        """Start, or stop if this binding is already clicking. Returns True when now running."""
        if self.running_binding == binding_id:
            self.stop(binding_id, "toggled off")
            return False
        self.start(binding_id, key, action)
        return True

    def stop(self, binding_id: Optional[str] = None, reason: str = "") -> bool:
        with self._lock:
            job = self._job
            if job is None or (binding_id is not None and job.binding_id != binding_id):
                return False
            self._job = None
        job.stopped.set()
        self._log(f"Auto-click off{f' ({reason})' if reason else ''}.")
        return True

    def stop_all(self, reason: str = "") -> bool:
        return self.stop(None, reason)

    def stop_for_key(self, key: str, reason: str = "key released") -> bool:
        job = self._job
        if job is not None and job.while_held and job.key == key:
            return self.stop(job.binding_id, reason)
        return False

    def close(self) -> None:
        self.stop_all("shutdown")
        self._closed = True
        self._wake.set()

    # ---- worker ----
    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="keyware-autoclick", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._closed:
            self._wake.wait()
            self._wake.clear()
            job = self._job
            if job is not None:
                self._run(job)

    def pump(self, seconds: float) -> None:
        """Test mode: run the current job for ``seconds`` of (fake) clock time."""
        job = self._job
        if job is not None:
            self._run(job, until=self._clock() + seconds)

    def _active(self, job: ClickJob) -> bool:
        return self._job is job and not job.stopped.is_set() and not self._closed

    def _run(self, job: ClickJob, until: Optional[float] = None) -> None:
        interval = 1.0 / max(1, int(job.action.cps))
        hold = min(MAX_HOLD_SECONDS, interval * 0.3)
        button = job.action.button
        down = getattr(self._injector, "mouse_down", None)
        up = getattr(self._injector, "mouse_up", None)
        pressed = False
        try:
            while self._active(job):
                now = self._clock()
                if until is not None and now >= until:
                    return
                if job.deadline is not None and now >= job.deadline:
                    self.stop(job.binding_id, f"safety stop after {int(job.action.max_seconds)} s")
                    break
                if now < job.next_at:
                    self._sleep(min(job.next_at - now, SLICE_SECONDS))
                    continue
                if self._guard is not None and not self._guard(job):
                    self.stop(job.binding_id, "trigger no longer active")
                    break
                if down is not None and up is not None:
                    down(button)
                    pressed = True
                    self._sleep(hold)
                    up(button)
                    pressed = False
                else:
                    self._injector.click(button)
                job.clicks += 1
                job.next_at += interval
                # After a stall (sleep, heavy load) do not burst to catch up.
                late = self._clock()
                if job.next_at < late - interval:
                    job.next_at = late
        except OSError as exc:
            self.stop(job.binding_id, f"Windows refused the click: {exc}")
        except Exception as exc:  # never let the worker die silently
            self.stop(job.binding_id, f"error: {exc}")
        finally:
            if pressed and up is not None:
                try:
                    up(button)
                except Exception:
                    pass
