"""Windows low-level keyboard hook.

The callback stays on the hook thread only long enough to classify the event
and queue a decision. It does not sleep, touch Tk, run macros, read
GetAsyncKeyState, or call SendInput.

Selective suppression only: nothing is swallowed unless the detector asks for
that one event. The listener is created with suppress=False. The filter
always returns False so pynput does not call ToUnicodeEx (that call consumes
dead keys and AltGr state). Swallowing is a separate suppress_event() call.

Events tagged with KEYWARE_EXTRA are ours and are ignored. Other injected
events are passed through untouched. Any error, or a lock that is not free
within 20 ms inside the service, fails open.
"""

from __future__ import annotations

import sys
import threading
from typing import Callable

from engine.sendinput import HookEvent, classify_hook_event

OnEvent = Callable[[str, bool], bool]
Annotate = Callable[[HookEvent], None]


class HookUnavailable(RuntimeError):
    """This process cannot install a keyboard hook."""


def is_windows() -> bool:
    """True when this process may install a Windows keyboard hook.

    Tests replace this. They do not depend on the machine they run on.
    """
    return sys.platform == "win32"


class HookEngine:
    def __init__(self, on_event: OnEvent, listener_factory: Callable | None = None):
        self._on_event = on_event
        self._listener_factory = listener_factory
        self._listener = None
        self._lock = threading.Lock()
        self._down: set[int] = set()
        # Print Screen and Pause often arrive as only a down or only an up.
        self._oneshot_swallow: set[int] = set()
        self._oneshot_done: set[int] = set()
        self.flags_for_callback: Annotate | None = None
        # Every keyboard event the hook was called for. Read by the silent-removal check.
        self.events_seen = 0

    def is_running(self) -> bool:
        listener = self._listener
        if listener is None:
            return False
        running = getattr(listener, "running", None)
        if callable(running):
            return bool(running())
        return bool(running)

    def start(self) -> None:
        if not is_windows():
            raise HookUnavailable(
                "The keyboard hook is only installed on Windows. Bindings can still be edited."
            )
        with self._lock:
            if self.is_running():
                return
            factory = self._listener_factory or _pynput_listener
            listener = factory(self._filter)
            # Publish before start so the first event can suppress safely.
            self._listener = listener
        try:
            listener.start()
            wait = getattr(listener, "wait", None)
            if callable(wait):
                wait()
        except Exception:
            self.stop()
            raise
        if not self.is_running():
            self.stop()
            raise HookUnavailable("The keyboard hook stopped during startup.")

    def stop(self) -> None:
        with self._lock:
            listener = self._listener
            self._listener = None
        self._down.clear()
        if listener is None:
            return
        try:
            listener.stop()
        except Exception:
            pass
        join = getattr(listener, "join", None)
        # Never join the listener from its own thread; that deadlocks the hook.
        if callable(join) and threading.current_thread() is not getattr(listener, "_thread", None):
            try:
                join(timeout=1.0)
            except Exception:
                pass

    def _filter(self, msg: int, data) -> bool:
        """Always return False so pynput does not translate the key.

        Returning False skips translation. It does not swallow the key.
        System-wide swallowing is a separate suppress_event() call, and it is
        skipped whenever anything looks unsafe.
        """
        self.events_seen += 1
        try:
            vk = int(getattr(data, "vkCode", -1))
            flags = int(getattr(data, "flags", 0) or 0)
            extra = int(getattr(data, "dwExtraInfo", 0) or 0)
            scan = int(getattr(data, "scanCode", 0) or 0)
            event = classify_hook_event(msg, vk, flags, extra, already_down=vk in self._down, scan=scan)
        except Exception:
            return False
        if event.kind != "key":
            return False
        if event.name in ("print_screen", "pause"):
            return self._oneshot(event)
        if event.pressed:
            self._down.add(event.vk)
        else:
            self._down.discard(event.vk)
        if self.flags_for_callback is not None:
            try:
                self.flags_for_callback(event)
            except Exception:
                return False
        try:
            suppress = bool(self._on_event(event.name, event.pressed))
        except Exception as exc:
            if type(exc).__name__ == "SuppressException":
                raise
            return False
        if suppress:
            listener = self._listener
            if listener is not None:
                listener.suppress_event()
        return False

    def _oneshot(self, event) -> bool:
        """Finish Print Screen / Pause even when Windows omits half of the pair.

        A tap binding fires on key-up. These keys often never send one, so the
        hook completes the pair itself. The real leftover event is swallowed
        only when the binding swallowed the key, so an unbound Print Screen
        still takes a screenshot.
        """
        vk = event.vk
        if event.pressed:
            self._down.add(vk)
            down = self._emit(event.name, True)
            up = self._emit(event.name, False)
            suppress = down or up
            self._down.discard(vk)
            self._oneshot_done.add(vk)
            if suppress:
                self._oneshot_swallow.add(vk)
                self._suppress_current()
            else:
                self._oneshot_swallow.discard(vk)
            return False
        swallowed = vk in self._oneshot_swallow
        self._oneshot_swallow.discard(vk)
        if vk in self._oneshot_done:
            self._oneshot_done.discard(vk)
            if swallowed:
                self._suppress_current()
            return False
        if vk not in self._down and not swallowed:
            down = self._emit(event.name, True)
            up = self._emit(event.name, False)
            suppress = down or up
            if suppress:
                self._suppress_current()
            return False
        self._down.discard(vk)
        if swallowed:
            self._suppress_current()
        return False

    def _emit(self, name: str, pressed: bool) -> bool:
        try:
            return bool(self._on_event(name, pressed))
        except Exception as exc:
            if type(exc).__name__ == "SuppressException":
                raise
            return False

    def _suppress_current(self) -> None:
        listener = self._listener
        if listener is not None:
            listener.suppress_event()


