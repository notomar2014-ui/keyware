"""Keyware window. The hook never calls into these widgets directly."""

from __future__ import annotations

import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

import customtkinter as ctk

from engine import __version__

DONATE_URL = "https://ko-fi.com/keyware_donate"
from engine.keys import format_keys, parse_key_expression
from engine.models import (
    parse_setup_text,
    setup_text,
    APP_RULE_LABELS,
    AUTOCLICK_DEFAULT_CPS,
    parse_app_rules,
    AUTOCLICK_MAX_CPS,
    AUTOCLICK_MIN_CPS,
    Action,
    Binding,
    MEDIA_LABELS,
    MacroStep,
    action_summary,
    parse_media_text,
    validate_step,
    action_type_label,
    is_web_url,
    new_id,
    trigger_text,
)
from ui.tooltip import Tooltip, dot_tooltip
from engine.service import KeywareService


THEME_PATH = Path(__file__).resolve().with_name("monochrome.json")
INK = ("#09090b", "#fafafa")
MUTED = ("#52525b", "#a1a1aa")
LINE = ("#d4d4d8", "#3f3f46")
HOVER = ("#f4f4f5", "#27272a")
ON_GREEN = ("#15803d", "#22c55e")
OFF_RED = ("#b91c1c", "#f87171")

MODES = ("Tap", "Hold", "Double-tap", "Triple-tap", "Chord", "Leader")
BINDER_MODES = ("Manual", "Text")
TRAY_HINT = "Keyware is still running in the tray. Your keys still work."
AI_KEY_STEPS = (
    "Which key?\n"
    "1. Recommended: a Google Gemini API key (free). Press Get a free key (opens aistudio.google.com/apikey), "
    "sign in with Google, click Create API key, copy it (it starts with AIza) and paste it above.\n"
    "2. Alternative: a Groq API key (free) from console.groq.com/keys. It starts with gsk_.\n"
    "Keyware detects which one you pasted. Press Save, then Test."
)
AI_PLACEHOLDER = "Describe what this key should do…\n\ne.g. F2 opens YouTube and searches minecraft\ne.g. Ctrl+Q types my email then presses Enter"
CARD = ("#fafafa", "#18181b")
ACTIONS = (
    "Open URL", "Open app", "Type text", "Key combo", "Macro", "Auto-click",
    "Open file or folder", "Media / volume", "Play music / video", "Lock screen", "Screenshot", "Run command",
    "Do nothing",
)
# Tap side of a Hold binding (v1.3.5). "Type the key" keeps the classic long press.
TAP_CHOICES = ("Type the key", "Do nothing", "Key or shortcut", "Open URL", "Open app", "Type text")
TAP_PLACEHOLDERS = {
    "Key or shortcut": "Click here, then press the key, e.g. Esc",
    "Open URL": "https://…",
    "Open app": "spotify.exe, cursor.exe…",
    "Type text": "Text to type",
}
KEY_HINTS = {
    "copilot": (
        "Copilot key: while this binding is on, Keyware takes the key over, so Copilot or Search does not open. "
        "Windows also has Settings › Bluetooth & devices › Keyboard › Customize Copilot key. Leave that on its "
        "default while Keyware handles the key. If Keyware never sees the key, that setting or your laptop "
        "maker's keyboard app is remapping it first."
    ),
    "caps_lock": "Caps Lock stops toggling while it is bound. Shift + Caps Lock still turns Caps Lock on and off.",
}
# Macro step types in the Manual editor. AI-made steps open here unchanged and can be edited.
STEP_OPS = (
    "Key", "Combo", "Text", "Delay", "Mouse", "Open URL", "Open app", "Open file/folder", "Media",
    "Wait for window", "Play on Spotify", "Play on YouTube", "Lock screen", "Screenshot",
)
STEP_PLACEHOLDERS = {
    "Key": "Press a key", "Combo": "Press the shortcut", "Text": "Text to type", "Delay": "Milliseconds, e.g. 500",
    "Mouse": "left, right or middle", "Open URL": "https://…", "Open app": "chrome.exe, code, spotify.exe…",
    "Open file/folder": r"C:\Users\you\Documents", "Media": "mute, play/pause, next, volume up x5",
    "Wait for window": "notepad.exe or part of the title", "Play on Spotify": "Song, artist or playlist",
    "Play on YouTube": "Video to play", "Lock screen": "", "Screenshot": "",
}
NO_VALUE_OPS = ("Lock screen", "Screenshot")
SERVICES = ("Spotify", "YouTube")
TAGLINE = "Make your keys your own"
CLICK_BUTTONS = ("Left", "Right", "Middle")
CLICK_MODES = ("While held", "Toggle")
SAFETY_STOPS = {"No limit": 0, "10 s": 10, "30 s": 30, "1 min": 60, "5 min": 300}
CLICK_HINTS = {
    "While held": "Clicks while the trigger key is down. Releasing it stops at once.",
    "Toggle": "One press starts, the next press stops.",
}
CLICK_STOPS_NOTE = "Always stops on Ctrl + Alt + F12, panic, Keyware off, or a profile switch."
UNDO_SECONDS = 5
MODE_HINTS = {
    "Tap": "A quick press runs the action. The physical key is not typed.",
    "Hold": "Holding past the time below runs the action. A short tap types the key, or does what you pick under Tap. Ctrl, Alt, Shift, or Win plus this key is left alone.",
    "Double-tap": "Two taps within 250 ms. One tap types the key after a short pause. A long press types it.",
    "Triple-tap": "Three taps within 250 ms. One tap types the key after a short pause. A long press types it.",
    "Chord": "Modifiers plus one key. Ctrl + Win + Shift + R opens YouTube in the sample profile.",
    "Leader": "Tap the leader key, then this key within 1.5 seconds. F13 is a safe leader key.",
}


def theme_file() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "ui" / "monochrome.json"
    return THEME_PATH


def _mode_label(binding: Binding) -> str:
    if binding.mode == "tap":
        return "Tap"
    if binding.mode == "hold":
        return "Hold"
    if binding.mode == "tap_dance" and binding.taps == 3:
        return "Triple-tap"
    if binding.mode == "tap_dance":
        return "Double-tap"
    if binding.mode == "chord":
        return "Chord"
    if binding.mode == "leader":
        return "Leader"
    return "Tap"


def _row_action_text(binding: Binding) -> str:
    main = f"{action_type_label(binding.action.type)}   ·   {action_summary(binding.action)}"
    if not binding.is_tap_hold():
        return main
    hold = f"Hold: {'keeps ' if binding.hold_press else ''}{action_summary(binding.action)}"
    tap = binding.tap_action
    tap_text = "types the key" if tap is None else "nothing" if tap.type == "none" else action_summary(tap)
    return f"{hold}   ·   Tap: {tap_text}"


def _action_label(action_type: str) -> str:
    return {
        "open_url": "Open URL",
        "open_app": "Open app",
        "type_text": "Type text",
        "key_combo": "Key combo",
        "macro": "Macro",
        "auto_click": "Auto-click",
        "open_path": "Open file or folder",
        "media": "Media / volume",
        "play": "Play music / video",
        "lock_screen": "Lock screen",
        "screenshot": "Screenshot",
        "run_command": "Run command",
        "none": "Do nothing",
    }.get(action_type, "Open URL")


def _seconds_label(seconds: int) -> str:
    for name, value in SAFETY_STOPS.items():
        if value == seconds:
            return name
    if seconds % 60 == 0:
        return f"{seconds // 60} min"
    return f"{seconds} s"


