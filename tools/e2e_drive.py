"""Real end-to-end check for Keyware on Windows.

Opens a small test window, focuses it, and injects UNTAGGED SendInput events
tagged KEYWARE_TEST_EXTRA ("KWTE", not Keyware's own KWRE tag) only while that window is the
foreground window. Keyware's own actions (type text, key combo, macro) land
in the window, and the script compares the text.

Needs an "E2E" profile bound to python.exe (see tools/e2e_config.py).
Modes:
  --app      drive the running Keyware app.
  --harness  start a private KeywareService with the real hook in this process.
Never sends Ctrl+Win (that is Pulse's hotkey). Always releases every key.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
import tkinter as tk
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT

VK = {"ctrl": 0xA2, "shift": 0xA0, "alt": 0xA4, "space": 0x20, "f1": 0x70, "f8": 0x77,
      "f20": 0x83, "f21": 0x84, "f22": 0x85}
for c in "abcdefghijklmnopqrstuvwxyz":
    VK[c] = ord(c.upper())

ulong_ptr = ctypes.c_size_t
TEST_TAG = 0x4B575445  # engine.sendinput.KEYWARE_TEST_EXTRA


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr))


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr))


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = (("ki", KEYBDINPUT), ("mi", MOUSEINPUT))
    _anonymous_ = ("u",)
    _fields_ = (("type", wintypes.DWORD), ("u", _U))


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)


def force_foreground(hwnd) -> None:
    """Take focus without sending input: share the foreground thread's input state briefly."""
    kernel32 = ctypes.WinDLL("kernel32")
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    me = kernel32.GetCurrentThreadId()
    attached = bool(fg_thread and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(me, fg_thread, False)


def foreground_name() -> str:
    try:
        from engine.context_tracker import windows_foreground_name
        return os.path.basename(windows_foreground_name())
    except Exception:
        return "?"


class Aborted(RuntimeError):
    pass


class Driver:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.hwnd = None
        self.down: set[str] = set()
        self.lost = False

    def pump(self, seconds: float) -> None:
        end = time.perf_counter() + seconds
        while True:
            self.root.update()
            if self.hwnd and not self.focused():
                self.lost = True
            if time.perf_counter() >= end:
                return
            time.sleep(0.005)

    def focused(self) -> bool:
        fg = user32.GetForegroundWindow()
        return bool(fg) and fg == self.hwnd

    def send(self, key: str, up: bool) -> None:
        if not up and not self.focused():
            raise Aborted("test window lost focus to " + foreground_name() + "; stopped injecting")
        vk = VK[key]
        rec = INPUT()
        rec.type = 1
        rec.ki = KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), 0x2 if up else 0, 0, TEST_TAG)
        user32.SendInput(1, ctypes.byref(rec), ctypes.sizeof(INPUT))
        (self.down.discard if up else self.down.add)(key)

    def tap(self, key: str, hold: float = 0.04, after: float = 0.12) -> None:
        self.send(key, False)
        self.pump(hold)
        self.send(key, True)
        self.pump(after)

    def chord(self, mods: list[str], key: str) -> None:
        for m in mods:
            self.send(m, False)
            self.pump(0.03)
        self.tap(key, after=0.03)
        for m in reversed(mods):
            self.send(m, True)
            self.pump(0.03)
        self.pump(0.15)

    def release_all(self) -> None:
        """Release only what this driver pressed. Nothing else is sent."""
        for key in list(self.down):
            rec = INPUT()
            rec.type = 1
            rec.ki = KEYBDINPUT(VK[key], user32.MapVirtualKeyW(VK[key], 0), 0x2, 0, TEST_TAG)
            user32.SendInput(1, ctypes.byref(rec), ctypes.sizeof(INPUT))
        self.down.clear()


def _leader(d, key):
    d.tap("f1", hold=0.05, after=0.15); d.tap(key); d.pump(0.3)


def _hold(d):
    d.send("p", False); d.pump(0.8); d.send("p", True); d.pump(0.3)


def _dance(d):
    d.tap("f8", hold=0.04, after=0.06); d.tap("f8", hold=0.04, after=0.4)


# (name, expected text, how to type it)
STEPS = [
    ("passthrough a b", "ab", lambda d: (d.tap("a"), d.tap("b"))),
    ("chord Ctrl+F20 -> type text", "[CHORD]", lambda d: (d.chord(["ctrl"], "f20"), d.pump(0.3))),
    ("leader F1 then Y -> type text", "[LEADER]", lambda d: _leader(d, "y")),
    ("hold P 600 ms -> type text", "[HOLD]", _hold),
    ("short P tap still types p", "p", lambda d: d.tap("p", hold=0.05, after=0.3)),
    ("double-tap F8 -> type text", "[DANCE]", _dance),
    ("chord Ctrl+F21 -> key combo Shift+X", "X", lambda d: (d.chord(["ctrl"], "f21"), d.pump(0.3))),
    ("chord Ctrl+F22 -> macro m, delay, n", "mn", lambda d: (d.chord(["ctrl"], "f22"), d.pump(0.4))),
    ("leader F1 then unbound Z passes z", "z", lambda d: _leader(d, "z")),
]


def take_focus(d, text) -> bool:
    for _ in range(20):
        d.root.deiconify(); d.root.lift(); d.root.focus_force(); text.focus_set()
        force_foreground(d.hwnd)
        d.pump(0.1)
        if d.focused():
            d.pump(0.4)  # let Keyware see python.exe in front
            return d.focused()
    return False


