"""Second-launch restore: show the running window, unless this start is silent.

No real mutex, event, or window. Win32 is a fake user32. The portable exe and
the Microsoft Store package share the decision and the Local\\ names.
"""

from __future__ import annotations

import os
import queue
import sys
from types import SimpleNamespace

import pytest

from engine.instance import (
    ACK_EVENT_NAME,
    ACTIVATE_EVENT_NAME,
    ASFW_ANY,
    GA_ROOT,
    MUTEX_NAME,
    SW_RESTORE,
    SW_SHOW,
    WINDOW_CLASS,
    WINDOW_TITLE,
    InstanceLock,
    activate_existing,
    find_keyware_window,
    handoff_foreground,
    restore_main_window,
    restore_show_command,
    second_launch_action,
)

needs_display = pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="Keyware window needs a display")


def test_second_launch_reveals_only_a_normal_start():
    assert second_launch_action([]) == "reveal"
    assert second_launch_action(["--not-a-real-flag"]) == "reveal"
    assert second_launch_action([], packaged_startup=True) == "quiet"
    assert second_launch_action(["--background"]) == "quiet"
    assert second_launch_action(["--minimized"]) == "quiet"
    assert second_launch_action(["--background", "--minimized"]) == "quiet"
    assert second_launch_action(["--minimized"], packaged_startup=True) == "quiet"
    assert restore_show_command(True) == SW_RESTORE == 9
    assert restore_show_command(False) == SW_SHOW == 5


def test_signal_names_are_the_session_namespace_shared_by_exe_and_msix():
    # Full-trust portable and MSIX processes see the same Local\\ objects.
    assert MUTEX_NAME == "Local\\Keyware.single"
    assert ACTIVATE_EVENT_NAME == "Local\\Keyware.activate"
    assert ACK_EVENT_NAME == "Local\\Keyware.activate.ack"
    for name in (MUTEX_NAME, ACTIVATE_EVENT_NAME, ACK_EVENT_NAME):
        assert name.startswith("Local\\")
    assert WINDOW_CLASS == "TkTopLevel"
    assert WINDOW_TITLE == "Keyware"


def test_handoff_names_the_owner_then_any_process():
    seen = []
    handoff_foreground(42, seen.append)
    handoff_foreground(0, seen.append)
    assert seen == [42, ASFW_ANY, ASFW_ANY]


def test_find_window_prefers_the_tk_frame_then_the_title():
    class ByTitle:
        def __init__(self):
            self.calls = []

        def FindWindowW(self, klass, title):
            self.calls.append((klass, title))
            if klass == WINDOW_CLASS:
                return 0
            return 15

    user = ByTitle()
    assert find_keyware_window(user) == 15
    assert user.calls == [(WINDOW_CLASS, WINDOW_TITLE), (None, WINDOW_TITLE)]

    class ByClass:
        def FindWindowW(self, klass, title):
            assert klass == WINDOW_CLASS and title == WINDOW_TITLE
            return 9

    assert find_keyware_window(ByClass()) == 9


class _User32:
    def __init__(self, iconic: bool, root: int | None = 50):
        self.iconic = iconic
        self.root = root
        self.calls = []

    def GetAncestor(self, hwnd, flag):
        assert flag == GA_ROOT
        self.calls.append(("ancestor", hwnd))
        return 0 if self.root is None else self.root

    def IsIconic(self, hwnd):
        self.calls.append(("iconic", hwnd))
        return self.iconic

    def ShowWindow(self, hwnd, command):
        self.calls.append(("show", hwnd, command))

    def SetForegroundWindow(self, hwnd):
        self.calls.append(("focus", hwnd))

    def BringWindowToTop(self, hwnd):
        self.calls.append(("top", hwnd))


def test_restore_uses_sw_restore_only_when_minimized():
    iconic = _User32(True)
    assert restore_main_window(5, user32=iconic) == "restore"
    assert iconic.calls == [
        ("ancestor", 5),
        ("iconic", 50),
        ("show", 50, SW_RESTORE),
        ("focus", 50),
        ("top", 50),
    ]
    shown = _User32(False)
    assert restore_main_window(5, user32=shown) == "show"
    assert ("show", 50, SW_SHOW) in shown.calls
    assert ("show", 50, SW_RESTORE) not in shown.calls