def trash_icon(size: int = 16, color: str = "#a1a1aa"):
    """A small outlined bin, drawn at 4x and scaled so it stays crisp at any DPI."""
    from PIL import Image, ImageDraw

    scale = 4
    side = size * scale
    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    stroke = max(1, int(round(1.6 * scale)))
    unit = side / 16.0

    def box(x0, y0, x1, y1):
        return [x0 * unit, y0 * unit, x1 * unit, y1 * unit]

    draw.line(box(2.5, 4.2, 13.5, 4.2), fill=color, width=stroke)            # lid
    draw.line(box(6.2, 2.2, 9.8, 2.2), fill=color, width=stroke)              # handle
    draw.line(box(6.2, 2.2, 6.2, 4.2), fill=color, width=stroke)
    draw.line(box(9.8, 2.2, 9.8, 4.2), fill=color, width=stroke)
    draw.line([(4.0 * unit, 4.6 * unit), (4.8 * unit, 14.0 * unit), (11.2 * unit, 14.0 * unit),
               (12.0 * unit, 4.6 * unit)], fill=color, width=stroke, joint="curve")  # body
    draw.line(box(6.7, 6.8, 6.9, 11.8), fill=color, width=max(1, stroke - scale // 2))
    draw.line(box(9.3, 6.8, 9.1, 11.8), fill=color, width=max(1, stroke - scale // 2))
    return image.resize((size * 2, size * 2), Image.LANCZOS)


def gear_icon(size: int = 18, color: str = "#a1a1aa"):
    """Settings gear: eight teeth around a ring, drawn at 4x then scaled down."""
    import math

    from PIL import Image, ImageDraw

    scale = 4
    full = size * scale
    image = Image.new("RGBA", (full, full), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    cx = cy = full / 2
    outer, inner, hole = full * 0.47, full * 0.34, full * 0.15
    points = []
    teeth = 8
    for index in range(teeth * 4):
        angle = (index / (teeth * 4)) * 2 * math.pi - math.pi / 2
        radius = outer if index % 4 in (1, 2) else inner
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    draw.polygon(points, fill=color)
    draw.ellipse((cx - hole, cy - hole, cx + hole, cy + hole), fill=(0, 0, 0, 0))
    return image.resize((size * 2, size * 2), Image.LANCZOS)


class KeywareApp(ctk.CTk):
    def __init__(
        self,
        service: KeywareService,
        *,
        autostart: bool = True,
        start_tray: bool = True,
        start_minimized: bool = False,
        nudge_state=None,
    ):
        ctk.set_appearance_mode("dark")  # Keyware is dark only
        ctk.set_default_color_theme(str(theme_file()))
        super().__init__()
        self.service = service
        self.selected_id = "global"
        self._editing_id: str | None = None
        self._after_id = None
        self.tray = None
        self.tray_notices: list[str] = []
        self._tray_events: queue.Queue[str] = queue.Queue()
        self._macro_rows: list[dict] = []
        self._record_target = None
        self._record_kind = ""
        self._record_prev = ""
        self._entry_border = None
        self._console_open = True
        self._status_text = ""
        self._badge_text = ""
        self._auto_banner = ""
        self._tray_paused: bool | None = None
        # Describe-it (Text mode). The model runs on a worker thread; results come back through this queue.
        self._binder_mode = "Manual"
        self._ai_results: queue.Queue = queue.Queue()
        self._ai_token = 0
        self._ai_busy = False
        self._ai_proposal = None
        self._ai_batch: list = []  # several proposals from one sentence (Proposal / Rejected)
        self._ai_placeholder_on = False
        self._ai_binder = None  # tests inject a fake DescribeBinder here
        self._settings_open = False
        self._ai_key_tester = None  # tests inject a fake check_own_key here
        self._ai_key_results: queue.Queue = queue.Queue()
        self._feedback_sender = None  # tests inject a fake send_feedback here
        self._feedback_results: queue.Queue = queue.Queue()
        self._feedback_busy = False
        # Undo for the trash button: (profile id, binding, index) of the last delete.
        self._undo: tuple[str, Binding, int] | None = None
        self._undo_after = None
        self._trash_image = None
        # Donation note: (state.json path, LaunchState) from run(); None in tests and previews.
        self._nudge_state = nudge_state

        self.title("Keyware")
        self.geometry("1120x760")
        self.minsize(980, 640)
        self._set_window_icon()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._build_header()
        self._build_banner()
        self._build_body()
        self._build_console()
        self._refresh_profiles()
        self._refresh_profile_editor()
        self._refresh_bindings()
        self._show_action_fields()
        self._on_mode(self.mode.get())
        self._build_nudge()

        if autostart:
            self.service.start()
        self._append(f"Config  {self.service.store.path}")
        self._sync_status()
        self._apply_switch()
        self._drain()
        if start_tray:
            self._open_tray()
        if start_minimized and self.tray is not None:
            self.withdraw()
        elif start_minimized:
            self._append("Tray is unavailable, so the window stayed open.")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._after_id = self.after(50, self._poll)

    def _set_window_icon(self) -> None:
        try:
            from PIL import ImageTk

            from PIL import Image

            from ui.tray import ASSETS

            if sys.platform != "win32":
                self._icon_images = [ImageTk.PhotoImage(Image.open(ASSETS / f"keyware_{size}.png")) for size in (256, 64, 32, 16)]
                self.iconphoto(True, *self._icon_images)
            else:
                ico = str(ASSETS / "keyware.ico")
                # default= applies the .ico to this and every later window
                # (dialogs too). With the AppUserModelID set in main(), the
                # taskbar button shows this icon instead of Python's.
                self.iconbitmap(ico, default=ico)
                # CustomTkinter swaps in its own icon about 200 ms after start. Set ours again.
                self.after(300, lambda: self.iconbitmap(ico, default=ico))
        except Exception:
            return

    def _font(self, size: int, weight: str = "normal") -> ctk.CTkFont:
        return ctk.CTkFont(size=size, weight=weight)

    def _ghost(self, parent, text: str, command, width: int = 120):
        return ctk.CTkButton(
            parent,
            text=text,
            width=width,
            command=command,
            fg_color="transparent",
            hover_color=HOVER,
            text_color=INK,
            border_width=1,
            border_color=LINE,
            height=32,
        )

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent", border_width=0)
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 10))
        header.grid_columnconfigure(2, weight=1)

        self._logo_image = None
        try:
            from PIL import Image

            from ui.tray import ASSETS

            # 128 px source, shown at 32 px: CTkImage rescales for HiDPI, so it stays sharp at 150-200 %.
            source = Image.open(ASSETS / "keyware_128.png")
            self._logo_image = ctk.CTkImage(light_image=source, dark_image=source, size=(32, 32))
        except Exception:
            self._logo_image = None
        self.logo = ctk.CTkLabel(header, text="", image=self._logo_image, width=32, height=32, fg_color="transparent")
        self.logo.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 12))

        words = ctk.CTkFrame(header, fg_color="transparent", border_width=0, corner_radius=0)
        words.grid(row=0, column=1, rowspan=2, sticky="w")
        title_row = ctk.CTkFrame(words, fg_color="transparent", border_width=0, corner_radius=0)
        title_row.pack(anchor="w")
        ctk.CTkLabel(title_row, text="KEYWARE", font=self._font(22, "bold"), fg_color="transparent").pack(side="left")
        # Small on/off dot. Click it to switch Keyware on or off (the tray and Ctrl + Alt + F12 still work).
        self.status_dot = ctk.CTkLabel(
            title_row, text="●", font=self._font(12), text_color=OFF_RED, fg_color="transparent", cursor="hand2"
        )
        self.status_dot.pack(side="left", padx=(10, 0), pady=(3, 0))
        self.status_dot.bind("<Button-1>", lambda _event: self._toggle_power())
        self.status_tip = Tooltip(self.status_dot, dot_tooltip(False, False))
        ctk.CTkLabel(
            words,
            text=TAGLINE,
            text_color=MUTED,
            font=self._font(13),
            fg_color="transparent",
        ).pack(anchor="w")

        # Settings live behind this gear so the main window stays clean.
        self._gear_image = None
        try:
            self._gear_image = ctk.CTkImage(light_image=gear_icon(18, "#52525b"), dark_image=gear_icon(18, "#a1a1aa"),
                                            size=(18, 18))
        except Exception:
            self._gear_image = None
        self.gear_button = ctk.CTkButton(
            header, text="" if self._gear_image else "⚙", image=self._gear_image, width=36, height=36,
            fg_color="transparent", hover_color=HOVER, text_color=INK, border_width=0, corner_radius=8,
            command=self._toggle_settings,
        )
        self.gear_button.grid(row=0, column=3, rowspan=2, sticky="e")
        # Donate: heart + word, left of the gear; opens Ko-fi in the default browser.
        self.donate_button = ctk.CTkButton(
            header, text="\u2665 Donate", width=84, height=32, font=self._font(13),
            fg_color="transparent", hover_color=HOVER, text_color="#e11d48", border_width=0, corner_radius=8,
            command=self._open_donate,
        )
        self.donate_button.grid(row=0, column=2, rowspan=2, sticky="e", padx=(0, 6))
        self.donate_tip = Tooltip(self.donate_button, "Support Keyware on Ko-fi")
        self.gear_tip = Tooltip(self.gear_button, "Settings")

        # Status text, master switch and theme button are no longer shown in the
        # header. The widgets stay (never placed) so the tray and tests share one code path.
        hidden = ctk.CTkFrame(self, fg_color="transparent", border_width=0)
        self.status = ctk.CTkLabel(hidden, text="OFF")
        self.status_detail = ctk.CTkLabel(hidden, text="")
        self.badge = ctk.CTkLabel(hidden, text="Desktop  ·  Global")
        self.theme_button = ctk.CTkButton(hidden, text="Light", command=self._toggle_theme)
        self.power = ctk.CTkButton(hidden, text="TURN ON", command=self._toggle_power)
        self._theme_caption()

    def _build_banner(self) -> None:
        self.banner = ctk.CTkLabel(self, text="", font=self._font(12), anchor="w", justify="left")
        self.banner.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 4))
        self.banner.grid_remove()

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self, fg_color="transparent", border_width=0)
        body.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 8))
        self.body = body
        body.grid_columnconfigure(0, weight=0, minsize=240)
        body.grid_columnconfigure(1, weight=1, minsize=360)
        body.grid_columnconfigure(2, weight=0, minsize=400)
        body.grid_rowconfigure(0, weight=1)

        side = ctk.CTkFrame(body)
        side.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(side, text="PROFILES", text_color=MUTED, font=self._font(11, "bold")).grid(
            row=0, column=0, sticky="w", padx=14, pady=(14, 6)
        )
        self.profile_row = ctk.CTkScrollableFrame(side, fg_color="transparent", width=210)
        self.profile_row.grid(row=1, column=0, sticky="nsew", padx=8)
        self.profile_row.grid_columnconfigure(0, weight=1)

        editor = ctk.CTkFrame(side, fg_color="transparent", border_width=0)
        editor.grid(row=2, column=0, sticky="ew", padx=12, pady=(8, 12))
        editor.grid_columnconfigure(0, weight=1)
        self.profile_name = ctk.CTkEntry(editor, placeholder_text="Profile name")
        self.profile_name.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.profile_processes = ctk.CTkEntry(editor, placeholder_text="javaw.exe")
        self.profile_processes.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.profile_enabled = ctk.CTkSwitch(editor, text="Enabled")
        self.profile_enabled.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 6))
        self.profile_setups = ctk.CTkEntry(editor, placeholder_text="Setups: 1 Elytra, 2 Spear")
        self.profile_setups.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        Tooltip(self.profile_setups, "Optional. Hold the number key to switch setups.")
        self.profile_save = self._ghost(editor, "Save", self._save_profile, width=100)
        self.profile_save.grid(row=4, column=0, sticky="ew", padx=(0, 4))
        self.profile_delete = self._ghost(editor, "Delete", self._delete_profile, width=100)
        self.profile_delete.grid(row=4, column=1, sticky="ew")

        center = ctk.CTkFrame(body)
        center.grid(row=0, column=1, sticky="nsew", padx=(0, 10))
        center.grid_columnconfigure(0, weight=1)
        center.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(center, text="BINDINGS", text_color=MUTED, font=self._font(11, "bold")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 6)
        )
        self.binding_list = ctk.CTkScrollableFrame(center, fg_color="transparent")
        self.binding_list.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 12))
        self.binding_list.grid_columnconfigure(0, weight=1)
        self._build_toast(center)

        right = ctk.CTkScrollableFrame(body, width=400)
        self.right_panel = right
        right.grid(row=0, column=2, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        self._build_binder(right)
        self._build_settings_view()

    def _build_binder(self, parent) -> None:
        ctk.CTkLabel(parent, text="QUICK BINDER", text_color=MUTED, font=self._font(11, "bold")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 8)
        )
        form = ctk.CTkFrame(parent, fg_color="transparent", border_width=0)
        form.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        form.grid_columnconfigure(0, weight=1)

        title_row = ctk.CTkFrame(form, fg_color="transparent", border_width=0)
        title_row.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        title_row.grid_columnconfigure(0, weight=1)
        self.form_title = ctk.CTkLabel(title_row, text="New binding", font=self._font(16, "bold"), anchor="w")
        self.form_title.grid(row=0, column=0, sticky="w")
        self._build_mode_toggle(title_row).grid(row=0, column=1, sticky="e")

        label_row = ctk.CTkFrame(form, fg_color="transparent", border_width=0)
        label_row.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        label_row.grid_columnconfigure(0, weight=1)
        self.label = ctk.CTkEntry(label_row, placeholder_text="Label, optional")
        self.label.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.setup_entry = ctk.CTkEntry(label_row, width=84, placeholder_text="Setup #")
        self.setup_entry.grid(row=0, column=1)
        Tooltip(self.setup_entry, "Blank = always on. 1-9 = only in that setup of this profile.")

        trigger_row = ctk.CTkFrame(form, fg_color="transparent", border_width=0)
        trigger_row.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._trigger_row = trigger_row
        trigger_row.grid_columnconfigure(0, weight=1)
        self.trigger = ctk.CTkEntry(trigger_row, placeholder_text="Click here, then press the keys")
        self.trigger.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._recorder_field(self.trigger, "trigger")
        self.capture_button = self._ghost(trigger_row, "Capture", self._capture)
        self.capture_button.grid(row=0, column=1)
        self.key_hint = ctk.CTkLabel(trigger_row, text="", text_color=MUTED, font=self._font(12), anchor="w",
                                     justify="left", wraplength=360)

        self.mode = ctk.CTkOptionMenu(form, values=list(MODES), command=self._on_mode)
        self.mode.grid(row=3, column=0, sticky="ew", pady=(0, 4))
        self.mode.set("Chord")
        self.mode_hint = ctk.CTkLabel(form, text="", text_color=MUTED, font=self._font(12), anchor="w", justify="left", wraplength=360)
        self.mode_hint.grid(row=4, column=0, sticky="ew", pady=(0, 8))

        self.hold_box = ctk.CTkFrame(form, fg_color="transparent", border_width=0)
        self.hold_box.grid_columnconfigure(0, weight=1)
        self.hold_readout = ctk.CTkLabel(self.hold_box, text="800 ms", anchor="w")
        self.hold_readout.grid(row=0, column=0, sticky="w")
        self.hold = ctk.CTkSlider(self.hold_box, from_=100, to=5000, number_of_steps=490, command=self._on_hold)
        self.hold.grid(row=1, column=0, sticky="ew", pady=(4, 6))
        self.hold.set(800)
        presets = ctk.CTkFrame(self.hold_box, fg_color="transparent", border_width=0)
        presets.grid(row=2, column=0, sticky="w")
        for milliseconds in (200, 800, 2000, 5000):
            self._ghost(presets, f"{milliseconds} ms", lambda ms=milliseconds: self._set_hold(ms), width=76).pack(side="left", padx=(0, 6))
        tap_row = ctk.CTkFrame(self.hold_box, fg_color="transparent", border_width=0)
        tap_row.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        tap_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(tap_row, text="Tap", text_color=MUTED, width=40, anchor="w").grid(row=0, column=0, sticky="w")
        self.tap_choice = ctk.CTkOptionMenu(tap_row, values=list(TAP_CHOICES), command=self._on_tap_choice)
        self.tap_choice.grid(row=0, column=1, sticky="ew")
        self.tap_choice.set("Type the key")
        self.tap_value = ctk.CTkEntry(self.hold_box, placeholder_text="")
        self._recorder_field(self.tap_value, "combo", when=lambda: self.tap_choice.get() == "Key or shortcut")
        self.keep_held = ctk.CTkCheckBox(self.hold_box, text="Keep the keys down while I hold (for Ctrl, Shift…)",
                                         command=self._on_keep_held)
        self.tap_hint = ctk.CTkLabel(self.hold_box, text="", text_color=MUTED, font=self._font(12), anchor="w",
                                     justify="left", wraplength=360)
        ctk.CTkLabel(self.hold_box, text="Hold does", text_color=MUTED, anchor="w").grid(
            row=7, column=0, sticky="w", pady=(10, 0))

        self.action = ctk.CTkOptionMenu(form, values=list(ACTIONS), command=self._on_action)
        self.action.grid(row=6, column=0, sticky="ew", pady=(0, 8))
        self.action.set("Open URL")

        self.url = ctk.CTkEntry(form, placeholder_text="https://www.youtube.com")
        self.app_box = ctk.CTkFrame(form, fg_color="transparent", border_width=0)
        self.app_box.grid_columnconfigure(0, weight=1)
        self.app_path = ctk.CTkEntry(self.app_box, placeholder_text="notepad.exe or a full path")
        self.app_path.grid(row=0, column=0, sticky="ew")
        self.app_args = ctk.CTkEntry(self.app_box, placeholder_text="Open with: links or files, ; between (optional)")
        self.app_args.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.app_fallback = ctk.CTkEntry(self.app_box, placeholder_text="If not installed, open this website (optional)")
        self.app_fallback.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.path_entry = ctk.CTkEntry(form, placeholder_text=r"C:\Users\you\Documents or a file")
        self.media_box = ctk.CTkFrame(form, fg_color="transparent", border_width=0)
        self.media_box.grid_columnconfigure(0, weight=1)
        self.media_menu = ctk.CTkOptionMenu(self.media_box, values=list(MEDIA_LABELS.values()))
        self.media_menu.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.media_menu.set(MEDIA_LABELS["media_play_pause"])
        ctk.CTkLabel(self.media_box, text="×", text_color=MUTED).grid(row=0, column=1, padx=(0, 4))
        self.media_count = ctk.CTkEntry(self.media_box, width=56)
        self.media_count.insert(0, "1")
        self.media_count.grid(row=0, column=2)
        ctk.CTkLabel(self.media_box, text="Each volume step is about 2%. Acts on the whole system; the audio device is never changed.",
                     text_color=MUTED, font=self._font(12), wraplength=360, justify="left").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.play_box = ctk.CTkFrame(form, fg_color="transparent", border_width=0)
        self.play_box.grid_columnconfigure(1, weight=1)
        self.play_service = ctk.CTkSegmentedButton(self.play_box, values=list(SERVICES))
        self.play_service.set("Spotify")
        self.play_service.grid(row=0, column=0, padx=(0, 6))
        self.play_query = ctk.CTkEntry(self.play_box, placeholder_text="Song, artist, playlist or video")
        self.play_query.grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(self.play_box, text="Uses the Spotify app if installed, otherwise the web player in your browser.",
                     text_color=MUTED, font=self._font(12), wraplength=360, justify="left").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.command_box = ctk.CTkFrame(form, fg_color="transparent", border_width=0)
        self.command_box.grid_columnconfigure(0, weight=1)
        self.command_entry = ctk.CTkEntry(self.command_box, placeholder_text=r"e.g. code C:\projects\site")
        self.command_entry.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(self.command_box, text="Keyware asks you to confirm the first time it runs. It never runs commands "
                     "that shut down, sign out, delete files or change the audio device.",
                     text_color=MUTED, font=self._font(12), wraplength=360, justify="left").grid(
            row=1, column=0, sticky="w", pady=(4, 0))
        self.simple_note = ctk.CTkLabel(form, text="", text_color=MUTED, font=self._font(12), anchor="w",
                                        justify="left", wraplength=360)
        self.text = ctk.CTkTextbox(form, height=80)
        self.combo = ctk.CTkEntry(form, placeholder_text="Click here, then press the keys to send")
        self._recorder_field(self.combo, "combo")
        self.macro_box = ctk.CTkFrame(form, fg_color="transparent", border_width=0)
        self.macro_box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self.macro_box, text="Steps run in order. Delays are exact. Keys get a 5–15 ms human jitter.", text_color=MUTED, font=self._font(12), wraplength=360, justify="left").grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )
        self.macro_steps = ctk.CTkFrame(self.macro_box, fg_color="transparent", border_width=0)
        self.macro_steps.grid(row=1, column=0, sticky="ew")
        self.macro_steps.grid_columnconfigure(0, weight=1)
        self._ghost(self.macro_box, "Add step", self._add_macro_step).grid(row=2, column=0, sticky="w", pady=(6, 0))
        self._build_autoclick(form)
        self._build_ai_panel(form)
        self._build_app_rules(form)

        self.form_error = ctk.CTkLabel(form, text="", text_color=INK, anchor="w", justify="left", wraplength=420)
        self.form_error.grid(row=12, column=0, sticky="ew", pady=(0, 8))
        buttons = ctk.CTkFrame(form, fg_color="transparent", border_width=0)
        buttons.grid(row=13, column=0, sticky="ew", pady=(0, 12))
        ctk.CTkButton(buttons, text="Save binding", command=self._save_binding, height=36).pack(side="left")
        self._ghost(buttons, "Clear", self._clear_form).pack(side="left", padx=(8, 0))
        # Shown only when the trigger clashes with another binding.
        self.replace_button = ctk.CTkButton(
            buttons, text="Replace it", height=36, fg_color=OFF_RED, hover_color=("#991b1b", "#ef4444"),
            text_color="#ffffff", command=lambda: self._save_binding(replace=True),
        )

    # --- Per-app rules ------------------------------------------------------
    def _build_app_rules(self, form) -> None:
        box = ctk.CTkFrame(form, fg_color=CARD, border_width=1, border_color=LINE, corner_radius=10)
        box.grid(row=11, column=0, sticky="ew", pady=(0, 8))
        box.grid_columnconfigure(0, weight=1)
        self.app_rules_box = box
        ctk.CTkLabel(box, text="PER-APP RULES", text_color=MUTED, font=self._font(11), anchor="w").grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 0))
        ctk.CTkLabel(box, text="Default for all other apps: Keyware only", text_color=MUTED, font=self._font(12),
                     anchor="w").grid(row=1, column=0, sticky="w", padx=12, pady=(0, 6))
        self.app_rule_list = ctk.CTkFrame(box, fg_color="transparent", border_width=0)
        self.app_rule_list.grid(row=2, column=0, sticky="ew", padx=12)
        self.app_rule_list.grid_columnconfigure(0, weight=1)
        self._ghost(box, "+ Add app", lambda: self._add_app_rule("", "keyware")).grid(
            row=3, column=0, sticky="w", padx=12, pady=(6, 0))
        ctk.CTkLabel(box, text="Match by exe name. App only: the app's own shortcut works. Both: run the action and pass the key.",
                     text_color=MUTED, font=self._font(11), anchor="w", justify="left", wraplength=360).grid(
            row=4, column=0, sticky="w", padx=12, pady=(4, 10))
        self._app_rule_rows: list = []

    def _add_app_rule(self, app: str, mode: str) -> None:
        row = ctk.CTkFrame(self.app_rule_list, fg_color="transparent", border_width=0)
        row.grid(row=len(self._app_rule_rows), column=0, sticky="ew", pady=(0, 6))
        row.grid_columnconfigure(0, weight=1)
        entry = ctk.CTkEntry(row, placeholder_text="Cursor.exe")
        entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        if app:
            entry.insert(0, app)
        menu = ctk.CTkOptionMenu(row, values=list(APP_RULE_LABELS.values()), width=130)
        menu.set(APP_RULE_LABELS.get(mode, APP_RULE_LABELS["keyware"]))
        menu.grid(row=0, column=1, padx=(0, 6))
        item = (row, entry, menu)
        ctk.CTkButton(row, text="x", width=28, height=28, fg_color="transparent", border_width=1, border_color=LINE,
                      text_color=MUTED, hover_color=LINE, command=lambda: self._remove_app_rule(item)).grid(row=0, column=2)
        self._app_rule_rows.append(item)

    def _remove_app_rule(self, item) -> None:
        if item in self._app_rule_rows:
            self._app_rule_rows.remove(item)
            item[0].destroy()
            for index, (row, _entry, _menu) in enumerate(self._app_rule_rows):
                row.grid(row=index, column=0, sticky="ew", pady=(0, 6))

    def _set_app_rules(self, rules: dict) -> None:
        for item in list(self._app_rule_rows):
            item[0].destroy()
        self._app_rule_rows = []
        for app, mode in (rules or {}).items():
            self._add_app_rule(app, mode)

    def _collect_setup(self) -> int:
        raw = self.setup_entry.get().strip()
        if not raw:
            return 0
        if not re.fullmatch(r"[1-9]", raw):
            raise ValueError("Setup is a number 1-9, or blank for always on.")
        return int(raw)

    def _collect_app_rules(self) -> dict:
        by_label = {label: mode for mode, label in APP_RULE_LABELS.items()}
        rules: dict = {}
        for _row, entry, menu in self._app_rule_rows:
            app = entry.get().strip()
            if app:
                rules[app] = by_label.get(menu.get(), "keyware")
        return parse_app_rules(rules)

    # --- Auto-click fields -------------------------------------------------

    def _segmented(self, parent, values, command=None):
        return ctk.CTkSegmentedButton(
            parent, values=list(values), height=28, corner_radius=7, border_width=2,
            font=self._font(12), command=command,
            fg_color=("#e4e4e7", "#18181b"),
            selected_color=INK, selected_hover_color=INK,
            unselected_color=("#e4e4e7", "#18181b"), unselected_hover_color=HOVER,
            text_color=(("#09090b", "#fafafa")),
        )

    def _build_autoclick(self, form) -> None:
        box = ctk.CTkFrame(form, fg_color="transparent", border_width=0)
        box.grid_columnconfigure(1, weight=1)
        self.autoclick_box = box

        def caption(row: int, text: str) -> None:
            ctk.CTkLabel(box, text=text, text_color=MUTED, font=self._font(12), anchor="w", width=78).grid(
                row=row, column=0, sticky="w", pady=(0, 8)
            )

        caption(0, "Button")
        self.ac_button = self._segmented(box, CLICK_BUTTONS)
        self.ac_button.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        self.ac_button.set("Left")

        caption(1, "Speed")
        speed = ctk.CTkFrame(box, fg_color="transparent", border_width=0)
        speed.grid(row=1, column=1, sticky="ew", pady=(0, 8))
        speed.grid_columnconfigure(0, weight=1)
        self.ac_cps = ctk.CTkSlider(
            speed, from_=AUTOCLICK_MIN_CPS, to=AUTOCLICK_MAX_CPS,
            number_of_steps=AUTOCLICK_MAX_CPS - AUTOCLICK_MIN_CPS, command=self._on_cps,
        )
        self.ac_cps.grid(row=0, column=0, sticky="ew")
        self.ac_cps_readout = ctk.CTkLabel(speed, text="", font=self._font(12, "bold"), anchor="e", width=86)
        self.ac_cps_readout.grid(row=0, column=1, sticky="e", padx=(8, 0))
        self._set_cps(AUTOCLICK_DEFAULT_CPS)

        caption(2, "Mode")
        self.ac_mode = self._segmented(box, CLICK_MODES, command=self._on_click_mode)
        self.ac_mode.grid(row=2, column=1, sticky="ew", pady=(0, 8))
        self.ac_mode.set("While held")

        caption(3, "Safety stop")
        self.ac_limit = ctk.CTkOptionMenu(box, values=list(SAFETY_STOPS), height=28)
        self.ac_limit.grid(row=3, column=1, sticky="ew", pady=(0, 8))
        self.ac_limit.set("No limit")

        self.ac_hint = ctk.CTkLabel(box, text="", text_color=MUTED, font=self._font(12), anchor="w",
                                    justify="left", wraplength=360)
        self.ac_hint.grid(row=4, column=0, columnspan=2, sticky="ew")
        self._on_click_mode("While held")

    def _on_cps(self, value: float) -> None:
        self.ac_cps_readout.configure(text=f"{int(round(float(value)))} clicks/s")

    def _set_cps(self, cps: int) -> None:
        clamped = min(AUTOCLICK_MAX_CPS, max(AUTOCLICK_MIN_CPS, int(cps)))
        self.ac_cps.set(clamped)
        self._on_cps(clamped)

    def _on_click_mode(self, value: str) -> None:
        self.ac_hint.configure(text=f"{CLICK_HINTS.get(value, '')} {CLICK_STOPS_NOTE}")

    def _autoclick_action(self) -> Action:
        return Action(
            type="auto_click",
            button=self.ac_button.get().lower() or "left",
            cps=int(round(float(self.ac_cps.get()))),
            click_mode="toggle" if self.ac_mode.get() == "Toggle" else "hold",
            max_seconds=SAFETY_STOPS.get(self.ac_limit.get(), self._custom_stop()),
        )

    def _custom_stop(self) -> int:
        text = self.ac_limit.get()
        try:
            number = int(text.split()[0])
        except (ValueError, IndexError):
            return 0
        return number * 60 if text.endswith("min") else number

    def _fill_autoclick(self, action: Action) -> None:
        self.ac_button.set(action.button.capitalize() if action.button in ("left", "right", "middle") else "Left")
        self._set_cps(action.cps or AUTOCLICK_DEFAULT_CPS)
        mode = "Toggle" if action.click_mode == "toggle" else "While held"
        self.ac_mode.set(mode)
        self._on_click_mode(mode)
        label = _seconds_label(int(action.max_seconds or 0))
        values = list(SAFETY_STOPS)
        if label not in values:
            values.append(label)
        self.ac_limit.configure(values=values)
        self.ac_limit.set(label)

    def _reset_autoclick(self) -> None:
        self._fill_autoclick(Action(type="auto_click"))
        self.ac_limit.configure(values=list(SAFETY_STOPS))

    # --- Trash + undo -----------------------------------------------------

    def _trash(self):
        if self._trash_image is None:
            try:
                dark = trash_icon(16, "#a1a1aa")
                light = trash_icon(16, "#52525b")
                self._trash_image = ctk.CTkImage(light_image=light, dark_image=dark, size=(16, 16))
            except Exception:
                self._trash_image = False
        return self._trash_image or None

    def _build_toast(self, parent) -> None:
        toast = ctk.CTkFrame(parent, fg_color=("#18181b", "#fafafa"), corner_radius=10, border_width=0)
        self.toast = toast
        self.toast_text = ctk.CTkLabel(toast, text="Deleted.", text_color=("#fafafa", "#09090b"),
                                       font=self._font(13), anchor="w")
        self.toast_text.pack(side="left", padx=(14, 6), pady=8)
        self.toast_undo = ctk.CTkButton(
            toast, text="Undo", width=56, height=26, corner_radius=6, border_width=0,
            fg_color="transparent", hover_color=("#27272a", "#e4e4e7"),
            text_color=("#fafafa", "#09090b"), font=self._font(13, "bold"), command=self._undo_delete,
        )
        self.toast_undo.pack(side="left", padx=(0, 8), pady=6)

    def _show_toast(self, text: str) -> None:
        self.toast_text.configure(text=text)
        self.toast.place(relx=0.5, rely=1.0, y=-18, anchor="s")
        self.toast.lift()
        if self._undo_after is not None:
            try:
                self.after_cancel(self._undo_after)
            except Exception:
                pass
        self._undo_after = self.after(UNDO_SECONDS * 1000, self._expire_undo)

    def _flash(self, text: str, ms: int = 1800) -> None:
        """Small always-on-top note near the bottom of the screen. Takes no focus, never blocks."""
        try:
            import tkinter as tk

            old = getattr(self, "_flash_win", None)
            if old is not None and old.winfo_exists():
                old.destroy()
            win = tk.Toplevel(self)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            try:
                win.attributes("-alpha", 0.92)
            except Exception:
                pass
            tk.Label(win, text=text, bg="#18181b", fg="#fafafa", font=("Segoe UI", 12, "bold"),
                     padx=16, pady=8).pack()
            win.update_idletasks()
            x = (win.winfo_screenwidth() - win.winfo_reqwidth()) // 2
            y = win.winfo_screenheight() - win.winfo_reqheight() - 120
            win.geometry(f"+{x}+{y}")
            self._flash_win = win
            self.after(ms, lambda: win.winfo_exists() and win.destroy())
        except Exception:
            pass

    def _hide_toast(self) -> None:
        self.toast.place_forget()

    def _expire_undo(self) -> None:
        self._undo_after = None
        self._undo = None
        self._hide_toast()

    def _undo_delete(self) -> None:
        pending, self._undo = self._undo, None
        if self._undo_after is not None:
            try:
                self.after_cancel(self._undo_after)
            except Exception:
                pass
            self._undo_after = None
        self._hide_toast()
        if pending is None:
            return
        profile_id, binding, index = pending
        try:
            notes = self.service.restore_binding(profile_id, binding, index)
        except ValueError as exc:  # the profile itself was deleted meanwhile
            self._show_banner(str(exc))
            return
        if notes:
            self._show_banner(" ".join(notes))
        if profile_id != self.selected_id:
            self._select_profile(profile_id)
        else:
            self._refresh_bindings()
        self._sync_status()

    # --- Support Keyware --------------------------------------------------

    def _donate_url(self) -> str:
        url = (self.service.config.donate_url or "").strip()
        return url if is_web_url(url) else ""

    def _open_donate(self) -> None:
        url = self._donate_url()
        if not url:
            return
        try:
            self.service.opener.open_url(url)
        except Exception as exc:
            self._append(f"Could not open the link: {exc}")

    def _sync_heart(self) -> None:
        if self._donate_url():
            self.heart.grid()
        else:
            self.heart.grid_remove()

    def _build_nudge(self) -> None:
        """The donation popup: a centered card over the window. Dismissible, never blocking."""
        from engine.nudge import NUDGE_MESSAGE, NUDGE_TITLE

        card = ctk.CTkFrame(self, fg_color=("#ffffff", "#141416"), border_width=1, border_color=("#d4d4d8", "#3f3f46"),
                            corner_radius=14, width=460)
        card.grid_columnconfigure(0, weight=1)
        self.nudge = card
        ctk.CTkLabel(card, text="♥", text_color=("#be185d", "#f472b6"), font=self._font(26)).grid(
            row=0, column=0, pady=(22, 0)
        )
        ctk.CTkLabel(card, text=NUDGE_TITLE, font=self._font(17, "bold")).grid(row=1, column=0, pady=(4, 0), padx=28)
        self.nudge_text = ctk.CTkLabel(card, text=NUDGE_MESSAGE, text_color=MUTED, font=self._font(13),
                                       justify="center", wraplength=400)
        self.nudge_text.grid(row=2, column=0, padx=28, pady=(10, 18))
        buttons = ctk.CTkFrame(card, fg_color="transparent", border_width=0)
        buttons.grid(row=3, column=0)
        ctk.CTkButton(buttons, text="♥  Support Keyware", height=36, width=170, command=self._nudge_support).pack(
            side="left", padx=(0, 10)
        )
        self._ghost(buttons, "Maybe later", self._nudge_later, width=120).pack(side="left")
        ctk.CTkButton(
            card, text="Don't ask again", height=22, width=10, fg_color="transparent",
            hover_color=("#ffffff", "#141416"), text_color=MUTED, font=self._font(11), border_width=0,
            command=self._nudge_never,
        ).grid(row=4, column=0, pady=(10, 16))
        if self._nudge_state is not None and self._donate_url():
            from engine.nudge import should_nudge

            path, state = self._nudge_state
            if should_nudge(state, self._donate_url()):
                self.show_nudge()

    def show_nudge(self) -> None:
        if not self._donate_url():
            return  # never shown without a real link
        self.nudge.place(relx=0.5, rely=0.45, anchor="center")
        self.nudge.lift()
        if self._nudge_state is not None:
            from engine.nudge import mark_nudged

            mark_nudged(*self._nudge_state)

    def _hide_nudge(self) -> None:
        self.nudge.place_forget()

    def _nudge_support(self) -> None:
        self._hide_nudge()
        self._open_donate()

    def _nudge_later(self) -> None:
        self._hide_nudge()

    def _nudge_never(self) -> None:
        self._hide_nudge()
        if self._nudge_state is not None:
            from engine.nudge import turn_off

            turn_off(*self._nudge_state)

    # --- Manual / Text toggle and the Describe-it panel -------------------

    def _build_mode_toggle(self, parent) -> ctk.CTkFrame:
        """Small two-way segmented toggle. Selected = light pill, other = muted text."""
        box = ctk.CTkFrame(parent, fg_color=("#e4e4e7", "#18181b"), border_width=1, border_color=LINE, corner_radius=8)
        self.mode_buttons: dict[str, ctk.CTkButton] = {}
        for index, name in enumerate(BINDER_MODES):
            button = ctk.CTkButton(
                box, text=name, width=64, height=24, corner_radius=6, border_width=0,
                font=self._font(12), command=lambda value=name: self._set_binder_mode(value),
            )
            button.grid(row=0, column=index, padx=(3 if index == 0 else 0, 3), pady=3)
            self.mode_buttons[name] = button
        self._paint_mode_toggle()
        return box

    def _paint_mode_toggle(self) -> None:
        for name, button in self.mode_buttons.items():
            if name == self._binder_mode:
                button.configure(fg_color=INK, hover_color=INK, text_color=("#fafafa", "#09090b"))
            else:
                button.configure(fg_color="transparent", hover_color=HOVER, text_color=MUTED)

    def _build_ai_panel(self, form) -> None:
        panel = ctk.CTkFrame(form, fg_color="transparent", border_width=0)
        panel.grid_columnconfigure(0, weight=1)
        self.ai_panel = panel
        self.ai_box = ctk.CTkTextbox(panel, height=120, wrap="word", font=self._font(13), border_width=1, border_color=LINE)
        self.ai_box.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.ai_box.bind("<FocusIn>", lambda _e: self._ai_placeholder(False))
        self.ai_box.bind("<FocusOut>", lambda _e: self._ai_placeholder(True))
        self.ai_box.bind("<Control-Return>", lambda _e: (self._ai_generate(), "break")[1])
        self._ai_placeholder(True)

        row = ctk.CTkFrame(panel, fg_color="transparent", border_width=0)
        row.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        row.grid_columnconfigure(1, weight=1)
        self.ai_button = ctk.CTkButton(row, text="Generate", width=104, height=30, command=self._ai_generate)
        self.ai_button.grid(row=0, column=0, sticky="w")
        self.ai_status = ctk.CTkLabel(row, text="Fills the fields. Nothing is saved until you press Save.",
                                      text_color=MUTED, font=self._font(12), anchor="w", justify="left", wraplength=250)
        self.ai_status.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        # Shown when the free daily limit is used up or the AI is busy.
        self.ai_add_key = ctk.CTkButton(row, text="Add my own free key", height=30, width=160,
                                        command=lambda: self._open_settings(focus="ai"))
        self.ai_add_key.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(6, 0))
        self.ai_add_key.grid_remove()

        self.ai_card = ctk.CTkFrame(panel, fg_color=CARD, border_width=1, border_color=LINE, corner_radius=10)
        self.ai_card.grid_columnconfigure(1, weight=1)
        self.ai_card_rows = ctk.CTkFrame(self.ai_card, fg_color="transparent", border_width=0)
        self.ai_card_rows.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14)
        self.ai_card_rows.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(self.ai_card, text="PROPOSED BINDING", text_color=MUTED, font=self._font(11, "bold")).grid(
            row=0, column=0, sticky="w", padx=14, pady=(10, 4)
        )
        self.ai_notes = ctk.CTkLabel(self.ai_card, text="", text_color=MUTED, font=self._font(12), anchor="w",
                                     justify="left", wraplength=330)
        self.ai_notes.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(4, 0))
        self.ai_edit = self._ghost(self.ai_card, "Edit in Manual", lambda: self._set_binder_mode("Manual"), width=120)
        self.ai_edit.grid(row=3, column=0, sticky="w", padx=14, pady=(8, 12))

        # Several bindings from one sentence: review, remove any, then add all.
        self.batch_card = ctk.CTkFrame(panel, fg_color=CARD, border_width=1, border_color=LINE, corner_radius=10)
        self.batch_card.grid_columnconfigure(0, weight=1)
        self.batch_title = ctk.CTkLabel(self.batch_card, text="PROPOSED BINDINGS", text_color=MUTED,
                                        font=self._font(11, "bold"))
        self.batch_title.grid(row=0, column=0, sticky="w", padx=14, pady=(10, 4))
        self.batch_rows = ctk.CTkFrame(self.batch_card, fg_color="transparent", border_width=0)
        self.batch_rows.grid(row=1, column=0, sticky="ew", padx=10)
        self.batch_rows.grid_columnconfigure(1, weight=1)
        self.batch_hint = ctk.CTkLabel(self.batch_card, text="", text_color=MUTED, font=self._font(12), anchor="w",
                                       justify="left", wraplength=330)
        self.batch_hint.grid(row=2, column=0, sticky="ew", padx=14, pady=(6, 0))
        batch_buttons = ctk.CTkFrame(self.batch_card, fg_color="transparent", border_width=0)
        batch_buttons.grid(row=3, column=0, sticky="w", padx=14, pady=(8, 12))
        self.batch_add = ctk.CTkButton(batch_buttons, text="Add all", height=32, width=110, command=self._save_batch)
        self.batch_add.pack(side="left")
        self._ghost(batch_buttons, "Discard", lambda: self._reset_ai(keep_text=True), width=90).pack(side="left", padx=(8, 0))

    def _ai_placeholder(self, show: bool) -> None:
        current = self.ai_box.get("1.0", "end").strip()
        if show and not current:
            self.ai_box.insert("1.0", AI_PLACEHOLDER)
            self.ai_box.configure(text_color=MUTED)
            self._ai_placeholder_on = True
        elif not show and self._ai_placeholder_on:
            self.ai_box.delete("1.0", "end")
            self.ai_box.configure(text_color=INK)
            self._ai_placeholder_on = False

    def _ai_text(self) -> str:
        if self._ai_placeholder_on:
            return ""
        return self.ai_box.get("1.0", "end").strip()

    def _set_ai_text(self, text: str) -> None:
        self._ai_placeholder(False)
        self.ai_box.delete("1.0", "end")
        self.ai_box.configure(text_color=INK)
        self._ai_placeholder_on = False
        if text:
            self.ai_box.insert("1.0", text)
        else:
            self._ai_placeholder(True)

    def _manual_widgets(self) -> list:
        return [self.label, self._trigger_row, self.mode, self.mode_hint, self.action]

    def _set_binder_mode(self, value: str) -> None:
        if value not in BINDER_MODES:
            return
        self._binder_mode = value
        self._paint_mode_toggle()
        if value == "Text":
            self._stop_recording(cancel=True)
            for widget in self._manual_widgets() + [self.hold_box, *self._action_widgets()]:
                widget.grid_remove()
            self.ai_panel.grid(row=1, column=0, rowspan=11, sticky="ew", pady=(0, 4))
            return
        self.ai_panel.grid_remove()
        for widget in self._manual_widgets():
            widget.grid()
        self._on_mode(self.mode.get())
        self._show_action_fields()

    def _make_binder(self):
        if self._ai_binder is not None:
            return self._ai_binder
        from assist.ai_binder import binder_for_config

        self.service.ensure_install_id()
        return binder_for_config(self.service.config)

    def _ai_generate(self) -> None:
        if self._ai_busy:
            return
        text = self._ai_text()
        if not text:
            self._ai_set_status("Describe what the key should do first.", error=True)
            return
        from assist.ai_binder import NOT_BINDING_MESSAGE, looks_like_binding

        if not looks_like_binding(text):
            self._show_not_binding(NOT_BINDING_MESSAGE)
            return
        from engine.models import Config

        profile = self.service.config.profile_by_id(self.selected_id)
        snapshot = Config.from_dict(self.service.config.to_dict())  # the worker never touches live config
        profile_name = profile.name if profile is not None else "Global"
        self._ai_token += 1
        token = self._ai_token
        self._ai_busy = True
        self.ai_button.configure(text="Thinking…", state="disabled")
        self._ai_set_status("Asking the model…")
        from assist.ai_binder import AIBinderError

        try:
            binder = self._make_binder()
        except AIBinderError as exc:
            self._ai_busy = False
            self.ai_button.configure(text="Generate", state="normal")
            self._ai_set_status(str(exc), error=True, offer_key=True)
            return
        results = self._ai_results

        def work() -> None:
            from assist.ai_binder import AIBinderError

            try:
                if hasattr(binder, "describe_batch"):
                    results.put((token, "ok", binder.describe_batch(text, snapshot, profile_name)))
                else:
                    results.put((token, "ok", binder.describe(text, snapshot, profile_name)))
            except AIBinderError as exc:
                if getattr(exc, "not_binding", False):
                    results.put((token, "not_binding", str(exc)))
                elif getattr(exc, "code", "") in ("rate_limited", "busy"):
                    results.put((token, "limit", str(exc)))
                else:
                    results.put((token, "error", str(exc)))
            except Exception:
                results.put((token, "error", "Something went wrong. Try again."))

        threading.Thread(target=work, name="keyware-describe", daemon=True).start()

    def _ai_set_status(self, text: str, *, error: bool = False, offer_key: bool = False) -> None:
        self.ai_status.configure(text=text, text_color=OFF_RED if error else MUTED)
        if offer_key:
            self.ai_add_key.grid()
        else:
            self.ai_add_key.grid_remove()

    def _drain_ai(self) -> None:
        while True:
            try:
                token, kind, payload = self._ai_results.get_nowait()
            except queue.Empty:
                return
            if token != self._ai_token:
                continue  # cleared or superseded
            self._ai_busy = False
            self.ai_button.configure(text="Generate", state="normal")
            if kind == "ok":
                self._apply_result(payload)
            elif kind == "not_binding":
                self._show_not_binding(str(payload))
            elif kind == "limit":
                self._ai_set_status(str(payload), error=True, offer_key=True)
            else:
                self._ai_set_status(str(payload), error=True)

    def _show_not_binding(self, message: str) -> None:
        """Off-topic text: a clear inline error, and nothing is filled in or saved."""
        self._ai_proposal = None
        self._ai_batch = []
        self.ai_card.grid_remove()
        self.batch_card.grid_remove()
        self._ai_set_status(message, error=True)

    def _apply_result(self, payload) -> None:
        from assist.ai_binder import Proposal

        items = payload if isinstance(payload, list) else [payload]
        if len(items) == 1 and isinstance(items[0], Proposal):
            self._ai_batch = []
            self.batch_card.grid_remove()
            self._apply_proposal(items[0])
            return
        self._apply_batch(items)

    def _apply_batch(self, items: list) -> None:
        self._ai_proposal = None
        self._ai_batch = list(items)
        self.ai_card.grid_remove()
        self._render_batch()
        model = next((getattr(item, "model", "") for item in items if getattr(item, "model", "")), "")
        seconds = next((getattr(item, "seconds", 0.0) for item in items if getattr(item, "model", "")), 0.0)
        source = f"by {model} · {seconds:.1f} s. " if model else ""
        self._ai_set_status(f"{len(items)} bindings proposed {source}Review them, then Add all.")

    def _render_batch(self) -> None:
        from assist.ai_binder import Proposal, batch_flags, batch_targets

        for child in self.batch_rows.winfo_children():
            child.destroy()
        items = self._ai_batch
        if not items:
            self._reset_ai(keep_text=True)
            return
        config = self.service.config
        flags = batch_flags(items, config, self.selected_id)
        targets = batch_targets(items, config, self.selected_id)
        self.batch_title.configure(text=f"PROPOSED BINDINGS ({len(items)})")
        for index, item in enumerate(items):
            row = index * 2
            ctk.CTkLabel(self.batch_rows, text=f"{index + 1}", text_color=MUTED, font=self._font(12), width=18,
                         anchor="nw").grid(row=row, column=0, sticky="nw", padx=(4, 6), pady=(6, 0))
            if isinstance(item, Proposal):
                binding = item.binding
                profile = config.profile_by_id(targets[index])
                where = profile.name if profile is not None else "Global"
                head = trigger_text(binding, config.leader_key)
                detail = f"{action_type_label(binding.action.type)} · {action_summary(binding.action)} · {where}"
            else:
                head = item.trigger or item.label
                detail = "Not added"
            words = ctk.CTkFrame(self.batch_rows, fg_color="transparent", border_width=0)
            words.grid(row=row, column=1, sticky="ew", pady=(4, 0))
            ctk.CTkLabel(words, text=head, font=self._font(13, "bold"), anchor="w").pack(anchor="w")
            ctk.CTkLabel(words, text=detail, text_color=MUTED, font=self._font(12), anchor="w", justify="left",
                         wraplength=250).pack(anchor="w")
            if flags[index]:
                ctk.CTkLabel(words, text=flags[index], text_color=OFF_RED, font=self._font(12), anchor="w",
                             justify="left", wraplength=250).pack(anchor="w", pady=(2, 0))
            remove = ctk.CTkButton(
                self.batch_rows, text="", image=self._trash(), width=28, height=28, corner_radius=6,
                fg_color="transparent", hover_color=HOVER, border_width=0,
                command=lambda i=index: self._remove_batch_item(i),
            )
            if self._trash() is None:
                remove.configure(text="✕", text_color=MUTED)
            remove.grid(row=row, column=2, sticky="ne", pady=(4, 0))
            Tooltip(remove, "Remove from this list")
        flagged = sum(1 for flag in flags if flag)
        ready = len(items) - flagged
        if flagged:
            self.batch_add.configure(state="disabled", text="Add all")
            noun = "item" if flagged == 1 else "items"
            self.batch_hint.configure(text=f"Remove the flagged {noun} to add the rest. Nothing is overwritten.",
                                      text_color=OFF_RED)
        else:
            self.batch_add.configure(state="normal", text=f"Add all ({ready})")
            self.batch_hint.configure(text="Nothing is saved until you press Add all.", text_color=MUTED)
        self.batch_card.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._batch_flags = flags

    def _remove_batch_item(self, index: int) -> None:
        if 0 <= index < len(self._ai_batch):
            self._ai_batch.pop(index)
        self._render_batch()

    def _save_batch(self) -> None:
        """Add every proposed binding. Refused while anything is flagged; never replaces."""
        from assist.ai_binder import Proposal, batch_flags, batch_targets
        from engine.conflicts import ConflictError

        items = self._ai_batch
        if not items:
            return
        config = self.service.config
        if any(batch_flags(items, config, self.selected_id)):
            self._render_batch()
            return
        targets = batch_targets(items, config, self.selected_id)
        added = 0
        for item, target in zip(list(items), targets):
            if not isinstance(item, Proposal):
                continue
            try:
                self.service.add_binding(target, item.binding)
            except (ConflictError, ValueError) as exc:
                self._ai_batch = items[added:]
                self._render_batch()
                self._ai_set_status(f"Stopped after {added}: {exc}", error=True)
                self._refresh_bindings()
                return
            added += 1
        self._reset_ai()
        self._refresh_bindings()
        self._apply_switch()
        self._sync_status()
        noun = "binding" if added == 1 else "bindings"
        self.form_error.configure(text=f"Added {added} {noun}.", text_color=MUTED)

    def _match_profile(self, proposal):
        from assist.ai_binder import resolve_profile

        return resolve_profile(self.service.config, proposal)

    def _apply_proposal(self, proposal) -> None:
        """Fill the normal editor fields. Never saves: the user reviews and presses Save."""
        from engine.conflicts import ConflictError, find_conflicts

        notes = list(proposal.notes)
        target = self._match_profile(proposal)
        if target is not None and target.id != self.selected_id:
            self.selected_id = target.id
            self._refresh_profiles()
            self._refresh_profile_editor()
            self._refresh_bindings()
            notes.insert(0, f"Switched to the {target.name} profile.")
        elif proposal.profile_name and target is None:
            here = getattr(self.service.config.profile_by_id(self.selected_id), "name", "Global")
            notes.insert(0, f"No {proposal.profile_name} profile yet, so this goes in {here}. Add the profile first for app-only.")

        from assist.ai_binder import merge_rules_proposal

        binding = proposal.binding
        merged = merge_rules_proposal(binding, self.service.bindings(self.selected_id))
        self._stop_recording(cancel=True)
        self._hide_conflict()
        self._editing_id = None
        if merged is not None:
            binding = merged
            proposal.binding = merged
            proposal.merged_into = merged.id
            proposal.label = proposal.label or merged.label
        self._set_app_rules(binding.app_rules)
        self.form_title.configure(text="Edit binding" if merged is not None else "New binding")
        self._set_entry(self.label, proposal.label)
        self._set_entry(self.trigger, format_keys(binding.keys))
        self.mode.set(_mode_label(binding))
        self._on_mode(self.mode.get())
        self._set_hold(binding.hold_ms if binding.mode == "hold" else 800)
        self._fill_tap_hold(binding)
        self.action.set(_action_label(binding.action.type))
        self._fill_action(binding.action)
        self._show_action_fields()
        self._update_key_hint(binding.keys)
        self.form_error.configure(text="")
        self._editing_id = merged.id if merged is not None else None
        self._ai_proposal = proposal

        for child in self.ai_card_rows.winfo_children():
            child.destroy()
        current = self.service.config.profile_by_id(self.selected_id)
        lines = []
        for name, value in proposal.summary_lines(self.service.config.leader_key):
            if name == "Profile" and current is not None:
                procs = ", ".join(current.process_names)
                value = f"{current.name} ({procs})" if procs else current.name
            lines.append((name, value))
        for index, (name, value) in enumerate(lines):
            ctk.CTkLabel(self.ai_card_rows, text=name, text_color=MUTED, font=self._font(12), anchor="nw", width=64,
                         height=18).grid(row=index, column=0, sticky="nw", pady=2)
            ctk.CTkLabel(self.ai_card_rows, text=value, font=self._font(12), anchor="nw", justify="left", wraplength=270,
                         height=18).grid(row=index, column=1, sticky="ew", pady=2)
        self.ai_notes.configure(text="  ".join(notes))
        if notes:
            self.ai_notes.grid()
        else:
            self.ai_notes.grid_remove()
        self.ai_card.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._ai_set_status(f"Filled in by {proposal.model} · {proposal.seconds:.1f} s. Review, then Save.")

        profile = self.service.config.profile_by_id(self.selected_id)
        if profile is not None:
            clash = find_conflicts(binding, profile.bindings, self.service.config.leader_key)
            if clash:
                self._show_conflict(ConflictError(clash))

    def _reset_ai(self, *, keep_text: bool = False) -> None:
        self._ai_token += 1  # drop any answer still on its way
        self._ai_busy = False
        self._ai_proposal = None
        self._ai_batch = []
        self.ai_button.configure(text="Generate", state="normal")
        self._ai_set_status("Fills the fields. Nothing is saved until you press Save.")
        self.ai_card.grid_remove()
        self.batch_card.grid_remove()
        self.ai_notes.configure(text="")
        if not keep_text:
            self._set_ai_text("")

    def _build_settings_view(self) -> None:
        """The Settings panel. It replaces the main body while open (Back or the gear goes back)."""
        view = ctk.CTkFrame(self, fg_color="transparent", border_width=0)
        self.settings_view = view
        view.grid_columnconfigure(0, weight=1)
        view.grid_rowconfigure(1, weight=1)
        top = ctk.CTkFrame(view, fg_color="transparent", border_width=0)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.grid_columnconfigure(1, weight=1)
        self.settings_back = self._ghost(top, "‹  Back", self._close_settings, width=90)
        self.settings_back.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(top, text="Settings", font=self._font(18, "bold")).grid(row=0, column=1, sticky="w", padx=(14, 0))
        scroll = ctk.CTkScrollableFrame(view)
        scroll.grid(row=1, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1, uniform="settings")
        scroll.grid_columnconfigure(1, weight=1, uniform="settings")
        self.settings_scroll = scroll
        general = ctk.CTkFrame(scroll, fg_color="transparent", border_width=0)
        general.grid(row=0, column=0, sticky="new", padx=(8, 16), pady=(8, 8))
        general.grid_columnconfigure(0, weight=1)
        self._build_settings(general)
        ai = ctk.CTkFrame(scroll, fg_color=CARD, border_width=1, border_color=LINE, corner_radius=10)
        ai.grid(row=0, column=1, sticky="new", padx=(0, 8), pady=(8, 8))
        ai.grid_columnconfigure(0, weight=1)
        self._build_ai_key_settings(ai)
        feedback = ctk.CTkFrame(scroll, fg_color=CARD, border_width=1, border_color=LINE, corner_radius=10)
        feedback.grid(row=1, column=0, columnspan=2, sticky="new", padx=8, pady=(8, 12))
        feedback.grid_columnconfigure(1, weight=1)
        self._build_feedback(feedback)

    def _open_donate(self) -> None:
        import webbrowser
        try:
            webbrowser.open(DONATE_URL)
        except Exception:
            pass

    def _toggle_settings(self) -> None:
        if self._settings_open:
            self._close_settings()
        else:
            self._open_settings()

    def _open_settings(self, focus: str = "") -> None:
        self._settings_open = True
        self.body.grid_remove()
        self.settings_view.grid(row=3, column=0, sticky="nsew", padx=20, pady=(0, 8))
        self._sync_ai_key_settings()
        if focus == "ai":
            if not self.ai_own_switch.get():
                self.ai_own_switch.select()
                self._on_ai_own_toggle()
            self.ai_key_status.configure(text="Paste a free key, press Save, then Test.", text_color=MUTED)
            self.after(50, self.ai_key_entry.focus_set)

    def _close_settings(self) -> None:
        self._settings_open = False
        self.settings_view.grid_remove()
        self.body.grid()

    def _build_settings(self, parent) -> None:
        ctk.CTkLabel(parent, text="GENERAL", text_color=MUTED, font=self._font(11, "bold")).grid(
            row=0, column=0, sticky="w", pady=(4, 8)
        )
        box = ctk.CTkFrame(parent, fg_color="transparent", border_width=0)
        box.grid(row=1, column=0, sticky="ew")
        box.grid_columnconfigure(0, weight=1)
        self.audio_switch = ctk.CTkSwitch(box, text="Audio ticks at hold tiers", command=self._on_audio)
        self.audio_switch.grid(row=0, column=0, sticky="w", pady=(0, 8))
        if self.service.config.audio_enabled:
            self.audio_switch.select()
        self.dry_switch = ctk.CTkSwitch(box, text="Dry run (log only, keys pass through)", command=self._on_dry_run)
        self.dry_switch.grid(row=1, column=0, sticky="w", pady=(0, 8))
        if self.service.config.dry_run:
            self.dry_switch.select()
        self.startup_switch = ctk.CTkSwitch(box, text="Start Keyware with Windows", command=self._on_startup)
        self.startup_switch.grid(row=2, column=0, sticky="w", pady=(0, 8))
        if self.service.config.start_with_windows:
            self.startup_switch.select()
        toggle_row = ctk.CTkFrame(box, fg_color="transparent", border_width=0)
        toggle_row.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        toggle_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(toggle_row, text="On/off shortcut", font=self._font(13, "bold"), anchor="w").grid(
            row=0, column=0, sticky="w", padx=(0, 8))
        self.toggle_entry = ctk.CTkEntry(toggle_row, placeholder_text="Click, then press the keys")
        self.toggle_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self._recorder_field(self.toggle_entry, "toggle")
        self.toggle_entry.insert(0, self.service.toggle_hotkey_text())
        self._ghost(toggle_row, "Save", self._save_toggle_hotkey).grid(row=0, column=2)
        ctk.CTkLabel(toggle_row, text="Setup hold (s)", font=self._font(13, "bold"), anchor="w").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=(6, 0))
        self.setup_hold_entry = ctk.CTkEntry(toggle_row, width=60)
        self.setup_hold_entry.grid(row=1, column=1, sticky="w", pady=(6, 0))
        self.setup_hold_entry.insert(0, str(self.service.config.setup_hold_seconds))
        self._ghost(toggle_row, "Save", self._save_setup_hold).grid(row=1, column=2, pady=(6, 0))
        ctk.CTkLabel(
            toggle_row,
            text="The shortcut turns Keyware off and on (keys pass through while off). "
                 "Hold a number key 1-9 that long to switch the profile's setup. 0 turns it off.",
            text_color=MUTED, font=self._font(12), wraplength=360, justify="left",
        ).grid(row=2, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        ctk.CTkLabel(box, text="Leader key", font=self._font(13, "bold"), anchor="w").grid(row=4, column=0, sticky="w")
        ctk.CTkLabel(
            box,
            text="F13 does not type anything. Caps Lock will stop toggling caps while Keyware is active.",
            text_color=MUTED,
            font=self._font(12),
            wraplength=360,
            justify="left",
        ).grid(row=5, column=0, sticky="ew", pady=(2, 6))
        leader_row = ctk.CTkFrame(box, fg_color="transparent", border_width=0)
        leader_row.grid(row=6, column=0, sticky="ew")
        leader_row.grid_columnconfigure(0, weight=1)
        self.leader_entry = ctk.CTkEntry(leader_row, placeholder_text="Click, then press one key")
        self.leader_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self._recorder_field(self.leader_entry, "leader")
        if self.service.config.leader_key:
            self.leader_entry.insert(0, self.service.config.leader_key)
        self._ghost(leader_row, "Save", self._save_leader).grid(row=0, column=1, padx=(0, 6))
        self._ghost(leader_row, "Clear", self._clear_leader).grid(row=0, column=2)
        self.settings_note = ctk.CTkLabel(box, text="", text_color=MUTED, anchor="w", wraplength=420, justify="left")
        self.settings_note.grid(row=7, column=0, sticky="ew", pady=(8, 0))

    def _on_startup(self) -> None:
        want = bool(self.startup_switch.get())
        try:
            now_on = self.service.set_start_with_windows(want)
        except ValueError as exc:
            now_on = bool(self.service.config.start_with_windows)
            self.settings_note.configure(text=str(exc))
        else:
            self.settings_note.configure(
                text="Keyware will start in the tray when you sign in to Windows." if now_on
                else "Keyware won't start with Windows."
            )
        if now_on:
            self.startup_switch.select()
        else:
            self.startup_switch.deselect()

    # --- AI key (optional) -------------------------------------------------

    def _build_ai_key_settings(self, box) -> None:
        ctk.CTkLabel(box, text="AI KEY (OPTIONAL)", text_color=MUTED, font=self._font(11, "bold")).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 4)
        )
        ctk.CTkLabel(
            box,
            text="Text mode works out of the box with a free daily limit. Add your own free key for unlimited use. "
                 "It stays on this PC, locked to your Windows account.",
            text_color=MUTED, font=self._font(12), wraplength=380, justify="left", anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 10))
        self.ai_own_switch = ctk.CTkSwitch(box, text="Use my own free key", command=self._on_ai_own_toggle)
        self.ai_own_switch.grid(row=2, column=0, sticky="w", padx=16, pady=(0, 10))
        self.ai_provider = ctk.CTkSegmentedButton(box, values=["Gemini", "Groq"], command=self._on_ai_provider)
        self.ai_provider.grid(row=3, column=0, sticky="w", padx=16, pady=(0, 10))
        key_row = ctk.CTkFrame(box, fg_color="transparent", border_width=0)
        key_row.grid(row=4, column=0, sticky="ew", padx=16)
        key_row.grid_columnconfigure(0, weight=1)
        self.ai_key_entry = ctk.CTkEntry(key_row, show="•", placeholder_text="Paste your key")
        self.ai_key_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.ai_key_save = self._ghost(key_row, "Save", self._save_ai_key, width=70)
        self.ai_key_save.grid(row=0, column=1, padx=(0, 6))
        self.ai_key_remove = self._ghost(key_row, "Remove", self._remove_ai_key, width=80)
        self.ai_key_remove.grid(row=0, column=2)
        buttons = ctk.CTkFrame(box, fg_color="transparent", border_width=0)
        buttons.grid(row=5, column=0, sticky="w", padx=16, pady=(10, 0))
        self.ai_get_key = ctk.CTkButton(buttons, text="Get a free key", height=32, width=130,
                                        command=self._open_key_page)
        self.ai_get_key.pack(side="left")
        self.ai_key_test = self._ghost(buttons, "Test", self._test_ai_key, width=80)
        self.ai_key_test.pack(side="left", padx=(8, 0))
        self.ai_key_status = ctk.CTkLabel(box, text="", text_color=MUTED, font=self._font(12), wraplength=380,
                                          justify="left", anchor="w")
        self.ai_key_status.grid(row=6, column=0, sticky="ew", padx=16, pady=(8, 4))
        self.ai_key_help = ctk.CTkLabel(
            box, text=AI_KEY_STEPS, text_color=MUTED, font=self._font(12), wraplength=380, justify="left", anchor="w",
        )
        self.ai_key_help.grid(row=7, column=0, sticky="ew", padx=16, pady=(4, 16))
        self.ai_key_entry.bind("<KeyRelease>", lambda _e: self._on_ai_key_typed(), add="+")
        self.ai_key_entry.bind("<<Paste>>", lambda _e: self.after(10, self._on_ai_key_typed), add="+")
        self._sync_ai_key_settings()

    def _sync_ai_key_settings(self) -> None:
        config = self.service.config
        self.ai_provider.set("Groq" if config.ai_provider == "groq" else "Gemini")
        if config.ai_use_own_key:
            self.ai_own_switch.select()
        else:
            self.ai_own_switch.deselect()
        has_key = bool(config.ai_key_protected)
        self.ai_key_entry.configure(state="normal")
        self.ai_key_entry.delete(0, "end")
        self.ai_key_entry.configure(placeholder_text="Key saved (hidden). Paste a new one to replace it."
                                    if has_key else "Paste your key")
        self.ai_key_remove.configure(state="normal" if has_key else "disabled")
        state = "normal" if config.ai_use_own_key else "disabled"
        for widget in (self.ai_provider, self.ai_key_entry, self.ai_key_save, self.ai_key_test):
            widget.configure(state=state)
        if not config.ai_use_own_key:
            self.ai_key_status.configure(text="Using Keyware's free AI (daily limit).", text_color=MUTED)
        elif not has_key:
            self.ai_key_status.configure(text="No key saved yet. Until you add one, Keyware's free AI is used.",
                                         text_color=MUTED)
        else:
            name = "Groq" if config.ai_provider == "groq" else "Gemini"
            self.ai_key_status.configure(text=f"Using your own {name} key.", text_color=MUTED)

    def _on_ai_own_toggle(self) -> None:
        self.service.set_ai_settings(use_own_key=bool(self.ai_own_switch.get()))
        self._sync_ai_key_settings()

    def _on_ai_provider(self, value: str) -> None:
        provider = "groq" if value == "Groq" else "gemini"
        if provider == self.service.config.ai_provider:
            return
        # A key belongs to one provider: switching asks for the other one's key.
        self.service.set_ai_settings(provider=provider, key_protected="")
        self._sync_ai_key_settings()
        self.ai_key_status.configure(text=f"Paste your {value} key and press Save.", text_color=MUTED)

    def _on_ai_key_typed(self) -> None:
        """Shows which provider the pasted key belongs to. Never shows the key."""
        from assist.ai_binder import detect_provider, key_format_problem

        raw = self.ai_key_entry.get().strip()
        if not raw:
            return
        provider = detect_provider(raw)
        if provider:
            name = "Groq" if provider == "groq" else "Google Gemini"
            self.ai_key_status.configure(text=f"Detected: {name} key. Press Save.", text_color=ON_GREEN)
            self.ai_provider.set("Groq" if provider == "groq" else "Gemini")
        elif len(raw) >= 8:
            self.ai_key_status.configure(text=key_format_problem(raw), text_color=OFF_RED)

    def _open_key_page(self) -> None:
        from assist.ai_binder import GEMINI_KEY_PAGE, GROQ_KEY_PAGE

        import webbrowser

        webbrowser.open(GROQ_KEY_PAGE if self.service.config.ai_provider == "groq" else GEMINI_KEY_PAGE)

    def _save_ai_key(self) -> None:
        from assist.secret_store import SecretError, protect

        from assist.ai_binder import detect_provider, key_format_problem

        raw = self.ai_key_entry.get().strip()
        problem = key_format_problem(raw)
        if problem:
            self.ai_key_status.configure(text=problem, text_color=OFF_RED)
            return
        provider = detect_provider(raw) or "gemini"
        try:
            blob = protect(raw)
        except SecretError as exc:
            self.ai_key_status.configure(text=str(exc), text_color=OFF_RED)
            return
        finally:
            raw = ""
        self.service.set_ai_settings(use_own_key=True, provider=provider, key_protected=blob)
        self._sync_ai_key_settings()
        name = "Groq" if provider == "groq" else "Google Gemini"
        self.ai_key_status.configure(
            text=f"{name} key saved and locked to your Windows account. Press Test to check it.", text_color=MUTED)

    def _remove_ai_key(self) -> None:
        self.service.set_ai_settings(use_own_key=False, key_protected="")
        self._sync_ai_key_settings()
        self.ai_key_status.configure(text="Key removed. Using Keyware's free AI (daily limit).", text_color=MUTED)

    def _test_ai_key(self) -> None:
        from assist.ai_binder import AIBinderError, check_own_key
        from assist.secret_store import SecretError, unprotect

        config = self.service.config
        typed = self.ai_key_entry.get().strip()
        try:
            key = typed or unprotect(config.ai_key_protected)
        except SecretError as exc:
            self.ai_key_status.configure(text=str(exc), text_color=OFF_RED)
            return
        from assist.ai_binder import detect_provider, key_format_problem

        if typed and key_format_problem(typed):
            self.ai_key_status.configure(text=key_format_problem(typed), text_color=OFF_RED)
            return
        if not key:
            self.ai_key_status.configure(text="Paste a key first.", text_color=OFF_RED)
            return
        provider = detect_provider(key) or config.ai_provider
        tester = self._ai_key_tester or check_own_key
        self.ai_key_test.configure(text="Testing…", state="disabled")
        self.ai_key_status.configure(text="Asking the model…", text_color=MUTED)
        results = self._ai_key_results

        def work() -> None:
            try:
                results.put(("ok", tester(provider, key)))
            except AIBinderError as exc:
                results.put(("error", str(exc)))
            except Exception:
                results.put(("error", "The test failed. Try again."))

        threading.Thread(target=work, name="keyware-keytest", daemon=True).start()

    def _drain_ai_key(self) -> None:
        while True:
            try:
                kind, payload = self._ai_key_results.get_nowait()
            except queue.Empty:
                return
            self.ai_key_test.configure(text="Test",
                                       state="normal" if self.service.config.ai_use_own_key else "disabled")
            if kind == "ok":
                self.ai_key_status.configure(text=f"Works. {payload} answered.", text_color=ON_GREEN)
            else:
                self.ai_key_status.configure(text=str(payload), text_color=OFF_RED)

    # --- Suggestions & feedback -----------------------------------------------

    def _build_feedback(self, box) -> None:
        from assist.feedback import FOLLOW_UP_NOTE, MAX_MESSAGE

        ctk.CTkLabel(box, text="SUGGESTIONS & FEEDBACK", text_color=MUTED, font=self._font(11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 4))
        ctk.CTkLabel(box, text="Got an idea, some feedback or a bug? Tell us.", text_color=MUTED,
                     font=self._font(12), anchor="w").grid(row=1, column=0, columnspan=2, sticky="w", padx=16)
        self.feedback_type = ctk.CTkSegmentedButton(box, values=["Idea", "Feedback", "Bug"])
        self.feedback_type.set("Idea")
        self.feedback_type.grid(row=2, column=0, sticky="w", padx=16, pady=(10, 8))
        self.feedback_email = ctk.CTkEntry(box, placeholder_text="Your email (required)")
        self.feedback_email.grid(row=2, column=1, sticky="ew", padx=(8, 16), pady=(10, 8))
        self.feedback_text = ctk.CTkTextbox(box, height=96, wrap="word", border_width=1, border_color=LINE)
        self.feedback_text.grid(row=3, column=0, columnspan=2, sticky="ew", padx=16)
        self.feedback_text.bind("<KeyRelease>", lambda _e: self._feedback_count(), add="+")
        row = ctk.CTkFrame(box, fg_color="transparent", border_width=0)
        row.grid(row=4, column=0, columnspan=2, sticky="ew", padx=16, pady=(8, 0))
        row.grid_columnconfigure(1, weight=1)
        self.feedback_send = ctk.CTkButton(row, text="Send", height=32, width=100, command=self._send_feedback)
        self.feedback_send.grid(row=0, column=0, sticky="w")
        self.feedback_status = ctk.CTkLabel(row, text="", text_color=MUTED, font=self._font(12), anchor="w",
                                            wraplength=520, justify="left")
        self.feedback_status.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        self.feedback_counter = ctk.CTkLabel(row, text=f"0 / {MAX_MESSAGE}", text_color=MUTED, font=self._font(11))
        self.feedback_counter.grid(row=0, column=2, sticky="e")
        ctk.CTkLabel(box, text=FOLLOW_UP_NOTE, text_color=MUTED, font=self._font(12), anchor="w").grid(
            row=5, column=0, columnspan=2, sticky="w", padx=16, pady=(8, 14))

    def _feedback_message(self) -> str:
        return self.feedback_text.get("1.0", "end").strip()

    def _feedback_count(self) -> None:
        from assist.feedback import MAX_MESSAGE

        count = len(self._feedback_message())
        self.feedback_counter.configure(text=f"{count} / {MAX_MESSAGE}",
                                        text_color=OFF_RED if count > MAX_MESSAGE else MUTED)

    def _send_feedback(self) -> None:
        from assist.feedback import FeedbackError, feedback_endpoint, send_feedback, validate
        from engine import __version__

        if self._feedback_busy:
            return
        kind = self.feedback_type.get()
        message = self._feedback_message()
        email = self.feedback_email.get().strip()
        try:
            validate(kind, message, email)
            install_id = self.service.ensure_install_id()
        except FeedbackError as exc:
            self.feedback_status.configure(text=str(exc), text_color=OFF_RED)
            return
        except Exception:
            self.feedback_status.configure(text="Couldn't send right now. Try again in a minute.", text_color=OFF_RED)
            return
        endpoint = feedback_endpoint(self.service.config.ai_endpoint)
        sender = self._feedback_sender or send_feedback
        results = self._feedback_results
        self._feedback_busy = True
        self.feedback_send.configure(text="Sending…", state="disabled")
        self.feedback_status.configure(text="", text_color=MUTED)

        def work() -> None:
            try:
                sender(endpoint, install_id, __version__, kind, message, email)
                results.put(("ok", ""))
            except FeedbackError as exc:
                results.put(("error", str(exc)))
            except Exception:
                results.put(("error", "Couldn't send right now. Try again in a minute."))

        threading.Thread(target=work, name="keyware-feedback", daemon=True).start()

    def _drain_feedback(self) -> None:
        from assist.feedback import SENT_MESSAGE

        while True:
            try:
                kind, payload = self._feedback_results.get_nowait()
            except queue.Empty:
                return
            self._feedback_busy = False
            self.feedback_send.configure(text="Send", state="normal")
            if kind == "ok":
                self.feedback_text.delete("1.0", "end")
                self._feedback_count()
                self.feedback_status.configure(text=SENT_MESSAGE, text_color=ON_GREEN)
            else:
                self.feedback_status.configure(text=str(payload), text_color=OFF_RED)

    def _build_console(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent", border_width=0)
        bar.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 12))
        bar.grid_columnconfigure(1, weight=1)
        self.console_toggle = self._ghost(bar, "Hide log", self._toggle_console)
        self.console_toggle.grid(row=0, column=0, sticky="w")
        # Shown only when config.donate_url holds an http(s) link.
        self.heart = ctk.CTkButton(
            bar, text="♥  Support Keyware", width=150, height=32, command=self._open_donate,
            fg_color="transparent", hover_color=HOVER, text_color=("#be185d", "#f472b6"),
            border_width=1, border_color=LINE, font=self._font(12),
        )
        self.heart.grid(row=0, column=1, sticky="e")
        self._sync_heart()
        ctk.CTkLabel(bar, text=f"v{__version__}", text_color=MUTED, font=self._font(12)).grid(row=0, column=2, sticky="e", padx=(8, 0))
        self._ghost(bar, "Clear", self._clear_console).grid(row=0, column=3, sticky="e", padx=(8, 0))
        self.console = ctk.CTkTextbox(bar, height=112, font=self._font(12))
        self.console.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.console.configure(state="disabled")

    def _theme_caption(self) -> None:
        mode = ctk.get_appearance_mode()
        self.theme_button.configure(text="Light" if mode == "Dark" else "Dark")

    def _toggle_theme(self) -> None:
        next_mode = "light" if ctk.get_appearance_mode() == "Dark" else "dark"
        self.service.set_theme(next_mode)
        ctk.set_appearance_mode(next_mode)
        self._theme_caption()

    def _toggle_power(self) -> None:
        want_on = self.service.snapshot()["hook"] != "on"
        message = self.service.set_enabled(want_on)
        if message:
            self._show_banner(message)
        else:
            self._hide_banner()
        self._apply_switch()
        self._sync_status()

    def _apply_switch(self) -> None:
        on = self.service.snapshot()["enabled"]
        if on:
            self.power.configure(text="TURN OFF", fg_color="transparent", text_color=INK,
                                 border_width=2, border_color=LINE, hover_color=HOVER)
        else:
            self.power.configure(text="TURN ON", fg_color=ON_GREEN, text_color="#ffffff",
                                 border_width=0, hover_color=("#166534", "#16a34a"))

    def _sync_status(self) -> None:
        snap = self.service.snapshot()
        summary = self.service.status_summary()
        status = summary["headline"]
        if status != self._status_text:
            self._status_text = status
            self.status.configure(text=status, text_color=ON_GREEN if summary["on"] and not snap.get("paused") else OFF_RED)
            self._apply_switch()
            try:
                self.status_dot.configure(
                    text_color=ON_GREEN if summary["on"] and not snap.get("paused") else OFF_RED
                )
                self.status_tip.set_text(dot_tooltip(bool(summary["on"]), bool(snap.get("paused"))))
            except Exception:
                pass
        if self.status_detail.cget("text") != summary["detail"]:
            self.status_detail.configure(text=summary["detail"])
        process = snap["process"] or "Desktop"
        badge = f"{process}   ·   {snap['profile_name']}"
        if badge != self._badge_text:
            self._badge_text = badge
            self.badge.configure(text=badge)
        auto = snap["unavailable"] if snap["hook"] == "unavailable" and snap["unavailable"] else ""
        self._sync_tray_indicator(bool(snap.get("paused")), bool(summary["on"]))
        if auto == self._auto_banner:
            return
        if auto:
            self._show_banner(auto)
        elif self._auto_banner and self.banner.cget("text") == self._auto_banner:
            self._hide_banner()
        self._auto_banner = auto

    def _show_banner(self, text: str) -> None:
        self.banner.configure(text=text)
        self.banner.grid()

    def _hide_banner(self) -> None:
        self.banner.grid_remove()

    def _refresh_profiles(self) -> None:
        for child in self.profile_row.winfo_children():
            child.destroy()
        for index, profile in enumerate(self.service.profiles()):
            selected = profile.id == self.selected_id
            button = ctk.CTkButton(
                self.profile_row,
                text=profile.name,
                height=34,
                anchor="w",
                fg_color=("#09090b", "#fafafa") if selected else "transparent",
                text_color=("#fafafa", "#09090b") if selected else INK,
                hover_color=("#27272a", "#e4e4e7") if selected else HOVER,
                border_width=0 if selected else 1,
                border_color=LINE,
                command=lambda profile_id=profile.id: self._select_profile(profile_id),
            )
            button.grid(row=index, column=0, sticky="ew", pady=(0, 6))
        extra = len(self.service.profiles())
        self._ghost(self.profile_row, "+ Profile", self._add_profile_dialog, width=200).grid(
            row=extra, column=0, sticky="ew", pady=(4, 4)
        )
        self._ghost(
            self.profile_row,
            "Edit live profile",
            lambda: self._select_profile(self.service.snapshot()["profile_id"] or "global"),
            width=200,
        ).grid(row=extra + 1, column=0, sticky="ew", pady=(0, 6))

    def _select_profile(self, profile_id: str) -> None:
        self.selected_id = profile_id
        self._clear_form()
        self._refresh_profiles()
        self._refresh_profile_editor()
        self._refresh_bindings()

    def _refresh_profile_editor(self) -> None:
        profile = self.service.config.profile_by_id(self.selected_id)
        if profile is None:
            self.selected_id = "global"
            profile = self.service.config.global_profile()
        self.profile_name.delete(0, "end")
        self.profile_name.insert(0, profile.name)
        self._set_entry(self.profile_setups, setup_text(profile.setups))
        self.profile_processes.configure(state="normal")
        self.profile_processes.delete(0, "end")
        if profile.is_global:
            self.profile_processes.insert(0, "Every other window")
            self.profile_processes.configure(state="disabled")
            self.profile_enabled.select()
            self.profile_enabled.configure(state="disabled")
            self.profile_delete.configure(state="disabled")
        else:
            self.profile_processes.insert(0, ", ".join(profile.process_names))
            self.profile_enabled.configure(state="normal")
            if profile.enabled:
                self.profile_enabled.select()
            else:
                self.profile_enabled.deselect()
            self.profile_delete.configure(state="normal")

    def _save_profile(self) -> None:
        try:
            processes = [part.strip() for part in self.profile_processes.get().split(",")]
            setups = parse_setup_text(self.profile_setups.get())
            self.service.update_profile(
                self.selected_id,
                self.profile_name.get(),
                processes,
                bool(self.profile_enabled.get()),
            )
            self.service.set_profile_setups(self.selected_id, setups)
        except ValueError as exc:
            self._show_banner(str(exc))
            return
        self._hide_banner()
        self._refresh_profiles()
        self._refresh_profile_editor()
        self._sync_status()

    def _delete_profile(self) -> None:
        profile = self.service.config.profile_by_id(self.selected_id)
        if profile is None or profile.is_global:
            return
        if not self._confirm("Delete profile", f"Delete {profile.name} and its bindings?"):
            return
        self.service.delete_profile(profile.id)
        self.selected_id = "global"
        self._refresh_profiles()
        self._refresh_profile_editor()
        self._refresh_bindings()

    def _add_profile_dialog(self) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title("Add profile")
        dialog.geometry("420x220")
        dialog.transient(self)
        dialog.grab_set()
        dialog.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(dialog, text="New profile", font=self._font(16, "bold")).grid(row=0, column=0, sticky="w", padx=16, pady=(16, 8))
        name = ctk.CTkEntry(dialog, placeholder_text="Notepad")
        name.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        processes = ctk.CTkEntry(dialog, placeholder_text="notepad.exe")
        processes.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        error = ctk.CTkLabel(dialog, text="", anchor="w")
        error.grid(row=3, column=0, sticky="ew", padx=16)

        def save() -> None:
            try:
                profile = self.service.add_profile(name.get(), [part.strip() for part in processes.get().split(",")])
            except ValueError as exc:
                error.configure(text=str(exc))
                return
            dialog.destroy()
            self._select_profile(profile.id)

        ctk.CTkButton(dialog, text="Add profile", command=save).grid(row=4, column=0, sticky="ew", padx=16, pady=16)
        name.focus()

    def _refresh_bindings(self) -> None:
        for child in self.binding_list.winfo_children():
            child.destroy()
        bindings = self.service.bindings(self.selected_id)
        if not bindings:
            ctk.CTkLabel(
                self.binding_list,
                text="Nothing is bound in this profile yet. Capture a key on the right, or type a chord, and save it.",
                text_color=MUTED,
                wraplength=340,
                justify="left",
            ).grid(row=0, column=0, sticky="ew", padx=8, pady=8)
            return
        for index, binding in enumerate(bindings):
            self._binding_card(binding).grid(row=index, column=0, sticky="ew", pady=(0, 8), padx=4)

    def _binding_card(self, binding: Binding) -> ctk.CTkFrame:
        card = ctk.CTkFrame(self.binding_list)
        card.grid_columnconfigure(0, weight=1)
        title = trigger_text(binding, self.service.config.leader_key)
        if binding.setup:
            owner = self.service.config.profile_by_id(self.selected_id)
            name = owner.setups.get(binding.setup, "missing") if owner else ""
            live = owner is not None and owner.active_setup == binding.setup
            title += f"  ·  Setup {binding.setup} {name}" + ("" if live else " (inactive)")
        ctk.CTkLabel(card, text=title, font=self._font(14, "bold"), anchor="w").grid(row=0, column=0, sticky="w", padx=12, pady=(10, 0))
        toggle = ctk.CTkSwitch(card, text="", width=40, command=lambda b=binding: self._toggle_binding(b, toggle))
        if binding.enabled:
            toggle.select()
        toggle.grid(row=0, column=1, padx=(12, 0), pady=(10, 0))
        trash = ctk.CTkButton(
            card, text="" if self._trash() else "✕", image=self._trash(), width=28, height=28, corner_radius=6,
            fg_color="transparent", hover_color=HOVER, border_width=0, text_color=MUTED,
            command=lambda b=binding: self._delete_binding(b),
        )
        trash.grid(row=0, column=2, padx=(2, 8), pady=(8, 0))
        Tooltip(trash, "Delete")
        card.trash = trash
        label = binding.label or action_type_label(binding.action.type)
        extra = ""
        if binding.mode == "leader" and not self.service.config.leader_key:
            extra = "  ·  set a leader key to arm this"
        if not binding.enabled:
            extra = "  ·  off"
        ctk.CTkLabel(card, text=label + extra, anchor="w", text_color=MUTED).grid(row=1, column=0, columnspan=3, sticky="w", padx=12)
        ctk.CTkLabel(
            card,
            text=_row_action_text(binding),
            anchor="w",
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=12, pady=(2, 0))
        actions = ctk.CTkFrame(card, fg_color="transparent", border_width=0)
        actions.grid(row=3, column=0, columnspan=3, sticky="w", padx=12, pady=(8, 10))
        self._ghost(actions, "Edit", lambda b=binding: self._edit_binding(b), width=72).pack(side="left", padx=(0, 6))
        if binding.action.type != "auto_click":  # an auto-clicker runs from its key only
            self._ghost(actions, "Run", lambda b=binding: self._run_binding(b), width=72).pack(side="left", padx=(0, 6))
        return card

    def _toggle_binding(self, binding: Binding, switch) -> None:
        try:
            self.service.set_binding_enabled(self.selected_id, binding.id, bool(switch.get()))
        except ValueError as exc:  # a trigger clash: keep it off and say why
            switch.deselect()
            self._show_banner(str(exc))
            return
        self._apply_switch()
        self._sync_status()
        self._refresh_bindings()

    def _edit_binding(self, binding: Binding) -> None:
        if self._binder_mode != "Manual":
            self._set_binder_mode("Manual")
        self._stop_recording(cancel=True)
        self._editing_id = binding.id
        self._set_app_rules(binding.app_rules)
        self.form_title.configure(text="Edit binding")
        self._set_entry(self.label, binding.label)
        self._set_entry(self.setup_entry, str(binding.setup) if binding.setup else "")
        self._set_entry(self.trigger, format_keys(binding.keys))
        self.mode.set(_mode_label(binding))
        self._on_mode(self.mode.get())
        self._set_hold(binding.hold_ms if binding.mode == "hold" else 800)
        self._fill_tap_hold(binding)
        self.action.set(_action_label(binding.action.type))
        self._fill_action(binding.action)
        self._on_action(self.action.get())
        self._update_key_hint(binding.keys)
        self.form_error.configure(text="")

    def _delete_binding(self, binding: Binding) -> None:
        """Instant delete from the trash icon. The toast offers Undo for a few seconds."""
        profile_id = self.selected_id
        try:
            removed = self.service.delete_binding(profile_id, binding.id)
        except ValueError as exc:
            self._show_banner(str(exc))
            return
        if self._editing_id == binding.id:
            self._clear_form()
        self._refresh_bindings()
        self._sync_status()
        if removed is None:
            return
        self._undo = (profile_id, removed[0], removed[1])
        name = binding.label or trigger_text(binding, self.service.config.leader_key)
        short = name if len(name) <= 28 else name[:27] + "…"
        self._show_toast(f"Deleted {short}.")

    def _run_binding(self, binding: Binding) -> None:
        self.service.run_action(binding.action)
        self._append(f"Ran {binding.label or trigger_text(binding, self.service.config.leader_key)}")

    def _on_mode(self, value: str) -> None:
        self.mode_hint.configure(text=MODE_HINTS.get(value, ""))
        if self._binder_mode == "Text":
            return  # fields stay hidden until Manual is shown
        if value == "Hold":
            self.hold_box.grid(row=5, column=0, sticky="ew", pady=(0, 8))
        else:
            self.hold_box.grid_remove()

    def _on_action(self, value: str) -> None:
        if value == "Auto-click" and self.mode.get() == "Chord" and not self._trigger_has_modifier():
            self.mode.set("Tap")  # "hold R to click" is the common case
            self._on_mode("Tap")
        self._show_action_fields()

    def _trigger_has_modifier(self) -> bool:
        try:
            keys = parse_key_expression(self.trigger.get())
        except ValueError:
            return False
        return any(key in ("ctrl", "alt", "shift", "win") for key in keys)

    def _show_action_fields(self) -> None:
        if self._binder_mode == "Text":
            return
        value = self.action.get()
        mapping = self._action_mapping()
        for widget, _options in mapping.values():
            widget.grid_remove()
        widget, options = mapping[value]
        if value == "Lock screen":
            self.simple_note.configure(text="Locks Windows, like Win + L.")
        elif value == "Screenshot":
            self.simple_note.configure(text="Takes a full-screen screenshot (Win + Print Screen). It is saved to Pictures › Screenshots.")
        elif value == "Do nothing":
            self.simple_note.configure(text="Turns the key off. Nothing is typed and nothing runs. "
                                            "For Caps Lock, Shift + Caps Lock still toggles it.")
        widget.grid(**options)
        self._sync_keep_held()
        if value == "Macro" and not self._macro_rows:
            self._add_macro_step("Key", "1")
            self._add_macro_step("Delay", "50")
            self._add_macro_step("Mouse", "right")

    def _action_mapping(self) -> dict:
        place = {"column": 0, "sticky": "ew", "pady": (0, 8)}
        return {
            "Open URL": (self.url, {"row": 7, **place}),
            "Open app": (self.app_box, {"row": 8, **place}),
            "Type text": (self.text, {"row": 9, **place}),
            "Key combo": (self.combo, {"row": 10, **place}),
            "Macro": (self.macro_box, {"row": 11, **place}),
            "Auto-click": (self.autoclick_box, {"row": 11, "column": 0, "sticky": "ew", "pady": (2, 8)}),
            "Open file or folder": (self.path_entry, {"row": 8, **place}),
            "Media / volume": (self.media_box, {"row": 8, **place}),
            "Play music / video": (self.play_box, {"row": 8, **place}),
            "Run command": (self.command_box, {"row": 8, **place}),
            "Lock screen": (self.simple_note, {"row": 8, **place}),
            "Screenshot": (self.simple_note, {"row": 8, **place}),
            "Do nothing": (self.simple_note, {"row": 8, **place}),
        }

    def _action_widgets(self) -> list:
        seen: list = []
        for widget, _options in self._action_mapping().values():
            if widget not in seen:
                seen.append(widget)
        return seen

    def _on_hold(self, value: float) -> None:
        self.hold_readout.configure(text=f"{int(round(float(value)))} ms")

    def _set_hold(self, milliseconds: int) -> None:
        clamped = min(5000, max(100, int(milliseconds)))
        self.hold.set(clamped)
        self.hold_readout.configure(text=f"{clamped} ms")

    # --- Tap / hold (v1.3.5) ---------------------------------------------

    def _on_tap_choice(self, value: str) -> None:
        if value in TAP_PLACEHOLDERS:
            self.tap_value.configure(placeholder_text=TAP_PLACEHOLDERS[value])
            self.tap_value.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        else:
            self.tap_value.grid_remove()
        if value != "Type the key" and int(round(float(self.hold.get()))) == 800:
            self._set_hold(200)  # tap vs hold wants a short threshold
        self._sync_tap_hint()

    def _on_keep_held(self) -> None:
        if self.keep_held.get() and int(round(float(self.hold.get()))) == 800:
            self._set_hold(200)
        self._sync_tap_hint()

    def _sync_keep_held(self) -> None:
        if self.action.get() == "Key combo":
            self.keep_held.grid(row=5, column=0, sticky="w", pady=(8, 0))
        else:
            self.keep_held.deselect()
            self.keep_held.grid_remove()

    def _sync_tap_hint(self) -> None:
        choice = self.tap_choice.get()
        if choice == "Type the key" and not self.keep_held.get():
            text = "Classic long press: a tap types the key. 300 ms or longer."
        elif self.keep_held.get():
            text = "Hold: the keys stay down until you let go, so Hold + C works like Ctrl + C. Another key pressed " \
                   "before the time is up counts as a hold too."
        else:
            text = "Tap and hold do different things. Keys without a hold action are never delayed."
        self.tap_hint.configure(text=text)
        self.tap_hint.grid(row=6, column=0, sticky="ew", pady=(6, 0))

    def _fill_tap_hold(self, binding: Binding) -> None:
        tap = binding.tap_action if binding.mode == "hold" else None
        value = ""
        if tap is None:
            choice = "Type the key"
        elif tap.type == "none":
            choice = "Do nothing"
        elif tap.type == "key_combo":
            choice, value = "Key or shortcut", format_keys(tap.keys)
        elif tap.type == "open_url":
            choice, value = "Open URL", tap.url
        elif tap.type == "open_app":
            choice, value = "Open app", tap.app
        elif tap.type == "type_text":
            choice, value = "Type text", tap.text
        else:
            choice = "Type the key"
        self.tap_choice.set(choice)
        self._set_entry(self.tap_value, value)
        self._on_tap_choice(choice)
        if binding.mode == "hold":
            self._set_hold(binding.hold_ms)
        if binding.mode == "hold" and binding.hold_press:
            self.keep_held.select()
        else:
            self.keep_held.deselect()
        self._sync_tap_hint()

    def _collect_tap_action(self) -> Action | None:
        choice = self.tap_choice.get()
        value = self.tap_value.get().strip()
        if choice == "Do nothing":
            return Action(type="none")
        if choice == "Key or shortcut":
            return Action(type="key_combo", keys=parse_key_expression(value))
        if choice == "Open URL":
            return Action(type="open_url", url=value)
        if choice == "Open app":
            return Action(type="open_app", app=value)
        if choice == "Type text":
            return Action(type="type_text", text=value)
        return None

    def _update_key_hint(self, keys=None) -> None:
        if keys is None:
            try:
                keys = parse_key_expression(self.trigger.get())
            except ValueError:
                keys = []
        hint = next((KEY_HINTS[key] for key in keys if key in KEY_HINTS), "")
        if hint:
            self.key_hint.configure(text=hint)
            self.key_hint.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        else:
            self.key_hint.grid_remove()

    def _add_macro_step(self, op: str = "Delay", value: str = "50", extra: str = "", fallback: str = "") -> dict:
        """One editable step: type menu, value, optional second line (app links / fallback, wait timeout).

        Reorder with ▲ ▼, delete with the trash icon. Saving validates every step again.
        """
        row = ctk.CTkFrame(self.macro_steps, fg_color="transparent", border_width=0)
        row.grid_columnconfigure(3, weight=1)
        record: dict = {"frame": row}
        up = ctk.CTkButton(row, text="▲", width=22, height=26, corner_radius=6, fg_color="transparent",
                           hover_color=HOVER, text_color=MUTED, border_width=0, command=lambda: self._move_step(record, -1))
        down = ctk.CTkButton(row, text="▼", width=22, height=26, corner_radius=6, fg_color="transparent",
                             hover_color=HOVER, text_color=MUTED, border_width=0, command=lambda: self._move_step(record, 1))
        up.grid(row=0, column=0)
        down.grid(row=0, column=1, padx=(0, 4))
        menu = ctk.CTkOptionMenu(row, values=list(STEP_OPS), width=132,
                                 command=lambda _value: self._sync_step(record))
        menu.set(op if op in STEP_OPS else "Delay")
        menu.grid(row=0, column=2, padx=(0, 6))
        entry = ctk.CTkEntry(row)
        entry.insert(0, value)
        entry.grid(row=0, column=3, sticky="ew", padx=(0, 4))
        # Key and Combo steps record keys like the trigger field. Everything else is typed.
        self._recorder_field(entry, "combo", when=lambda: menu.get() in ("Key", "Combo"))
        remove = ctk.CTkButton(row, text="", image=self._trash(), width=28, height=28, corner_radius=6,
                               fg_color="transparent", hover_color=HOVER, border_width=0,
                               command=lambda: self._remove_step(record))
        if self._trash() is None:
            remove.configure(text="✕", text_color=MUTED)
        remove.grid(row=0, column=4)
        Tooltip(remove, "Remove step")
        extra_entry = ctk.CTkEntry(row)
        if extra:
            extra_entry.insert(0, extra)
        fallback_entry = ctk.CTkEntry(row, placeholder_text="If not installed, open this website (optional)")
        if fallback:
            fallback_entry.insert(0, fallback)
        record.update({"op": menu, "value": entry, "extra": extra_entry, "fallback": fallback_entry,
                       "up": up, "down": down, "remove": remove})
        self._macro_rows.append(record)
        self._regrid_steps()
        self._sync_step(record)
        return record

    def _sync_step(self, record: dict) -> None:
        op = record["op"].get()
        entry = record["value"]
        entry.configure(placeholder_text=STEP_PLACEHOLDERS.get(op, ""))
        entry.configure(state="disabled" if op in NO_VALUE_OPS else "normal")
        extra = record["extra"]
        fallback = record["fallback"]
        if op == "Open app":
            extra.configure(placeholder_text="Open with: links or files, ; between (optional)")
            extra.grid(row=1, column=2, columnspan=2, sticky="ew", padx=(0, 4), pady=(4, 0))
            fallback.grid(row=2, column=2, columnspan=2, sticky="ew", padx=(0, 4), pady=(4, 0))
        elif op == "Wait for window":
            extra.configure(placeholder_text="Give up after (seconds), default 10")
            extra.grid(row=1, column=2, columnspan=2, sticky="ew", padx=(0, 4), pady=(4, 0))
            fallback.grid_remove()
        else:
            extra.grid_remove()
            fallback.grid_remove()

    def _regrid_steps(self) -> None:
        last = len(self._macro_rows) - 1
        for index, item in enumerate(self._macro_rows):
            item["frame"].grid(row=index, column=0, sticky="ew", pady=(0, 6))
            item["up"].configure(state="normal" if index > 0 else "disabled")
            item["down"].configure(state="normal" if index < last else "disabled")

    def _move_step(self, record: dict, delta: int) -> None:
        if record not in self._macro_rows:
            return
        index = self._macro_rows.index(record)
        target = index + delta
        if 0 <= target < len(self._macro_rows):
            rows = self._macro_rows
            rows[index], rows[target] = rows[target], rows[index]
            self._regrid_steps()

    def _remove_step(self, record: dict) -> None:
        if record in self._macro_rows:
            self._macro_rows.remove(record)
        record["frame"].destroy()
        self._regrid_steps()

    def _clear_macro_steps(self) -> None:
        for record in list(self._macro_rows):
            record["frame"].destroy()
        self._macro_rows.clear()

    def _capture(self) -> None:
        self.trigger.focus_set()
        self._start_recording(self.trigger, "trigger")

    # --- hotkey recorder fields ------------------------------------------
    def _recorder_field(self, entry, kind: str, when=None) -> None:
        """Clicking or focusing the field records keys; nothing is typed into it."""
        inner = getattr(entry, "_entry", entry)

        def allowed() -> bool:
            return when is None or bool(when())

        def on_focus(_event=None):
            if allowed() and self._record_target is not entry:
                self.after(1, lambda: self._start_recording(entry, kind))

        def on_click(_event=None):
            if allowed() and self._record_target is not entry:
                self.after(1, lambda: self._start_recording(entry, kind))

        def on_blur(_event=None):
            if self._record_target is entry:
                self._stop_recording(cancel=True)

        def on_key(event, pressed: bool):
            if self._record_target is not entry:
                return None
            name = tk_key_name(event.keysym)
            if name:
                self.service.feed_recording(name, pressed)
            return "break"  # never type the key name into the field

        def on_mouse(event, button: str, pressed: bool):
            # While recording, Middle/X1/X2 record as triggers; Left/Right only with a modifier held.
            if self._record_target is not entry:
                return None
            if button in ("mouse_left", "mouse_right") and not (int(event.state) & 0x2000D):
                return None
            self.service.feed_recording(button, pressed)
            return "break"

        x_buttons = (4, 5) if sys.platform == "win32" else (8, 9)
        for number, button in ((1, "mouse_left"), (3, "mouse_right"), (2, "mouse_middle"),
                               (x_buttons[0], "mouse_x1"), (x_buttons[1], "mouse_x2")):
            inner.bind(f"<ButtonPress-{number}>", lambda e, b=button: on_mouse(e, b, True), add="+")
            inner.bind(f"<ButtonRelease-{number}>", lambda e, b=button: on_mouse(e, b, False), add="+")
        def on_wheel(event):
            # Wheel up/down record as triggers only with a modifier held (plain scrolling stays normal).
            if self._record_target is not entry or not (int(event.state) & 0x2000D):
                return None
            delta = int(getattr(event, "delta", 0) or 0) or (120 if getattr(event, "num", 0) == 4 else -120)
            name = "mouse_wheel_up" if delta > 0 else "mouse_wheel_down"
            self.service.feed_recording(name, True)
            self.service.feed_recording(name, False)
            return "break"

        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            inner.bind(sequence, on_wheel, add="+")
        inner.bind("<FocusIn>", on_focus, add="+")
        inner.bind("<Button-1>", on_click, add="+")
        inner.bind("<FocusOut>", on_blur, add="+")
        inner.bind("<KeyPress>", lambda e: on_key(e, True), add="+")
        inner.bind("<KeyRelease>", lambda e: on_key(e, False), add="+")

    def _start_recording(self, entry, kind: str) -> None:
        if self._record_target is entry:
            return
        if self._record_target is not None:
            self._stop_recording(cancel=True)
        self._record_target = entry
        self._record_kind = kind
        self._record_prev = entry.get()
        try:
            self._entry_border = entry.cget("border_color")
        except Exception:
            self._entry_border = None
        try:
            self.service.begin_recording(leader=(kind == "trigger"), single=(kind == "leader"))
        except Exception as exc:
            self._record_target = None
            self._note_for(kind, f"Could not record keys: {exc}")
            return
        try:
            entry.configure(border_color=ON_GREEN)
        except Exception:
            pass
        self._set_entry(entry, "")
        if kind == "trigger":
            self.capture_button.configure(text="Listening…")
        self._note_for(kind, "Recording. Press the keys or a mouse button: Middle, Back/Forward, or Ctrl/Alt/Shift + Left/Right (hold to record a hold). Esc cancels, Backspace clears. Keyware bindings are paused meanwhile.")

    def _stop_recording(self, *, cancel: bool) -> None:
        entry = self._record_target
        if entry is None:
            return
        kind = self._record_kind
        self._record_target = None
        if cancel:
            self.service.cancel_recording()
            self._set_entry(entry, self._record_prev)
        if self._entry_border is not None:
            try:
                entry.configure(border_color=self._entry_border)
            except Exception:
                pass
        if kind == "trigger":
            self.capture_button.configure(text="Capture")

    def _note_for(self, kind: str, text: str) -> None:
        if kind in ("leader", "toggle"):
            self.settings_note.configure(text=text)
        else:
            self.form_error.configure(text=text)

    def _on_record_message(self, message) -> None:
        entry = self._record_target
        if entry is None:
            return
        kind = self._record_kind
        if message.kind == "record_live":
            if message.keys or message.text and not message.text.startswith("Press keys"):
                self._set_entry(entry, message.text)
            return
        if message.kind == "record_cancel":
            self._set_entry(entry, self._record_prev)
            self._finish_recording(kind, message.text or "Recording cancelled.")
            return
        if message.kind == "record_clear":
            self._set_entry(entry, "")
            self._finish_recording(kind, "Cleared.")
            return
        if message.kind != "record_done":
            return
        keys = list(message.keys)
        if kind == "leader":
            self._set_entry(entry, keys[-1] if keys else "")
            self._finish_recording(kind, "")
            self._save_leader()
            return
        self._set_entry(entry, format_keys(keys))
        note = f"Recorded {format_keys(keys)}."
        if kind == "trigger":
            self._update_key_hint(keys)
            if "copilot" in keys:
                note = "Recorded the Copilot key."
        if kind == "trigger":
            current = self.mode.get()
            if message.mode == "hold":
                self.mode.set("Hold")
                self._set_hold(int(message.hold_ms or 800))
                note = f"Recorded a hold of {format_keys(keys)} for {int(message.hold_ms)} ms."
            elif message.mode == "chord":
                self.mode.set("Chord")
            elif message.mode == "leader":
                self.mode.set("Leader")
                lead = self.service.config.leader_key or ""
                note = f"Recorded leader {format_keys([lead]) if lead else ''} → {format_keys(keys)}."
            elif current not in ("Tap", "Hold", "Double-tap", "Triple-tap", "Leader"):
                self.mode.set("Tap")
            self._on_mode(self.mode.get())
        self._append(note)
        self._finish_recording(kind, note)

    def _finish_recording(self, kind: str, note: str) -> None:
        self._stop_recording(cancel=False)
        self._note_for(kind, note)
        try:
            self.focus_set()  # leave the field so coming back to the window does not re-record
        except Exception:
            pass

    def _show_conflict(self, error) -> None:
        names = "; ".join(item.reason for item in error.conflicts[:2])
        self.form_error.configure(text=f"{names}  Replace it, or pick another trigger.", text_color=OFF_RED)
        self.replace_button.pack(side="left", padx=(8, 0))

    def _hide_conflict(self) -> None:
        self.form_error.configure(text_color=INK)
        try:
            self.replace_button.pack_forget()
        except Exception:
            pass

    def _save_binding(self, replace: bool = False) -> None:
        from engine.conflicts import ConflictError

        self._hide_conflict()
        if self._binder_mode == "Text" and self._ai_batch:
            self._save_batch()
            return
        if self._binder_mode == "Text" and self._ai_proposal is None:
            self.form_error.configure(text="Describe the binding and press Generate first, or switch to Manual.")
            return
        try:
            keys = parse_key_expression(self.trigger.get())
            mode_name = self.mode.get()
            mode, taps = {
                "Tap": ("tap", 1),
                "Hold": ("hold", 1),
                "Double-tap": ("tap_dance", 2),
                "Triple-tap": ("tap_dance", 3),
                "Chord": ("chord", 1),
                "Leader": ("leader", 1),
            }[mode_name]
            binding = Binding(
                id=self._editing_id or new_id("bind"),
                enabled=True,
                mode=mode,
                keys=keys,
                hold_ms=int(round(float(self.hold.get()))),
                taps=taps,
                label=self.label.get().strip(),
                action=self._collect_action(),
                app_rules=self._collect_app_rules(),
                setup=self._collect_setup(),
            )
            if mode == "hold":
                binding.tap_action = self._collect_tap_action()
                binding.hold_press = bool(self.keep_held.get()) and binding.action.type == "key_combo"
            if self._editing_id:
                current = next((item for item in self.service.bindings(self.selected_id) if item.id == self._editing_id), None)
                if current is not None:
                    binding.enabled = current.enabled
                notes = self.service.update_binding(self.selected_id, binding, replace=replace)
            else:
                notes = self.service.add_binding(self.selected_id, binding, replace=replace)
        except ConflictError as exc:
            self._show_conflict(exc)
            return
        except ValueError as exc:
            self.form_error.configure(text=str(exc))
            return
        self._clear_form()
        # Overriding Global from an app profile is allowed; show a calm note, not an error.
        self.form_error.configure(text=" ".join(notes or [])[:300], text_color=MUTED)
        self._refresh_bindings()
        self._apply_switch()
        self._sync_status()

    def _collect_action(self) -> Action:
        kind = self.action.get()
        if kind == "Open URL":
            return Action(type="open_url", url=self.url.get())
        if kind == "Open app":
            args = [part.strip() for part in self.app_args.get().split(";") if part.strip()]
            return Action(type="open_app", app=self.app_path.get(), args=args, fallback=self.app_fallback.get().strip())
        if kind == "Open file or folder":
            return Action(type="open_path", path=self.path_entry.get())
        if kind == "Media / volume":
            by_label = {label: name for name, label in MEDIA_LABELS.items()}
            try:
                count = int(self.media_count.get().strip() or "1")
            except ValueError as exc:
                raise ValueError("Repeat must be a whole number.") from exc
            return Action(type="media", media=by_label.get(self.media_menu.get(), ""), count=count)
        if kind == "Play music / video":
            service = "youtube" if self.play_service.get() == "YouTube" else "spotify"
            return Action(type="play", service=service, text=self.play_query.get())
        if kind == "Lock screen":
            return Action(type="lock_screen")
        if kind == "Screenshot":
            return Action(type="screenshot")
        if kind == "Run command":
            return Action(type="run_command", command=self.command_entry.get())
        if kind == "Type text":
            return Action(type="type_text", text=self.text.get("1.0", "end").strip("\n"))
        if kind == "Key combo":
            return Action(type="key_combo", keys=parse_key_expression(self.combo.get()))
        if kind == "Macro":
            return Action(type="macro", steps=self._collect_steps())
        if kind == "Auto-click":
            return self._autoclick_action()
        if kind == "Do nothing":
            return Action(type="none")
        raise ValueError("Choose an action.")

    def _collect_steps(self) -> list[MacroStep]:
        steps: list[MacroStep] = []
        for number, record in enumerate(self._macro_rows, 1):
            try:
                steps.append(self._collect_step(record))
            except ValueError as exc:
                raise ValueError(f"Step {number}: {exc}") from None
        return steps

    def _collect_step(self, record: dict) -> MacroStep:
        op = record["op"].get()
        value = record["value"].get().strip() if op not in NO_VALUE_OPS else ""
        extra = record["extra"].get().strip()
        if op == "Key":
            keys = parse_key_expression(value)
            if len(keys) != 1:
                raise ValueError("A key step takes one key. Use Combo for several.")
            step = MacroStep(op="key", key=keys[0])
        elif op == "Combo":
            step = MacroStep(op="combo", keys=parse_key_expression(value))
        elif op == "Delay":
            try:
                milliseconds = int(value)
            except ValueError as exc:
                raise ValueError("Delay must be a whole number of milliseconds.") from exc
            step = MacroStep(op="delay", ms=milliseconds)
        elif op == "Text":
            step = MacroStep(op="text", text=value)
        elif op == "Mouse":
            step = MacroStep(op="mouse", button=value.lower())
        elif op == "Open URL":
            step = MacroStep(op="url", target=value)
        elif op == "Open app":
            args = [part.strip() for part in extra.split(";") if part.strip()]
            step = MacroStep(op="app", target=value, args=args, fallback=record["fallback"].get().strip())
        elif op == "Open file/folder":
            step = MacroStep(op="path", target=value)
        elif op == "Media":
            name, count = parse_media_text(value)
            step = MacroStep(op="media", key=name, count=count)
        elif op == "Wait for window":
            try:
                seconds = float(extra) if extra else 10.0
            except ValueError as exc:
                raise ValueError("Wait time must be a number of seconds.") from exc
            is_exe = value.lower().endswith(".exe")
            step = MacroStep(op="wait_window", target=value if is_exe else "", text="" if is_exe else value,
                             ms=int(round(seconds * 1000)))
        elif op in ("Play on Spotify", "Play on YouTube"):
            step = MacroStep(op="play", target="spotify" if op.endswith("Spotify") else "youtube", text=value)
        elif op == "Lock screen":
            step = MacroStep(op="lock")
        elif op == "Screenshot":
            step = MacroStep(op="screenshot")
        else:
            raise ValueError(f"Unknown step “{op}”.")
        validate_step(step)
        return step

    def _add_step_from(self, step: MacroStep) -> None:
        """Show a saved or AI-made step in the editor, ready to tweak."""
        op = step.op
        if op == "key":
            self._add_macro_step("Key", step.key)
        elif op == "combo":
            self._add_macro_step("Combo", format_keys(step.keys))
        elif op == "delay":
            self._add_macro_step("Delay", str(step.ms))
        elif op == "text":
            self._add_macro_step("Text", step.text)
        elif op == "mouse":
            self._add_macro_step("Mouse", step.button)
        elif op == "url":
            self._add_macro_step("Open URL", step.target)
        elif op == "app":
            self._add_macro_step("Open app", step.target, "; ".join(step.args), step.fallback)
        elif op == "path":
            self._add_macro_step("Open file/folder", step.target)
        elif op == "media":
            label = MEDIA_LABELS.get(step.key, step.key)
            self._add_macro_step("Media", f"{label} x{int(step.count)}" if int(step.count) > 1 else label)
        elif op == "wait_window":
            self._add_macro_step("Wait for window", step.target or step.text, f"{int(step.ms) / 1000:g}")
        elif op == "play":
            self._add_macro_step("Play on YouTube" if step.target == "youtube" else "Play on Spotify", step.text)
        elif op == "lock":
            self._add_macro_step("Lock screen", "")
        elif op == "screenshot":
            self._add_macro_step("Screenshot", "")

    def _fill_action(self, action: Action) -> None:
        self._set_entry(self.url, action.url)
        self._set_entry(self.app_path, action.app)
        self.text.delete("1.0", "end")
        if action.text:
            self.text.insert("1.0", action.text)
        self._set_entry(self.combo, format_keys(action.keys) if action.keys else "")
        self._clear_macro_steps()
        for step in action.steps:
            self._add_step_from(step)
        self._set_entry(self.app_args, "; ".join(action.args))
        self._set_entry(self.app_fallback, action.fallback)
        self._set_entry(self.path_entry, action.path)
        self._set_entry(self.command_entry, action.command)
        self._set_entry(self.play_query, action.text if action.type == "play" else "")
        self.play_service.set("YouTube" if action.service == "youtube" else "Spotify")
        self.media_menu.set(MEDIA_LABELS.get(action.media, MEDIA_LABELS["media_play_pause"]))
        self._set_entry(self.media_count, str(int(action.count or 1)))
        if action.type == "auto_click":
            self._fill_autoclick(action)
        else:
            self._reset_autoclick()

    def _clear_form(self) -> None:
        self._stop_recording(cancel=True)
        self._set_app_rules({})
        self._hide_conflict()
        self._editing_id = None
        self.form_title.configure(text="New binding")
        for entry in (self.label, self.setup_entry, self.trigger, self.url, self.app_path, self.combo, self.app_args, self.app_fallback,
                      self.path_entry, self.command_entry, self.play_query):
            entry.delete(0, "end")
        self._set_entry(self.media_count, "1")
        self.text.delete("1.0", "end")
        self.mode.set("Chord")
        self._on_mode("Chord")
        self.action.set("Open URL")
        self._on_action("Open URL")
        self._set_hold(800)
        self.tap_choice.set("Type the key")
        self._set_entry(self.tap_value, "")
        self._on_tap_choice("Type the key")
        self.keep_held.deselect()
        self.key_hint.grid_remove()
        self._clear_macro_steps()
        self._reset_autoclick()
        self.form_error.configure(text="")
        self.capture_button.configure(text="Capture")
        self._reset_ai()

    def _set_entry(self, entry, value: str) -> None:
        entry.delete(0, "end")
        if value:
            entry.insert(0, value)

    def _on_audio(self) -> None:
        self.service.set_audio_enabled(bool(self.audio_switch.get()))

    def _on_dry_run(self) -> None:
        self.service.set_dry_run(bool(self.dry_switch.get()))
        self.settings_note.configure(text="Dry run on." if self.service.config.dry_run else "Dry run off.")

    def _save_toggle_hotkey(self) -> None:
        try:
            self.service.set_pause_hotkey(parse_key_expression(self.toggle_entry.get().strip()))
        except ValueError as exc:
            self.settings_note.configure(text=str(exc))
            return
        self._set_entry(self.toggle_entry, self.service.toggle_hotkey_text())
        self.settings_note.configure(text=f"On/off shortcut: {self.service.toggle_hotkey_text()}.")

    def _save_setup_hold(self) -> None:
        try:
            self.service.set_setup_hold_seconds(int(self.setup_hold_entry.get().strip() or "8"))
        except ValueError as exc:
            self.settings_note.configure(text=str(exc) if "Use" in str(exc) else "Enter whole seconds, e.g. 8.")
            return
        self.settings_note.configure(text=f"Setup hold: {self.service.config.setup_hold_seconds} s.")

    def _save_leader(self) -> None:
        try:
            raw = self.leader_entry.get().strip()
            key = None if not raw else parse_key_expression(raw)[0]
            if key and len(parse_key_expression(raw)) != 1:
                raise ValueError("The leader key is a single key.")
            self.service.set_leader_key(key)
        except ValueError as exc:
            self.settings_note.configure(text=str(exc))
            return
        self.settings_note.configure(text="Leader key saved." if self.service.config.leader_key else "Leader key cleared.")
        self._refresh_bindings()

    def _clear_leader(self) -> None:
        self.leader_entry.delete(0, "end")
        try:
            self.service.set_leader_key(None)
        except ValueError as exc:
            self.settings_note.configure(text=str(exc))
            return
        self.settings_note.configure(text="Leader key cleared.")
        self._refresh_bindings()

    def _confirm(self, title: str, message: str) -> bool:
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("420x170")
        dialog.transient(self)
        dialog.grab_set()
        answer = {"ok": False}
        ctk.CTkLabel(dialog, text=message, wraplength=380, justify="left").pack(anchor="w", padx=16, pady=(20, 12))

        def choose(value: bool) -> None:
            answer["ok"] = value
            dialog.destroy()

        row = ctk.CTkFrame(dialog, fg_color="transparent", border_width=0)
        row.pack(anchor="e", padx=16)
        self._ghost(row, "Cancel", lambda: choose(False)).pack(side="right")
        ctk.CTkButton(row, text="Delete", command=lambda: choose(True), width=100).pack(side="right", padx=(0, 8))
        dialog.wait_window()
        return answer["ok"]

    def _append(self, text: str) -> None:
        if not text:
            return
        stamp = time.strftime("%H:%M:%S")
        self.console.configure(state="normal")
        self.console.insert("end", f"{stamp}   {text}\n")
        try:
            lines = int(float(self.console.index("end-1c")))
        except ValueError:
            lines = 0
        if lines > 300:
            self.console.delete("1.0", "2.0")
        self.console.configure(state="disabled")
        self.console.see("end")

    def _clear_console(self) -> None:
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def _toggle_console(self) -> None:
        self._console_open = not self._console_open
        if self._console_open:
            self.console.grid()
            self.console_toggle.configure(text="Hide log")
        else:
            self.console.grid_remove()
            self.console_toggle.configure(text="Show log")

    def _drain(self) -> None:
        for message in self.service.poll():
            if message.kind.startswith("record_"):
                self._on_record_message(message)
                continue
            if message.kind == "capture":
                self._set_entry(self.trigger, format_keys(message.keys))
                self.capture_button.configure(text="Capture")
                self.form_error.configure(text="")
                if len(message.keys) > 1:
                    self.mode.set("Chord")
                    self._on_mode("Chord")
                self._append(f"Captured {format_keys(message.keys)}")
            elif message.kind == "capture_cancel":
                self.capture_button.configure(text="Capture")
                self.form_error.configure(text="Capture cancelled.")
                self._append(message.text or "Capture cancelled.")
            elif message.kind == "panic":
                self._apply_switch()
                self._append(message.text)
                self._sync_status()
            elif message.kind == "reload":
                self._append(message.text or "Config reloaded.")
                self._refresh_profiles()
                self._refresh_profile_editor()
                self._refresh_bindings()
                self._apply_switch()
                self._sync_heart()
            elif message.kind == "pause":
                self._append(message.text)
                self._sync_status()
            elif message.kind == "toast":
                self._flash(message.text)
                if self.selected_id:
                    self._refresh_bindings()
            elif message.text:
                self._append(message.text)

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            if self._drain_tray():
                return
            self._drain()
            self._drain_ai()
            self._drain_ai_key()
            self._drain_feedback()
            self._sync_status()
        except Exception:
            pass
        self._after_id = self.after(50, self._poll)

    def _drain_tray(self) -> bool:
        """Tray callbacks only enqueue. This runs on the Tk thread."""
        quit_requested = False
        while True:
            try:
                event = self._tray_events.get_nowait()
            except queue.Empty:
                break
            if event == "quit":
                quit_requested = True
            elif event == "show":
                self._show()
            elif event == "pause":
                self.service.toggle_pause()
                self._sync_status()
            elif event == "reload":
                self.service.reload_config()
                self._refresh_profiles()
                self._refresh_profile_editor()
                self._refresh_bindings()
                self._apply_switch()
            elif event == "config":
                self._open_config_file()
            elif event == "power":
                self._toggle_power()
        if quit_requested:
            self.quit_app()
            return True
        return False

    def _open_tray(self) -> None:
        from ui.tray import start_tray

        self.tray = start_tray(
            on_show=lambda: self._tray_events.put("show"),
            on_pause=lambda: self._tray_events.put("pause"),
            on_reload=lambda: self._tray_events.put("reload"),
            on_open_config=lambda: self._tray_events.put("config"),
            on_quit=lambda: self._tray_events.put("quit"),
            is_paused=lambda: bool(self.service.snapshot().get("paused")),
            on_power=lambda: self._tray_events.put("power"),
            is_on=lambda: bool(self.service.snapshot().get("enabled")),
        )

    def _sync_tray_indicator(self, paused: bool, on: bool = True) -> None:
        state = (paused, on)
        if self.tray is None or state == self._tray_paused:
            return
        self._tray_paused = state
        try:
            from ui.tray import make_icon

            self.tray.icon = make_icon(paused=paused, off=not on)
            self.tray.title = "Keyware (paused)" if paused else ("Keyware: ON" if on else "Keyware: OFF")
        except Exception:
            return

    def _open_config_file(self) -> None:
        path = self.service.store.path
        if sys.platform == "win32":
            try:
                os.startfile(path)  # type: ignore[attr-defined]
                return
            except Exception as exc:
                self._append(f"Could not open the config file: {exc}")
                return
        self._append(f"Config file  {path}")

    def _show(self) -> None:
        self.deiconify()
        self.lift()
        try:
            self.focus_force()
        except Exception:
            return

    def _on_close(self) -> None:
        self._stop_recording(cancel=True)
        if self.tray is not None:
            self.withdraw()
            self._append("Still running in the tray. Quit from the tray icon to exit.")
            try:
                first = self.service.mark_tray_hint_shown()
            except Exception:
                first = False
            if first:
                self._notify_tray(TRAY_HINT)
            return
        self.quit_app()

    def _notify_tray(self, message: str) -> None:
        self.tray_notices.append(message)
        try:
            self.tray.notify(message, "Keyware")
        except Exception:
            return

    def quit_app(self) -> None:
        try:
            self.service.cancel_recording()
        except Exception:
            pass
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
        try:
            self.service.shutdown()
        except Exception:
            pass
        if self.tray is not None:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.destroy()


