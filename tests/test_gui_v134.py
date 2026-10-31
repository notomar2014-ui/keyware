"""v1.3.4 window: gear + Settings panel, AI key (optional), limit-reached message. Fully mocked."""

from __future__ import annotations

import json
import os
import time

import pytest

from tests.fakes import service

FAKE_PROTECTED_BLOB = "PROTECTED-BLOB-FOR-TEST"  # a placeholder, not a real key

needs_display = pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="Keyware window needs a display")


class LimitBinder:
    def __init__(self, code="rate_limited", message="Daily AI limit reached, try tomorrow or add your own free key"):
        self.code = code
        self.message = message

    def describe_batch(self, text, config, profile_name):
        from assist.ai_binder import AIBinderError

        raise AIBinderError(self.message, code=self.code)


def _app(tmp_path):
    from ui.app import KeywareApp

    created, *_ = service(tmp_path)
    app = KeywareApp(created, autostart=False, start_tray=False)
    app.update()
    return app, created


def _wait(app, attr="_ai_busy", timeout=3.0):
    end = time.monotonic() + timeout
    while getattr(app, attr) and time.monotonic() < end:
        app._drain_ai()
        app.update()
        time.sleep(0.02)
    app.update()


@needs_display
def test_gear_opens_settings_with_the_old_settings_and_ai_key(tmp_path):
    app, _created = _app(tmp_path)
    try:
        assert app.gear_button.winfo_ismapped()
        assert not app.settings_view.winfo_viewable()
        assert not app.audio_switch.winfo_viewable()  # moved off the main window
        app.gear_button.invoke()
        app.update()
        assert app._settings_open and app.settings_view.winfo_ismapped() and not app.body.winfo_ismapped()
        assert app.audio_switch.winfo_viewable()
        for widget in (app.audio_switch, app.dry_switch, app.leader_entry, app.ai_own_switch, app.ai_key_entry,
                       app.ai_get_key, app.ai_key_test, app.ai_provider):
            assert widget.winfo_exists()
        app.ai_key_entry.insert(0, "abc")  # the placeholder shows in clear text; typed keys are masked
        assert app.ai_key_entry._entry.cget("show") == "\u2022"
        app.ai_key_entry.delete(0, "end")
        app.settings_back.invoke()
        app.update()
        assert not app._settings_open and app.body.winfo_ismapped()
    finally:
        app.destroy()


@needs_display
def test_get_a_free_key_opens_ai_studio(tmp_path, monkeypatch):
    import webbrowser

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url))
    app, created = _app(tmp_path)
    try:
        app.ai_get_key.invoke()
        assert opened == ["https://aistudio.google.com/apikey"]
        created.set_ai_settings(provider="groq")
        app.ai_get_key.invoke()
        assert opened[-1] == "https://console.groq.com/keys"
    finally:
        app.destroy()


@needs_display
def test_save_key_stores_only_the_protected_blob(tmp_path, monkeypatch):
    import assist.secret_store as store

    monkeypatch.setattr(store, "protect", lambda key: FAKE_PROTECTED_BLOB)
    app, created = _app(tmp_path)
    try:
        app._open_settings()
        app.ai_own_switch.select()
        app._on_ai_own_toggle()
        secret = "AIza" + "Sy" + "FAKE-KEY-FOR-TESTS-ONLY" + "0" * 10
        app.ai_key_entry.insert(0, secret)
        app._save_ai_key()
        app.update()
        text = (tmp_path / "config.json").read_text(encoding="utf-8")
        assert secret not in text
        saved = json.loads(text)
        assert saved["ai_key_protected"] == FAKE_PROTECTED_BLOB and saved["ai_use_own_key"] is True
        assert app.ai_key_entry.get() == ""  # the field is cleared after saving
        assert "saved" in app.ai_key_status.cget("text").lower()
        app._remove_ai_key()
        assert created.config.ai_key_protected == "" and not created.config.ai_use_own_key
    finally:
        app.destroy()


