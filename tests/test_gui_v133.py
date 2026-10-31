"""v1.3.3 window features with a fake engine. No real input, launches, or audio."""

from __future__ import annotations

import os

import pytest

from engine.models import Action, Binding
from tests.fakes import service
from tests.test_gui import _labels

needs_display = pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="Keyware window needs a display")


def _app(tmp_path, **kwargs):
    from ui.app import KeywareApp

    created, opener, injector, _audio, hook = service(tmp_path)
    app = KeywareApp(created, autostart=False, start_tray=False, **kwargs)
    app.update_idletasks()
    return app, created, opener, injector


@needs_display
def test_tagline_and_no_ads(tmp_path):
    app, *_ = _app(tmp_path)
    try:
        labels = _labels(app)
        assert "Make your keys your own" in labels
        assert "Chords, holds, and macros" not in labels
        assert not app.heart.winfo_ismapped() and not app.nudge.winfo_ismapped()
    finally:
        app.destroy()


@needs_display
def test_trash_deletes_instantly_and_undo_restores(tmp_path, monkeypatch):
    app, created, *_ = _app(tmp_path)
    try:
        monkeypatch.setattr(app, "_confirm", lambda *_a: pytest.fail("no confirm dialog"))
        before = [b.id for b in created.bindings("global")]
        target = created.bindings("global")[0]
        app._delete_binding(target)
        app.update()
        assert target.id not in [b.id for b in created.bindings("global")]
        assert app.toast.winfo_ismapped() and "Deleted" in app.toast_text.cget("text")
        app._undo_delete()
        app.update()
        assert [b.id for b in created.bindings("global")] == before
        assert not app.toast.winfo_ismapped()
        app._delete_binding(created.bindings("global")[0])
        app._expire_undo()
        assert app._undo is None and not app.toast.winfo_ismapped()
        cards = app.binding_list.winfo_children()
        assert all(hasattr(card, "trash") for card in cards)
    finally:
        app.destroy()


@needs_display
def test_autoclick_form_saves_and_edits(tmp_path):
    app, created, *_ = _app(tmp_path)
    try:
        app._set_entry(app.trigger, "f6")
        app.action.set("Auto-click")
        app._on_action("Auto-click")
        app.update()
        assert app.autoclick_box.winfo_ismapped()
        assert app.mode.get() == "Tap"
        app.ac_button.set("Right")
        app._set_cps(20)
        app.ac_mode.set("Toggle")
        app.ac_limit.set("30 s")
        app._save_binding()
        saved = next(b for b in created.bindings("global") if b.action.type == "auto_click")
        assert (saved.action.button, saved.action.cps, saved.action.click_mode, saved.action.max_seconds) == ("right", 20, "toggle", 30)
        assert any("Right · 20 cps · toggle" in label for label in _labels(app))
        app._clear_form()
        assert app.ac_cps_readout.cget("text") == "10 clicks/s"
        saved.action.max_seconds = 45
        app._edit_binding(saved)
        assert app.ac_limit.get() == "45 s" and app.ac_mode.get() == "Toggle"
        assert app._collect_action().max_seconds == 45
        app._set_binder_mode("Text")
        app.update()
        assert not app.autoclick_box.winfo_ismapped()
    finally:
        app.destroy()


@needs_display
def test_heart_and_nudge_follow_donate_url(tmp_path):
    from engine import nudge

    state_file = tmp_path / "state.json"
    nudge.record_launch(state_file)
    state = nudge.record_launch(state_file)  # second launch: an even one
    created, opener, *_ = service(tmp_path)
    created.config.donate_url = "https://example.com/donate"
    from ui.app import KeywareApp

    app = KeywareApp(created, autostart=False, start_tray=False, nudge_state=(state_file, state))
    try:
        app.update()
        assert app.heart.winfo_ismapped() and app.nudge.winfo_ismapped()
        app._nudge_support()
        assert opener.urls == ["https://example.com/donate"] and not app.nudge.winfo_ismapped()
        app.show_nudge()
        app._nudge_never()
        assert nudge.load_state(state_file).nudge_off is True
    finally:
        app.destroy()


@needs_display
def test_nudge_hidden_without_link_even_on_even_launch(tmp_path):
    from engine import nudge

    state_file = tmp_path / "state.json"
    nudge.record_launch(state_file)
    state = nudge.record_launch(state_file)
    app, created, *_ = _app(tmp_path, nudge_state=(state_file, state))
    try:
        app.update()
        app.show_nudge()
        app.update()
        assert not app.nudge.winfo_ismapped() and not app.heart.winfo_ismapped()
    finally:
        app.destroy()


# ---- Manual edits AI-made multi-step bindings ------------------------------------

AI_MACRO = {"ok": True, "bindings": [{
    "label": "Play Du bist genug", "profile": {"scope": "global"},
    "trigger": {"mode": "tap", "keys": ["f5"]},
    "action": {"type": "macro", "steps": [
        {"op": "app", "target": "chrome.exe", "args": ["https://a.com"], "fallback": "https://www.google.com"},
        {"op": "wait_window", "target": "chrome.exe", "ms": 8000},
        {"op": "play", "target": "spotify", "text": "Du bist genug"},
        {"op": "media", "key": "volume_up", "count": 3},
    ]},
}]}


