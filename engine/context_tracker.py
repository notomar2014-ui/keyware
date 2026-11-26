"""Foreground process name, polled every 500 ms. Failures become an empty name."""

from __future__ import annotations

import os
import sys
import threading
from typing import Callable

from engine.appidentity import hosted_app_name, is_frame_host


Provider = Callable[[], str]


class ContextTracker:
    def __init__(self, provider: Provider, interval: float = 0.5):
        self.provider = provider
        self.interval = interval
        self.name = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def read(self) -> str:
        try:
            value = self.provider() or ""
        except Exception:
            value = ""
        self.name = os.path.basename(str(value))
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


def windows_foreground_name() -> str:
    """Best-effort exe name of the foreground window. Empty when it cannot be read.

    UWP apps hosted by ``ApplicationFrameHost.exe`` report the real child process
    (``CalculatorApp.exe``) instead of the frame.
    """
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
        if not is_frame_host(host):
            return host
        return hosted_app_name(host, child_windows(hwnd))
    except Exception:
        return ""