def run(minimized: bool = False, background: bool = False) -> None:
    """background: the Windows sign-in launch. Tray only, no window, no popups, no nudge, not counted."""
    from engine.actions import SystemOpener
    from engine.audio_cue import AudioCue, winsound_player
    from assist.web_lookup import fetch_text
    from engine.config_store import ConfigStore, default_config_path
    from engine.context_tracker import ContextTracker, windows_foreground_name
    from engine.hook_engine import CombinedHook, HookEngine, MouseHookEngine
    from engine.hook_health import windows_input_probe
    from engine.integrity import windows_foreground_elevated
    from engine.keystate import windows_key_down
    from engine.sendinput import TaggedInjector
    from engine.service import KeywareService
    from engine.session import SessionMonitor, win32_input_desktop
    from engine.startup import StartupManager
    from engine import PROFILE_POLL_SECONDS

    on_windows = sys.platform == "win32"
    service = KeywareService(
        store=ConfigStore(default_config_path()),
        opener=SystemOpener(fetcher=fetch_text),
        injector=TaggedInjector(),
        audio=AudioCue(winsound_player),
        startup=StartupManager(),
        context=ContextTracker(windows_foreground_name, interval=PROFILE_POLL_SECONDS),
        synchronous=False,
        key_down=windows_key_down if on_windows else None,
        session=SessionMonitor(win32_input_desktop if on_windows else None),
        elevated_reader=windows_foreground_elevated if on_windows else None,
        window_probe=_foreground_window if on_windows else None,
        input_probe=windows_input_probe(),
    )
    service.attach_hook(CombinedHook(
        HookEngine(service.hook_event),
        MouseHookEngine(service.hook_event, wants=service.wants_mouse),
    ))
    if on_windows:
        service.command_confirmer = confirm_command_dialog
    from engine import nudge

    service.apply_startup_default()
    nudge_state = None
    if not background:
        state_file = nudge.state_path(service.store.path)
        nudge_state = (state_file, nudge.record_launch(state_file))
    app = KeywareApp(service, start_minimized=minimized or background, nudge_state=nudge_state)
    app.mainloop()