def test_restore_falls_back_when_the_frame_cannot_be_resolved():
    user = _User32(False, root=None)
    assert restore_main_window(11, user32=user) == "show"
    assert ("show", 11, SW_SHOW) in user.calls

    class NoAncestor:
        def __init__(self):
            self.hwnd = 0

        def IsIconic(self, hwnd):
            self.hwnd = hwnd
            return False

        def ShowWindow(self, hwnd, command):
            assert command == SW_SHOW

        def SetForegroundWindow(self, hwnd):
            return True

    plain = NoAncestor()
    assert restore_main_window(11, user32=plain) == "show"
    assert plain.hwnd == 11
    assert restore_main_window(0, user32=plain) == ""


def test_activate_existing_hands_off_foreground_before_it_exits():
    calls = []

    def find_window():
        calls.append("find")
        return 5

    def window_pid(hwnd):
        calls.append(("pid", hwnd))
        return 9

    assert activate_existing(
        find_window=find_window,
        window_pid=window_pid,
        allow=lambda pid: calls.append(("allow", pid)),
        restore=lambda hwnd: calls.append(("restore", hwnd)),
        signal=lambda: calls.append("signal") or True,
        open_ack=lambda: calls.append("open-ack") or "ack",
        wait_ack=lambda handle: calls.append(("wait", handle)),
    ) is True
    assert calls == [
        "open-ack",
        "find",
        ("pid", 5),
        ("allow", 9),
        ("allow", ASFW_ANY),
        ("restore", 5),
        "signal",
        ("wait", "ack"),
    ]


def test_activate_existing_signals_when_the_window_is_not_up_yet():
    calls = []
    assert activate_existing(
        find_window=lambda: 0,
        window_pid=lambda _hwnd: calls.append("pid") or 1,
        allow=lambda pid: calls.append(("allow", pid)),
        restore=lambda _hwnd: calls.append("restore"),
        signal=lambda: calls.append("signal") or True,
        open_ack=lambda: "ack",
        wait_ack=lambda _handle: calls.append("wait"),
    ) is True
    assert calls == [("allow", ASFW_ANY), "signal", "wait"]


def test_activate_existing_still_signals_when_restore_fails():
    def restore(_hwnd):
        raise OSError("show")

    assert activate_existing(
        find_window=lambda: 3,
        window_pid=lambda _hwnd: 4,
        allow=lambda _pid: None,
        restore=restore,
        signal=lambda: True,
        open_ack=lambda: None,
        wait_ack=lambda _handle: (_ for _ in ()).throw(AssertionError("no ack handle")),
    ) is True


def test_owner_polls_one_request_and_then_closes_the_event():
    signaled = {"on": False}
    closed = []

    def poll(event):
        assert event == "event"
        if signaled["on"]:
            signaled["on"] = False
            return True
        return False

    lock = InstanceLock(
        acquire=lambda: "mutex",
        release=lambda token: closed.append(("mutex", token)),
        arm=lambda: "event",
        poll=poll,
        disarm=lambda event: closed.append(("event", event)),
        acknowledge=lambda: closed.append("ack"),
    )
    assert lock.acquire() is True
    assert lock.activation_requested() is False
    signaled["on"] = True
    assert lock.activation_requested() is True
    assert lock.activation_requested() is False
    lock.acknowledge()
    lock.release()
    assert closed == ["ack", ("event", "event"), ("mutex", "mutex")]


def test_a_failed_acquire_drops_the_event_it_opened():
    closed = []
    lock = InstanceLock(
        acquire=lambda: False,
        arm=lambda: "event",
        disarm=lambda event: closed.append(event),
    )
    assert lock.acquire() is False
    assert closed == ["event"]
    assert lock.activation_requested() is False
    lock.release()
    assert closed == ["event"]


