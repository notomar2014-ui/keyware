"""One Keyware process per Windows session.

Windows uses a named mutex, ``Local\\Keyware.single``. A second process sees
``ERROR_ALREADY_EXISTS`` (183). A normal second launch (Start menu, desktop
shortcut, or ``Keyware.exe`` with no silent flag) then asks the running
instance to show its window and exits 0. ``--background`` and ``--minimized``
do not ask: those are silent starts and must leave the window in the tray.

The new process is the one the user just started, so Windows lets it take
the foreground. It finds the Tk frame (``TkTopLevel`` / title ``Keyware``),
calls ``AllowSetForegroundWindow`` for the owner's pid, and shows that frame
with ``SW_RESTORE`` when it is minimized and ``SW_SHOW`` otherwise. Tk still
has to ``deiconify`` on its own thread (its deiconify uses
``SW_SHOWNOACTIVATE`` and does not clear the withdrawn flag by itself), so
the new process also signals ``Local\\Keyware.activate`` and waits briefly
for ``Local\\Keyware.activate.ack``. The portable exe and the Microsoft Store
package are both full-trust in the user session, so they share these
``Local\\`` names with the mutex.

Tests pass their own callables and never call ``CreateMutexW``.
"""

from __future__ import annotations

import sys
from typing import Callable

MUTEX_NAME = "Local\\Keyware.single"
ACTIVATE_EVENT_NAME = "Local\\Keyware.activate"
ACK_EVENT_NAME = "Local\\Keyware.activate.ack"
ERROR_ALREADY_EXISTS = 183
WINDOW_TITLE = "Keyware"
WINDOW_CLASS = "TkTopLevel"  # TK_WIN_TOPLEVEL_CLASS_NAME, the wrapper around winfo_id()
SW_SHOW = 5
SW_RESTORE = 9
ASFW_ANY = 0xFFFFFFFF
GA_ROOT = 2
ACK_TIMEOUT_MS = 1000
WAIT_OBJECT_0 = 0

Acquire = Callable[[], object]
Release = Callable[[object], None]


def second_launch_action(args: list[str], *, packaged_startup: bool = False) -> str:
    """``reveal`` or ``quiet`` for a process that lost the single-instance lock.

    Quiet launches must not pop the window: ``--background`` (the Run key and
    the MSIX startup task), ``--minimized``, and a packaged startup-task
    activation that arrived with no arguments. Every other launch, including
    a Start menu tile and a plain ``Keyware.exe``, reveals the window. The
    portable exe and the Store package use the same rule.
    """
    if packaged_startup or "--background" in args or "--minimized" in args:
        return "quiet"
    return "reveal"


def handoff_foreground(pid: int, allow: Callable[[int], None]) -> None:
    """Let the running instance call ``SetForegroundWindow``.

    ``pid`` is that process. ``ASFW_ANY`` covers the moment before its window
    exists. The caller is the new process, which is the one Windows allows.
    """
    if pid:
        allow(int(pid))
    allow(ASFW_ANY)


def restore_show_command(iconic: bool) -> int:
    """Minimized windows use ``SW_RESTORE``. Hidden or covered windows use ``SW_SHOW``.

    ``SW_SHOW`` does not undo a maximized window. ``SW_RESTORE`` would.
    """
    return SW_RESTORE if iconic else SW_SHOW


def activate_existing(
    find_window: Callable[[], int],
    window_pid: Callable[[int], int],
    allow: Callable[[int], None],
    restore: Callable[[int], object],
    signal: Callable[[], bool],
    open_ack: Callable[[], object],
    wait_ack: Callable[[object], None],
) -> bool:
    """Focus the running window from this process, then ask it to deiconify.

    ``open_ack`` runs first so the reply event exists before the owner can
    answer. ``wait_ack`` keeps this process alive until that deiconify has
    run, so the foreground hand-off is still valid. A missing window still
    signals: the owner may still be creating its frame. Returns True when
    the activate event was signaled.
    """
    ack = None
    try:
        ack = open_ack()
    except Exception:
        ack = None
    hwnd = 0
    try:
        hwnd = int(find_window() or 0)
    except Exception:
        hwnd = 0
    pid = 0
    if hwnd:
        try:
            pid = int(window_pid(hwnd) or 0)
        except Exception:
            pid = 0
    try:
        handoff_foreground(pid, allow)
    except Exception:
        pass
    if hwnd:
        try:
            restore(hwnd)
        except Exception:
            pass
    try:
        signaled = bool(signal())
    except Exception:
        signaled = False
    if ack is not None:
        try:
            wait_ack(ack)
        except Exception:
            pass
    return signaled


