"""UIPI: a low-integrity process cannot inject into an elevated window.

The check is "could we open the foreground process?". Access denied means
the window is elevated (or otherwise sealed). Tests pass a fake opener.
The real opener is not called from tests.
"""

from __future__ import annotations

import sys
from typing import Callable

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ACCESS_DENIED = 5

OpenResult = Callable[[int], str]


def elevation_from_open(result: str) -> bool:
    """True only for an access-denied open. Any other result fails open."""
    return result == "denied"


def probe_pid(pid: int, open_process: OpenResult) -> bool:
    """True when ``pid`` cannot be opened. A reader error is not elevated."""
    if not pid or int(pid) <= 0:
        return False
    try:
        result = open_process(int(pid))
    except Exception:
        return False
    return elevation_from_open(str(result))


def win32_process_access(pid: int) -> str:
    """``ok``, ``denied``, or ``missing``. Windows-only."""
    if sys.platform != "win32":
        return "missing"
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if handle:
        kernel32.CloseHandle(handle)
        return "ok"
    if ctypes.get_last_error() == ERROR_ACCESS_DENIED:
        return "denied"
    return "missing"


def win32_foreground_pid() -> int:
    if sys.platform != "win32":
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def windows_foreground_elevated() -> bool:
    """True when the foreground process cannot be opened. Fail open on errors."""
    if sys.platform != "win32":
        return False
    try:
        return probe_pid(win32_foreground_pid(), win32_process_access)
    except Exception:
        return False