def _pynput_listener(event_filter):
    from pynput import keyboard

    listener = keyboard.Listener(
        on_press=lambda *_args, **_kwargs: None,
        on_release=lambda *_args, **_kwargs: None,
        suppress=False,
        win32_event_filter=event_filter,
    )
    return listener


# --- Mouse triggers -------------------------------------------------------
# WM_* button messages seen by WH_MOUSE_LL -> (trigger name or None for X, pressed).
_MOUSE_MSGS = {
    0x0201: ("mouse_left", True), 0x0202: ("mouse_left", False),
    0x0204: ("mouse_right", True), 0x0205: ("mouse_right", False),
    0x0207: ("mouse_middle", True), 0x0208: ("mouse_middle", False),
    0x020B: (None, True), 0x020C: (None, False),
}
WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x020A, 0x020E


def _signed16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


def classify_wheel(msg: int, mouse_data: int) -> str | None:
    """Wheel notch direction for WM_MOUSEWHEEL / WM_MOUSEHWHEEL, else None."""
    delta = _signed16(int(mouse_data) >> 16)
    if not delta:
        return None
    if msg == WM_MOUSEWHEEL:
        return "mouse_wheel_up" if delta > 0 else "mouse_wheel_down"
    if msg == WM_MOUSEHWHEEL:
        return "mouse_wheel_right" if delta > 0 else "mouse_wheel_left"
    return None


def classify_mouse_event(msg: int, mouse_data: int, extra: int) -> tuple[str, bool] | None:
    """Return (trigger, pressed) for a button event, or None to ignore it."""
    from engine.sendinput import KEYWARE_EXTRA

    if (int(extra) & 0xFFFFFFFF) == KEYWARE_EXTRA:
        return None  # our own replay
    hit = _MOUSE_MSGS.get(int(msg))
    if hit is None:
        return None  # moves and wheel are never touched
    name, pressed = hit
    if name is None:
        x = (int(mouse_data) >> 16) & 0xFFFF
        if not 1 <= x <= 16:
            return None
        name = f"mouse_x{x}"  # X1/X2 plus any extra button the hook reports
    return name, pressed