def restore_main_window(hwnd: int, user32=None) -> str:
    """Show ``hwnd`` and give it the foreground. Returns ``restore`` or ``show``.

    ``winfo_id()`` is the child window. ``GetAncestor(GA_ROOT)`` is the
    ``TkTopLevel`` frame ``FindWindowW`` returns, which is the window Windows
    actually hides and minimizes. An empty hwnd does nothing.
    """
    if not hwnd:
        return ""
    if user32 is None:
        if sys.platform != "win32":
            return ""
        user32 = _user32()
    frame = _frame_hwnd(user32, int(hwnd))
    iconic = bool(user32.IsIconic(frame))
    command = restore_show_command(iconic)
    user32.ShowWindow(frame, command)
    user32.SetForegroundWindow(frame)
    bring = getattr(user32, "BringWindowToTop", None)
    if callable(bring):
        try:
            bring(frame)
        except Exception:
            pass
    return "restore" if iconic else "show"


def _frame_hwnd(user32, hwnd: int) -> int:
    get_ancestor = getattr(user32, "GetAncestor", None)
    if not callable(get_ancestor):
        return int(hwnd)
    try:
        root = int(get_ancestor(int(hwnd), GA_ROOT) or 0)
    except Exception:
        return int(hwnd)
    return root or int(hwnd)


def find_keyware_window(user32) -> int:
    """The main frame, hidden or not. 0 when Keyware has no window yet."""
    hwnd = user32.FindWindowW(WINDOW_CLASS, WINDOW_TITLE)
    if hwnd:
        return int(hwnd)
    hwnd = user32.FindWindowW(None, WINDOW_TITLE)
    return int(hwnd or 0)


class InstanceLock:
    def __init__(
        self,
        acquire: Acquire | None = None,
        release: Release | None = None,
        arm: Callable[[], object] | None = None,
        poll: Callable[[object], bool] | None = None,
        disarm: Callable[[object], None] | None = None,
        acknowledge: Callable[[], None] | None = None,
    ):
        self._acquire = acquire if acquire is not None else _default_acquire
        self._release = release if release is not None else _default_release
        # Any injected piece is a test. Do not open the real named event.
        injected = any(piece is not None for piece in (acquire, release, arm, poll, disarm, acknowledge))
        if injected:
            self._arm = arm if arm is not None else (lambda: None)
            self._poll = poll if poll is not None else (lambda _event: False)
            self._disarm = disarm if disarm is not None else (lambda _event: None)
            self._acknowledge = acknowledge if acknowledge is not None else (lambda: None)
        else:
            self._arm = _default_arm
            self._poll = _default_poll
            self._disarm = _default_disarm
            self._acknowledge = _default_acknowledge
        self._token: object = None
        self._event: object = None

    def acquire(self) -> bool:
        # The event exists before the mutex is taken, so a second process that
        # loses the mutex can signal an object this process already holds.
        try:
            self._event = self._arm()
        except Exception:
            self._event = None
        token = self._acquire()
        if token is None or token is False:
            self._token = None
            self._close_event()
            return False
        self._token = token
        return True

    def activation_requested(self) -> bool:
        """True once when another launch has asked this instance to show its window."""
        event = self._event
        if not event:
            return False
        try:
            return bool(self._poll(event))
        except Exception:
            return False

    def acknowledge(self) -> None:
        """Tell the new process that the window has been deiconified."""
        try:
            self._acknowledge()
        except Exception:
            return

    def release(self) -> None:
        self._close_event()
        token = self._token
        self._token = None
        if not token:
            return
        try:
            self._release(token)
        except Exception:
            return

    def _close_event(self) -> None:
        event = self._event
        self._event = None
        if not event:
            return
        try:
            self._disarm(event)
        except Exception:
            return


