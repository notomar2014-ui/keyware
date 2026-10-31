"""Foreground process name, polled every 500 ms. Failures become an empty name."""

from __future__ import annotations

import os
import sys
import threading
from typing import Callable


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


def windows_foreground_name() -> str:
    """Best-effort exe name of the foreground window. Empty when it cannot be read."""
    if sys.platform != "win32":
        return ""
    try:
        import win32api
        import win32con
        import win32gui
        import win32process
    except Exception:
        return ""
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return ""
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
    except Exception:
        return ""