def test_show_still_restores_when_focus_is_refused():
    from ui.app import KeywareApp

    calls = []

    class Host:
        def deiconify(self):
            calls.append("deiconify")

        def lift(self):
            calls.append("lift")

        def focus_force(self):
            raise RuntimeError("foreground denied")

        def _restore_os_window(self):
            calls.append("win32")

    KeywareApp._show(Host())
    assert calls == ["deiconify", "lift", "win32"]


def test_restore_os_window_uses_an_injected_restorer():
    from ui.app import KeywareApp

    seen = []

    class Host:
        def winfo_id(self):
            return 42

        def _restore_window(self, hwnd):
            seen.append(hwnd)

    KeywareApp._restore_os_window(Host())
    assert seen == [42]


def test_restore_os_window_skips_win32_off_windows(monkeypatch):
    from ui.app import KeywareApp

    monkeypatch.setattr(sys, "platform", "linux")

    def boom(_hwnd):
        raise AssertionError("win32 restore")

    monkeypatch.setattr("engine.instance.restore_main_window", boom)

    class Host:
        def winfo_id(self):
            return 7

    KeywareApp._restore_os_window(Host())


def test_restore_os_window_calls_win32_on_windows(monkeypatch):
    from ui.app import KeywareApp

    seen = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("engine.instance.restore_main_window", lambda hwnd: seen.append(hwnd) or "show")

    class Host:
        def winfo_id(self):
            return 7

    KeywareApp._restore_os_window(Host())
    assert seen == [7]


def test_tray_show_and_a_second_launch_share_show():
    from ui.app import KeywareApp

    calls = []

    class Host:
        _activation = None
        _tray_events = queue.Queue()

        def _show(self):
            calls.append("tray")

    Host._tray_events.put("show")
    assert KeywareApp._drain_tray(Host()) is False
    assert calls == ["tray"]

    class Listener:
        def __init__(self):
            self.left = 1
            self.acked = 0

        def activation_requested(self):
            if self.left:
                self.left -= 1
                return True
            return False

        def acknowledge(self):
            self.acked += 1

    listener = Listener()
    host = Host()
    host._activation = listener
    host._show = lambda: calls.append("activate")
    host._check_activation = lambda: KeywareApp._check_activation(host)
    KeywareApp._reveal_if_requested(host)
    KeywareApp._reveal_if_requested(host)
    assert calls == ["tray", "activate"]
    assert listener.acked == 1


def test_a_broken_activation_event_does_not_raise():
    from ui.app import KeywareApp

    class Bad:
        def activation_requested(self):
            raise OSError("event")

    assert KeywareApp._check_activation(SimpleNamespace(_activation=None)) is False
    assert KeywareApp._check_activation(SimpleNamespace(_activation=Bad())) is False


def test_reveal_acks_even_when_show_fails():
    from ui.app import KeywareApp

    class Listener:
        def __init__(self):
            self.acked = False

        def activation_requested(self):
            return True

        def acknowledge(self):
            self.acked = True

    listener = Listener()

    class Host:
        _activation = listener

        def _show(self):
            raise RuntimeError("tk")

    host = Host()
    host._check_activation = lambda: KeywareApp._check_activation(host)
    with pytest.raises(RuntimeError):
        KeywareApp._reveal_if_requested(host)
    assert listener.acked is True


def test_second_launch_reveals_and_exits_zero(monkeypatch, capsys):
    from engine.instance import InstanceLock
    from main import main

    monkeypatch.setattr(InstanceLock, "acquire", lambda _self: False)
    monkeypatch.setattr("engine.instance.notify_running_instance", lambda: True)

    def boom(*_args, **_kwargs):
        raise AssertionError("a second launch must not start another window")

    monkeypatch.setattr("ui.app.run", boom)
    assert main([]) == 0
    assert "Bringing the window forward" in capsys.readouterr().out


