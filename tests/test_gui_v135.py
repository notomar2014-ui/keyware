"""v1.3.5 window: tap / hold fields, Do nothing, Copilot and Caps Lock hints. Fully mocked."""

from __future__ import annotations

import os

import pytest

from engine.models import Action, Binding
from tests.fakes import service

needs_display = pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="Keyware window needs a display")


def _app(tmp_path):
    from ui.app import KeywareApp

    created, *_ = service(tmp_path)
    app = KeywareApp(created, autostart=False, start_tray=False)
    app.update()
    return app, created


@needs_display
def test_caps_lock_tap_esc_hold_ctrl_saves_from_the_form(tmp_path):
    app, created = _app(tmp_path)
    try:
        app._set_entry(app.trigger, "Caps Lock")
        app.mode.set("Hold")
        app._on_mode("Hold")
        app.action.set("Key combo")
        app._on_action("Key combo")
        app._set_entry(app.combo, "Ctrl")
        app.tap_choice.set("Key or shortcut")
        app._on_tap_choice("Key or shortcut")
        app._set_entry(app.tap_value, "Esc")
        app.keep_held.select()
        app._on_keep_held()
        app.update()
        assert int(round(float(app.hold.get()))) == 200
        app._save_binding()
        saved = [b for b in created.bindings("global") if b.keys == ["caps_lock"]]
        assert saved, app.form_error.cget("text")
        [b] = saved
        assert b.mode == "hold" and b.hold_ms == 200 and b.hold_press and b.tap_action.keys == ["esc"]
        # Editing shows the same fields again.
        app._edit_binding(b)
        app.update()
        assert app.tap_choice.get() == "Key or shortcut" and app.tap_value.get() == "Esc"
        assert app.keep_held.get() == 1
        assert "Shift + Caps Lock" in app.key_hint.cget("text") and app.key_hint.winfo_ismapped()
    finally:
        app.destroy()


@needs_display
def test_do_nothing_and_copilot_hint(tmp_path):
    app, created = _app(tmp_path)
    try:
        binding = Binding(id="cp", enabled=True, mode="tap", keys=["copilot"], action=Action(type="none"))
        created.add_binding("global", binding)
        app._edit_binding(binding)
        app.update()
        assert app.trigger.get() == "Copilot key"
        assert app.action.get() == "Do nothing"
        assert "Customize Copilot key" in app.key_hint.cget("text")
        app._clear_form()
        app.update()
        assert not app.key_hint.winfo_ismapped()
    finally:
        app.destroy()


@needs_display
def test_text_mode_caps_proposal_saves_tap_and_hold(tmp_path):
    import json

    from assist.ai_binder import parse_batch

    app, created = _app(tmp_path)
    try:
        [proposal] = parse_batch(json.dumps({
            "ok": True, "label": "Caps", "profile": {"scope": "global"},
            "trigger": {"mode": "hold", "keys": ["caps_lock"], "hold_ms": 200, "keep_held": True},
            "action": {"type": "key_combo", "keys": ["ctrl"]}, "tap_action": {"type": "key_combo", "keys": ["esc"]}}))
        app._set_binder_mode("Text")
        app._apply_proposal(proposal)
        app.update()
        app._save_binding()
        [b] = [b for b in created.bindings("global") if b.keys == ["caps_lock"]]
        assert b.hold_press and b.tap_action.keys == ["esc"] and b.hold_ms == 200
    finally:
        app.destroy()