@needs_display
def test_test_button_reports_the_model(tmp_path):
    app, created = _app(tmp_path)
    try:
        app._open_settings()
        created.set_ai_settings(use_own_key=True)
        app._sync_ai_key_settings()
        seen = []
        app._ai_key_tester = lambda provider, key: seen.append((provider, len(key))) or "gemini-3.5-flash-lite"
        app.ai_key_entry.insert(0, "AIza" + "Sy" + "FAKE-KEY-FOR-TESTS-ONLY" + "0" * 10)
        app._test_ai_key()
        end = time.monotonic() + 3
        while "Works" not in app.ai_key_status.cget("text") and time.monotonic() < end:
            app._drain_ai_key()
            app.update()
            time.sleep(0.02)
        assert "gemini-3.5-flash-lite" in app.ai_key_status.cget("text")
        assert seen == [("gemini", 39)]
    finally:
        app.destroy()


@needs_display
@pytest.mark.parametrize("code", ["rate_limited", "busy"])
def test_limit_reached_offers_to_add_a_key(tmp_path, code):
    app, _created = _app(tmp_path)
    try:
        app._ai_binder = LimitBinder(code=code)
        app._set_binder_mode("Text")
        app._set_ai_text("F2 opens YouTube")
        app._ai_generate()
        _wait(app)
        app.update()
        assert "limit" in app.ai_status.cget("text").lower()
        assert app.ai_add_key.winfo_ismapped()
        app.ai_add_key.invoke()
        app.update()
        assert app._settings_open and app.ai_own_switch.get() == 1
    finally:
        app.destroy()


@needs_display
def test_plain_errors_do_not_offer_a_key(tmp_path):
    app, _created = _app(tmp_path)
    try:
        app._ai_binder = LimitBinder(code="offline", message="No internet connection. Text mode needs to be online.")
        app._set_binder_mode("Text")
        app._set_ai_text("F2 opens YouTube")
        app._ai_generate()
        _wait(app)
        assert "internet" in app.ai_status.cget("text")
        assert not app.ai_add_key.winfo_ismapped()
    finally:
        app.destroy()


@needs_display
def test_window_makes_an_install_id_and_uses_the_proxy(tmp_path, monkeypatch):
    monkeypatch.delenv("KEYWARE_DEV_KEYS", raising=False)
    app, created = _app(tmp_path)
    try:
        binder = app._make_binder()
        assert binder.uses_proxy and binder.install_id == created.config.install_id != ""
    finally:
        app.destroy()


def test_key_detection_and_format():
    from assist.ai_binder import detect_provider, key_format_problem

    gemini = "AIza" + "Sy" + "A" * 33
    groq = "gsk_" + "a1B2" * 13
    assert detect_provider(gemini) == "gemini" and key_format_problem(gemini) == ""
    assert detect_provider(groq) == "groq" and key_format_problem(groq) == ""
    assert detect_provider("sk-proj-abcdefabcdefabcdefabcdef") is None
    assert "Gemini or Groq" in key_format_problem("sk-proj-abcdefabcdefabcdefabcdef")
    assert "wrong length" in key_format_problem("AIzaShort")
    assert "space" in key_format_problem("AIza" + "A" * 20 + " " + "A" * 14)
    for bad in ("AIzaSyA" + "A" * 30 + "!", "gsk_" + "x" * 30):
        assert detect_provider(bad) is None
        assert bad not in key_format_problem(bad)  # never echoes the key


@needs_display
def test_pasted_key_shows_the_detected_provider_and_saves_it(tmp_path, monkeypatch):
    import assist.secret_store as store

    monkeypatch.setattr(store, "protect", lambda key: FAKE_PROTECTED_BLOB)
    app, created = _app(tmp_path)
    try:
        app._open_settings(focus="ai")
        app.ai_key_entry.insert(0, "gsk_" + "a1B2" * 13)
        app._on_ai_key_typed()
        assert "Detected: Groq" in app.ai_key_status.cget("text")
        app._save_ai_key()
        assert created.config.ai_provider == "groq" and created.config.ai_use_own_key
        assert "Groq key saved" in app.ai_key_status.cget("text")
        assert "AIza" in app.ai_key_help.cget("text") and "gsk_" in app.ai_key_help.cget("text")
        assert "aistudio.google.com/apikey" in app.ai_key_help.cget("text")
        app.ai_key_entry.insert(0, "not-a-key-at-all-123")
        app._save_ai_key()
        assert "isn't a Gemini or Groq" in app.ai_key_status.cget("text")
    finally:
        app.destroy()
