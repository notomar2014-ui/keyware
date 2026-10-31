"""Screenshot the Keyware window without running Keyware.

Uses a copy of the config, a fake hook and fake injector: nothing is
remapped, typed, or opened. The window shows for a few seconds and closes.

    python tools/preview_ui.py out.png [manual|text|text-empty|autoclick|profiles|nudge|offtopic|multi|aimacro|manualedit] [geometry]

"autoclick" fills the Manual binder with an Auto-click binding (Gaming profile).
"profiles" selects Gaming, deletes one binding in the copy, and shows the Undo toast.
"nudge" shows the occasional Support Keyware note. The donate link in these
previews is a placeholder on the temporary copy only; the real config is untouched.

"offtopic" shows the inline error for text that is not a key binding.
"multi" shows several proposed bindings from one sentence, with conflicts flagged.
"aimacro" shows a multi-step binding the AI built (open Chrome, play on Spotify).
"manualedit" opens that AI-made macro in the Manual step editor.
"settings" opens the Settings panel (gear). "aikey" shows it with "Use my own free key" on.
"feedback" scrolls Settings to Suggestions & feedback with a sample.
"limit" shows the Text mode message when the free daily AI limit is used up.

"text" shows the Describe-it box with a sample proposal filled in. The
proposal is parsed locally from a saved model reply; no network call is made.
"""

from __future__ import annotations

