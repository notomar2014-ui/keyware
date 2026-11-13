"""Config, profiles, bindings, and actions. Plain data, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import os
import re
import uuid

from engine.keys import (
    ALLOWED_KEYS,
    is_allowed,
    is_trigger_only,
    MODIFIERS,
    TRIGGER_ONLY_KEYS,
    canonicalize,
    format_key,
    format_keys,
    modifiers_in,
    non_modifiers,
    ordered_keys,
)
from engine import PANIC_KEYS

SCHEMA_VERSION = 1

# Ctrl+Alt+F12. It is not the panic chord and it is not a Pulse hotkey.
DEFAULT_PAUSE_HOTKEY = ["ctrl", "alt", "f12"]
# Exact combos Pulse uses. Exclusion is an exact set match, so
# Ctrl+Win+Shift+R still fires.
DEFAULT_PULSE_HOTKEYS = [
    ["ctrl", "win"],
    ["ctrl", "win", "space"],
    ["ctrl", "shift", "win"],
]

ACTION_TYPES = (
    "open_url", "open_app", "type_text", "key_combo", "macro", "auto_click",
    # v1.3.3 single actions
    "open_path", "media", "lock_screen", "screenshot", "run_command", "play",
    # v1.3.5: swallow the key and do nothing (turn Caps Lock or the Copilot key off)
    "none",
)
# Hold bindings with a separate tap action, or that keep keys held, may use a
# shorter threshold than a plain hold. 200 ms is the usual tap/hold split.
TAP_HOLD_DEFAULT_MS = 200
TAP_HOLD_MIN_MS = 100
HOLD_MIN_MS = 300
HOLD_MAX_MS = 10_000
TRIGGER_MODES = ("tap", "hold", "tap_dance", "chord", "leader")
MACRO_OPS = (
    "key", "delay", "text", "mouse", "combo",
    # v1.3.3: do things on the computer and inside apps
    "url", "app", "path", "media", "lock", "screenshot", "wait_window", "play",
)
MACRO_MAX_STEPS = 64
MEDIA_KEYS = (
    "volume_mute", "volume_up", "volume_down", "media_play_pause", "media_next", "media_prev", "media_stop",
)
MEDIA_MAX_COUNT = 50
PLAY_SERVICES = ("spotify", "youtube")
WAIT_MIN_MS = 500
WAIT_MAX_MS = 30_000
COMMAND_MAX_CHARS = 500
# Opening one of these with "Open file or folder" would run code. Use Open app or Run command.
BLOCKED_PATH_EXTENSIONS = frozenset({
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".msi",
    ".msp", ".scr", ".hta", ".cpl", ".reg", ".lnk", ".pif", ".jar", ".appref-ms", ".msc",
})
# Run command never does these: power off, sign out, deleting files, disk or boot changes, audio device changes,
# downloading and running code. They are refused when saved and again right before running.
_BLOCKED_COMMAND = re.compile(
    r"(\bshutdown\b|\blogoff\b|stop-computer|restart-computer|\btsdiscon\b|lockworkstation.*,\s*exitwindows"
    r"|\bdel\b|\berase\b|\brd\b|\brmdir\b|remove-item|\brm\b|\bri\b|\bsdelete\b|clear-content|\bcipher\b"
    r"|\bformat(-volume)?\b|\bdiskpart\b|\bbcdedit\b|\bvssadmin\b|\bwbadmin\b|\breg\s+(add|delete|import)\b"
    r"|\btakeown\b|\bicacls\b|\bnet\s+user\b|\bschtasks\b|set-executionpolicy|-enc(odedcommand)?\b"
    r"|invoke-(expression|webrequest|restmethod)|\biex\b|downloadstring|downloadfile|\bcertutil\b|\bbitsadmin\b"
    r"|\bmshta\b|\brundll32\b|\bregsvr32\b|\bwmic\b|setdefaultsounddevice|set-audiodevice|\bnircmd\b"
    r"|\bsounddevice\b|audiodeviceswitch|\bsvcl\b|\bmove\b.*\bnul\b|>\s*nul.*del"
    r"|\bcurl\b|\bwget\b|\biwr\b|\birm\b|start-bitstransfer|net\.webclient|\bftp\b|\btftp\b)",
    re.IGNORECASE,
)
MOUSE_BUTTONS = ("left", "right", "middle")
# Auto-click: "hold" clicks while the trigger key is held, "toggle" starts on
# one press and stops on the next.
AUTOCLICK_MODES = ("hold", "toggle")
AUTOCLICK_MIN_CPS = 1
AUTOCLICK_MAX_CPS = 50
AUTOCLICK_DEFAULT_CPS = 10
AUTOCLICK_MAX_SECONDS = 3600
# "While held" needs a trigger that has a clear key-down and key-up.
AUTOCLICK_HOLD_TRIGGERS = ("tap", "hold", "chord")
# Profile renamed in v1.3.3. Old configs are migrated once by ConfigStore.
GAMING_PROFILE_ID = "gaming"
GAMING_PROFILE_NAME = "Gaming"
GAMING_PROCESSES = ["javaw.exe", "minecraft.exe"]


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@dataclass
class MacroStep:
    op: str
    key: str = ""
    keys: list[str] = field(default_factory=list)
    ms: int = 0
    text: str = ""
    button: str = "left"
    # v1.3.3: url / app / path / wait_window / play
    target: str = ""
    args: list[str] = field(default_factory=list)
    fallback: str = ""  # app steps: website to open when the app is not installed
    count: int = 1  # media steps: how many presses

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"op": self.op}
        if self.op == "key":
            data["key"] = self.key
        elif self.op == "combo":
            data["keys"] = list(self.keys)
        elif self.op == "delay":
            data["ms"] = int(self.ms)
        elif self.op == "text":
            data["text"] = self.text
        elif self.op == "mouse":
            data["button"] = self.button
        elif self.op in ("url", "path"):
            data["target"] = self.target
        elif self.op == "app":
            data["target"] = self.target
            if self.args:
                data["args"] = list(self.args)
            if self.fallback:
                data["fallback"] = self.fallback
        elif self.op == "media":
            data["key"] = self.key
            data["count"] = int(self.count)
        elif self.op == "wait_window":
            data["target"] = self.target
            data["text"] = self.text
            data["ms"] = int(self.ms)
        elif self.op == "play":
            data["target"] = self.target
            data["text"] = self.text
        return data

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> MacroStep:
        op = str(raw.get("op", "")).strip()
        return MacroStep(
            op=op,
            key=str(raw.get("key", "") or ""),
            keys=[str(item) for item in _list_or_empty(raw.get("keys"))],
            ms=_int_or(raw.get("ms"), 0),
            text=str(raw.get("text", "") or ""),
            button=str(raw.get("button", "left") or "left"),
            target=str(raw.get("target", "") or ""),
            args=[str(item) for item in _list_or_empty(raw.get("args"))],
            fallback=str(raw.get("fallback", "") or ""),
            count=_int_or(raw.get("count"), 1),
        )


def _list_or_empty(value: Any) -> list:
    return value if isinstance(value, list) else []


@dataclass
class Action:
    type: str
    url: str = ""
    app: str = ""
    text: str = ""
    keys: list[str] = field(default_factory=list)
    steps: list[MacroStep] = field(default_factory=list)
    # auto_click only
    button: str = "left"
    cps: int = AUTOCLICK_DEFAULT_CPS
    click_mode: str = "hold"
    max_seconds: int = 0  # 0 = no safety stop
    # v1.3.3
    path: str = ""  # open_path
    media: str = ""  # media: one of MEDIA_KEYS
    count: int = 1  # media: presses
    command: str = ""  # run_command
    args: list[str] = field(default_factory=list)  # open_app
    fallback: str = ""  # open_app: website when the app is not installed
    service: str = ""  # play: spotify / youtube (query in text)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.type}
        if self.type == "open_url":
            data["url"] = self.url
        elif self.type == "open_app":
            data["app"] = self.app
            if self.args:
                data["args"] = list(self.args)
            if self.fallback:
                data["fallback"] = self.fallback
        elif self.type == "open_path":
            data["path"] = self.path
        elif self.type == "media":
            data["media"] = self.media
            data["count"] = int(self.count)
        elif self.type == "run_command":
            data["command"] = self.command
        elif self.type == "play":
            data["service"] = self.service
            data["text"] = self.text
        elif self.type == "type_text":
            data["text"] = self.text
        elif self.type == "key_combo":
            data["keys"] = list(self.keys)
        elif self.type == "macro":
            data["steps"] = [step.to_dict() for step in self.steps]
        elif self.type == "auto_click":
            data["button"] = self.button
            data["cps"] = int(self.cps)
            data["click_mode"] = self.click_mode
            data["max_seconds"] = int(self.max_seconds)
        return data

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Action:
        # open_calculator was a broken preset. Keep the binding and open calc.exe.
        kind = str(raw.get("type", "")).strip()
        app = str(raw.get("app", "") or "")
        if kind.lower() in ("open_calculator", "calculator"):
            kind = "open_app"
            if not app.strip():
                app = "calc.exe"
        return Action(
            type=kind,
            url=str(raw.get("url", "") or ""),
            app=app,
            text=str(raw.get("text", "") or ""),
            keys=[str(item) for item in _list_or_empty(raw.get("keys"))],
            steps=[MacroStep.from_dict(item) for item in _list_or_empty(raw.get("steps")) if isinstance(item, dict)],
            button=str(raw.get("button", "left") or "left").strip().lower(),
            cps=_int_or(raw.get("cps"), AUTOCLICK_DEFAULT_CPS),
            click_mode=str(raw.get("click_mode", "hold") or "hold").strip().lower(),
            max_seconds=_int_or(raw.get("max_seconds"), 0),
            path=str(raw.get("path", "") or ""),
            media=str(raw.get("media", "") or "").strip().lower(),
            count=_int_or(raw.get("count"), 1),
            command=str(raw.get("command", "") or ""),
            args=[str(item) for item in _list_or_empty(raw.get("args"))],
            fallback=str(raw.get("fallback", "") or ""),
            service=str(raw.get("service", "") or "").strip().lower(),
        )


def _int_or(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default  # "x", NaN, and inf from a hand-edited config


APP_RULE_MODES = ("keyware", "app", "both")
APP_RULE_LABELS = {"keyware": "Keyware handles it", "app": "App only", "both": "Both"}


def app_rule_key(name: Any) -> str:
    """Canonical exe key: lowercase file name with ".exe" ("Cursor" -> "cursor.exe")."""
    text = str(name or "").strip().replace("\\", "/").rsplit("/", 1)[-1].strip().lower()
    if not text:
        return ""
    return text if text.endswith(".exe") else text + ".exe"


def parse_app_rules(raw: Any) -> dict[str, str]:
    """dict exe->mode or list of {app, mode} -> dict. Modes are kept as written; validate_binding checks them."""
    items: list[tuple[Any, Any]] = []
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        items = [(item.get("app"), item.get("mode")) for item in raw if isinstance(item, dict)]
    rules: dict[str, str] = {}
    for app, mode in items:
        key = app_rule_key(app)
        if key and key != ".exe":
            rules[key] = str(mode or "keyware").strip().lower()
    return rules


def validate_app_rules(rules: dict[str, str]) -> dict[str, str]:
    clean: dict[str, str] = {}
    for app, mode in (rules or {}).items():
        key = app_rule_key(app)
        if not re.fullmatch(r"[^<>:\"/\\|?*\x00-\x1f]{1,60}\.exe", key or ""):
            raise ValueError(f"Per-app rule: “{app}” is not an app name like Cursor.exe.")
        if mode not in APP_RULE_MODES:
            raise ValueError(f"Per-app rule for {key}: choose Keyware handles it, App only or Both.")
        clean[key] = mode
    if len(clean) > 50:
        raise ValueError("Up to 50 per-app rules per binding.")
    return clean


MAX_SETUPS = 9
DEFAULT_SETUP_HOLD_SECONDS = 8


def _setup_number(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return number if 1 <= number <= MAX_SETUPS else 0


def parse_setups(raw: Any) -> dict[int, str]:
    """{"1": "Elytra", "2": "Spear"} -> {1: "Elytra", 2: "Spear"}. Bad entries are dropped."""
    if not isinstance(raw, dict):
        return {}
    out: dict[int, str] = {}
    for key, value in raw.items():
        number = _setup_number(key)
        name = str(value or "").strip()[:40]
        if number:
            out[number] = name or f"Setup {number}"
    return out


def parse_setup_text(text: str) -> dict[int, str]:
    """"1 Elytra, 2 Spear" -> {1: "Elytra", 2: "Spear"}. Blank clears. Raises ValueError."""
    out: dict[int, str] = {}
    for part in re.split(r"[,;\n]", text or ""):
        part = part.strip()
        if not part:
            continue
        match = re.fullmatch(r"([1-9])(?:\s*[:.=-]\s*|\s+|$)(.*)", part)
        if not match:
            raise ValueError(f"Setups look like 1 Elytra, 2 Spear (numbers 1-9). Could not read: {part}")
        number = int(match.group(1))
        if number in out:
            raise ValueError(f"Setup {number} is listed twice.")
        out[number] = match.group(2).strip()[:40] or f"Setup {number}"
    return out


def setup_text(setups: dict[int, str]) -> str:
    return ", ".join(f"{number} {name}" for number, name in sorted(setups.items()))


@dataclass
class Binding:
    id: str
    enabled: bool
    mode: str
    keys: list[str]
    action: Action
    hold_ms: int = 800
    taps: int = 2
    label: str = ""
    # v1.3.5 tap vs hold, an extension of mode "hold" (never a separate mode):
    # tap_action runs on a press shorter than hold_ms (instead of typing the key),
    # hold_press keeps the Key combo keys down for as long as the trigger is held
    # (Caps Lock: tap = Esc, hold = Ctrl).
    tap_action: Action | None = None
    hold_press: bool = False
    # Per-app rules: exe name (lowercase, e.g. "cursor.exe") -> "keyware" | "app" | "both".
    # Apps not listed use "keyware" (run the action, swallow the key).
    app_rules: dict[str, str] = field(default_factory=dict)
    # Setups: 0 = always active; 1-9 = only while that setup of its profile is active.
    setup: int = 0
    # Optional Keyware script. Empty means the action runs exactly as saved.
    # The hook never reads this. See engine/script.py.
    script: str = ""

    def is_tap_hold(self) -> bool:
        return self.mode == "hold" and (self.tap_action is not None or bool(self.hold_press))

    def signature(self) -> tuple[Any, ...]:
        trigger = tuple(ordered_keys(self.keys))
        if self.mode == "hold":
            return ("hold", trigger, int(self.hold_ms))
        if self.mode == "tap_dance":
            return ("tap_dance", trigger, int(self.taps))
        return (self.mode, trigger)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "enabled": bool(self.enabled),
            "mode": self.mode,
            "keys": list(self.keys),
            "hold_ms": int(self.hold_ms),
            "taps": int(self.taps),
            "label": self.label,
            "action": self.action.to_dict(),
            **({"tap_action": self.tap_action.to_dict()} if self.tap_action is not None else {}),
            **({"hold_press": True} if self.hold_press else {}),
            **({"app_rules": dict(self.app_rules)} if self.app_rules else {}),
            **({"setup": int(self.setup)} if self.setup else {}),
            **({"script": self.script} if self.script else {}),
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Binding:
        action_raw = raw.get("action") or {}
        if not isinstance(action_raw, dict):
            action_raw = {}
        return Binding(
            id=str(raw.get("id") or new_id("bind")),
            enabled=bool(raw.get("enabled", True)),
            mode=str(raw.get("mode", "")).strip(),
            keys=[str(item) for item in raw.get("keys", []) or []],
            hold_ms=int(raw.get("hold_ms", 800) or 800),
            taps=int(raw.get("taps", 2) or 2),
            label=str(raw.get("label", "") or ""),
            action=Action.from_dict(action_raw),
            tap_action=Action.from_dict(raw["tap_action"]) if isinstance(raw.get("tap_action"), dict) else None,
            hold_press=bool(raw.get("hold_press", False)),
            app_rules=parse_app_rules(raw.get("app_rules")),
            setup=_setup_number(raw.get("setup")),
            script=_script_text(raw.get("script")),
        )


@dataclass
class Profile:
    id: str
    name: str
    process_names: list[str]
    enabled: bool
    bindings: list[Binding] = field(default_factory=list)
    # Up to 9 numbered setups ("1" -> "Elytra"); hold that number key to switch.
    setups: dict[int, str] = field(default_factory=dict)
    active_setup: int = 0

    @property
    def is_global(self) -> bool:
        return self.id == "global"

    def setup_label(self, number: int) -> str:
        name = self.setups.get(int(number), "")
        return f"Setup {int(number)}: {name}" if name else f"Setup {int(number)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "process_names": list(self.process_names),
            "enabled": bool(self.enabled),
            "bindings": [binding.to_dict() for binding in self.bindings],
            **({"setups": {str(k): v for k, v in sorted(self.setups.items())}} if self.setups else {}),
            **({"active_setup": int(self.active_setup)} if self.active_setup else {}),
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Profile:
        bindings: list[Binding] = []
        for item in raw.get("bindings", []) or []:
            if isinstance(item, dict):
                bindings.append(Binding.from_dict(item))
        return Profile(
            id=str(raw.get("id") or new_id("profile")),
            name=str(raw.get("name") or "Profile"),
            process_names=[str(item) for item in raw.get("process_names", []) or []],
            enabled=bool(raw.get("enabled", True)),
            bindings=bindings,
            setups=parse_setups(raw.get("setups")),
            active_setup=_setup_number(raw.get("active_setup")),
        )


DEFAULT_AI_ENDPOINT = "https://keyware-app.vercel.app/api/bind"
AI_PROVIDERS = ("gemini", "groq")
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def _hold_seconds(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SETUP_HOLD_SECONDS
    return max(0, min(60, number))


def _ai_endpoint(value: Any) -> str:
    text = str(value or "").strip()
    # https anywhere; plain http only to this PC (local proxy for testing).
    if re.fullmatch(r"https://[^\s]{4,300}", text) or re.fullmatch(r"http://(127\.0\.0\.1|localhost)(:\d{1,5})?/[^\s]{0,200}", text):
        return text
    return DEFAULT_AI_ENDPOINT


def _install_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if _UUID.fullmatch(text) else ""


def _protected_blob(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"[A-Za-z0-9+/=]{16,8000}", text) else ""


def _version_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower().startswith("v"):
        text = text[1:]
    return text if re.fullmatch(r"\d+\.\d+\.\d+", text) else ""


def _epoch(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if number < 0 or number > 4e10:
        return 0.0
    return number


@dataclass
class Config:
    version: int = SCHEMA_VERSION
    enabled: bool = False
    theme: str = "dark"
    start_with_windows: bool = False
    audio_enabled: bool = True
    leader_key: str | None = None
    leader_window_ms: int = 1500
    tap_max_ms: int = 300
    tap_dance_window_ms: int = 250
    jitter_min_ms: int = 5
    jitter_max_ms: int = 15
    audio_tier1_hz: int = 600
    audio_tier2_hz: int = 850
    # Keys pressed within this many ms of a held-back key are replayed in
    # order. 0 turns that overlap handling off. Edited in the config file.
    rollover_ms: int = 120
    # Log the action and do not swallow the key or run it.
    dry_run: bool = False
    pause_hotkey: list[str] = field(default_factory=lambda: list(DEFAULT_PAUSE_HOTKEY))
    # Hold a number key 1-9 this long to switch the current profile's setup. 0 turns it off.
    setup_hold_seconds: int = DEFAULT_SETUP_HOLD_SECONDS
    # Leave Pulse's hotkeys alone. The list is exact combos, not prefixes.
    respect_pulse: bool = True
    pulse_hotkeys: list[list[str]] = field(default_factory=lambda: [list(item) for item in DEFAULT_PULSE_HOTKEYS])
    # "Support Keyware" link. Empty hides the button and the occasional nudge.
    donate_url: str = ""
    # One-time config migrations already applied, e.g. "v133_gaming".
    migrations: list[str] = field(default_factory=list)
    # SHA-256 of each Run command the user has confirmed once.
    approved_commands: list[str] = field(default_factory=list)
    # Text mode (v1.3.4). The proxy is used unless the user adds their own free key.
    ai_endpoint: str = DEFAULT_AI_ENDPOINT
    install_id: str = ""  # random UUID, sent to the proxy for its daily limit; nothing else identifies the user
    ai_use_own_key: bool = False
    tray_hint_shown: bool = False
    startup_default_applied: bool = False
    ai_provider: str = "gemini"  # gemini | groq
    ai_key_protected: str = ""  # Windows DPAPI blob (base64) of the user's own key; never the key itself
    # Update check (v1.4.0). GitHub releases/latest. Off skips the network entirely.
    check_updates: bool = True
    # Shows the script box in the Quick binder. Off by default. Saved scripts
    # still run when this is off; the flag only reveals the editor.
    scripting: bool = False
    update_skipped: str = ""  # x.y.z the user chose not to hear about
    update_later_until: float = 0  # unix time; "Later" hides the notice until then
    update_checked_at: float = 0  # unix time of the last successful check
    profiles: list[Profile] = field(default_factory=list)

    def global_profile(self) -> Profile:
        for profile in self.profiles:
            if profile.is_global:
                return profile
        profile = Profile(id="global", name="Global", process_names=[], enabled=True, bindings=[])
        self.profiles.insert(0, profile)
        return profile

    def profile_by_id(self, profile_id: str) -> Profile | None:
        for profile in self.profiles:
            if profile.id == profile_id:
                return profile
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "enabled": bool(self.enabled),
            "theme": self.theme if self.theme in ("light", "dark") else "dark",
            "start_with_windows": bool(self.start_with_windows),
            "audio_enabled": bool(self.audio_enabled),
            "leader_key": self.leader_key,
            "leader_window_ms": int(self.leader_window_ms),
            "tap_max_ms": int(self.tap_max_ms),
            "tap_dance_window_ms": int(self.tap_dance_window_ms),
            "jitter_min_ms": int(self.jitter_min_ms),
            "jitter_max_ms": int(self.jitter_max_ms),
            "audio_tier1_hz": int(self.audio_tier1_hz),
            "audio_tier2_hz": int(self.audio_tier2_hz),
            "rollover_ms": int(self.rollover_ms),
            "dry_run": bool(self.dry_run),
            "pause_hotkey": list(self.pause_hotkey),
            "setup_hold_seconds": int(self.setup_hold_seconds),
            "respect_pulse": bool(self.respect_pulse),
            "pulse_hotkeys": [list(combo) for combo in self.pulse_hotkeys],
            "donate_url": self.donate_url,
            "migrations": list(self.migrations),
            "approved_commands": list(self.approved_commands),
            "ai_endpoint": self.ai_endpoint or DEFAULT_AI_ENDPOINT,
            "install_id": self.install_id,
            "ai_use_own_key": bool(self.ai_use_own_key),
            "tray_hint_shown": bool(self.tray_hint_shown),
            "startup_default_applied": bool(self.startup_default_applied),
            "ai_provider": self.ai_provider if self.ai_provider in AI_PROVIDERS else "gemini",
            "ai_key_protected": self.ai_key_protected,
            "check_updates": bool(self.check_updates),
            "update_skipped": self.update_skipped,
            "update_later_until": float(self.update_later_until or 0),
            "update_checked_at": float(self.update_checked_at or 0),
            **({"scripting": True} if self.scripting else {}),
            "profiles": [profile.to_dict() for profile in self.profiles],
        }

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> Config:
        profiles: list[Profile] = []
        for item in raw.get("profiles", []) or []:
            if isinstance(item, dict):
                profiles.append(Profile.from_dict(item))
        leader = raw.get("leader_key")
        leader_key = str(leader).strip() if leader else None
        if leader_key == "":
            leader_key = None
        theme = str(raw.get("theme", "dark") or "dark")
        if theme not in ("light", "dark"):
            theme = "dark"
        return Config(
            version=int(raw.get("version", SCHEMA_VERSION) or SCHEMA_VERSION),
            enabled=bool(raw.get("enabled", False)),
            theme=theme,
            start_with_windows=bool(raw.get("start_with_windows", False)),
            audio_enabled=bool(raw.get("audio_enabled", True)),
            leader_key=leader_key,
            leader_window_ms=int(raw.get("leader_window_ms", 1500) or 1500),
            tap_max_ms=int(raw.get("tap_max_ms", 300) or 300),
            tap_dance_window_ms=int(raw.get("tap_dance_window_ms", 250) or 250),
            jitter_min_ms=int(raw.get("jitter_min_ms", 5) or 5),
            jitter_max_ms=int(raw.get("jitter_max_ms", 15) or 15),
            audio_tier1_hz=int(raw.get("audio_tier1_hz", 600) or 600),
            audio_tier2_hz=int(raw.get("audio_tier2_hz", 850) or 850),
            rollover_ms=_optional_int(raw, "rollover_ms", 120),
            dry_run=bool(raw.get("dry_run", False)),
            pause_hotkey=_string_list(raw.get("pause_hotkey"), DEFAULT_PAUSE_HOTKEY),
            setup_hold_seconds=_hold_seconds(raw.get("setup_hold_seconds", DEFAULT_SETUP_HOLD_SECONDS)),
            respect_pulse=bool(raw.get("respect_pulse", True)),
            pulse_hotkeys=_combo_list(raw.get("pulse_hotkeys")),
            donate_url=str(raw.get("donate_url", "") or "").strip(),
            migrations=[str(item) for item in _list_or_empty(raw.get("migrations")) if isinstance(item, str)],
            approved_commands=[
                str(item) for item in _list_or_empty(raw.get("approved_commands"))
                if isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item)
            ][:500],
            ai_endpoint=_ai_endpoint(raw.get("ai_endpoint")),
            install_id=_install_id(raw.get("install_id")),
            ai_use_own_key=bool(raw.get("ai_use_own_key", False)),
            tray_hint_shown=bool(raw.get("tray_hint_shown", False)),
            startup_default_applied=bool(raw.get("startup_default_applied", False)),
            ai_provider=str(raw.get("ai_provider", "gemini") or "gemini").strip().lower()
            if str(raw.get("ai_provider", "gemini") or "gemini").strip().lower() in AI_PROVIDERS else "gemini",
            ai_key_protected=_protected_blob(raw.get("ai_key_protected")),
            check_updates=bool(raw.get("check_updates", True)),
            update_skipped=_version_text(raw.get("update_skipped")),
            update_later_until=_epoch(raw.get("update_later_until")),
            update_checked_at=_epoch(raw.get("update_checked_at")),
            scripting=bool(raw.get("scripting", False)),
            profiles=profiles,
        )


def _url(url: str) -> Action:
    return Action(type="open_url", url=url)


def _macro(steps: list[MacroStep], label: str, **binding: Any) -> Binding:
    return Binding(action=Action(type="macro", steps=steps), label=label, **binding)


def default_config() -> Config:
    """Sample profiles. The hook stays off until the user turns Active on."""
    youtube = "https://www.youtube.com"
    return Config(
        enabled=False,
        theme="dark",
        audio_enabled=True,
        leader_key=None,
        migrations=["v133_gaming"],
        profiles=[
            Profile(
                id="global",
                name="Global",
                process_names=[],
                enabled=True,
                bindings=[
                    Binding(
                        id="bind-youtube-chord",
                        enabled=True,
                        mode="chord",
                        keys=["ctrl", "win", "shift", "r"],
                        label="Open YouTube",
                        action=_url(youtube),
                    ),
                    Binding(
                        id="bind-youtube-hold-p",
                        enabled=False,
                        mode="hold",
                        keys=["p"],
                        hold_ms=2000,
                        label="Hold P to open YouTube",
                        action=_url(youtube),
                    ),
                    Binding(
                        id="bind-leader-youtube",
                        enabled=True,
                        mode="leader",
                        keys=["y"],
                        label="Open YouTube",
                        action=_url(youtube),
                    ),
                ],
            ),
            Profile(
                id=GAMING_PROFILE_ID,
                name=GAMING_PROFILE_NAME,
                process_names=list(GAMING_PROCESSES),
                enabled=True,
                bindings=[
                    Binding(
                        id="bind-mc-q-tier1",
                        enabled=True,
                        mode="hold",
                        keys=["q"],
                        hold_ms=800,
                        label="Sneak and hotbar 1",
                        action=Action(type="key_combo", keys=["shift", "1"]),
                    ),
                    _macro(
                        [
                            MacroStep(op="key", key="1"),
                            MacroStep(op="delay", ms=50),
                            MacroStep(op="mouse", button="right"),
                            MacroStep(op="delay", ms=20),
                            MacroStep(op="key", key="2"),
                        ],
                        label="Hotbar 1, use, hotbar 2",
                        id="bind-mc-q-tier2",
                        enabled=True,
                        mode="hold",
                        keys=["q"],
                        hold_ms=2000,
                    ),
                ],
            ),
            Profile(
                id="browser",
                name="Browser",
                process_names=["chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"],
                enabled=True,
                bindings=[
                    Binding(
                        id="bind-browser-f8",
                        enabled=False,
                        mode="tap_dance",
                        keys=["f8"],
                        taps=2,
                        label="Double-tap F8 opens YouTube",
                        action=_url(youtube),
                    ),
                ],
            ),
            Profile(
                id="code",
                name="Code",
                process_names=["code.exe"],
                enabled=True,
                bindings=[
                    Binding(
                        id="bind-code-log",
                        enabled=True,
                        mode="chord",
                        keys=["ctrl", "win", "shift", "l"],
                        label="Insert console.log",
                        action=Action(type="type_text", text="console.log();"),
                    ),
                ],
            ),
        ],
    )


def trigger_text(binding: Binding, leader_key: str | None) -> str:
    if binding.mode == "chord":
        return format_keys(binding.keys)
    if binding.mode == "hold" and binding.is_tap_hold():
        return f"Tap / hold {format_key(binding.keys[0])} · {int(binding.hold_ms)} ms"
    if binding.mode == "hold":
        return f"Hold {format_key(binding.keys[0])} · {int(binding.hold_ms)} ms"
    if binding.mode == "tap":
        return f"Tap {format_key(binding.keys[0])}"
    if binding.mode == "tap_dance":
        word = {2: "Double-tap", 3: "Triple-tap"}.get(int(binding.taps), f"{int(binding.taps)} taps")
        return f"{word} {format_key(binding.keys[0])}"
    if binding.mode == "leader":
        leader = format_key(leader_key) if leader_key else "Leader"
        follow = format_key(binding.keys[0]) if binding.keys else "?"
        return f"{leader} → {follow}"
    return format_keys(binding.keys)


def action_type_label(action_type: str) -> str:
    return {
        "open_url": "Open URL",
        "open_app": "Open app",
        "type_text": "Type text",
        "key_combo": "Key combo",
        "macro": "Macro",
        "auto_click": "Auto-click",
        "open_path": "Open file or folder",
        "media": "Media / volume",
        "lock_screen": "Lock screen",
        "screenshot": "Screenshot",
        "run_command": "Run command",
        "play": "Play music / video",
        "none": "Do nothing",
    }.get(action_type, action_type)


MEDIA_LABELS = {
    "volume_mute": "Mute / unmute",
    "volume_up": "Volume up",
    "volume_down": "Volume down",
    "media_play_pause": "Play / pause",
    "media_next": "Next track",
    "media_prev": "Previous track",
    "media_stop": "Stop",
}
SERVICE_LABELS = {"spotify": "Spotify", "youtube": "YouTube"}


def media_text(name: str, count: int = 1) -> str:
    label = MEDIA_LABELS.get(name, name)
    return f"{label} ×{int(count)}" if int(count) > 1 else label


def step_summary(step: MacroStep) -> str:
    """One line per macro step, used by the AI preview and the binding card."""
    op = step.op
    if op == "key":
        return f"Press {format_key(step.key)}"
    if op == "combo":
        return f"Press {format_keys(step.keys)}"
    if op == "delay":
        return f"Wait {int(step.ms)} ms"
    if op == "text":
        compact = " ".join(step.text.split())
        return f"Type “{compact[:40]}{'…' if len(compact) > 40 else ''}”"
    if op == "mouse":
        return f"{step.button.capitalize()} click"
    if op == "url":
        return f"Open {step.target}"
    if op == "app":
        extra = f" {' '.join(step.args)}" if step.args else ""
        fallback = f" (else {step.fallback})" if step.fallback else ""
        return f"Open {step.target}{extra}{fallback}"
    if op == "path":
        return f"Open {step.target}"
    if op == "media":
        return media_text(step.key, step.count)
    if op == "lock":
        return "Lock the screen"
    if op == "screenshot":
        return "Take a screenshot"
    if op == "wait_window":
        what = step.text or step.target or "the window"
        return f"Wait for {what} (up to {int(step.ms) / 1000:g} s)"
    if op == "play":
        return f"Play “{step.text}” on {SERVICE_LABELS.get(step.target, step.target)}"
    return op


def action_summary(action: Action) -> str:
    if action.type == "open_url":
        return action.url
    if action.type == "open_app":
        return action.app
    if action.type == "type_text":
        compact = " ".join(action.text.split())
        if len(compact) > 64:
            return compact[:61] + "..."
        return compact or "(empty text)"
    if action.type == "key_combo":
        return format_keys(action.keys) if action.keys else "(no keys)"
    if action.type == "macro":
        count = len(action.steps)
        noun = "step" if count == 1 else "steps"
        return f"{count} {noun}"
    if action.type == "auto_click":
        return autoclick_summary(action)
    if action.type == "open_path":
        return action.path
    if action.type == "media":
        return media_text(action.media, action.count)
    if action.type == "lock_screen":
        return "Locks Windows"
    if action.type == "screenshot":
        return "Saves to Pictures › Screenshots"
    if action.type == "run_command":
        compact = " ".join(action.command.split())
        return compact[:61] + "..." if len(compact) > 64 else compact
    if action.type == "play":
        return f"“{action.text}” on {SERVICE_LABELS.get(action.service, action.service)}"
    if action.type == "none":
        return "Key turned off"
    return action.type


def autoclick_summary(action: Action) -> str:
    how = "toggle" if action.click_mode == "toggle" else "while held"
    text = f"{action.button.capitalize()} · {int(action.cps)} cps · {how}"
    if action.max_seconds:
        text += f" · stops after {_seconds_text(int(action.max_seconds))}"
    return text


def _seconds_text(seconds: int) -> str:
    if seconds % 60 == 0 and seconds >= 60:
        minutes = seconds // 60
        return f"{minutes} min"
    return f"{seconds} s"


def validate_url(url: str) -> str:
    text = url.strip()
    lower = text.lower()
    if not (lower.startswith("https://") or lower.startswith("http://")):
        raise ValueError("URL must start with http:// or https://.")
    rest = text.split("://", 1)[1]
    if not rest or rest.startswith("/") or any(char in text for char in " \n\r\t\\"):
        raise ValueError("URL needs a host and no spaces.")
    return text


def validate_app(app: str) -> str:
    text = app.strip().strip('"')
    if not text:
        raise ValueError("Enter an application name or path.")
    if any(char in text for char in "\n\r|&<>^"):
        raise ValueError("App path cannot contain shell characters.")
    if "\x00" in text:
        raise ValueError("App path is invalid.")
    return text


def validate_link(url: str) -> str:
    """A web address, or a spotify: link (used only when the Spotify app is installed)."""
    text = (url or "").strip()
    if text.lower().startswith("spotify:"):
        if not re.fullmatch(r"spotify:[A-Za-z0-9:%._\-]{1,300}", text):
            raise ValueError("That spotify: link is not valid.")
        return text
    return validate_url(text)


def validate_path(path: str) -> str:
    text = (path or "").strip().strip('"')
    if not text:
        raise ValueError("Enter a file or folder path.")
    if "\x00" in text or any(char in text for char in "\n\r|&<>^*?"):
        raise ValueError("The path has characters Windows paths cannot use.")
    if "://" in text:
        raise ValueError("That is a web address. Use Open URL.")
    suffix = os.path.splitext(text.rstrip("\\/"))[1].lower()
    if suffix in BLOCKED_PATH_EXTENSIONS:
        raise ValueError(f"{suffix} files run code. Use Open app or Run command instead.")
    return text


def validate_command(command: str) -> str:
    text = (command or "").strip()
    if not text:
        raise ValueError("Enter the command to run.")
    if len(text) > COMMAND_MAX_CHARS:
        raise ValueError(f"Commands are limited to {COMMAND_MAX_CHARS} characters.")
    if "\x00" in text or "\n" in text or "\r" in text:
        raise ValueError("Put the command on one line.")
    if _BLOCKED_COMMAND.search(text):
        raise ValueError("Keyware will not run commands that shut down, sign out, delete files, "
                         "change disks or audio devices, or download code.")
    return text


def validate_media(name: str, count: int) -> tuple[str, int]:
    if name not in MEDIA_KEYS:
        raise ValueError("Choose a media or volume key.")
    if not 1 <= int(count) <= MEDIA_MAX_COUNT:
        raise ValueError(f"Repeat must be between 1 and {MEDIA_MAX_COUNT}.")
    return name, int(count)


def validate_play(service: str, query: str) -> str:
    if service not in PLAY_SERVICES:
        raise ValueError("Play works with Spotify or YouTube.")
    text = " ".join((query or "").split())
    if not text:
        raise ValueError("Enter what to play.")
    if len(text) > 200:
        raise ValueError("Keep what to play under 200 characters.")
    return text


def _validate_args(args: list[str]) -> list[str]:
    cleaned = [str(item).strip() for item in args if str(item).strip()]
    if len(cleaned) > 8:
        raise ValueError("Use at most 8 app arguments.")
    for item in cleaned:
        if any(char in item for char in "\n\r|&<>^\x00"):
            raise ValueError("App arguments cannot contain shell characters.")
        if _BLOCKED_COMMAND.search(item):
            raise ValueError("That app argument is not allowed.")
    return cleaned


def _require_keys(keys: list[str], *, sending: bool = False) -> list[str]:
    if not keys:
        raise ValueError("Choose a key.")
    canonical: list[str] = []
    for key in keys:
        if not is_allowed(key):
            raise ValueError(f"Unknown key '{key}'.")
        if sending and is_trigger_only(key):
            raise ValueError(f"{format_key(key)} can be a trigger, but Keyware cannot send it.")
        if key not in canonical:
            canonical.append(key)
    return ordered_keys(canonical)


def _require_mouse_modifier(keys: list[str]) -> None:
    from engine.keys import MODIFIERS, MOUSE_NEEDS_MODIFIER

    clicks = [key for key in keys if key in MOUSE_NEEDS_MODIFIER]
    if clicks and not any(key in MODIFIERS for key in keys):
        raise ValueError(f"{format_key(clicks[0])} needs a modifier (Ctrl, Alt, Shift or Win) so normal clicks keep working.")


def validate_action(action: Action) -> None:
    if action.type not in ACTION_TYPES:
        raise ValueError("Choose an action.")
    if action.type == "open_url":
        action.url = validate_url(action.url)
    elif action.type == "open_app":
        action.app = validate_app(action.app)
        action.args = _validate_args(list(action.args))
        if action.fallback:
            action.fallback = validate_url(action.fallback)
    elif action.type == "open_path":
        action.path = validate_path(action.path)
    elif action.type == "media":
        action.media, action.count = validate_media(action.media, action.count)
    elif action.type == "run_command":
        action.command = validate_command(action.command)
    elif action.type == "play":
        action.text = validate_play(action.service, action.text)
    elif action.type == "type_text":
        if not action.text:
            raise ValueError("Enter the text to type.")
        if len(action.text) > 8000:
            raise ValueError("Text is limited to 8000 characters.")
    elif action.type == "key_combo":
        action.keys = _require_keys(list(action.keys), sending=True)
    elif action.type == "macro":
        if not action.steps:
            raise ValueError("Add at least one macro step.")
        if len(action.steps) > MACRO_MAX_STEPS:
            raise ValueError(f"Macros are limited to {MACRO_MAX_STEPS} steps.")
        for step in action.steps:
            if step.op not in MACRO_OPS:
                raise ValueError(f"Unknown macro step '{step.op}'.")
            validate_step(step)
    elif action.type == "auto_click":
        if action.button not in MOUSE_BUTTONS:
            raise ValueError("Mouse button must be left, right, or middle.")
        if not AUTOCLICK_MIN_CPS <= int(action.cps) <= AUTOCLICK_MAX_CPS:
            raise ValueError(f"Clicks per second must be between {AUTOCLICK_MIN_CPS} and {AUTOCLICK_MAX_CPS}.")
        if action.click_mode not in AUTOCLICK_MODES:
            raise ValueError("Auto-click mode must be while held or toggle.")
        if not 0 <= int(action.max_seconds) <= AUTOCLICK_MAX_SECONDS:
            raise ValueError("The safety stop must be between 0 and 3600 seconds.")


def validate_step(step: MacroStep) -> None:
    """Validate one macro step in place. Raises ValueError with a friendly message."""
    if step.op == "key":
        step.key = _require_keys([step.key], sending=True)[0]
    elif step.op == "combo":
        step.keys = _require_keys(list(step.keys), sending=True)
    elif step.op == "delay":
        if step.ms < 0 or step.ms > 60_000:
            raise ValueError("Delay must be between 0 and 60000 ms.")
    elif step.op == "text":
        if not step.text:
            raise ValueError("A text step needs text.")
        if len(step.text) > 8000:
            raise ValueError("Text is limited to 8000 characters.")
    elif step.op == "mouse":
        if step.button not in MOUSE_BUTTONS:
            raise ValueError("Mouse button must be left, right, or middle.")
    elif step.op == "url":
        step.target = validate_link(step.target)
    elif step.op == "app":
        step.target = validate_app(step.target)
        step.args = _validate_args(list(step.args))
        if step.fallback:
            step.fallback = validate_url(step.fallback)
    elif step.op == "path":
        step.target = validate_path(step.target)
    elif step.op == "media":
        step.key, step.count = validate_media(step.key, step.count)
    elif step.op == "wait_window":
        if not (step.target.strip() or step.text.strip()):
            raise ValueError("Say which window to wait for (app name or title).")
        if not WAIT_MIN_MS <= int(step.ms) <= WAIT_MAX_MS:
            raise ValueError(f"Wait for window needs a timeout between {WAIT_MIN_MS} and {WAIT_MAX_MS} ms.")
        if step.target:
            step.target = validate_app(step.target)
    elif step.op == "play":
        step.text = validate_play(step.target, step.text)
    elif step.op in ("lock", "screenshot"):
        pass
    else:
        raise ValueError(f"Unknown macro step '{step.op}'.")


def _script_text(value: Any) -> str:
    """A script is text. Anything else is kept as a marker that validation turns off."""
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    return "invalid"


def _string_list(raw: Any, default: list[str]) -> list[str]:
    if not isinstance(raw, list) or not raw:
        return list(default)
    return [str(item) for item in raw]


def _combo_list(raw: Any) -> list[list[str]]:
    if raw is None:
        return [list(item) for item in DEFAULT_PULSE_HOTKEYS]
    if not isinstance(raw, list):
        return [list(item) for item in DEFAULT_PULSE_HOTKEYS]
    combos: list[list[str]] = []
    for item in raw:
        if isinstance(item, list):
            combos.append([str(part) for part in item])
    return combos


def _optional_int(raw: dict[str, Any], name: str, default: int) -> int:
    """Read an integer that is allowed to be 0. Missing values use default."""
    if name not in raw or raw.get(name) is None or raw.get(name) == "":
        return default
    try:
        return int(raw[name])
    except (TypeError, ValueError):
        return default


def validate_binding(binding: Binding, config: Config, *, check_leader: bool = True) -> None:
    if binding.mode not in TRIGGER_MODES:
        raise ValueError("Choose a trigger mode.")
    binding.keys = _require_keys(list(binding.keys))
    _require_mouse_modifier(binding.keys)
    if binding.mode == "chord" and set(binding.keys) == {"win", "shift", "f23"}:
        # Win + Shift + F23 is what the Copilot key sends. Bind the key itself.
        binding.mode, binding.keys = "tap", ["copilot"]
    if set(binding.keys) == set(PANIC_KEYS):
        raise ValueError("Ctrl + Alt + Shift + Esc is the panic switch and cannot be bound.")
    binding.app_rules = validate_app_rules(binding.app_rules)
    binding.script = binding.script.replace("\r\n", "\n").replace("\r", "\n").strip()
    validate_action(binding.action)
    if (
        binding.action.type == "auto_click"
        and binding.action.click_mode == "hold"
        and binding.mode not in AUTOCLICK_HOLD_TRIGGERS
    ):
        raise ValueError("“While held” auto-click needs a Tap, Hold, or Chord trigger. Use Toggle for this trigger.")
    if binding.mode == "chord":
        triggers = non_modifiers(binding.keys)
        mods = modifiers_in(binding.keys)
        if len(triggers) != 1 or not mods:
            raise ValueError("A chord needs at least one modifier and one normal key.")
    elif binding.mode in ("tap", "hold", "tap_dance", "leader"):
        if len(binding.keys) != 1 or binding.keys[0] in MODIFIERS:
            raise ValueError("Pick one non-modifier key for this mode.")
    if binding.mode != "hold":
        binding.tap_action = None
        binding.hold_press = False
    if binding.mode == "hold":
        if binding.is_tap_hold():
            if binding.hold_ms < TAP_HOLD_MIN_MS or binding.hold_ms > HOLD_MAX_MS:
                raise ValueError(f"Tap / hold threshold must be between {TAP_HOLD_MIN_MS} and {HOLD_MAX_MS} ms.")
        elif binding.hold_ms < HOLD_MIN_MS or binding.hold_ms > HOLD_MAX_MS:
            raise ValueError("Hold duration must be between 300 and 10000 ms.")
        if binding.hold_press and binding.action.type != "key_combo":
            raise ValueError("“Keep held” needs a Key combo hold action, e.g. Ctrl.")
        if binding.tap_action is not None:
            validate_action(binding.tap_action)
            if binding.tap_action.type == "auto_click" and binding.tap_action.click_mode == "hold":
                raise ValueError("A tap cannot start a “While held” auto-click. Use Toggle.")
    if binding.mode == "tap_dance":
        if binding.taps not in (2, 3):
            raise ValueError("Tap-dance supports double-tap or triple-tap.")
    if check_leader and binding.mode == "leader":
        if not config.leader_key:
            raise ValueError("Set a leader key in Settings before saving a leader shortcut.")
        if binding.keys[0] == config.leader_key:
            raise ValueError("The leader key cannot also be the follow-up key.")
    leader = config.leader_key
    if check_leader and leader and binding.mode != "leader" and leader in binding.keys:
        raise ValueError(f"{format_key(leader)} is the leader key. Pick a different trigger.")
    if binding.script:
        if binding.action.type == "auto_click":
            raise ValueError("Auto-click runs from the key. Remove the script or pick another action.")
        if binding.hold_press:
            raise ValueError("A script cannot run while keys stay held. Turn off “Keep held” or clear the script.")
        from engine.script import compile_script

        compile_script(binding.script)


def _canonical_list(keys: list[str]) -> list[str] | None:
    """Canonical key names, or None if any name is unknown."""
    cleaned: list[str] = []
    for key in keys:
        name = canonicalize(str(key))
        if name is None:
            return None
        if name not in cleaned:
            cleaned.append(name)
    return ordered_keys(cleaned)


def _clean_action_keys(action: Action) -> bool:
    """Rewrite action keys in place. Return False if a key name is unknown."""
    if action.type == "key_combo":
        cleaned = _canonical_list(list(action.keys))
        if cleaned is None:
            return False
        action.keys = cleaned
    elif action.type == "macro":
        for step in action.steps:
            if step.op == "key":
                name = canonicalize(step.key)
                if name is None:
                    return False
                step.key = name
            elif step.op == "combo":
                cleaned = _canonical_list(list(step.keys))
                if cleaned is None:
                    return False
                step.keys = cleaned
    return True


def is_web_url(url: str) -> bool:
    text = (url or "").strip()
    lowered = text.lower()
    return (lowered.startswith("https://") or lowered.startswith("http://")) and len(text) > 10 and " " not in text


def sanitize_config(config: Config) -> list[str]:
    """Make a loaded config safe to run. Invalid bindings are disabled, not deleted."""
    warnings: list[str] = []
    for profile in config.profiles:
        if profile.active_setup and profile.active_setup not in profile.setups:
            profile.active_setup = 0
    if config.leader_key:
        leader = canonicalize(config.leader_key)
        if leader is None or leader in MODIFIERS or leader == "esc":
            warnings.append("Leader key was cleared because it is not a normal key.")
            config.leader_key = None
        else:
            config.leader_key = leader
    if config.rollover_ms < 0 or config.rollover_ms > 1000:
        warnings.append("Rollover window was limited to 0–1000 ms.")
        config.rollover_ms = min(1000, max(0, int(config.rollover_ms)))
    if config.donate_url and not is_web_url(config.donate_url):
        warnings.append("Donate link was cleared because it is not an http(s) address.")
        config.donate_url = ""
    pause = _canonical_list(list(config.pause_hotkey))
    if not pause or set(pause) == set(PANIC_KEYS):
        warnings.append("Pause hotkey was reset to Ctrl + Alt + F12.")
        config.pause_hotkey = list(DEFAULT_PAUSE_HOTKEY)
    else:
        config.pause_hotkey = pause
    cleaned_pulse: list[list[str]] = []
    for combo in config.pulse_hotkeys:
        canonical = _canonical_list(list(combo))
        if canonical is None:
            warnings.append("Dropped a Pulse hotkey with an unknown key.")
            continue
        if canonical not in cleaned_pulse:
            cleaned_pulse.append(canonical)
    if config.respect_pulse and not cleaned_pulse:
        warnings.append("Pulse hotkeys were restored to the defaults.")
        cleaned_pulse = [list(item) for item in DEFAULT_PULSE_HOTKEYS]
    config.pulse_hotkeys = cleaned_pulse

    for profile in config.profiles:
        for binding in profile.bindings:
            label = binding.label or binding.id or "binding"
            cleaned = _canonical_list(list(binding.keys))
            if cleaned is None:
                if binding.enabled:
                    binding.enabled = False
                    warnings.append(f"Disabled “{label}” because a trigger key is unknown.")
                continue
            binding.keys = cleaned
            if not _clean_action_keys(binding.action) or (
                binding.tap_action is not None and not _clean_action_keys(binding.tap_action)
            ):
                if binding.enabled:
                    binding.enabled = False
                    warnings.append(f"Disabled “{label}” because an action key is unknown.")
                continue
            try:
                validate_binding(binding, config, check_leader=False)
            except ValueError as exc:
                if binding.enabled:
                    binding.enabled = False
                    warnings.append(f"Disabled “{label}”: {exc}")
    return warnings


def validate_leader_key(key: str | None, config: Config) -> str | None:
    if key is None or key == "":
        return None
    if not is_allowed(key) or key in MODIFIERS or key == "esc":
        raise ValueError("Leader key must be a normal key. F13 is a safe choice.")
    for profile in config.profiles:
        for binding in profile.bindings:
            if binding.mode != "leader" and key in binding.keys:
                raise ValueError(
                    f"{format_key(key)} is already used by “{binding.label or trigger_text(binding, config.leader_key)}”."
                )
    return key


def command_fingerprint(command: str) -> str:
    """Approval key for a Run command. Any edit to the command asks again."""
    import hashlib

    return hashlib.sha256(" ".join(command.split()).encode("utf-8")).hexdigest()


def parse_media_text(text: str) -> tuple[str, int]:
    """"Volume up x5" / "mute" / "Play / pause" -> ("volume_up", 5). Raises ValueError."""
    raw = " ".join((text or "").strip().lower().split())
    count = 1
    match = re.search(r"\s*[x×*]\s*(\d{1,3})$", raw)
    if match:
        count = int(match.group(1))
        raw = raw[: match.start()].strip()
    words = {label.lower(): name for name, label in MEDIA_LABELS.items()}
    words.update({name: name for name in MEDIA_KEYS})
    words.update({
        "mute": "volume_mute", "unmute": "volume_mute", "volume up": "volume_up", "louder": "volume_up",
        "volume down": "volume_down", "quieter": "volume_down", "play": "media_play_pause",
        "pause": "media_play_pause", "play/pause": "media_play_pause", "play pause": "media_play_pause",
        "next": "media_next", "previous": "media_prev", "prev": "media_prev", "stop": "media_stop",
    })
    name = words.get(raw) or words.get(raw.replace(" / ", "/"))
    if name is None:
        raise ValueError("Media step: use mute, play/pause, next, previous, stop, volume up x5 or volume down x5.")
    return validate_media(name, count)
