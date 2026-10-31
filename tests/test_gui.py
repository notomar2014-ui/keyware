"""Window behavior with a fake engine. No real keystrokes or launches."""

from __future__ import annotations

import os

import pytest

from tests.fakes import service


def _labels(widget) -> list[str]:
    found: list[str] = []

    def walk(node) -> None:
        if node.__class__.__name__ == "Label":
            text = node.cget("text")
            if text:
                found.append(str(text))
        for child in node.winfo_children():
            walk(child)

    walk(widget)
    return found


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="Keyware window needs a display")
def test_window_shows_samples_saves_a_binding_and_toggles_theme(tmp_path):
    from ui.app import KeywareApp

    created, opener, _injector, _audio, hook = service(tmp_path)
    app = KeywareApp(created, autostart=False, start_tray=False)
    try:
        app.update_idletasks()
        app.update()
        labels = _labels(app)
        assert "KEYWARE" in labels
        assert "Ctrl + Win + Shift + R" in labels
        assert "Open YouTube" in labels
        # v1.3.1 header: logo + wordmark + subtitle only. No panic row, status card, or theme/power buttons on screen.
        assert not any("Panic" in label for label in labels)
        assert app.title() == "Keyware"
        assert app.logo.cget("image") is not None
        app._sync_status()
        assert app.status_tip.text in ("Keyware is ON - click to turn off", "Keyware is OFF - click to turn on")
        app.status_tip.show()
        assert app.status_tip._tip is not None
        app.status_tip.hide()
        assert app.status_tip._tip is None
        assert not app.power.winfo_ismapped() and not app.theme_button.winfo_ismapped()
        assert not app.status.winfo_ismapped()
        assert not any("Start with Windows" in label for label in labels)

        app._set_entry(app.trigger, "v")
        app.mode.set("Tap")
        app._on_mode("Tap")
        app.action.set("Open URL")
        app._show_action_fields()
        app._set_entry(app.url, "https://example.com/demo")
        app._set_entry(app.label, "Example")
        assert app.power.cget("text") == "TURN ON"
        assert app.status.cget("text") == "OFF"
        app._save_binding()
        saved = [item for item in created.bindings("global") if item.keys == ["v"]]
        assert saved and saved[0].action.url == "https://example.com/demo"
        assert "Example" in _labels(app)
        # Saving a binding while OFF arms Keyware so the binding works right away.
        assert hook.starts == 1
        assert created.snapshot()["hook"] == "on"
        assert app.power.cget("text") == "TURN OFF"
        assert app.status.cget("text") == "ON"

        app._toggle_power()
        assert created.snapshot()["hook"] == "off"
        assert app.power.cget("text") == "TURN ON"
        app._toggle_power()
        assert created.snapshot()["hook"] == "on"
        assert hook.starts == 2
        app._toggle_power()

        app._select_profile("gaming")
        gaming = _labels(app)
        assert any("Hold Q" in label for label in gaming)
        assert any("Sneak" in label for label in gaming)

        import customtkinter as ctk

        assert ctk.get_appearance_mode() == "Dark"
        app._sync_status()
        assert app.title() == "Keyware"  # status lives in the dot, not the title
    finally:
        app.destroy()


def _walk_labels(widget):
    found = []

    def walk(node) -> None:
        if node.__class__.__name__ == "Label":
            found.append(node)
        for child in node.winfo_children():
            walk(child)

    walk(widget)
    return found