def caps_lock_on() -> bool:
    try:
        return bool(ctypes.windll.user32.GetKeyState(0x14) & 1)
    except Exception:
        return False


def run_steps(d, text, result) -> None:
    """Each step gets focus first. A step that loses focus is retried (max 6 tries)."""
    only = os.environ.get("KW_E2E_ONLY", "")
    repeat = int(os.environ.get("KW_E2E_REPEAT", "1"))
    for name, want, do in [step for step in STEPS if only in step[0]] * repeat:
        entry = {"step": name, "want": want, "got": "", "tries": 0, "pass": False}
        result["steps"].append(entry)
        while entry["tries"] < 6 and not entry["pass"]:
            entry["tries"] += 1
            if not take_focus(d, text):
                entry["note"] = "could not focus the test window"
                continue
            text.delete("1.0", "end")
            d.lost = False
            try:
                do(d)
                d.pump(0.1)
            except Aborted as exc:
                entry["note"] = str(exc)
            finally:
                d.release_all()
            got = text.get("1.0", "end-1c")
            if d.lost:
                entry["note"] = "focus moved to another window mid-step; retried"
            if d.focused() and not d.lost:
                entry["got"] = got
                # Caps Lock is the user's own state; the driver never toggles it.
                entry["pass"] = got == want or (caps_lock_on() and got == want.swapcase())
                if not entry["pass"]:
                    break  # focused the whole time and still wrong: a real failure
            if not entry["pass"]:
                d.pump(1.7)  # let any half-typed leader window expire before retrying


def main() -> int:
    mode = "--harness" if "--harness" in sys.argv else "--app"
    service = None
    if mode == "--harness":
        from engine.config_store import ConfigStore
        from engine.context_tracker import ContextTracker, windows_foreground_name
        from engine.hook_engine import HookEngine
        from engine.sendinput import TaggedInjector
        from engine.service import KeywareService
        from engine.keystate import windows_key_down
        from engine.startup import StartupManager
        from engine.audio_cue import AudioCue

        class Opener:
            urls: list = []
            def open_url(self, url): self.urls.append(url)
            def open_app(self, app): self.urls.append(app)

        path = sys.argv[sys.argv.index("--harness") + 1]
        from engine.sendinput import send_inputs

        focus = {"hwnd": None}

        def focus_only_send(events):
            """Harness output goes only to the test window. Key-ups always go (no stuck keys)."""
            if focus["hwnd"] and user32.GetForegroundWindow() == focus["hwnd"]:
                return send_inputs(events)
            ups = [e for e in events if e.kind == "key" and e.flags & 0x2]
            return send_inputs(ups) if ups else 0

        injector = TaggedInjector(send=focus_only_send)
        service = KeywareService(
            store=ConfigStore(path), opener=Opener(), injector=injector,
            audio=AudioCue(lambda *_: None, enabled=False), startup=StartupManager(),
            context=ContextTracker(windows_foreground_name, interval=0.2), synchronous=False,
            key_down=windows_key_down,  # same OS key-state reconcile as the real app
            window_probe=lambda: int(user32.GetForegroundWindow() or 0),
        )
        hook_trace: list = []

        def traced(key, pressed):
            out = service.hook_event(key, pressed)
            if os.environ.get("KW_E2E_TRACE"):
                hook_trace.append(f"{key}{'v' if pressed else '^'}{'S' if out else ''}@{time.perf_counter():.3f}")
            return out

        service.attach_hook(HookEngine(traced))

    prev = user32.GetForegroundWindow()
    prev_name = foreground_name()
    root = tk.Tk()
    root.title("Keyware E2E test (closes itself)")
    root.geometry("520x140+60+60")
    root.attributes("-topmost", True)
    text = tk.Text(root, font=("Consolas", 12))
    text.pack(fill="both", expand=True)
    trace: list = []
    if os.environ.get("KW_E2E_TRACE"):
        text.bind("<KeyPress>", lambda e: trace.append(f"{e.keysym}/{e.state:#x}"), add="+")
    d = Driver(root)
    root.update()
    d.hwnd = user32.GetAncestor(root.winfo_id(), 2)
    if service is not None:
        focus["hwnd"] = d.hwnd
    result = {"mode": mode, "steps": [], "aborted": "", "foreground_before": prev_name}
    try:
        if not take_focus(d, text):
            raise Aborted("could not focus the test window; nothing was injected")
        if service is not None:
            service.start()
            r = service.set_enabled(True)
            if r:
                raise Aborted(f"hook did not start: {r}")
        run_steps(d, text, result)
    except Aborted as exc:
        result["aborted"] = str(exc)
    finally:
        d.release_all()
        d.pump(0.2)
        if service is not None:
            result["logs"] = [m.text for m in service.poll()]
            if hook_trace:
                result["hook_trace"] = hook_trace
            result["opened"] = list(getattr(service.opener, "urls", []))
            service.shutdown()
        root.destroy()
        if prev:
            force_foreground(prev)
        result["foreground_after"] = foreground_name()
    if trace:
        result["tk_trace"] = trace
    ok_all = not result["aborted"] and len(result["steps"]) >= len(STEPS)
    result["caps_lock"] = caps_lock_on()
    ok_all = ok_all and all(step["pass"] for step in result["steps"])
    result["pass"] = ok_all
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