class MouseHookEngine:
    """Low-level mouse hook that feeds button triggers into the same pipeline as keys.

    Plain Left/Right clicks are only forwarded when wants(name) says a binding
    uses them (bindings require a modifier for those). An up is only forwarded
    when its down was, so the detector never sees half a click. Fails open.
    """

    def __init__(self, on_event: OnEvent, wants: Callable[[str], bool] | None = None,
                 listener_factory: Callable | None = None):
        self._on_event = on_event
        self._wants = wants
        self._listener_factory = listener_factory
        self._listener = None
        self._down: set[str] = set()
        self.events_seen = 0

    def is_running(self) -> bool:
        listener = self._listener
        running = getattr(listener, "running", False) if listener is not None else False
        return bool(running() if callable(running) else running)

    def start(self) -> None:
        if not is_windows():
            raise HookUnavailable("The mouse hook is only installed on Windows.")
        if self.is_running():
            return
        factory = self._listener_factory or _pynput_mouse_listener
        self._listener = factory(self._filter)
        try:
            self._listener.start()
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        listener, self._listener = self._listener, None
        self._down.clear()
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass

    def _filter(self, msg: int, data) -> bool:
        self.events_seen += 1
        try:
            mouse_data = getattr(data, "mouseData", 0) or 0
            extra = getattr(data, "dwExtraInfo", 0) or 0
            wheel = None if (int(extra) & 0xFFFFFFFF) == _keyware_extra() else classify_wheel(msg, mouse_data)
            if wheel is not None:
                return self._wheel(wheel)
            hit = classify_mouse_event(msg, mouse_data, extra)
            if hit is None:
                return False
            name, pressed = hit
            if pressed:
                if self._wants is not None and not self._wants(name):
                    return False
                self._down.add(name)
            elif name in self._down:
                self._down.discard(name)
            else:
                return False
            suppress = bool(self._on_event(name, pressed))
        except Exception as exc:
            if type(exc).__name__ == "SuppressException":
                raise
            return False
        if suppress:
            listener = self._listener
            if listener is not None:
                listener.suppress_event()
        return False


    def _wheel(self, name: str) -> bool:
        """A notch is an instant press+release. Only bound wheels are forwarded."""
        if self._wants is not None and not self._wants(name):
            return False
        try:
            suppress = bool(self._on_event(name, True))
            self._on_event(name, False)
        except Exception as exc:
            if type(exc).__name__ == "SuppressException":
                raise
            return False
        if suppress and self._listener is not None:
            self._listener.suppress_event()
        return False


def _keyware_extra() -> int:
    from engine.sendinput import KEYWARE_EXTRA

    return KEYWARE_EXTRA


class CombinedHook:
    """Keyboard hook plus optional mouse hook behind the HookEngine interface.

    The keyboard hook decides health and status; a mouse hook failure never
    takes the keyboard down.
    """

    def __init__(self, keyboard: HookEngine, mouse: MouseHookEngine | None = None):
        self.keyboard = keyboard
        self.mouse = mouse

    @property
    def flags_for_callback(self):
        return self.keyboard.flags_for_callback

    @flags_for_callback.setter
    def flags_for_callback(self, value) -> None:
        self.keyboard.flags_for_callback = value

    @property
    def events_seen(self) -> int:
        return self.keyboard.events_seen

    def is_running(self) -> bool:
        return self.keyboard.is_running()

    def start(self) -> None:
        self.keyboard.start()
        if self.mouse is not None:
            try:
                self.mouse.start()
            except Exception:
                self.mouse.stop()

    def stop(self) -> None:
        if self.mouse is not None:
            self.mouse.stop()
        self.keyboard.stop()


def _pynput_mouse_listener(event_filter):
    from pynput import mouse

    return mouse.Listener(suppress=False, win32_event_filter=event_filter)