def test_self_test_and_build_contract():
    from pathlib import Path

    from engine.selftest import run

    assert run() == 0
    spec = Path("keyware.spec").read_text(encoding="utf-8")
    script = Path("build.py").read_text(encoding="utf-8")
    assert 'name="Keyware"' in spec
    assert "console=False" in spec
    assert "COLLECT" not in spec
    assert "monochrome.json" in spec
    assert "PyInstaller" in script
    assert "Keyware.exe" in script


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="Keyware window needs a display")
def test_trigger_and_combo_fields_record_keys_directly(tmp_path):
    from ui.app import KeywareApp, tk_key_name

    created, opener, _injector, _audio, hook = service(tmp_path)
    app = KeywareApp(created, autostart=False, start_tray=False)
    try:
        app.update()
        # Clicking / focusing the trigger field starts recording (Keyware OFF: hook runs just for it).
        app._start_recording(app.trigger, "trigger")
        assert created.recording and hook.running and created.config.enabled is False
        assert app.capture_button.cget("text") == "Listening…"
        for key, down, t in [("ctrl", True, 1.0), ("win", True, 1.02), ("shift", True, 1.04)]:
            created.handle_input(key, down, t)
        app._drain()
        assert app.trigger.get() == "Ctrl + Win + Shift + …"  # live modifiers
        for key, down, t in [("r", True, 1.06), ("r", False, 1.1), ("shift", False, 1.12), ("win", False, 1.13), ("ctrl", False, 1.14)]:
            created.handle_input(key, down, t)
        app._drain()
        assert app.trigger.get() == "Ctrl + Win + Shift + R"
        assert app.mode.get() == "Chord"
        assert created.recording is False and opener.urls == []

        # A hold fills Hold mode and its length.
        app._start_recording(app.trigger, "trigger")
        created.handle_input("p", True, 5.0)
        created.handle_input("p", False, 7.0)
        app._drain()
        assert app.trigger.get() == "P" and app.mode.get() == "Hold"
        assert int(app.hold.get()) == 2000

        # Esc cancels and keeps the previous value.
        app._start_recording(app.trigger, "trigger")
        created.handle_input("esc", True, 9.0)
        created.handle_input("esc", False, 9.05)
        app._drain()
        assert app.trigger.get() == "P"

        # Backspace clears.
        app._start_recording(app.trigger, "trigger")
        created.handle_input("backspace", True, 10.0)
        created.handle_input("backspace", False, 10.05)
        app._drain()
        assert app.trigger.get() == ""

        # Key combo output records the same way.
        app._start_recording(app.combo, "combo")
        for key, down, t in [("shift", True, 11.0), ("1", True, 11.02), ("1", False, 11.05), ("shift", False, 11.06)]:
            created.handle_input(key, down, t)
        app._drain()
        assert app.combo.get() == "Shift + 1"

        # Leader key field takes one key and saves it.
        app._start_recording(app.leader_entry, "leader")
        created.handle_input("f13", True, 12.0)
        created.handle_input("f13", False, 12.05)
        app._drain()
        assert created.config.leader_key == "f13"

        # Leader then key in the trigger field records a leader binding.
        app._start_recording(app.trigger, "trigger")
        for key, down, t in [("f13", True, 13.0), ("f13", False, 13.05), ("y", True, 13.2), ("y", False, 13.25)]:
            created.handle_input(key, down, t)
        app._drain()
        assert app.trigger.get() == "Y" and app.mode.get() == "Leader"

        assert tk_key_name("Control_L") == "ctrl" and tk_key_name("F1") == "f1" and tk_key_name("Escape") == "esc"
    finally:
        app.destroy()


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="Keyware window needs a display")
def test_saving_a_clashing_trigger_is_blocked_with_a_replace_option(tmp_path):
    from engine.models import Action, Binding
    from ui.app import KeywareApp

    created, *_ = service(tmp_path)
    created.add_binding("global", Binding(id="", enabled=True, mode="chord", keys=["ctrl", "f20"],
                                          action=Action(type="type_text", text="one"), label="First"))
    app = KeywareApp(created, autostart=False, start_tray=False)
    try:
        app.update()
        app._set_entry(app.trigger, "Ctrl + F20")
        app.mode.set("Chord")
        app._on_mode("Chord")
        app.action.set("Type text")
        app._show_action_fields()
        app._set_entry(app.label, "Second")
        app.text.delete("1.0", "end") if hasattr(app.text, "delete") else None
        app.text.insert("1.0", "two") if hasattr(app.text, "insert") else None
        app._save_binding()
        assert "First" in app.form_error.cget("text")
        assert app.replace_button.winfo_manager() == "pack"
        assert [b.label for b in created.bindings("global") if b.keys == ["ctrl", "f20"]] == ["First"]
        app._save_binding(replace=True)
        assert [b.label for b in created.bindings("global") if b.keys == ["ctrl", "f20"]] == ["Second"]
        assert app.replace_button.winfo_manager() == ""
    finally:
        app.destroy()