def test_second_launch_exits_zero_when_the_signal_fails(monkeypatch, capsys):
    from engine.instance import InstanceLock
    from main import main

    monkeypatch.setattr(InstanceLock, "acquire", lambda _self: False)
    monkeypatch.setattr("engine.instance.notify_running_instance", lambda: False)
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "already running" in out
    assert "Bringing the window forward" not in out

    def boom():
        raise OSError("event")

    monkeypatch.setattr("engine.instance.notify_running_instance", boom)
    assert main([]) == 0
    assert "already running" in capsys.readouterr().out


def test_silent_second_launch_does_not_reveal(monkeypatch, capsys):
    from engine.instance import InstanceLock
    from main import main

    monkeypatch.setattr(InstanceLock, "acquire", lambda _self: False)

    def boom():
        raise AssertionError("a silent launch must not signal the running instance")

    monkeypatch.setattr("engine.instance.notify_running_instance", boom)
    assert main(["--minimized"]) == 0
    assert main(["--background"]) == 0
    assert capsys.readouterr().out == ""


def test_store_start_menu_reveals_and_the_startup_task_does_not(monkeypatch, capsys):
    import engine.startup as startup
    from engine.instance import InstanceLock
    from main import main

    monkeypatch.setattr(InstanceLock, "acquire", lambda _self: False)
    calls = []
    monkeypatch.setattr("engine.instance.notify_running_instance", lambda: calls.append("notify") or True)

    # Portable exe. No package identity, so a startup-task answer cannot hide the window.
    monkeypatch.setattr(startup, "is_packaged", lambda kernel32=None: False)
    monkeypatch.setattr(startup, "launched_by_startup_task", lambda app_instance=None: True)
    assert main([]) == 0

    # Store tile: packaged, ActivationKind.Launch, not the startup task.
    monkeypatch.setattr(startup, "is_packaged", lambda kernel32=None: True)
    monkeypatch.setattr(startup, "launched_by_startup_task", lambda app_instance=None: False)
    assert main([]) == 0

    # Store sign-in. The startup task (or a dropped --background) stays in the tray.
    monkeypatch.setattr(startup, "launched_by_startup_task", lambda app_instance=None: True)
    assert main([]) == 0
    assert main(["--background"]) == 0
    assert main(["--minimized"]) == 0
    assert calls == ["notify", "notify"]
    assert capsys.readouterr().out.count("Bringing the window forward") == 2


def test_the_running_instance_listens_for_activation(monkeypatch):
    from engine.instance import InstanceLock
    from main import main

    monkeypatch.setattr(InstanceLock, "acquire", lambda _self: True)
    monkeypatch.setattr(InstanceLock, "release", lambda _self: None)
    monkeypatch.setattr("main._install_excepthook", lambda: None)
    seen = {}

    def fake_run(minimized=False, background=False, activation=None):
        seen["minimized"] = minimized
        seen["background"] = background
        seen["activation"] = activation

    monkeypatch.setattr("ui.app.run", fake_run)
    assert main([]) == 0
    assert seen["minimized"] is False and seen["background"] is False
    assert isinstance(seen["activation"], InstanceLock)
    assert main(["--minimized"]) == 0
    assert seen["minimized"] is True and seen["background"] is False
    assert main(["--background"]) == 0
    assert seen["minimized"] is True and seen["background"] is True


@needs_display
def test_activation_unhides_a_withdrawn_window(tmp_path):
    from ui.app import KeywareApp
    from tests.fakes import service

    created, *_rest = service(tmp_path)

    class Listener:
        def __init__(self):
            self.pending = True
            self.acked = 0

        def activation_requested(self):
            if self.pending:
                self.pending = False
                return True
            return False

        def acknowledge(self):
            self.acked += 1

    listener = Listener()
    app = KeywareApp(created, autostart=False, start_tray=False, activation=listener)
    try:
        app.withdraw()
        app.update()
        assert app.state() == "withdrawn"
        app._reveal_if_requested()
        app.update()
        assert app.state() == "normal"
        assert listener.acked == 1
        app._reveal_if_requested()
        assert listener.acked == 1
    finally:
        app._activation = None
        app.destroy()
