"""One Keyware process per session.

Windows uses a named mutex, ``Local\\Keyware.single``. A second process sees
``ERROR_ALREADY_EXISTS`` (183) and exits. Tests pass their own acquire and
release callables and never call CreateMutexW.
"""

from __future__ import annotations

import sys
from typing import Callable

MUTEX_NAME = "Local\\Keyware.single"
ERROR_ALREADY_EXISTS = 183

Acquire = Callable[[], object]
Release = Callable[[object], None]


class InstanceLock:
    def __init__(self, acquire: Acquire | None = None, release: Release | None = None):
        self._acquire = acquire or _default_acquire
        self._release = release or _default_release
        self._token: object = None

    def acquire(self) -> bool:
        token = self._acquire()
        if token is None or token is False:
            self._token = None
            return False
        self._token = token
        return True

    def release(self) -> None:
        token = self._token
        self._token = None
        if not token:
            return
        try:
            self._release(token)
        except Exception:
            return


def _default_acquire() -> object:
    if sys.platform != "win32":
        return _file_acquire()
    return _win32_acquire()


def _default_release(token: object) -> None:
    if sys.platform != "win32":
        _file_release(token)
        return
    _win32_release(token)


def _win32_acquire() -> object:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return False
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    return handle


def _win32_release(token: object) -> None:
    import ctypes

    ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(token)


def _file_acquire() -> object:
    """A non-Windows stand-in so a second process still exits."""
    import os
    from pathlib import Path

    from engine.config_store import appdata_dir

    directory = appdata_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "keyware.lock"
    handle = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if sys.platform == "win32":
            return handle
        import fcntl

        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(handle)
        return False
    return (handle, Path(path))


def _file_release(token: object) -> None:
    import os

    handle = token[0] if isinstance(token, tuple) else token
    try:
        if sys.platform != "win32":
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(handle)
    except OSError:
        return
