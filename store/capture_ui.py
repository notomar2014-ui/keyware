"""Open the real Keyware 1.4.1 window and save one PNG per Store scene.

Nothing is hooked, launched, or sent. The window is the shipping UI
(ui.app.KeywareApp) with the sample profiles that ship in a new config.
Run on a display:  xvfb-run -s "-screen 0 1920x1200x24" python3 store/capture_ui.py out
On Windows the same script grabs the window with PrintWindow.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRIPT = "\n".join([
    "if app is notepad.exe",
    "  do",
    "end",
])


def _settle(app, seconds: float = 0.45) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.update()
        time.sleep(0.02)
    app.update_idletasks()
    app.update()


def _scroll_into(frame, widget) -> None:
    """CTkScrollableFrame is the inner frame; its canvas is _parent_canvas."""
    canvas = frame._parent_canvas
    canvas.update_idletasks()
    frame.update_idletasks()
    widget.update_idletasks()
    top = widget.winfo_rooty() - frame.winfo_rooty()
    span = max(1, frame.winfo_height() - canvas.winfo_height())
    canvas.yview_moveto(max(0.0, min(1.0, (top - 12) / span)))
    _settle(widget, 0.25)


def _grab(app, path: Path) -> None:
    app.lift()
    try:
        app.attributes("-topmost", True)
    except Exception:
        pass
    _settle(app, 0.35)
    if sys.platform == "win32":
        _grab_windows(app, path)
        return
    x, y = app.winfo_rootx(), app.winfo_rooty()
    w, h = app.winfo_width(), app.winfo_height()
    display = os.environ.get("DISPLAY", ":0")
    import subprocess

    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "x11grab", "-draw_mouse", "0", "-video_size", f"{w}x{h}",
            "-i", f"{display}+{x},{y}",
            "-frames:v", "1", str(path),
        ],
        check=True,
    )


def _grab_windows(app, path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    from PIL import Image

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    hwnd = user32.GetAncestor(app.winfo_id(), 2)  # GA_ROOT
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    hwnd_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    gdi32.SelectObject(mem_dc, bitmap)
    # 2 = PW_RENDERFULLCONTENT, so the window is captured even if it is covered.
    user32.PrintWindow(hwnd, mem_dc, 2)
    info = ctypes.create_string_buffer(40)
    ctypes.memset(info, 0, 40)
    ctypes.cast(info, ctypes.POINTER(ctypes.c_uint32))[0] = 40
    bits = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem_dc, bitmap, 0, height, bits, info, 0)
    image = Image.frombuffer("RGB", (width, height), bits, "raw", "BGRX", 0, -1)
    image.save(path)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, hwnd_dc)


def _app(tmp: Path):
    from engine.config_store import ConfigStore
    from engine.service import KeywareService
    from engine.startup import MemoryRegistry, StartupManager
    from tests.fakes import Clock, FakeHook, RecordingAudio, RecordingInjector, RecordingOpener
    from ui.app import KeywareApp

    service = KeywareService(
        store=ConfigStore(tmp / "config.json"),
        opener=RecordingOpener(),
        injector=RecordingInjector(),
        audio=RecordingAudio(),
        startup=StartupManager(registry=MemoryRegistry(), command="Keyware.exe"),
        hook=FakeHook(),
        clock=Clock(),
        synchronous=True,
    )
    app = KeywareApp(service, autostart=False, start_tray=False)
    app.geometry("1320x980+40+20")
    _settle(app, 0.6)
    app._clear_console()
    return app


def main(out_dir: str) -> None:
    import tempfile

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="keyware-shots-") as tmp:
        app = _app(Path(tmp))
        try:
            _grab(app, out / "06-home.png")

            app._set_binder_mode("Text")
            app._set_ai_text("F2 opens YouTube")
            _settle(app)
            _grab(app, out / "01-text.png")

            app._set_binder_mode("Manual")
            app._select_profile("gaming")
            _settle(app)
            _grab(app, out / "02-profiles.png")

            macro = next(item for item in app.service.bindings("gaming") if item.action.type == "macro")
            app._edit_binding(macro)
            if app._console_open:
                app._toggle_console()
            # Macro and the per-app rules share one grid cell. Hide the rules
            # card for this frame so the real step list is readable.
            app.app_rules_box.grid_remove()
            app.macro_box.lift()
            _scroll_into(app.right_panel, app.macro_steps)
            _grab(app, out / "03-macro.png")
            app.app_rules_box.grid()
            if not app._console_open:
                app._toggle_console()

            app._select_profile("global")
            app.service.set_scripting(True)
            app.script_switch.select()
            chord = next(item for item in app.service.bindings("global") if item.mode == "chord")
            app._edit_binding(chord)
            app.script_text.delete("1.0", "end")
            app.script_text.insert("1.0", SCRIPT)
            app._sync_script_editor()
            app._check_script()
            if app._console_open:
                app._toggle_console()
            _scroll_into(app.right_panel, app.script_box)
            _grab(app, out / "04-script.png")
            if not app._console_open:
                app._toggle_console()

            app._clear_form()
            app.service._on_panic(False)
            app._drain()
            app._show_update("1.4.1", "https://keyware.site")
            app._open_settings()
            _settle(app)
            _grab(app, out / "05-safety.png")
        finally:
            app.destroy()
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "store-captures")
