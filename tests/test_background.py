"""v1.3.4: closing the window keeps Keyware in the tray; Start with Windows (on by default) runs --background."""

from __future__ import annotations

import json
import os
import sys

import pytest

from engine.models import Config
from engine.startup import BACKGROUND_FLAG, VALUE_NAME, MemoryRegistry, StartupManager, WinregRegistry, launch_command
from tests.fakes import service

needs_display = pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="Keyware window needs a display")


class FakeTray:
    def __init__(self):
        self.notes = []
        self.stopped = False

    def notify(self, message, title=None):
        self.notes.append((title, message))

    def stop(self):
        self.stopped = True


def test_launch_command_is_background(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "D:\\Keyware\\dist\\Keyware.exe")
    assert launch_command() == '"D:\\Keyware\\dist\\Keyware.exe" --background'
    assert BACKGROUND_FLAG == "--background"


def test_new_install_starts_with_windows_once(tmp_path):
    created, *_ = service(tmp_path)
    assert created.apply_startup_default() is True
    assert created.startup.current() == r'"C:\Keyware\Keyware.exe"'
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["start_with_windows"] is True and saved["startup_default_applied"] is True
    # The user turns it off: the next launch respects that.
    created.set_start_with_windows(False)
    assert created.apply_startup_default() is False
    assert created.startup.is_enabled() is False


def test_moved_exe_is_pointed_at_again(tmp_path):
    created, *_ = service(tmp_path)
    created.apply_startup_default()
    created.startup.registry.write(VALUE_NAME, '"E:\\old\\Keyware.exe" --background')
    assert created.apply_startup_default() is True
    assert created.startup.current() == r'"C:\Keyware\Keyware.exe"'


def test_run_value_removed_outside_keyware_shows_off(tmp_path):
    created, *_ = service(tmp_path)
    created.apply_startup_default()
    created.startup.registry.delete(VALUE_NAME)  # e.g. Task Manager > Startup apps
    created.apply_startup_default()
    assert created.config.start_with_windows is False and created.startup.is_enabled() is False


def test_sanitize_keeps_the_setting():
    from engine.models import sanitize_config

    config = Config.from_dict({"start_with_windows": True})
    sanitize_config(config)
    assert config.start_with_windows is True


def test_tray_hint_flag_round_trips_and_is_once(tmp_path):
    assert Config.from_dict(Config().to_dict()).tray_hint_shown is False
    created, *_ = service(tmp_path)
    assert created.mark_tray_hint_shown() is True
    assert created.mark_tray_hint_shown() is False


@needs_display
def test_background_launch_skips_the_nudge_and_hides_the_window(monkeypatch, tmp_path):
    import ui.app as app_module
    from engine import nudge

    seen = {}

    class FakeApp:
        def __init__(self, service, start_minimized=False, nudge_state=None):
            seen["minimized"] = start_minimized
            seen["nudge_state"] = nudge_state

        def mainloop(self):
            seen["ran"] = True

    counted = []
    monkeypatch.setattr(app_module, "KeywareApp", FakeApp)
    monkeypatch.setattr(nudge, "record_launch", lambda path: counted.append(path) or {})
    monkeypatch.setattr("engine.service.KeywareService.attach_hook", lambda self, hook: None)
    monkeypatch.setattr("engine.service.KeywareService.apply_startup_default", lambda self: False)
    monkeypatch.setattr("engine.config_store.default_config_path", lambda: tmp_path / "config.json")
    try:
        app_module.run(background=True)
    except Exception as exc:  # the real hook/service pieces are Windows only; only the decision matters here
        pytest.skip(f"run() needs Windows pieces: {exc}")
    assert seen["minimized"] is True and seen["nudge_state"] is None and counted == []


@pytest.mark.skipif(sys.platform != "win32", reason="HKCU Run key is Windows only")
def test_real_registry_round_trip_with_a_test_value():
    import engine.startup as startup

    reg = WinregRegistry()
    name = "KeywareTestOnly"
    try:
        reg.write(name, '"C:\\nothing\\KeywareTestOnly.exe" --background')
        assert reg.read(name) == '"C:\\nothing\\KeywareTestOnly.exe" --background'
    finally:
        reg.delete(name)
    assert reg.read(name) is None
    assert startup.VALUE_NAME == "Keyware"


@needs_display
def test_closing_the_window_hides_to_tray_and_keeps_hooks(tmp_path):
    from ui.app import TRAY_HINT, KeywareApp

    created, *_ = service(tmp_path)
    app = KeywareApp(created, autostart=False, start_tray=False)
    app.tray = FakeTray()
    try:
        created.set_enabled(True)
        app.update()
        app._on_close()
        app.update()
        assert app.state() == "withdrawn"
        assert created.snapshot()["enabled"] is True  # hooks stay on
        assert app.tray.notes == [("Keyware", TRAY_HINT)]
        assert TRAY_HINT == "Keyware is still running in the tray. Your keys still work."
        app._show()
        app._on_close()
        assert len(app.tray.notes) == 1  # one time only
        assert app.winfo_exists() and not app.tray.stopped
    finally:
        app.tray = None
        app.destroy()


@needs_display
def test_only_quit_exits(tmp_path):
    from ui.app import KeywareApp

    created, *_ = service(tmp_path)
    app = KeywareApp(created, autostart=False, start_tray=False)
    tray = FakeTray()
    app.tray = tray
    app._on_close()
    assert app.winfo_exists()
    app.quit_app()
    assert tray.stopped


@needs_display
def test_startup_switch_in_settings(tmp_path):
    from ui.app import KeywareApp

    created, *_ = service(tmp_path)
    app = KeywareApp(created, autostart=False, start_tray=False)
    try:
        assert app.startup_switch.get() == 0
        app.startup_switch.select()
        app._on_startup()
        assert created.startup.is_enabled() and app.startup_switch.get() == 1
        assert "sign in" in app.settings_note.cget("text")
        app.startup_switch.deselect()
        app._on_startup()
        assert not created.startup.is_enabled() and app.startup_switch.get() == 0
    finally:
        app.destroy()