import ctypes
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "ui_preview.png").resolve()
    view = sys.argv[2] if len(sys.argv) > 2 else "manual"
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    from engine.config_store import default_config_path
    from main import set_app_user_model_id
    from tests.fakes import service
    from ui.app import KeywareApp

    set_app_user_model_id()
    work = Path(tempfile.mkdtemp(prefix="kw_preview_"))
    if default_config_path().exists():
        shutil.copy2(default_config_path(), work / "config.json")
    created, *_ = service(work)
    app = KeywareApp(created, autostart=False, start_tray=False)
    created.set_enabled(True)  # fake hook: shows the ON state, captures nothing
    if len(sys.argv) > 3:
        app.geometry(sys.argv[3])  # e.g. 1120x900 to show the whole binder column
    if view in ("autoclick", "profiles", "nudge"):
        created.config.donate_url = "https://example.com/support-keyware"  # preview copy only
        app._sync_heart()
        gaming = created.config.profile_by_id("gaming")
        if gaming is not None:
            from engine.models import Action, Binding

            sample = Binding(id="preview-autoclick", enabled=True, mode="tap", keys=["f6"], label="Right auto-click",
                             action=Action(type="auto_click", button="right", cps=20, click_mode="toggle"))
            try:
                created.add_binding("gaming", sample)
            except ValueError:
                pass  # F6 already used in this config; show the rest
            app._select_profile("gaming")
    if view == "autoclick":
        from engine.models import Action

        app._set_entry(app.label, "Auto-click")
        app._set_entry(app.trigger, "R")
        app.mode.set("Tap")
        app._on_mode("Tap")
        app.action.set("Auto-click")
        app._fill_autoclick(Action(type="auto_click", button="left", cps=15, click_mode="hold", max_seconds=0))
        app._show_action_fields()
    if view == "profiles":
        bindings = created.bindings(app.selected_id)
        victims = [b for b in bindings if b.id != "preview-autoclick"]
        if victims:
            app._delete_binding(victims[-1])
            app.after_cancel(app._undo_after)  # keep the toast up for the screenshot
    if view == "nudge":
        app.show_nudge()
    if view.startswith("text"):
        app._set_binder_mode("Text")
    if view == "text":
        from assist.ai_binder import parse_reply

        sample = {
            "ok": True, "label": "YouTube: minecraft", "profile": {"scope": "global"},
            "trigger": {"mode": "tap", "keys": ["f2"]},
            "action": {"type": "open_url", "url": "https://www.youtube.com/results?search_query=minecraft"},
            "notes": "",
        }
        app._set_ai_text("F2 opens YouTube and searches minecraft")
        proposal = parse_reply(json.dumps(sample), created.config)
        proposal.model, proposal.seconds = "gemini-3.5-flash-lite", 1.5
        app._apply_proposal(proposal)

    if view in ("aimacro", "manualedit"):
        from assist.ai_binder import parse_reply

        sample = {"ok": True, "bindings": [{
            "label": "Chrome + Du bist genug", "profile": {"scope": "global"},
            "trigger": {"mode": "tap", "keys": ["f5"]},
            "action": {"type": "macro", "steps": [
                {"op": "app", "target": "chrome.exe", "fallback": "https://www.google.com"},
                {"op": "wait_window", "target": "", "text": "Google", "ms": 10000},
                {"op": "play", "target": "spotify", "text": "Du bist genug"},
                {"op": "media", "key": "volume_up", "count": 2},
            ]},
            "notes": "Spotify plays in the web player because the app isn't installed.",
        }]}
        app._set_binder_mode("Text")
        app._set_ai_text('F5 opens Chrome and plays "du bist gueng" on Spotify')
        proposal = parse_reply(json.dumps(sample), created.config)
        proposal.model, proposal.seconds = "gemini-3.5-flash-lite", 2.1
        app._apply_proposal(proposal)
        if view == "manualedit":
            app._set_binder_mode("Manual")
            app.form_title.configure(text="Edit binding")
    if view == "offtopic":
        from assist.ai_binder import NOT_BINDING_MESSAGE

        app._set_binder_mode("Text")
        app._set_ai_text("what's the weather in London tomorrow?")
        app._show_not_binding(NOT_BINDING_MESSAGE)
    if view == "multi":
        from assist.ai_binder import parse_batch

        from engine.models import Action, Binding

        try:  # preview copy only: something already bound, to show the "already bound" flag
            created.add_binding("global", Binding(id="preview-yt", enabled=True, mode="chord",
                                                  keys=["ctrl", "win", "shift", "r"], label="YouTube",
                                                  action=Action(type="open_url", url="https://www.youtube.com")))
        except ValueError:
            pass
        app._refresh_bindings()
        taken = next((b for b in created.bindings("global") if b.action.type == "open_url"), None)
        taken_keys = taken.keys if taken is not None else ["ctrl", "win", "shift", "r"]
        taken_mode = taken.mode if taken is not None else "chord"
        sample = {"ok": True, "bindings": [
            {"label": "YouTube", "profile": {"scope": "global"}, "trigger": {"mode": "tap", "keys": ["f9"]},
             "action": {"type": "open_url", "url": "https://www.youtube.com"}},
            {"label": "Lock PC", "profile": {"scope": "global"}, "trigger": {"mode": "tap", "keys": ["f10"]},
             "action": {"type": "lock_screen"}},
            {"label": "Spotify", "profile": {"scope": "global"}, "trigger": {"mode": "tap", "keys": ["f9"]},
             "action": {"type": "play", "service": "spotify", "query": "Du bist genug"}},
            {"label": "GitHub", "profile": {"scope": "global"}, "trigger": {"mode": taken_mode, "keys": taken_keys},
             "action": {"type": "open_url", "url": "https://github.com"}},
        ]}
        app._set_binder_mode("Text")
        app._set_ai_text("F9 opens YouTube, F10 locks my PC, F9 plays Du bist genug, and the shortcut opens GitHub")
        app._apply_result(parse_batch(json.dumps(sample), created.config))
        app.update()
        app.right_panel._parent_canvas.yview_moveto(0.25)

    if view in ("settings", "aikey", "feedback"):
        if view == "aikey":
            created.config.ai_use_own_key = True  # preview copy only
        app._open_settings()
        if view == "aikey":
            app._sync_ai_key_settings()
            app.ai_key_status.configure(text="Detected: Google Gemini key. Press Save.", text_color=("#15803d", "#22c55e"))
        if view == "feedback":
            app.feedback_type.set("Idea")
            app.feedback_email.insert(0, "you@example.com")
            app.feedback_text.insert("1.0", "It would be great if profiles could switch automatically for more games.")
            app._feedback_count()
            app.update()
            app.after(300, lambda: app.settings_scroll._parent_canvas.yview_moveto(1.0))
    if view == "limit":
        from assist.ai_binder import LIMIT_MESSAGE

        app._set_binder_mode("Text")
        app._set_ai_text("F9 opens YouTube and F10 types my email")
        app._ai_set_status(LIMIT_MESSAGE, error=True, offer_key=True)

    def shoot() -> None:
        from PIL import ImageGrab

        app._sync_status()
        app.update()
        hwnd = int(app.wm_frame(), 16)
        import ctypes.wintypes as wt

        rect = wt.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True).save(out)
        print(out)
        app.after(200, app.destroy)

    app.attributes("-topmost", True)
    app.after(2500, shoot)
    app.mainloop()
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
