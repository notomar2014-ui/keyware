"""Foreground process name, polled every 500 ms. Failures become an empty name."""

from __future__ import annotations

import os
import sys
import threading
from typing import Callable

from engine.appidentity import _basename, hosted_app_name, is_frame_host


Provider = Callable[[], str]


class ContextTracker:
    def __init__(self, provider: Provider, interval: float = 0.5):
        self.provider = provider
        self.interval = interval
        self.name = ""
        self.hosted_in_frame = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def read(self) -> str:
        try:
            value = self.provider() or ""
        except Exception:
            value = ""
        self.name = os.path.basename(str(value))
        # windows_foreground_name sets this. A test provider leaves it false.
        self.hosted_in_frame = bool(getattr(self.provider, "hosted_in_frame", False))
        return self.name

    def start(self, on_change: Callable[[str], None]) -> None:
        if self._thread is not None:
            return

        def loop() -> None:
            last = object()
            while not self._stop.is_set():
                name = self.read()
                if name != last:
                    last = name
                    try:
                        on_change(name)
                    except Exception:
                        pass
                self._stop.wait(self.interval)

        self._thread = threading.Thread(target=loop, name="keyware-context", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._thread = None


def _process_basename(hwnd) -> str:
    """Exe file name for a window. Empty when the process cannot be opened."""
    import win32api
    import win32con
    import win32process

    _thread_id, pid = win32process.GetWindowThreadProcessId(hwnd)
    if not pid:
        return ""
    access = getattr(win32con, "PROCESS_QUERY_LIMITED_INFORMATION", 0x1000)
    handle = win32api.OpenProcess(access, False, pid)
    try:
        if hasattr(win32process, "QueryFullProcessImageName"):
            path = win32process.QueryFullProcessImageName(handle)
        else:
            path = win32process.GetModuleFileNameEx(handle, 0)
    finally:
        win32api.CloseHandle(handle)
    return os.path.basename(path or "")


def child_windows(hwnd) -> list[tuple[str, str]]:
    """``(window class, exe name)`` for each child. Empty when they cannot be read."""
    import win32gui

    found: list[tuple[str, str]] = []

    def visit(child, _extra):
        try:
            class_name = win32gui.GetClassName(child) or ""
            exe = _process_basename(child)
        except Exception:
            return True
        if exe:
            found.append((class_name, exe))
        return True

    try:
        win32gui.EnumChildWindows(hwnd, visit, None)
    except Exception:
        return found
    return found


# Last ApplicationFrameHost window whose children were read. The hook asks for
# the foreground name on every key; the child list is walked once per hwnd.
# The poll thread and the hook thread both read this, so the update is locked.
_frame_cache = {"hwnd": 0, "name": "", "hosted": False}
_frame_lock = threading.Lock()


def clear_foreground_cache() -> None:
    """Drop the cached frame-host child. Tests call this so runs stay independent."""
    with _frame_lock:
        _frame_cache["hwnd"] = 0
        _frame_cache["name"] = ""
        _frame_cache["hosted"] = False
    windows_foreground_name.hosted_in_frame = False


def resolve_foreground(hwnd: int, host_name: str, children_of) -> str:
    """Exe name for a top-level window.

    ``children_of(hwnd)`` runs only when the window is ``ApplicationFrameHost``
    and this hwnd was not resolved already. The returned name is the hosted
    app (``CalculatorApp.exe``) when that child can be read.
    """
    with _frame_lock:
        return _resolve_foreground(hwnd, host_name, children_of)


def _resolve_foreground(hwnd: int, host_name: str, children_of) -> str:
    host = _basename(host_name)
    if not host:
        _frame_cache.update(hwnd=0, name="", hosted=False)
        return ""
    if not is_frame_host(host):
        _frame_cache.update(hwnd=hwnd, name=host, hosted=False)
        return host
    if hwnd and _frame_cache["hwnd"] == hwnd and _frame_cache["hosted"]:
        return _frame_cache["name"]
    children = children_of(hwnd) if children_of is not None else []
    name = hosted_app_name(host, children)
    _frame_cache.update(hwnd=hwnd or 0, name=name, hosted=True)
    return name


def foreground_was_frame_host() -> bool:
    """True when the last ``resolve_foreground`` window was ``ApplicationFrameHost``."""
    with _frame_lock:
        return bool(_frame_cache["hosted"])


def windows_foreground_name() -> str:
    """Best-effort exe name of the foreground window. Empty when it cannot be read.

    UWP apps hosted by ``ApplicationFrameHost.exe`` report the real child process
    (``CalculatorApp.exe``) instead of the frame.
    """
    windows_foreground_name.hosted_in_frame = False
    if sys.platform != "win32":
        return ""
    try:
        import win32gui
    except Exception:
        return ""
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return ""
        host = _process_basename(hwnd)
        if not host:
            return ""
        with _frame_lock:
            name = _resolve_foreground(hwnd, host, child_windows)
            windows_foreground_name.hosted_in_frame = bool(_frame_cache["hosted"])
        return name
    except Exception:
        windows_foreground_name.hosted_in_frame = False
        return ""


windows_foreground_name.hosted_in_frame = False