_TK_NAMES = {
    "control_l": "ctrl", "control_r": "ctrl", "shift_l": "shift", "shift_r": "shift",
    "alt_l": "alt", "alt_r": "alt", "win_l": "win", "win_r": "win", "super_l": "win", "super_r": "win",
    "meta_l": "alt", "meta_r": "alt", "escape": "esc", "backspace": "backspace", "return": "enter",
    "prior": "page up", "next": "page down", "caps_lock": "caps lock",
}


def tk_key_name(keysym: str) -> str | None:
    """Tk keysym -> Keyware key name (fallback recorder when there is no hook)."""
    from engine.keys import canonicalize

    raw = (keysym or "").lower()
    if raw in _TK_NAMES:
        return canonicalize(_TK_NAMES[raw])
    return canonicalize(raw)


def confirm_command_dialog(command: str) -> bool:
    """First-time Yes/No for a Run command. Called on the action thread; No is the default button."""
    import ctypes

    text = (
        "A Keyware binding wants to run this command for the first time:\n\n"
        f"{command}\n\n"
        "Run it? Keyware will not ask again for this exact command."
    )
    mb_yesno, mb_iconwarning, mb_defbutton2, mb_topmost, mb_setforeground = 0x4, 0x30, 0x100, 0x40000, 0x10000
    flags = mb_yesno | mb_iconwarning | mb_defbutton2 | mb_topmost | mb_setforeground
    return ctypes.windll.user32.MessageBoxW(None, text, "Keyware: run command?", flags) == 6  # IDYES


def _foreground_window() -> int:
    import ctypes

    return int(ctypes.windll.user32.GetForegroundWindow() or 0)