def _ai_macro_in_manual(app):
    import json

    from assist.ai_binder import parse_reply

    proposal = parse_reply(json.dumps(AI_MACRO))
    app._set_binder_mode("Text")
    app._apply_proposal(proposal)
    app._set_binder_mode("Manual")
    app.update()
    return proposal


@needs_display
def test_ai_macro_opens_in_the_step_editor(tmp_path):
    app, *_ = _app(tmp_path)
    try:
        _ai_macro_in_manual(app)
        assert app.action.get() == "Macro" and app.macro_box.winfo_ismapped()
        ops = [row["op"].get() for row in app._macro_rows]
        assert ops == ["Open app", "Wait for window", "Play on Spotify", "Media"]
        assert app._macro_rows[0]["extra"].get() == "https://a.com"
        assert app._macro_rows[0]["fallback"].get() == "https://www.google.com"
        assert app._macro_rows[1]["extra"].get() == "8"
        assert app._macro_rows[3]["value"].get() == "Volume up x3"
        # The collected action is the same as what the AI made.
        assert [s.to_dict() for s in app._collect_steps()] == [
            s.to_dict() for s in parse_reply_steps()]
    finally:
        app.destroy()


def parse_reply_steps():
    import json

    from assist.ai_binder import parse_reply

    return parse_reply(json.dumps(AI_MACRO)).binding.action.steps


@needs_display
def test_step_editor_edit_reorder_add_remove_and_save(tmp_path):
    app, created, *_ = _app(tmp_path)
    try:
        _ai_macro_in_manual(app)
        rows = app._macro_rows
        play = rows[2]
        play["value"].delete(0, "end")
        play["value"].insert(0, "Ohne dich")
        play["op"].set("Play on YouTube")
        app._sync_step(play)
        app._move_step(play, -1)
        app._move_step(play, -1)
        assert app._macro_rows[0] is play
        app._move_step(play, -1)  # already first: no-op
        assert app._macro_rows[0] is play
        app._remove_step(app._macro_rows[-1])  # the media step
        added = app._add_macro_step("Lock screen", "")
        assert str(added["value"].cget("state")) == "disabled"
        app._set_entry(app.trigger, "F6")
        app._save_binding()
        app.update()
        saved = [b for b in created.bindings("global") if b.keys == ["f6"]]
        assert saved, app.form_error.cget("text")
        ops = [(s.op, s.target, s.text) for s in saved[0].action.steps]
        assert ops[0] == ("play", "youtube", "Ohne dich")
        assert [op for op, *_ in ops] == ["play", "app", "wait_window", "lock"]
    finally:
        app.destroy()


@needs_display
def test_step_editor_save_rechecks_safety_and_conflicts(tmp_path):
    app, created, *_ = _app(tmp_path)
    try:
        _ai_macro_in_manual(app)
        count = len(created.bindings("global"))
        bad = app._macro_rows[0]
        bad["value"].delete(0, "end")
        bad["value"].insert(0, "calc & del C:\\x")
        app._save_binding()
        app.update()
        assert "Step 1" in app.form_error.cget("text")
        assert len(created.bindings("global")) == count
        bad["value"].delete(0, "end")
        bad["value"].insert(0, "chrome.exe")
        existing = created.bindings("global")[0]
        from engine.keys import format_keys

        app.mode.set("Chord" if existing.mode == "chord" else app.mode.get())
        app._set_entry(app.trigger, format_keys(existing.keys))
        app.mode.set({"chord": "Chord", "tap": "Tap", "hold": "Hold", "leader": "Leader"}.get(existing.mode, "Chord"))
        app._on_mode(app.mode.get())
        app._save_binding()
        app.update()
        # Either the conflict panel or an error shows; the existing binding is never overwritten silently.
        assert created.bindings("global")[0].to_dict() == existing.to_dict()
    finally:
        app.destroy()


@needs_display
def test_new_single_actions_save_from_manual(tmp_path):
    app, created, *_ = _app(tmp_path)
    try:
        cases = [
            ("Open file or folder", lambda: app._set_entry(app.path_entry, r"C:\Users\Me\Music"), "open_path"),
            ("Media / volume", lambda: (app.media_menu.set("Volume up"), app._set_entry(app.media_count, "4")), "media"),
            ("Play music / video", lambda: (app.play_service.set("YouTube"), app._set_entry(app.play_query, "lofi")), "play"),
            ("Lock screen", lambda: None, "lock_screen"),
            ("Screenshot", lambda: None, "screenshot"),
            ("Run command", lambda: app._set_entry(app.command_entry, "code C:\\site"), "run_command"),
        ]
        for index, (label, fill, kind) in enumerate(cases):
            app._clear_form()
            app.action.set(label)
            app._on_action(label)
            fill()
            app.mode.set("Tap")
            app._on_mode("Tap")
            app._set_entry(app.trigger, f"F{13 + index}")
            app._save_binding()
            app.update()
            match = [b for b in created.bindings("global") if b.action.type == kind]
            assert match, (label, app.form_error.cget("text"))
        media = [b for b in created.bindings("global") if b.action.type == "media"][0].action
        assert (media.media, media.count) == ("volume_up", 4)
        app._set_entry(app.command_entry, "shutdown /s")
        app.action.set("Run command")
        app._on_action("Run command")
        app._set_entry(app.trigger, "F24")
        app._save_binding()
        app.update()
        assert not [b for b in created.bindings("global") if b.action.command == "shutdown /s"]
    finally:
        app.destroy()