def notify_running_instance(
    find_window: Callable[[], int] | None = None,
    window_pid: Callable[[int], int] | None = None,
    allow: Callable[[int], None] | None = None,
    restore: Callable[[int], object] | None = None,
    signal: Callable[[], bool] | None = None,
    open_ack: Callable[[], object] | None = None,
    wait_ack: Callable[[object], None] | None = None,
) -> bool:
    """Ask the running instance to show its window. Tests pass every callable.

    With no arguments this is the real Win32 hand-off on Windows, and a no-op
    elsewhere (there is no tray window to raise).
    """
    injected = any(piece is not None for piece in (find_window, window_pid, allow, restore, signal, open_ack, wait_ack))
    if injected:
        return activate_existing(
            find_window=find_window or (lambda: 0),
            window_pid=window_pid or (lambda _hwnd: 0),
            allow=allow or (lambda _pid: None),
            restore=restore or (lambda _hwnd: ""),
            signal=signal or (lambda: False),
            open_ack=open_ack or (lambda: None),
            wait_ack=wait_ack or (lambda _handle: None),
        )
    if sys.platform != "win32":
        return False
    return _notify_win32()


def _default_acquire() -> object:
    if sys.platform != "win32":
        return _file_acquire()
    return _win32_acquire()


def _default_release(token: object) -> None:
    if sys.platform != "win32":
        _file_release(token)
        return
    _win32_release(token)


def _default_arm() -> object:
    if sys.platform != "win32":
        return None
    return _open_named(_kernel32(), ACTIVATE_EVENT_NAME)


def _default_poll(event: object) -> bool:
    if sys.platform != "win32" or not event:
        return False
    kernel32 = _kernel32()
    return kernel32.WaitForSingleObject(event, 0) == WAIT_OBJECT_0


def _default_disarm(event: object) -> None:
    if sys.platform != "win32" or not event:
        return
    _kernel32().CloseHandle(event)


def _default_acknowledge() -> None:
    if sys.platform != "win32":
        return
    _signal_named(_kernel32(), ACK_EVENT_NAME)


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


def _notify_win32() -> bool:
    user32 = _user32()
    kernel32 = _kernel32()
    state = {"ok": False}

    def signal() -> bool:
        state["ok"] = _signal_named(kernel32, ACTIVATE_EVENT_NAME)
        return state["ok"]

    def wait_ack(handle: object) -> None:
        try:
            # No point waiting out the timeout when the owner was never told.
            if state["ok"]:
                kernel32.WaitForSingleObject(handle, ACK_TIMEOUT_MS)
        finally:
            kernel32.CloseHandle(handle)

    return activate_existing(
        find_window=lambda: find_keyware_window(user32),
        window_pid=lambda hwnd: _process_id(user32, hwnd),
        allow=lambda pid: user32.AllowSetForegroundWindow(int(pid) & 0xFFFFFFFF),
        restore=lambda hwnd: restore_main_window(hwnd, user32=user32),
        signal=signal,
        open_ack=lambda: _open_named(kernel32, ACK_EVENT_NAME),
        wait_ack=wait_ack,
    )


def _user32():
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    user32.FindWindowW.restype = wintypes.HWND
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AllowSetForegroundWindow.argtypes = (wintypes.DWORD,)
    user32.AllowSetForegroundWindow.restype = wintypes.BOOL
    user32.IsIconic.argtypes = (wintypes.HWND,)
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = (wintypes.HWND,)
    user32.BringWindowToTop.restype = wintypes.BOOL
    return user32


def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateEventW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
    kernel32.SetEvent.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def _open_named(kernel32, name: str) -> object:
    # Auto-reset, initially nonsignaled. A second open of the same name
    # returns the existing event and ignores this initial state.
    handle = kernel32.CreateEventW(None, False, False, name)
    if not handle:
        return None
    return handle


def _signal_named(kernel32, name: str) -> bool:
    handle = kernel32.CreateEventW(None, False, False, name)
    if not handle:
        return False
    try:
        return bool(kernel32.SetEvent(handle))
    finally:
        kernel32.CloseHandle(handle)


def _process_id(user32, hwnd: int) -> int:
    import ctypes
    from ctypes import wintypes

    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
    return int(pid.value)


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
