"""Microsoft Store (MSIX) build: Start with Windows uses the manifest startup task, never HKCU Run.

Everything here is faked: no real kernel32, no WinRT, no registry, no window.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import engine.startup as startup
from engine.startup import (
    DISABLED_BY_USER_MESSAGE,
    PACKAGED_COMMAND,
    STARTUP_TASK_ID,
    TASK_DISABLED,
    TASK_DISABLED_BY_POLICY,
    TASK_DISABLED_BY_USER,
    TASK_ENABLED,
    TASK_ENABLED_BY_POLICY,
    MemoryRegistry,
    StartupManager,
    StartupTaskRegistry,
    WinregRegistry,
    is_packaged,
    launched_by_startup_task,
)
from engine.config_store import ConfigStore
from engine.service import build_service
from tests.fakes import RecordingAudio, RecordingInjector, RecordingOpener

ROOT = Path(__file__).resolve().parents[1]


class FakeKernel32:
    def __init__(self, result: int):
        self.result = result
        self.calls = 0

    def GetCurrentPackageFullName(self, length, buffer):  # noqa: N802 (Win32 name)
        self.calls += 1
        assert buffer is None
        return self.result


class FakeTask:
    """Stands in for Windows.ApplicationModel.StartupTask."""

    def __init__(self, state: int = TASK_DISABLED, enable_to: int = TASK_ENABLED):
        self._state = state
        self.enable_to = enable_to
        self.enable_calls = 0
        self.disable_calls = 0

    def state(self) -> int:
        return self._state

    def request_enable(self) -> int:
        self.enable_calls += 1
        if self._state in (TASK_DISABLED,):
            self._state = self.enable_to
        return self._state

    def disable(self) -> None:
        self.disable_calls += 1
        if self._state == TASK_ENABLED:
            self._state = TASK_DISABLED


def packaged_service(tmp_path, task: FakeTask):
    manager = StartupManager(registry=StartupTaskRegistry(task), command=PACKAGED_COMMAND, packaged=True)
    return build_service(
        ConfigStore(tmp_path / "config.json"),
        opener=RecordingOpener(),
        injector=RecordingInjector(),
        audio=RecordingAudio(),
        startup=manager,
    )


# --- package detection ---------------------------------------------------------


def test_not_packaged_when_windows_says_no_package():
    kernel = FakeKernel32(startup.APPMODEL_ERROR_NO_PACKAGE)
    assert is_packaged(kernel) is False and kernel.calls == 1


def test_packaged_when_windows_asks_for_a_buffer():
    assert is_packaged(FakeKernel32(startup.ERROR_INSUFFICIENT_BUFFER)) is True


def test_detection_failure_means_not_packaged():
    class Broken:
        def GetCurrentPackageFullName(self, *_):  # noqa: N802
            raise OSError("no such API (Windows 7)")

    assert is_packaged(Broken()) is False


@pytest.mark.skipif(sys.platform == "win32", reason="checks the non-Windows default")
def test_off_windows_is_never_packaged():
    assert is_packaged() is False


# --- backend selection: the plain exe is unchanged -----------------------------


def test_unpackaged_manager_keeps_the_run_key(monkeypatch):
    monkeypatch.setattr(startup, "is_packaged", lambda kernel32=None: False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "D:\\Keyware\\dist\\Keyware.exe")
    manager = StartupManager()
    assert manager.packaged is False
    assert isinstance(manager.registry, WinregRegistry)
    assert manager.command == '"D:\\Keyware\\dist\\Keyware.exe" --background'


def test_packaged_manager_uses_the_startup_task(monkeypatch):
    monkeypatch.setattr(startup, "is_packaged", lambda kernel32=None: True)
    manager = StartupManager()
    assert manager.packaged is True
    assert isinstance(manager.registry, StartupTaskRegistry)
    assert manager.command == PACKAGED_COMMAND == "startupTask:KeywareStartup --background"


def test_injected_registry_is_never_packaged(monkeypatch):
    monkeypatch.setattr(startup, "is_packaged", lambda kernel32=None: pytest.fail("must not probe"))
    manager = StartupManager(registry=MemoryRegistry(), command="x")
    assert manager.packaged is False


def test_packaged_never_touches_winreg(monkeypatch):
    def no_winreg(*_a, **_k):
        raise AssertionError("the MSIX build must not write HKCU Run")

    monkeypatch.setattr(WinregRegistry, "write", no_winreg)
    monkeypatch.setattr(WinregRegistry, "delete", no_winreg)
    task = FakeTask(TASK_DISABLED)
    manager = StartupManager(registry=StartupTaskRegistry(task), command=PACKAGED_COMMAND, packaged=True)
    manager.set_enabled(True)
    manager.set_enabled(False)
    assert task.enable_calls == 1 and task.disable_calls == 1


# --- StartupTask states ----------------------------------------------------------


def test_enable_and_disable_round_trip():
    task = FakeTask(TASK_DISABLED)
    manager = StartupManager(registry=StartupTaskRegistry(task), command=PACKAGED_COMMAND, packaged=True)
    assert manager.is_enabled() is False
    manager.set_enabled(True)
    assert manager.is_enabled() is True and manager.current() == PACKAGED_COMMAND
    manager.set_enabled(False)
    assert manager.is_enabled() is False and manager.current() is None


def test_already_enabled_does_not_ask_again():
    task = FakeTask(TASK_ENABLED)
    StartupTaskRegistry(task).write("Keyware", PACKAGED_COMMAND)
    assert task.enable_calls == 0


def test_disabled_by_user_is_reported_not_overridden():
    task = FakeTask(TASK_DISABLED_BY_USER)
    with pytest.raises(OSError, match="Settings > Apps > Startup"):
        StartupTaskRegistry(task).write("Keyware", PACKAGED_COMMAND)
    assert task.enable_calls == 0 and task.state() == TASK_DISABLED_BY_USER


def test_request_that_lands_on_disabled_by_user_raises():
    task = FakeTask(TASK_DISABLED, enable_to=TASK_DISABLED_BY_USER)
    with pytest.raises(OSError) as info:
        StartupTaskRegistry(task).write("Keyware", PACKAGED_COMMAND)
    assert str(info.value) == DISABLED_BY_USER_MESSAGE


def test_policy_states():
    with pytest.raises(OSError, match="policy"):
        StartupTaskRegistry(FakeTask(TASK_DISABLED_BY_POLICY)).write("Keyware", PACKAGED_COMMAND)
    forced = FakeTask(TASK_ENABLED_BY_POLICY)
    assert StartupTaskRegistry(forced).read("Keyware") == PACKAGED_COMMAND
    with pytest.raises(OSError, match="policy"):
        StartupTaskRegistry(forced).delete("Keyware")


def test_disable_when_already_off_is_a_no_op():
    task = FakeTask(TASK_DISABLED_BY_USER)
    StartupTaskRegistry(task).delete("Keyware")
    assert task.disable_calls == 0


def test_missing_winrt_becomes_oserror():
    class Missing:
        def state(self):
            raise ImportError("No module named 'winrt'")

    with pytest.raises(OSError, match="startup task is unavailable"):
        StartupTaskRegistry(Missing()).read("Keyware")


# --- the service and the Settings switch -----------------------------------------


def test_packaged_default_is_on_and_silent(tmp_path):
    task = FakeTask(TASK_DISABLED)
    created = packaged_service(tmp_path, task)
    assert created.apply_startup_default() is True
    assert task.state() == TASK_ENABLED
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["start_with_windows"] is True and saved["startup_default_applied"] is True
    # Second launch: already enabled, nothing rewritten (no "moved exe" logic for packages).
    assert created.apply_startup_default() is False
    assert task.enable_calls == 1


def test_packaged_toggle_off_then_on(tmp_path):
    task = FakeTask(TASK_ENABLED)
    created = packaged_service(tmp_path, task)
    assert created.set_start_with_windows(False) is False
    assert task.state() == TASK_DISABLED and created.config.start_with_windows is False
    assert created.apply_startup_default() is False  # the user's choice wins on the next launch
    assert task.state() == TASK_DISABLED
    assert created.set_start_with_windows(True) is True and task.state() == TASK_ENABLED


def test_turned_off_in_task_manager_shows_off_and_explains(tmp_path):
    task = FakeTask(TASK_ENABLED)
    created = packaged_service(tmp_path, task)
    created.apply_startup_default()
    task._state = TASK_DISABLED_BY_USER  # Task Manager > Startup apps > Disable
    created.apply_startup_default()
    assert created.config.start_with_windows is False
    with pytest.raises(ValueError, match="Settings > Apps > Startup"):
        created.set_start_with_windows(True)
    assert created.config.start_with_windows is False


def test_first_run_with_startup_blocked_does_not_crash(tmp_path):
    created = packaged_service(tmp_path, FakeTask(TASK_DISABLED_BY_USER))
    assert created.apply_startup_default() is False
    assert created.config.start_with_windows is False


# --- launch kind -----------------------------------------------------------------


class FakeArgs:
    def __init__(self, kind):
        self.kind = kind


class FakeAppInstance:
    def __init__(self, kind):
        self._kind = kind

    def get_activated_event_args(self):
        return None if self._kind is None else FakeArgs(self._kind)


def test_launch_kind_detection():
    assert launched_by_startup_task(FakeAppInstance(1020)) is True  # ActivationKind.StartupTask
    assert launched_by_startup_task(FakeAppInstance(0)) is False  # Launch (Start menu)
    assert launched_by_startup_task(FakeAppInstance(None)) is False


def test_launch_kind_without_winrt_is_false():
    # Off Windows (and in the plain exe) winrt is not installed: never background by surprise.
    assert launched_by_startup_task() is False


def test_main_uses_startup_task_launch_only_when_packaged(monkeypatch):
    import main as entry

    seen = {}
    monkeypatch.setattr("engine.instance.InstanceLock.acquire", lambda self: True)
    monkeypatch.setattr("engine.instance.InstanceLock.release", lambda self: None)
    monkeypatch.setattr(entry, "_install_excepthook", lambda: None)
    import ui.app as app_module

    monkeypatch.setattr(app_module, "run", lambda minimized=False, background=False: seen.update(m=minimized, b=background))
    monkeypatch.setattr(startup, "is_packaged", lambda kernel32=None: True)
    monkeypatch.setattr(startup, "launched_by_startup_task", lambda app_instance=None: True)
    assert entry.main([]) == 0 and seen == {"m": True, "b": True}
    monkeypatch.setattr(startup, "is_packaged", lambda kernel32=None: False)
    assert entry.main([]) == 0 and seen == {"m": False, "b": False}
    assert entry.main(["--background"]) == 0 and seen == {"m": True, "b": True}


def test_startup_status_reports_without_changing_anything(tmp_path, monkeypatch):
    import main as entry

    monkeypatch.setattr(startup, "is_packaged", lambda kernel32=None: False)
    monkeypatch.setattr(sys, "platform", "linux")
    out = tmp_path / "status.json"
    assert entry.main(["--startup-status", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report == {"packaged": False, "backend": "MemoryRegistry", "enabled": False}


# --- manifest and packaging files stay in step with the code -----------------------


def test_store_files_match_the_code():
    manifest_path = ROOT / "store" / "AppxManifest.xml"
    if not manifest_path.exists():
        pytest.skip("store/ packaging files are not in this checkout")
    text = manifest_path.read_text(encoding="utf-8")
    assert f'TaskId="{STARTUP_TASK_ID}"' in text
    assert 'uap10:Parameters="--background"' in text
    assert 'Enabled="true"' in text
    assert 'Name="runFullTrust"' in text
    assert "windows.startupTask" in text
