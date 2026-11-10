"""Manual / Text binder in the window, with a fake model. Nothing is opened, typed, or sent."""

from __future__ import annotations

import json
import os
import threading
import time

import pytest

from tests.fakes import service

needs_display = pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="Keyware window needs a display")


class FakeBinder:
    def __init__(self, data=None, error: str | None = None):
        self.data = data
        self.error = error
        self.threads: list[str] = []
        self.calls: list[tuple] = []

    def describe(self, text, config, profile_name):
        from assist.ai_binder import AIBinderError, parse_reply

        self.threads.append(threading.current_thread().name)
        self.calls.append((text, profile_name))
        if self.error:
            raise AIBinderError(self.error)
        proposal = parse_reply(json.dumps(self.data), config)
        proposal.model = "fake-flash-lite"
        proposal.seconds = 0.4
        return proposal


def _wait(app, timeout: float = 3.0) -> None:
    end = time.monotonic() + timeout
    while app._ai_busy and time.monotonic() < end:
        app._drain_ai()
        app.update()
        time.sleep(0.02)
    app.update()


def _app(tmp_path, binder):
    from ui.app import KeywareApp

    created, opener, injector, _audio, hook = service(tmp_path)
    app = KeywareApp(created, autostart=False, start_tray=False)
    app._ai_binder = binder
    app.update()
    return app, created, opener, injector, hook


YOUTUBE = {
    "ok": True, "label": "YouTube: minecraft", "profile": {"scope": "global"},
    "trigger": {"mode": "tap", "keys": ["f2"]},
    "action": {"type": "open_url", "url": "https://www.youtube.com/results?search_query=minecraft"},
}


@needs_display
def test_toggle_switches_between_manual_fields_and_the_text_box(tmp_path):
    app, *_ = _app(tmp_path, FakeBinder(YOUTUBE))
    try:
        assert app._binder_mode == "Manual" and app.action.winfo_ismapped() and not app.ai_panel.winfo_ismapped()
        values = list(app.action.cget("values"))
        assert values[:6] == ["Open URL", "Open app", "Type text", "Key combo", "Macro", "Auto-click"]
        assert {"Open file or folder", "Media / volume", "Play music / video", "Lock screen", "Screenshot", "Run command"} <= set(values)
        app._set_binder_mode("Text")
        app.update()
        assert app.ai_panel.winfo_ismapped() and app.ai_box.winfo_ismapped()
        assert not app.action.winfo_ismapped() and not app.trigger.winfo_ismapped() and not app.url.winfo_ismapped()
        assert app._ai_text() == "" and "Write a normal sentence" in app.ai_box.get("1.0", "end")
        app._set_binder_mode("Manual")
        app.update()
        assert app.action.winfo_ismapped() and app.url.winfo_ismapped() and not app.ai_panel.winfo_ismapped()
    finally:
        app.destroy()


@needs_display
def test_text_mode_fills_the_editor_off_thread_and_never_saves(tmp_path):
    fake = FakeBinder(YOUTUBE)
    app, created, opener, injector, hook = _app(tmp_path, fake)
    try:
        before = [b.id for b in created.bindings("global")]
        disk_before = created.store.path.read_bytes() if created.store.path.exists() else b""
        app._set_binder_mode("Text")
        app._set_ai_text("F2 opens YouTube and searches minecraft")
        app._ai_generate()
        assert app.ai_button.cget("state") == "disabled"
        _wait(app)
        assert fake.threads == ["keyware-describe"]  # ran on the worker, not the Tk thread
        assert fake.calls == [("F2 opens YouTube and searches minecraft", "Global")]
        # Fields are filled, the summary is shown, nothing is saved or run.
        assert app.trigger.get() == "F2" and app.mode.get() == "Tap" and app.action.get() == "Open URL"
        assert app.url.get().endswith("search_query=minecraft") and app.label.get() == "YouTube: minecraft"
        assert app.ai_card.winfo_ismapped()
        assert "Review, then Save" in app.ai_status.cget("text")
        assert [b.id for b in created.bindings("global")] == before
        assert (created.store.path.read_bytes() if created.store.path.exists() else b"") == disk_before
        assert opener.urls == [] and injector.events == [] and hook.starts == 0
        # Switch to Manual to tweak, then the user presses Save.
        app._set_binder_mode("Manual")
        app.update()
        assert app.url.winfo_ismapped()
        app._set_entry(app.label, "YT mc")
        app._save_binding()
        saved = [b for b in created.bindings("global") if b.keys == ["f2"]]
        assert saved and saved[0].label == "YT mc" and saved[0].action.type == "open_url"
        assert opener.urls == []
    finally:
        app.destroy()


@needs_display
def test_generated_trigger_that_clashes_shows_the_conflict_message(tmp_path):
    clash = dict(YOUTUBE, trigger={"mode": "chord", "keys": ["ctrl", "win", "shift", "r"]})
    app, created, *_ = _app(tmp_path, FakeBinder(clash))
    try:
        count = len(created.bindings("global"))
        app._set_binder_mode("Text")
        app._set_ai_text("Ctrl+Win+Shift+R opens google")
        app._ai_generate()
        _wait(app)
        assert "already used by" in app.form_error.cget("text")
        assert app.replace_button.winfo_manager() == "pack"
        assert len(created.bindings("global")) == count
    finally:
        app.destroy()


@needs_display
def test_errors_and_refusals_show_a_short_status(tmp_path):
    app, created, *_ = _app(tmp_path, FakeBinder(error="Timed out. Try again."))
    try:
        app._set_binder_mode("Text")
        app._ai_generate()  # empty box: no call
        assert "Describe" in app.ai_status.cget("text") and app._ai_binder.calls == []
        app._set_ai_text("F2 does something")
        app._ai_generate()
        _wait(app)
        assert app.ai_status.cget("text") == "Timed out. Try again."
        assert app.ai_button.cget("state") == "normal" and app._ai_proposal is None
        app._save_binding()  # nothing generated yet: Save refuses in Text mode
        assert "Generate first" in app.form_error.cget("text")
    finally:
        app.destroy()


@needs_display
def test_app_profile_suggestion_selects_the_matching_profile(tmp_path):
    data = dict(YOUTUBE, profile={"scope": "app", "name": "Gaming", "process_names": ["javaw.exe"]},
                trigger={"mode": "hold", "keys": ["r"], "hold_ms": 1500},
                action={"type": "macro", "steps": [{"op": "key", "key": "3"}, {"op": "mouse", "button": "right"}]})
    app, created, *_ = _app(tmp_path, FakeBinder(data))
    try:
        app._set_binder_mode("Text")
        app._set_ai_text("in gaming hold R to press 3 and right click")
        app._ai_generate()
        _wait(app)
        assert app.selected_id == "gaming"
        assert "Switched to the Gaming profile" in app.ai_notes.cget("text")
        assert app.mode.get() == "Hold" and int(app.hold.get()) == 1500 and app.action.get() == "Macro"
        assert [r["op"].get() for r in app._macro_rows] == ["Key", "Mouse"]
        assert not app.hold_box.winfo_ismapped()  # stays hidden in Text mode
        app._clear_form()
        assert app._ai_proposal is None and not app.ai_card.winfo_ismapped() and app._ai_text() == ""
    finally:
        app.destroy()
