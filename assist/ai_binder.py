"""Describe-it binder: plain words in, a proposed Binding out. It never saves.

A small "mini agent": one model call with a thorough system prompt that
teaches the model Keyware's binding schema, then strict validation. The
result only fills the editor; the user reviews it and presses Save.

Two ways to reach a model (v1.3.4):
  * Default: Keyware's proxy (config.ai_endpoint, https://keyware-app.vercel.app/api/bind).
    The proxy owns the system prompt and the API keys and allows a daily
    number of requests per install. The app sends the sentence plus a little
    context (profiles, existing triggers, versions) and a random install ID.
  * "Use my own free key" (Settings): the user's Gemini or Groq key, stored with
    Windows DPAPI, calls the provider directly with the same prompt.

Providers are free tiers only, tried in order:
  1. Gemini flash-lite (OpenAI-compatible endpoint)
  2. Gemini 3.1 flash-lite
  3. Groq openai/gpt-oss-120b, then openai/gpt-oss-20b

The shipped app never reads keys from a .env file or the environment. Running
from source with KEYWARE_DEV_KEYS=1 lets a developer use GEMINI_API_KEY /
GROQ_API_KEY from the environment. Keys stay in memory only and are never
logged, printed, or put in an error message.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from engine.keys import MODIFIERS, canonicalize, format_key, format_keys, ordered_keys
from engine.models import (
    ACTION_TYPES,
    MACRO_OPS,
    AUTOCLICK_DEFAULT_CPS,
    AUTOCLICK_MAX_CPS,
    AUTOCLICK_MAX_SECONDS,
    AUTOCLICK_MIN_CPS,
    MACRO_MAX_STEPS,
    MEDIA_MAX_COUNT,
    MOUSE_BUTTONS,
    TAP_HOLD_DEFAULT_MS,
    TAP_HOLD_MIN_MS,
    SERVICE_LABELS,
    WAIT_MAX_MS,
    WAIT_MIN_MS,
    Action,
    Binding,
    Config,
    MacroStep,
    action_type_label,
    media_text,
    new_id,
    step_summary,
    validate_action,
    validate_step,
    trigger_text,
    validate_binding,
    parse_app_rules,
)
from engine import PANIC_KEYS, __version__

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_KEY_PAGE = "https://aistudio.google.com/apikey"
GROQ_KEY_PAGE = "https://console.groq.com/keys"
PER_CALL_TIMEOUT = 6.0  # seconds for one provider
TOTAL_BUDGET = 14.0  # seconds across fallbacks
PROXY_TIMEOUT = 25.0  # the proxy tries its own fallbacks
MAX_DESCRIPTION = 500  # the proxy refuses longer text
LIMIT_MESSAGE = "Daily AI limit reached, try tomorrow or add your own free key"
BUSY_MESSAGE = "AI busy. Try again in a minute, or add your own free key."
OFFLINE_MESSAGE = "No internet connection. Text mode needs to be online."
MAX_BATCH = 8
NOT_BINDING_MESSAGE = (
    "That doesn't look like a key binding. Describe what you want your keys to do, like “F9 opens YouTube”."
)


@dataclass(frozen=True)
class Provider:
    name: str
    url: str
    model: str
    key_env: str


DEFAULT_PROVIDERS: tuple[Provider, ...] = (
    Provider("gemini", GEMINI_URL, "gemini-3.5-flash-lite", "GEMINI_API_KEY"),
    Provider("gemini", GEMINI_URL, "gemini-3.1-flash-lite", "GEMINI_API_KEY"),
    Provider("groq", GROQ_URL, "openai/gpt-oss-120b", "GROQ_API_KEY"),
    Provider("groq", GROQ_URL, "openai/gpt-oss-20b", "GROQ_API_KEY"),
)


_GEMINI_KEY_RE = re.compile(r"^AIza[0-9A-Za-z_\-]{30,40}$")
_GROQ_KEY_RE = re.compile(r"^gsk_[0-9A-Za-z]{40,64}$")
KEY_HELP = "Gemini keys start with AIza, Groq keys start with gsk_."


def detect_provider(key: str) -> str | None:
    """'gemini' for AIza… keys, 'groq' for gsk_… keys, else None. Only the shape is checked."""
    key = (key or "").strip()
    if _GEMINI_KEY_RE.match(key):
        return "gemini"
    if _GROQ_KEY_RE.match(key):
        return "groq"
    return None


def key_format_problem(key: str) -> str:
    """'' when the key looks right, else a short reason that never repeats the key."""
    key = (key or "").strip()
    if not key:
        return "Paste a key first."
    if any(ch.isspace() for ch in key):
        return "The key has a space in it. Copy it again."
    if detect_provider(key):
        return ""
    if key.startswith("AIza"):
        return "That looks like a Gemini key, but it's the wrong length. Copy it again."
    if key.startswith("gsk_"):
        return "That looks like a Groq key, but it's the wrong length. Copy it again."
    return f"That isn't a Gemini or Groq API key. {KEY_HELP}"


class AIBinderError(Exception):
    """Short, user-facing message. Never contains a key.

    final=True means a model answered and the answer was refused or unsafe;
    asking another model would only shop for a different verdict.
    not_binding=True means the text was not about key bindings at all.
    """

    def __init__(self, message: str, final: bool = False, not_binding: bool = False, code: str = ""):
        super().__init__(message)
        self.final = final or not_binding
        self.not_binding = not_binding
        # "rate_limited" | "busy" | "offline" | "timeout" | "key" | "" — the window offers "add your own key" for some.
        self.code = code


@dataclass
class Rejected:
    """One item of a multi-binding reply that failed validation. Shown, never saved."""

    label: str
    error: str
    trigger: str = ""


@dataclass
class Proposal:
    binding: Binding
    label: str
    profile_name: str = ""  # "" means Global / keep the current profile
    process_names: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    model: str = ""
    seconds: float = 0.0

    def summary_lines(self, leader_key: str | None = None) -> list[tuple[str, str]]:
        binding = self.binding
        hold_name = "Hold does" if binding.is_tap_hold() else "Action"
        lines = [("Trigger", trigger_text(binding, leader_key)), (hold_name, action_type_label(binding.action.type))]
        lines.extend(_detail_lines(binding.action))
        for app, rule in binding.app_rules.items():
            lines.append((app, {"keyware": "Keyware only", "app": "App only", "both": "Both"}.get(rule, rule)))
        if binding.hold_press:
            lines.append(("While held", f"keeps {format_keys(binding.action.keys)} down"))
        if binding.is_tap_hold():
            tap = binding.tap_action
            if tap is None:
                lines.append(("Tap does", f"types {format_keys(binding.keys)} as usual"))
            else:
                lines.append(("Tap does", action_type_label(tap.type)))
                lines.extend(_detail_lines(tap))
        if self.profile_name:
            procs = ", ".join(self.process_names)
            lines.append(("Profile", f"{self.profile_name} ({procs})" if procs else self.profile_name))
        else:
            lines.append(("Profile", "Global"))
        if self.label:
            lines.insert(0, ("Label", self.label))
        return lines


def _detail_lines(action: Action) -> list[tuple[str, str]]:
    if action.type == "open_url":
        return [("URL", action.url)]
    if action.type == "open_app":
        lines = [("App", " ".join([action.app, *action.args]))]
        if action.fallback:
            lines.append(("If not installed", action.fallback))
        return lines
    if action.type == "type_text":
        text = action.text if len(action.text) <= 120 else action.text[:117] + "..."
        return [("Text", text.replace("\n", " ⏎ "))]
    if action.type == "key_combo":
        return [("Keys", format_keys(action.keys))]
    if action.type == "macro":
        return [("Steps", "\n".join(f"{index}. {step_summary(step)}" for index, step in enumerate(action.steps, 1)))]
    if action.type == "open_path":
        return [("Opens", action.path)]
    if action.type == "media":
        return [("Media", media_text(action.media, action.count))]
    if action.type == "lock_screen":
        return [("Does", "Locks Windows (Win + L)")]
    if action.type == "screenshot":
        return [("Does", "Full-screen screenshot to Pictures › Screenshots")]
    if action.type == "run_command":
        return [("Command", action.command), ("Safety", "Keyware asks you to confirm the first time it runs")]
    if action.type == "play":
        return [("Plays", f"“{action.text}” on {SERVICE_LABELS.get(action.service, action.service)}")]
    if action.type == "auto_click":
        how = "toggle on/off" if action.click_mode == "toggle" else "while the key is held"
        lines = [("Clicks", f"{action.button.capitalize()} · {int(action.cps)} per second · {how}")]
        if action.max_seconds:
            lines.append(("Safety stop", f"after {int(action.max_seconds)} s"))
        return lines
    return []


def describe_step(step: MacroStep) -> str:
    if step.op == "key":
        return format_key(step.key)
    if step.op == "combo":
        return format_keys(step.keys)
    if step.op == "delay":
        return f"wait {step.ms} ms"
    if step.op == "text":
        short = step.text if len(step.text) <= 40 else step.text[:37] + "..."
        return f"type “{short}”"
    if step.op == "mouse":
        return f"{step.button} click"
    return step.op


# --------------------------------------------------------------------------
# Off-topic check (local, before any network call)
# --------------------------------------------------------------------------

_BINDING_WORDS = {
    "key", "keys", "keyboard", "press", "pressed", "pressing", "presses", "hold", "holding", "held", "holds",
    "tap", "taps", "tapping", "tapped", "click", "clicks", "clicking", "clicker", "autoclick", "autoclicker",
    "autoclicks", "auto-click", "auto-clicker", "shortcut", "shortcuts", "hotkey", "hotkeys", "bind", "binding",
    "bindings", "remap", "map", "chord", "leader", "macro", "trigger", "triggers", "type", "types", "typing",
    "open", "opens", "launch", "launches", "start", "starts", "run", "runs", "search", "searches", "paste",
    "send", "sends", "toggle", "toggles", "spam", "cps", "double", "triple", "combo",
    "ctrl", "control", "alt", "shift", "win", "windows", "cmd", "super", "esc", "escape", "enter", "return",
    "tab", "space", "spacebar", "backspace", "delete", "del", "insert", "home", "end", "pgup", "pgdn",
    "capslock", "caps", "numpad", "f-key", "mouse", "button",
}


def looks_like_binding(text: str) -> bool:
    """Cheap guard against obvious off-topic text ("write me a poem", "what's the weather", gibberish).

    It only says "no" when nothing in the text looks like a key, a shortcut or
    a binding verb. Everything else goes to the model, which can still answer
    not_binding.
    """
    lowered = re.sub(r"['’]\w*", "", (text or "").lower())  # "what's" -> "what", not the key S
    if not lowered.strip():
        return False
    if re.search(r"\b(ctrl|alt|shift|win)\s*\+", lowered) or re.search(r"\+\s*\w", lowered):
        return True
    if re.search(r"\bf([1-9]|1[0-9]|2[0-4])\b", lowered):
        return True
    for token in re.findall(r"[a-z0-9\-]+", lowered):
        if token in _BINDING_WORDS:
            return True
        if len(token) == 1 and token not in ("a", "i"):
            return True  # "R", "Q", "5": a single key name
    return False


# --------------------------------------------------------------------------
# System prompt
# --------------------------------------------------------------------------

# Shared with the proxy (site/api/_lib/prompt.js, written by tools/sync_prompt.py).
# Bump PROMPT_VERSION whenever the prompt text changes.
PROMPT_VERSION = "2026.11.02-1"

SYSTEM_PROMPT = r"""You are Keyware's binding builder. Keyware is a Windows key remapper. A user describes, in plain words, what a key should do. You reply with ONE JSON object describing a single binding. You never run anything; a human reviews your JSON in an editor and saves it.

# Output (strict JSON, no prose, no markdown fences)
{
  "ok": true,
  "label": "short human label, max 40 chars",
  "profile": {"scope": "global"}  OR  {"scope": "app", "name": "Gaming", "process_names": ["javaw.exe", "minecraft.exe"]},
  "trigger": {"mode": "tap" | "hold" | "double_tap" | "triple_tap" | "chord" | "leader", "keys": ["..."], "hold_ms": 800},
  "action": { ...one of the action shapes below... },
  "tap_action": { ...optional, only with mode "hold": what a SHORT tap does (see Tap vs hold) },
  "app_rules": {"cursor.exe": "app"}  (optional, see Per-app rules),
  "notes": "optional, one short sentence for the user (assumptions, placeholders)"
}
If the user asks for SEVERAL bindings in one description, reply instead with up to 8 items, each shaped like the single object above (without "ok"):
{"ok": true, "bindings": [ {"label": "...", "profile": {...}, "trigger": {...}, "action": {...}, "notes": ""}, ... ]}
Keep each binding exactly as the user asked; never drop or merge one, never change a key to dodge a clash.
If two of them use the same trigger, or a trigger looks taken, still return every binding: Keyware flags clashes and the user picks. Never refuse the whole reply over a clash.
If the request is impossible or unsafe, reply:
{"ok": false, "error": "one short sentence why"}
If the text is NOT about key bindings at all (questions, chat, poems, weather, math, gibberish, random words), reply exactly:
{"ok": false, "not_binding": true, "error": "Not a key binding."}

# Key names (lowercase canonical names ONLY)
Letters a-z, digits 0-9, f1-f24, modifiers ctrl alt shift win,
esc enter tab space backspace delete insert home end page_up page_down up down left right
caps_lock print_screen menu, numpad0-numpad9 numpad_add numpad_subtract numpad_multiply numpad_divide numpad_decimal,
semicolon equals comma minus period slash backtick bracket_left bracket_right backslash quote,
media keys volume_mute volume_up volume_down media_play_pause media_next media_prev media_stop,
copilot = the Copilot key on newer Windows laptops ("Copilot key", "Copilot button", "co-pilot"). It is a TRIGGER only, never part of an action. Use mode tap/hold/double_tap/triple_tap with keys ["copilot"], never a chord, never ["win","shift","f23"].
"Control"→ctrl, "Windows"/"Win"→win, "Escape"→esc, "Return"→enter, "PgUp"→page_up, "Del"→delete, "Caps"/"Caps Lock"→caps_lock.
Macropads and stream decks usually send f13-f24 (often with ctrl/alt/shift); treat them like any other key.
Never invent other names (no "mouse4", "fn").

# Triggers (what the user presses)
- "chord": one or more modifiers PLUS exactly one normal key. keys e.g. ["ctrl","shift","r"]. Use for anything written like "Ctrl+Q", "Ctrl + Alt + T", "Win+Shift+Y".
- "tap": a single normal key pressed quickly, e.g. "F2 opens YouTube" → mode tap, keys ["f2"]. The key's normal function is replaced. Modifiers alone are NOT allowed as a trigger.
- "hold": hold one normal key for hold_ms (300-5000, default 800). A short press still types the key. "hold P for 2 seconds" → mode hold, keys ["p"], hold_ms 2000. Holds of different lengths on the same key are separate tiers.
- Tap vs hold on ONE key ("tap X does A, hold X does B", "Caps Lock: Esc when tapped, Ctrl when held", "dual role"): mode "hold", "action" = the HOLD side, "tap_action" = the TAP side, hold_ms = the threshold (100-5000, default 200 for tap vs hold).
  When the hold side should act like a held-down modifier or key (hold = Ctrl, Shift, Alt, Win, or "holds space"), use action key_combo with those keys and add "keep_held": true to the trigger: the keys stay down while the key is held, so hold Caps + C = Ctrl+C.
  Without "keep_held" the hold action runs once when the threshold passes. If the user gives only a hold side with keep_held, leave tap_action out: a tap then types the key itself.
- Turning a key off ("disable Caps Lock", "Caps Lock does nothing", "turn off the Copilot key"): mode tap, action {"type":"none"}. Caps Lock that is bound or turned off no longer toggles; Shift+Caps Lock still toggles it.
- "double_tap" / "triple_tap": tap one normal key 2 or 3 times quickly. keys ["f8"].
- "leader": the user taps their leader key (set in Settings, e.g. F13) then this ONE follow-up key. "leader then Y" → mode leader, keys ["y"]. Do NOT put the leader key itself in keys.
- Exactly one key for tap/hold/double_tap/triple_tap/leader. A chord must have ≥1 modifier and exactly 1 non-modifier.
- The TRIGGER may never be the panic chord Ctrl+Alt+Shift+Esc or Ctrl+Alt+Delete. This rule is ONLY about the trigger. Any other shortcut is fine as the ACTION, e.g. "Ctrl+Alt+M presses Ctrl+Shift+Esc" is valid: trigger chord ["ctrl","alt","m"], action key_combo ["ctrl","shift","esc"] (opens Task Manager). Never refuse a request just because its action uses Esc, Ctrl, Alt or Shift.
- If the user names no trigger key at all, reply ok:false with error "Say which key or shortcut should trigger it."

# Actions (what happens). "type" must be one of:
1. "open_url": {"type":"open_url","url":"https://..."}  — http or https only, no spaces (URL-encode with + or %20).
   Searches: YouTube https://www.youtube.com/results?search_query=WORDS ; Google https://www.google.com/search?q=WORDS ;
   Wikipedia https://en.wikipedia.org/w/index.php?search=WORDS ; Amazon https://www.amazon.com/s?k=WORDS ; Spotify web https://open.spotify.com/search/WORDS .
   Plain sites: "open gmail" → https://mail.google.com , "open YouTube" → https://www.youtube.com .
2. "open_app": {"type":"open_app","app":"notepad.exe"} — an exe name on PATH or a full path. Common: notepad.exe, calc.exe, mspaint.exe, explorer.exe, code (VS Code), cursor.exe (Cursor), chrome.exe, msedge.exe, spotify.exe, discord.exe, slack.exe, steam.exe, obs64.exe, winword.exe (Word), excel.exe, figma.exe, blender.exe, photoshop.exe. No command-line arguments. NEVER cmd, powershell, pwsh, wscript, cscript, mshta, rundll32, regsvr32, reg, regedit, bcdedit, diskpart, shutdown, taskkill, schtasks, netsh, certutil, or any .bat/.cmd/.ps1/.vbs/.js/.hta/.msi/.reg file.
3. "type_text": {"type":"type_text","text":"exact text"} — types literal text (max 8000 chars). Use only when there are no extra key presses.
4. "key_combo": {"type":"key_combo","keys":["ctrl","shift","t"]} — sends one shortcut once (also a single key like ["enter"] or ["f5"]).
5. "macro": {"type":"macro","steps":[...]} — ordered steps, 1-64 of them. Use whenever more than one thing happens (text then Enter, several keys, clicks, waits). Step shapes:
   {"op":"key","key":"enter"}           one key press
   {"op":"combo","keys":["ctrl","c"]}   a shortcut
   {"op":"text","text":"hello"}         literal text
   {"op":"delay","ms":50}               wait 0-60000 ms
   {"op":"mouse","button":"left"}       click at the current mouse position: left | right | middle
   {"op":"url","target":"https://..."}  open a web page in the default browser
   {"op":"app","target":"chrome.exe","args":["https://classroom.google.com"],"fallback":"https://..."}  open an app (args = URLs or files only, never switches like /c or -e). Keyware checks whether the app is installed; if not, it opens any URL args in the default browser, then "fallback" (the app's website, e.g. Discord → https://discord.com/app, VS Code → https://vscode.dev, Spotify → https://open.spotify.com). Always give a fallback when the app has a website.
   {"op":"path","target":"C:\\Users\\Me\\Documents"}  open a file or folder (never .exe/.bat/.ps1/.lnk etc.)
   {"op":"media","key":"volume_mute","count":1}  media/volume key: volume_mute | volume_up | volume_down | media_play_pause | media_next | media_prev | media_stop. count 1-50; each volume step is about 2%.
   {"op":"lock"}  lock the screen        {"op":"screenshot"}  full-screen screenshot to Pictures › Screenshots
   {"op":"wait_window","target":"notepad.exe","text":"","ms":10000}  wait until that app's window (or a window whose title contains "text") is open and bring it to the front; 500-30000 ms. Use this instead of long fixed delays before typing or pressing keys inside an app that was just opened.
   {"op":"play","target":"spotify","text":"Du bist genug"}  play a song/artist/playlist: target spotify | youtube. Keyware uses the Spotify app if installed, otherwise the Spotify web player in the browser, finds the top result and starts it. Fix obvious misspellings of song or artist names ("du bist gueng" → "Du bist genug").
   Put a short delay (30-100 ms) between steps that switch windows or menus; "wait 2 seconds" → {"op":"delay","ms":2000}.
   Multi-step requests ("F6 opens VS Code, Chrome with Classroom and Spotify, and mutes the volume") are ONE macro with one step per thing, in the user's order.
6. "auto_click": {"type":"auto_click","button":"left","cps":10,"click_mode":"hold","max_seconds":0} — an auto-clicker / autoclicker / "spam click" / "rapid click".
   button: left | right | middle (default left). cps: clicks per second, 1-50 (default 10; "fast" ≈ 20).
   click_mode "hold" = clicks only while the trigger key is held down ("hold R to autoclick", "while I hold"). Use trigger mode "tap" (or "chord") with it — NOT trigger "hold", which would wait before starting.
   click_mode "toggle" = one press starts, the next press stops ("F6 toggles", "turn on/off", "press to start and stop"). Use trigger "tap" or "chord".
   With double_tap, triple_tap or leader triggers, click_mode must be "toggle".
   max_seconds: optional safety stop, 0 = none, up to 3600 ("stop after 30 seconds" → 30). Never put clicks inside a macro to fake an auto-clicker.
7. "open_path": {"type":"open_path","path":"%USERPROFILE%\\Downloads"} — open a file or folder. For the user's own folders (Downloads, Documents, Desktop, Pictures, Music, Videos) always use "%USERPROFILE%\\<Folder>", never a made-up user name. Never executables or scripts.
8. "media": {"type":"media","media":"media_play_pause","count":1} — one media/volume key (names as in the media step). "volume up a bit" → volume_up count 5. Keyware cannot set an exact volume, mute one app only, or change the audio output device: for "mute Discord" use volume_mute and say in notes it mutes the whole system.
9. "lock_screen": {"type":"lock_screen"}   10. "screenshot": {"type":"screenshot"}
11. "run_command": {"type":"run_command","command":"code C:\\projects\\site"} — only when the user explicitly asks to run a command or script. One line, max 500 chars. Keyware asks the user to confirm the first time it runs. NEVER shutdown/restart/sign-out, deleting files, formatting, registry/boot/disk changes, changing the audio device, or downloading code: refuse those (ok:false).
12. "play": {"type":"play","service":"spotify","query":"Du bist genug"} — play music or a video by name (spotify | youtube). "play X on YouTube/Spotify" always uses play, never a search URL. Use a macro with a "play" step when other things happen too.
13. "none": {"type":"none"} — the key does nothing (turned off). Also valid as a tap_action ("tap does nothing, hold opens X").
Prefer the simplest shape: one thing → a single action; several things → one macro.

# Profiles
- Global (default): works in every app. Use {"scope":"global"} unless the user names an app/game where it should apply.
- App profile: only while that app is in front. The user's games profile is called "Gaming": "in gaming", "in games", "while gaming", "in Minecraft" → {"scope":"app","name":"Gaming","process_names":["javaw.exe","minecraft.exe"]}. Other apps: Chrome chrome.exe, Edge msedge.exe, Firefox firefox.exe, VS Code code.exe, Cursor cursor.exe, Discord discord.exe, Slack slack.exe, Spotify spotify.exe, Notepad notepad.exe, Word winword.exe, Excel excel.exe, PowerPoint powerpnt.exe, Photoshop photoshop.exe, Premiere Pro "adobe premiere pro.exe", After Effects afterfx.exe, OBS obs64.exe, Figma figma.exe, Blender blender.exe, Roblox robloxplayerbeta.exe, Valorant valorant.exe, Fortnite fortniteclient-win64-shipping.exe.
- An app profile may intentionally override a Global binding on the same key.

# Per-app rules (optional "app_rules": exe name -> mode)
- "keyware" (default for every app not listed): Keyware runs the action and swallows the key.
- "app": in that app Keyware does nothing and the key reaches the app, so the app's own shortcut works.
- "both": Keyware runs the action AND the key reaches the app.
Use it when the user says a shortcut should "work normally", "be left alone" or "still do its own thing" in an app, or should "also" reach an app. Keep the binding Global; do not make an app profile for this. Use exe names from the Profiles list.
"make Ctrl+Shift+S work normally in Cursor" with no other action named: reply with the binding the user already has if they describe it, otherwise a key-off binding {"type":"none"} on that trigger with "app_rules": {"cursor.exe": "app"} and a note that the rule can be added to their existing binding instead.

# Conflict rules (Keyware enforces these; you just produce the binding)
In one profile, one trigger can do only one thing: two chords with the same keys clash, two leader shortcuts with the same follow-up clash, and tap/hold/double-tap/triple-tap on the same key clash — except holds of different lengths, which are tiers. Do not change the user's requested key to dodge a clash; Keyware will show the conflict.

# Personal details
If the user says "my email", "my address", "my name" and you do not know it, use a clear placeholder like "you@example.com" or "YOUR NAME" and say so in notes. Never invent passwords or card numbers; refuse those.

# Safety
Refuse (ok:false) anything meant to delete files, shut down / restart / sign out, change system settings or the default audio device, download code, steal data, or type harmful commands. Only use run_command when the user plainly asks to run a command. Typing a normal message, opening apps/sites/files, media keys, locking and screenshots are fine.

# Examples
User: F2 opens YouTube and searches minecraft
{"ok":true,"label":"YouTube: minecraft","profile":{"scope":"global"},"trigger":{"mode":"tap","keys":["f2"]},"action":{"type":"open_url","url":"https://www.youtube.com/results?search_query=minecraft"},"notes":""}

User: Ctrl+Q types my email then presses enter
{"ok":true,"label":"Type email + Enter","profile":{"scope":"global"},"trigger":{"mode":"chord","keys":["ctrl","q"]},"action":{"type":"macro","steps":[{"op":"text","text":"you@example.com"},{"op":"key","key":"enter"}]},"notes":"Replace you@example.com with your real email."}

User: hold Q for 2 seconds in gaming to press 1, right click, then 2
{"ok":true,"label":"Hotbar 1, use, hotbar 2","profile":{"scope":"app","name":"Gaming","process_names":["javaw.exe","minecraft.exe"]},"trigger":{"mode":"hold","keys":["q"],"hold_ms":2000},"action":{"type":"macro","steps":[{"op":"key","key":"1"},{"op":"delay","ms":50},{"op":"mouse","button":"right"},{"op":"delay","ms":20},{"op":"key","key":"2"}]},"notes":""}

User: hold R to autoclick left at 15 cps in gaming
{"ok":true,"label":"Auto-click left 15/s","profile":{"scope":"app","name":"Gaming","process_names":["javaw.exe","minecraft.exe"]},"trigger":{"mode":"tap","keys":["r"]},"action":{"type":"auto_click","button":"left","cps":15,"click_mode":"hold","max_seconds":0},"notes":"Clicks while R is held."}

User: F6 toggles a right click autoclicker at 20 cps
{"ok":true,"label":"Right auto-click 20/s","profile":{"scope":"global"},"trigger":{"mode":"tap","keys":["f6"]},"action":{"type":"auto_click","button":"right","cps":20,"click_mode":"toggle","max_seconds":0},"notes":"Press F6 again to stop."}

User: ctrl alt n opens notepad
{"ok":true,"label":"Open Notepad","profile":{"scope":"global"},"trigger":{"mode":"chord","keys":["ctrl","alt","n"]},"action":{"type":"open_app","app":"notepad.exe"},"notes":""}

User: double tap F8 to reopen the last closed tab in chrome
{"ok":true,"label":"Reopen closed tab","profile":{"scope":"app","name":"Chrome","process_names":["chrome.exe"]},"trigger":{"mode":"double_tap","keys":["f8"]},"action":{"type":"key_combo","keys":["ctrl","shift","t"]},"notes":""}

User: leader then G searches google for weather
{"ok":true,"label":"Google: weather","profile":{"scope":"global"},"trigger":{"mode":"leader","keys":["g"]},"action":{"type":"open_url","url":"https://www.google.com/search?q=weather"},"notes":"Needs a leader key set in Settings."}

User: in VS Code, ctrl shift L types console.log()
{"ok":true,"label":"Insert console.log","profile":{"scope":"app","name":"Code","process_names":["code.exe"]},"trigger":{"mode":"chord","keys":["ctrl","shift","l"]},"action":{"type":"type_text","text":"console.log();"},"notes":""}

User: triple tap caps lock to type gg then enter, wait half a second, and type wp then enter
{"ok":true,"label":"gg wp","profile":{"scope":"global"},"trigger":{"mode":"triple_tap","keys":["caps_lock"]},"action":{"type":"macro","steps":[{"op":"text","text":"gg"},{"op":"key","key":"enter"},{"op":"delay","ms":500},{"op":"text","text":"wp"},{"op":"key","key":"enter"}]},"notes":""}

User: F6 turns on work mode: opens VS Code, Chrome with Google Classroom and EduPage, and Spotify, and mutes Discord
{"ok":true,"label":"Work mode","profile":{"scope":"global"},"trigger":{"mode":"tap","keys":["f6"]},"action":{"type":"macro","steps":[{"op":"app","target":"code","fallback":"https://vscode.dev"},{"op":"app","target":"chrome.exe","args":["https://classroom.google.com","https://www.edupage.org"],"fallback":"https://classroom.google.com"},{"op":"app","target":"spotify.exe","fallback":"https://open.spotify.com"},{"op":"media","key":"volume_mute","count":1}]},"notes":"Keyware can't mute only Discord, so this mutes the whole system. Replace the EduPage link with your school's."}

User: F5 opens chrome and plays du bist gueng on spotify
{"ok":true,"label":"Play Du bist genug","profile":{"scope":"global"},"trigger":{"mode":"tap","keys":["f5"]},"action":{"type":"macro","steps":[{"op":"app","target":"chrome.exe","fallback":"https://www.google.com"},{"op":"play","target":"spotify","text":"Du bist genug"}]},"notes":"Spotify plays in the browser if the app isn't installed."}

User: ctrl+alt+n opens notepad and types hello when it's open
{"ok":true,"label":"Notepad hello","profile":{"scope":"global"},"trigger":{"mode":"chord","keys":["ctrl","alt","n"]},"action":{"type":"macro","steps":[{"op":"app","target":"notepad.exe"},{"op":"wait_window","target":"notepad.exe","text":"","ms":10000},{"op":"text","text":"hello"}]},"notes":""}

User: F7 locks my pc
{"ok":true,"label":"Lock PC","profile":{"scope":"global"},"trigger":{"mode":"tap","keys":["f7"]},"action":{"type":"lock_screen"},"notes":""}

User: caps lock is esc when tapped and ctrl when held
{"ok":true,"label":"Caps: Esc / Ctrl","profile":{"scope":"global"},"trigger":{"mode":"hold","keys":["caps_lock"],"hold_ms":200,"keep_held":true},"action":{"type":"key_combo","keys":["ctrl"]},"tap_action":{"type":"key_combo","keys":["esc"]},"notes":"Shift+Caps Lock still toggles Caps Lock."}

User: turn off caps lock
{"ok":true,"label":"Caps Lock off","profile":{"scope":"global"},"trigger":{"mode":"tap","keys":["caps_lock"]},"action":{"type":"none"},"notes":"Shift+Caps Lock still toggles Caps Lock."}

User: make the copilot key open cursor
{"ok":true,"label":"Copilot key: Cursor","profile":{"scope":"global"},"trigger":{"mode":"tap","keys":["copilot"]},"action":{"type":"open_app","app":"cursor.exe"},"notes":""}

User: copilot key acts as right ctrl
{"ok":true,"label":"Copilot key = Ctrl","profile":{"scope":"global"},"trigger":{"mode":"hold","keys":["copilot"],"hold_ms":200,"keep_held":true},"action":{"type":"key_combo","keys":["ctrl"]},"notes":"Keyware sends Ctrl (it does not tell left and right Ctrl apart)."}

User: tap F9 to play/pause, hold it to open spotify
{"ok":true,"label":"F9: play / Spotify","profile":{"scope":"global"},"trigger":{"mode":"hold","keys":["f9"],"hold_ms":400},"action":{"type":"open_app","app":"spotify.exe"},"tap_action":{"type":"media","media":"media_play_pause","count":1},"notes":""}

User: in OBS, F13 starts recording with ctrl shift r
{"ok":true,"label":"OBS: record","profile":{"scope":"app","name":"OBS","process_names":["obs64.exe"]},"trigger":{"mode":"tap","keys":["f13"]},"action":{"type":"key_combo","keys":["ctrl","shift","r"]},"notes":"Set the same shortcut in OBS Settings › Hotkeys."}

User: ctrl shift s saves all files, but let it work normally in Cursor
{"ok":true,"label":"Save all","profile":{"scope":"global"},"trigger":{"mode":"chord","keys":["ctrl","shift","s"]},"action":{"type":"key_combo","keys":["ctrl","k","s"]},"app_rules":{"cursor.exe":"app"},"notes":"In Cursor the key goes straight to Cursor."}

User: F12 shuts down my computer
{"ok":false,"error":"Keyware won't shut down, restart or sign out."}

User: F9 opens YouTube, F10 types my email, and holding F8 autoclicks at 15 cps
{"ok":true,"bindings":[{"label":"Open YouTube","profile":{"scope":"global"},"trigger":{"mode":"tap","keys":["f9"]},"action":{"type":"open_url","url":"https://www.youtube.com"},"notes":""},{"label":"Type email","profile":{"scope":"global"},"trigger":{"mode":"tap","keys":["f10"]},"action":{"type":"type_text","text":"you@example.com"},"notes":"Replace you@example.com with your real email."},{"label":"Auto-click 15/s","profile":{"scope":"global"},"trigger":{"mode":"tap","keys":["f8"]},"action":{"type":"auto_click","button":"left","cps":15,"click_mode":"hold","max_seconds":0},"notes":""}]}

User: what's the weather tomorrow
{"ok":false,"not_binding":true,"error":"Not a key binding."}

User: write me a poem about cats
{"ok":false,"not_binding":true,"error":"Not a key binding."}

User: F9 opens powershell and deletes my temp folder
{"ok":false,"error":"Keyware will not run shell commands or delete files."}

User: make it nice
{"ok":false,"error":"Say which key or shortcut should trigger it, and what it should do."}

Reply with the JSON object only."""


# --------------------------------------------------------------------------
# Keys (never logged)
# --------------------------------------------------------------------------


DEV_FLAG = "KEYWARE_DEV_KEYS"


def dev_keys_enabled(env: dict[str, str] | None = None) -> bool:
    """Only from source (never the built exe), and only with KEYWARE_DEV_KEYS=1."""
    source = os.environ if env is None else env
    return source.get(DEV_FLAG) == "1" and not getattr(sys, "frozen", False)


def load_keys(env: dict[str, str] | None = None) -> dict[str, str]:
    """Developer keys from the environment, behind the dev flag. The shipped app returns {}."""
    source = dict(os.environ if env is None else env)
    if not dev_keys_enabled(source):
        return {}
    return {name: source[name] for name in ("GEMINI_API_KEY", "GROQ_API_KEY") if source.get(name)}


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------

Transport = Callable[[str, dict, bytes, float], dict]


class _HTTPFailure(Exception):
    def __init__(self, status: int, retryable: bool):
        self.status = status
        self.retryable = retryable
        super().__init__(f"HTTP {status}")


def urllib_transport(url: str, headers: dict, body: bytes, timeout: float) -> dict:
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise _HTTPFailure(exc.code, True) from None


class ProxyReply(Exception):
    """Internal: the proxy answered with an HTTP error."""

    def __init__(self, status: int, data: dict):
        self.status = status
        self.data = data
        super().__init__(f"HTTP {status}")


def urllib_proxy_transport(url: str, headers: dict, body: bytes, timeout: float) -> dict:
    """POST to the proxy. Raises ProxyReply for an HTTP error (with its JSON body when there is one)."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read(256 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read(16 * 1024).decode("utf-8"))
        except Exception:
            data = {}
        raise ProxyReply(exc.code, data if isinstance(data, dict) else {}) from None


def _scrub(text: str, keys: dict[str, str]) -> str:
    for value in keys.values():
        if value:
            text = text.replace(value, "***")
    return re.sub(r"(AIza[0-9A-Za-z_\-]{10,}|gsk_[0-9A-Za-z]{10,})", "***", text)


# --------------------------------------------------------------------------
# Parsing and validation
# --------------------------------------------------------------------------

_MODE_MAP = {
    "tap": ("tap", 1),
    "hold": ("hold", 1),
    "double_tap": ("tap_dance", 2),
    "double-tap": ("tap_dance", 2),
    "doubletap": ("tap_dance", 2),
    "triple_tap": ("tap_dance", 3),
    "triple-tap": ("tap_dance", 3),
    "tripletap": ("tap_dance", 3),
    "tap_dance": ("tap_dance", 2),
    "chord": ("chord", 1),
    "leader": ("leader", 1),
}

BLOCKED_APPS = frozenset(
    {
        "cmd", "powershell", "powershell_ise", "pwsh", "wscript", "cscript", "mshta", "rundll32", "regsvr32",
        "reg", "regedit", "regedt32", "bcdedit", "diskpart", "format", "shutdown", "schtasks", "sc", "net",
        "net1", "netsh", "taskkill", "wmic", "certutil", "bitsadmin", "msiexec", "vssadmin", "cipher", "takeown",
        "icacls", "attrib", "del", "erase", "rmdir", "rd", "bash", "wsl", "sh", "curl", "wget", "ftp", "tftp",
        "msbuild", "installutil", "forfiles", "control", "gpedit", "secpol", "wevtutil", "fsutil",
    }
)
BLOCKED_EXTENSIONS = (".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta",
                      ".msi", ".msp", ".reg", ".scr", ".cpl", ".inf", ".jar", ".py", ".pyw", ".com", ".pif")
_DANGEROUS_TEXT = re.compile(
    r"(powershell|pwsh|\bcmd(\.exe)?\s*/[ck]\b|\brm\s+-[rf]|\bdel\s+/|\brd\s+/s|\brmdir\s+/s|\bformat\s+[a-z]:"
    r"|\bshutdown\s+/|\breg\s+(add|delete)\b|\bbcdedit\b|\bdiskpart\b|invoke-(webrequest|expression|restmethod)"
    r"|\biex\b|\|\s*(sh|bash)\b|\bnet\s+user\b|\bcertutil\b|\bmshta\b|\brundll32\b|\bregsvr32\b"
    r"|\bvssadmin\b|\bschtasks\b|\bcurl\s+.*-o\b|start-process|set-executionpolicy|downloadstring)",
    re.IGNORECASE,
)
_BLOCKED_COMBOS = (frozenset({"ctrl", "alt", "delete"}),)
_ALLOWED_TOP = {"ok", "label", "profile", "trigger", "action", "notes", "error", "not_binding", "bindings", "tap_action", "app_rules"}
_ALLOWED_ITEM = {"label", "profile", "trigger", "action", "notes", "ok", "tap_action", "app_rules"}


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    return text


def _keys(raw: Any, what: str) -> list[str]:
    if isinstance(raw, str):
        raw = [part for part in raw.split("+")]
    if not isinstance(raw, list) or not raw:
        raise AIBinderError(f"The model gave no {what}.")
    cleaned: list[str] = []
    for item in raw:
        name = canonicalize(str(item))
        if name is None:
            raise AIBinderError(f"Unknown key “{item}” in the {what}.")
        if name not in cleaned:
            cleaned.append(name)
    return ordered_keys(cleaned)


def _check_combo(keys: list[str]) -> None:
    if set(keys) == set(PANIC_KEYS):
        raise AIBinderError("Ctrl + Alt + Shift + Esc is the panic switch and cannot be used.", final=True)
    for blocked in _BLOCKED_COMBOS:
        if blocked <= set(keys):
            raise AIBinderError(f"{format_keys(sorted(blocked))} is not allowed.", final=True)


def _check_text(text: str) -> None:
    if _DANGEROUS_TEXT.search(text):
        raise AIBinderError(final=True, message="Rejected: the text looks like a system command.")


def _check_app(app: str) -> None:
    name = app.strip().strip('"').replace("/", "\\").rsplit("\\", 1)[-1].lower()
    stem = name[:-4] if name.endswith(".exe") else name
    if stem in BLOCKED_APPS or name.endswith(BLOCKED_EXTENSIONS):
        raise AIBinderError(final=True, message=f"Rejected: Keyware will not launch {name}.")
    if re.search(r"\s[/-]\w", app):
        raise AIBinderError(final=True, message="Rejected: apps cannot take command-line arguments.")


def _parse_action(raw: Any) -> Action:
    if not isinstance(raw, dict):
        raise AIBinderError("The model gave no action.")
    kind = str(raw.get("type", "")).strip().lower()
    if kind not in ACTION_TYPES:
        raise AIBinderError(final=True, message=f"Rejected unknown action “{kind or '?'}”.")
    if kind == "open_url":
        url = str(raw.get("url", "")).strip()
        if not url.lower().startswith(("http://", "https://")):
            raise AIBinderError(final=True, message="Rejected: only http and https links are allowed.")
        return Action(type=kind, url=url)
    if kind == "open_app":
        app = str(raw.get("app", "")).strip()
        _check_app(app)
        action = Action(type=kind, app=app, args=_str_list(raw.get("args")), fallback=str(raw.get("fallback", "") or "").strip())
        return _checked(action)
    if kind == "open_path":
        return _checked(Action(type=kind, path=str(raw.get("path", "") or "")))
    if kind == "media":
        return _checked(Action(type=kind, media=_media_name(raw.get("media") or raw.get("key")),
                               count=_count(raw.get("count"))))
    if kind in ("lock_screen", "screenshot", "none"):
        return Action(type=kind)
    if kind == "run_command":
        command = str(raw.get("command", "") or "")
        return _checked(Action(type=kind, command=command))
    if kind == "play":
        service = str(raw.get("service", "") or "").strip().lower()
        return _checked(Action(type=kind, service=service, text=str(raw.get("query", raw.get("text", "")) or "")))
    if kind == "type_text":
        text = str(raw.get("text", ""))
        _check_text(text)
        return Action(type=kind, text=text)
    if kind == "key_combo":
        keys = _keys(raw.get("keys"), "shortcut")
        _check_combo(keys)
        return Action(type=kind, keys=keys)
    if kind == "auto_click":
        return _parse_autoclick(raw)
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise AIBinderError("The macro has no steps.")
    steps: list[MacroStep] = []
    for item in steps_raw:
        if not isinstance(item, dict):
            raise AIBinderError("A macro step is malformed.")
        op = str(item.get("op", "")).strip().lower()
        if op not in MACRO_OPS:
            raise AIBinderError(final=True, message=f"Rejected unknown macro step “{op or '?'}”.")
        if op == "key":
            keys = _keys([item.get("key", "")], "macro key")
            steps.append(MacroStep(op="key", key=keys[0]))
        elif op == "combo":
            keys = _keys(item.get("keys"), "macro shortcut")
            _check_combo(keys)
            steps.append(MacroStep(op="combo", keys=keys))
        elif op == "delay":
            try:
                ms = int(item.get("ms", 0))
            except (TypeError, ValueError):
                raise AIBinderError("A macro delay is not a number.") from None
            steps.append(MacroStep(op="delay", ms=ms))
        elif op == "text":
            text = str(item.get("text", ""))
            _check_text(text)
            steps.append(MacroStep(op="text", text=text))
        elif op == "mouse":
            button = str(item.get("button", "left")).strip().lower()
            if button not in MOUSE_BUTTONS:
                raise AIBinderError("Mouse button must be left, right, or middle.")
            steps.append(MacroStep(op="mouse", button=button))
        else:
            steps.append(_parse_new_step(op, item))
    if len(steps) > MACRO_MAX_STEPS:
        raise AIBinderError(final=True, message=f"Rejected: macros are limited to {MACRO_MAX_STEPS} steps.")
    # Win + R followed by typed text is a command prompt in disguise.
    for index, step in enumerate(steps):
        if step.op == "combo" and set(step.keys) == {"win", "r"}:
            if any(later.op == "text" for later in steps[index + 1 :]):
                raise AIBinderError(final=True, message="Rejected: typing into the Run box is not allowed.")
    return Action(type="macro", steps=steps)


def _checked(action: Action) -> Action:
    try:
        validate_action(action)
    except ValueError as exc:
        raise AIBinderError(final=True, message=f"Rejected: {exc}") from None
    return action


def _str_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list):
        raise AIBinderError("App arguments must be a list.")
    return [str(item) for item in value[:9] if isinstance(item, (str, int, float))]


def _count(value: Any) -> int:
    if value is None or value == "":
        return 1
    if isinstance(value, bool):
        raise AIBinderError("A repeat count is not a number.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise AIBinderError("A repeat count is not a number.") from None
    if not math.isfinite(number):
        raise AIBinderError("A repeat count is not a number.")
    return max(1, min(MEDIA_MAX_COUNT, int(round(number))))


_MEDIA_WORDS = {
    "mute": "volume_mute", "unmute": "volume_mute", "volume_mute": "volume_mute",
    "volume_up": "volume_up", "vol_up": "volume_up", "louder": "volume_up",
    "volume_down": "volume_down", "vol_down": "volume_down", "quieter": "volume_down",
    "play_pause": "media_play_pause", "play": "media_play_pause", "pause": "media_play_pause",
    "media_play_pause": "media_play_pause", "next": "media_next", "media_next": "media_next",
    "next_track": "media_next", "previous": "media_prev", "prev": "media_prev", "media_prev": "media_prev",
    "previous_track": "media_prev", "stop": "media_stop", "media_stop": "media_stop",
}


def _media_name(value: Any) -> str:
    word = "_".join(str(value or "").strip().lower().replace("-", " ").replace("/", " ").split())
    name = _MEDIA_WORDS.get(word)
    if name is None:
        raise AIBinderError(final=True, message=f"Rejected unknown media key “{str(value)[:30]}”.")
    return name


def _parse_new_step(op: str, item: dict) -> MacroStep:
    """v1.3.3 macro steps: open things, media keys, lock, screenshot, wait for a window, play music."""
    if op == "url":
        step = MacroStep(op=op, target=str(item.get("target", item.get("url", "")) or "").strip())
        if not step.target.lower().startswith(("http://", "https://", "spotify:")):
            raise AIBinderError(final=True, message="Rejected: only http and https links are allowed.")
    elif op == "app":
        target = str(item.get("target", item.get("app", "")) or "").strip()
        _check_app(target)
        args = _str_list(item.get("args"))
        # From the AI, app arguments are links or files only (the prompt says so); a switch such as
        # /c, -e or --disable-web-security is refused. People can still add switches by hand.
        if any(arg.strip().startswith(("-", "/")) for arg in args):
            raise AIBinderError(final=True, message="Rejected: apps from Text mode can only open links or files.")
        step = MacroStep(op=op, target=target, args=args,
                         fallback=str(item.get("fallback", "") or "").strip())
    elif op == "path":
        step = MacroStep(op=op, target=str(item.get("target", item.get("path", "")) or ""))
    elif op == "media":
        step = MacroStep(op=op, key=_media_name(item.get("key") or item.get("media")), count=_count(item.get("count")))
    elif op == "wait_window":
        try:
            ms = int(float(item.get("ms", 10_000) or 10_000))
        except (TypeError, ValueError, OverflowError):
            raise AIBinderError("A wait step has a bad timeout.") from None
        step = MacroStep(op=op, target=str(item.get("target", item.get("app", "")) or "").strip(),
                         text=str(item.get("text", item.get("title", "")) or "").strip(),
                         ms=max(WAIT_MIN_MS, min(WAIT_MAX_MS, ms)))
        if step.target:
            _check_app(step.target)
    elif op == "play":
        step = MacroStep(op=op, target=str(item.get("target", item.get("service", "")) or "").strip().lower(),
                         text=str(item.get("text", item.get("query", "")) or ""))
    else:  # lock, screenshot
        step = MacroStep(op=op)
    try:
        validate_step(step)
    except ValueError as exc:
        raise AIBinderError(final=True, message=f"Rejected: {exc}") from None
    return step


def _number(value: Any, default: float, what: str) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        raise AIBinderError(f"The auto-click {what} is not a number.")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise AIBinderError(f"The auto-click {what} is not a number.") from None
    if not math.isfinite(number):
        raise AIBinderError(f"The auto-click {what} is not a number.")
    return number


def _parse_autoclick(raw: dict) -> Action:
    button = str(raw.get("button", "left") or "left").strip().lower()
    if button not in MOUSE_BUTTONS:
        raise AIBinderError("Mouse button must be left, right, or middle.")
    cps = int(round(_number(raw.get("cps"), AUTOCLICK_DEFAULT_CPS, "speed")))
    cps = min(AUTOCLICK_MAX_CPS, max(AUTOCLICK_MIN_CPS, cps))
    mode = str(raw.get("click_mode", raw.get("mode", "hold")) or "hold").strip().lower()
    mode = {"while_held": "hold", "held": "hold", "while held": "hold", "toggled": "toggle"}.get(mode, mode)
    if mode not in ("hold", "toggle"):
        raise AIBinderError(final=True, message=f"Rejected unknown auto-click mode “{mode}”.")
    seconds = int(round(_number(raw.get("max_seconds"), 0, "safety stop")))
    seconds = min(AUTOCLICK_MAX_SECONDS, max(0, seconds))
    return Action(type="auto_click", button=button, cps=cps, click_mode=mode, max_seconds=seconds)


def _load_reply(text: str) -> dict:
    try:
        data = json.loads(_strip_fences(text or ""))
    except (json.JSONDecodeError, ValueError, RecursionError):
        raise AIBinderError("The model's reply was not valid JSON.") from None
    if not isinstance(data, dict):
        raise AIBinderError("The model's reply was not a JSON object.")
    if data.get("not_binding") is True:
        raise AIBinderError(NOT_BINDING_MESSAGE, not_binding=True)
    if data.get("ok") is False:
        reason = " ".join(str(data.get("error") or "The model could not make a binding from that.").split())
        raise AIBinderError(reason[:200], final=True)
    unknown = set(data) - _ALLOWED_TOP
    if unknown:
        raise AIBinderError(final=True, message=f"Rejected unknown field “{sorted(unknown, key=str)[0]}”.")
    return data


def parse_batch(text: str, config: Config | None = None) -> list:
    """Strict JSON -> a list of Proposal (valid) and Rejected (shown, never saved) items.

    Raises AIBinderError when the whole reply is unusable or every item failed.
    """
    data = _load_reply(text)
    if "bindings" not in data:
        return [_parse_item(data, config)]
    raw_items = data.get("bindings")
    if not isinstance(raw_items, list) or not raw_items:
        raise AIBinderError("The model gave no bindings.")
    if len(raw_items) > MAX_BATCH:
        raise AIBinderError(final=True, message=f"Too many bindings at once. Ask for {MAX_BATCH} or fewer.")
    items: list = []
    first_error: AIBinderError | None = None
    for raw in raw_items:
        if not isinstance(raw, dict):
            error = AIBinderError("A binding in the reply is malformed.")
            items.append(Rejected(label="?", error=str(error)))
            first_error = first_error or error
            continue
        try:
            unknown = set(raw) - _ALLOWED_ITEM
            if unknown:
                raise AIBinderError(final=True, message=f"Rejected unknown field “{sorted(unknown, key=str)[0]}”.")
            if raw.get("ok") is False:
                raise AIBinderError(" ".join(str(raw.get("error") or "Refused.").split())[:200], final=True)
            items.append(_parse_item(raw, config))
        except AIBinderError as exc:
            first_error = first_error or exc
            items.append(Rejected(label=_safe_label(raw), error=str(exc), trigger=_safe_trigger(raw)))
    if not any(isinstance(item, Proposal) for item in items):
        raise first_error or AIBinderError("The model gave no bindings.")
    return items


def parse_reply(text: str, config: Config | None = None) -> Proposal:
    """Strict JSON from the model -> one validated Proposal. Raises AIBinderError."""
    items = parse_batch(text, config)
    for item in items:
        if isinstance(item, Rejected):
            raise AIBinderError(item.error)
    return items[0]


def _safe_label(raw: dict) -> str:
    return " ".join(str(raw.get("label", "") or "").split())[:40] or "Binding"


def _safe_trigger(raw: dict) -> str:
    trigger = raw.get("trigger")
    if not isinstance(trigger, dict) or not isinstance(trigger.get("keys"), list):
        return ""
    names = [canonicalize(str(key)) for key in trigger["keys"][:5]]
    if any(name is None for name in names):
        return ""
    return format_keys(names)


def _parse_item(data: dict, config: Config | None) -> Proposal:
    trigger = data.get("trigger")
    if not isinstance(trigger, dict):
        raise AIBinderError("The model gave no trigger.")
    mode_raw = str(trigger.get("mode", "")).strip().lower()
    if mode_raw not in _MODE_MAP:
        raise AIBinderError(final=True, message=f"Rejected unknown trigger mode “{mode_raw or '?'}”.")
    mode, taps = _MODE_MAP[mode_raw]
    if mode == "tap_dance" and mode_raw == "tap_dance":
        try:
            taps = int(trigger.get("taps", 2))
        except (TypeError, ValueError):
            taps = 2
    keys = _keys(trigger.get("keys"), "trigger")
    _check_combo(keys)
    if mode == "chord" and not any(key in MODIFIERS for key in keys):
        # "Ctrl" missing but several normal keys is a model slip; one plain key means tap.
        if len(keys) == 1:
            mode = "tap"
    if "copilot" in keys:
        # The Copilot key is one key: never a chord, never with modifiers.
        keys = ["copilot"]
        if mode == "chord":
            mode = "tap"
    tap_raw = data.get("tap_action")
    if tap_raw is None and isinstance(trigger.get("tap_action"), dict):
        tap_raw = trigger.get("tap_action")
    keep_held = trigger.get("keep_held") in (True, "true", 1)
    tap_hold = mode == "hold" and (isinstance(tap_raw, dict) or keep_held)
    hold_ms = 800
    if mode == "hold":
        default = TAP_HOLD_DEFAULT_MS if tap_hold else 800
        try:
            hold_ms = int(trigger.get("hold_ms", default) or default)
        except (TypeError, ValueError):
            hold_ms = default
        hold_ms = min(5000, max(TAP_HOLD_MIN_MS if tap_hold else 300, hold_ms))

    action = _parse_action(data.get("action"))
    tap_action = _parse_action(tap_raw) if mode == "hold" and isinstance(tap_raw, dict) else None
    hold_press = bool(mode == "hold" and keep_held and action.type == "key_combo")
    if mode == "hold" and tap_action is None and not hold_press:
        hold_ms = max(300, hold_ms)  # a plain long press
    clamp_note = ""
    if action.type == "auto_click":
        if mode == "hold" and action.click_mode == "hold" and "hold_ms" not in trigger:
            # "Hold R to autoclick": click from key-down, not after a hold delay.
            mode = "tap"
        try:
            asked = float((data.get("action") or {}).get("cps", action.cps))
        except (TypeError, ValueError):
            asked = action.cps
        if asked != action.cps:
            clamp_note = f"Speed limited to {action.cps} clicks per second (Keyware allows 1-50)."
    label = " ".join(str(data.get("label", "") or "").split())[:40]
    app_rules = parse_app_rules(data.get("app_rules"))
    binding = Binding(id=new_id("bind"), enabled=True, mode=mode, keys=keys, hold_ms=hold_ms, taps=taps,
                      label=label, action=action, tap_action=tap_action, hold_press=hold_press,
                      app_rules=app_rules)
    try:
        validate_binding(binding, config or Config(), check_leader=False)
    except ValueError as exc:
        raise AIBinderError(str(exc)) from None

    notes: list[str] = []
    note = " ".join(str(data.get("notes", "") or "").split())
    if note:
        notes.append(note[:200])
    if clamp_note:
        notes.append(clamp_note)
    profile_name = ""
    processes: list[str] = []
    profile = data.get("profile")
    if isinstance(profile, dict) and str(profile.get("scope", "global")).lower() == "app":
        profile_name = " ".join(str(profile.get("name", "") or "").split())[:40]
        raw_procs = profile.get("process_names") or []
        if isinstance(raw_procs, list):
            for item in raw_procs[:6]:
                proc = str(item).strip().lower()
                if re.fullmatch(r"[a-z0-9_.\-]{1,64}\.exe", proc):
                    processes.append(proc)
        if not profile_name and processes:
            profile_name = processes[0][:-4].title()
    if config is not None and mode == "leader" and not config.leader_key:
        notes.append("Set a leader key in Settings before saving a leader shortcut.")
    return Proposal(binding=binding, label=label, profile_name=profile_name, process_names=processes, notes=notes)


def resolve_profile(config: Config, proposal: "Proposal"):
    """The existing app profile a proposal names (by process or name), else None."""
    if not proposal.profile_name:
        return None
    wanted = {name.lower() for name in proposal.process_names}
    for profile in config.profiles:
        if profile.is_global:
            continue
        procs = {name.lower() for name in profile.process_names}
        if (wanted and wanted & procs) or profile.name.lower() == proposal.profile_name.lower():
            return profile
    return None


def batch_targets(items: list, config: Config, current_profile_id: str) -> list[str]:
    """Profile id each item would be saved in ("" for a Rejected item)."""
    targets: list[str] = []
    for item in items:
        if not isinstance(item, Proposal):
            targets.append("")
            continue
        profile = resolve_profile(config, item)
        targets.append(profile.id if profile is not None else current_profile_id)
    return targets


def batch_flags(items: list, config: Config, current_profile_id: str) -> list[str]:
    """One message per item; "" means it can be saved as is.

    Flags: a rejected item (bad key, panic chord, unsafe action), a trigger
    already used in the target profile, and a trigger repeated inside the list.
    Nothing is ever overwritten: the user removes flagged items first.
    """
    from engine.conflicts import find_conflicts

    targets = batch_targets(items, config, current_profile_id)
    flags: list[str] = []
    accepted: list[tuple[int, str, Binding]] = []
    for index, item in enumerate(items):
        if not isinstance(item, Proposal):
            flags.append(item.error)
            continue
        profile = config.profile_by_id(targets[index]) or config.global_profile()
        clash = find_conflicts(item.binding, profile.bindings, config.leader_key)
        if clash:
            flags.append(f"{clash[0].reason} ({profile.name})")
            continue
        twin = next(
            (number for number, target, other in accepted
             if target == targets[index] and find_conflicts(item.binding, [other], config.leader_key)),
            None,
        )
        if twin is not None:
            flags.append(f"Same trigger as #{twin + 1} in this list.")
            continue
        flags.append("")
        accepted.append((index, targets[index], item.binding))
    return flags


# --------------------------------------------------------------------------
# The agent
# --------------------------------------------------------------------------


MAX_CONTEXT_TRIGGERS = 60


def existing_triggers(config: Config | None, profile_name: str) -> list[str]:
    """Triggers already used in Global and the profile being edited, e.g. "F9" or "Leader → Y"."""
    if config is None:
        return []
    wanted = (profile_name or "Global").lower()
    seen: list[str] = []
    for profile in config.profiles:
        if not (profile.is_global or profile.name.lower() == wanted):
            continue
        for binding in profile.bindings:
            try:
                text = trigger_text(binding, config.leader_key)
            except Exception:
                continue
            item = text if profile.is_global else f"{text} ({profile.name})"
            if item not in seen:
                seen.append(item)
    return seen[:MAX_CONTEXT_TRIGGERS]


def proxy_context(config: Config | None, profile_name: str) -> dict:
    """What the proxy gets besides the sentence. No names, paths, typed text or URLs from bindings."""
    context: dict[str, Any] = {"app_version": __version__, "prompt_version": PROMPT_VERSION,
                               "current_profile": profile_name or "Global"}
    if config is not None:
        context["leader_key"] = format_key(config.leader_key) if config.leader_key else ""
        context["profiles"] = [{"name": p.name, "process_names": list(p.process_names)} for p in config.profiles][:20]
        context["triggers"] = existing_triggers(config, profile_name)
    return context


def _context_message(config: Config | None, profile_name: str) -> str:
    """Same wording the proxy builds (site/api/_lib/core.js contextMessage)."""
    if config is None:
        return ""
    leader = format_key(config.leader_key) if config.leader_key else "not set"
    profiles = "; ".join(
        f"{p.name} ({', '.join(p.process_names) or 'all apps'})" for p in config.profiles
    )
    text = f"Context: leader key is {leader}. The editor is on profile “{profile_name or 'Global'}”."
    if profiles:
        text += f" Existing profiles: {profiles}."
    triggers = existing_triggers(config, profile_name)
    if triggers:
        text += f" Existing triggers: {'; '.join(triggers)}."
    return text


class DescribeBinder:
    """Turn a sentence into a Proposal. Safe to call off the UI thread."""

    def __init__(
        self,
        providers: tuple[Provider, ...] = DEFAULT_PROVIDERS,
        transport: Transport | None = None,
        keys: dict[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        per_call_timeout: float = PER_CALL_TIMEOUT,
        total_budget: float = TOTAL_BUDGET,
        endpoint: str = "",
        install_id: str = "",
        proxy_transport: Transport | None = None,
    ):
        """keys given (own key, dev keys, tests): call the providers directly.
        Otherwise endpoint: ask Keyware's proxy."""
        self.providers = providers
        self.transport = transport or urllib_transport
        self.proxy_transport = proxy_transport or urllib_proxy_transport
        self._keys = keys
        self.clock = clock
        self.per_call_timeout = per_call_timeout
        self.total_budget = total_budget
        self.endpoint = endpoint
        self.install_id = install_id

    @property
    def uses_proxy(self) -> bool:
        return self._keys is None and bool(self.endpoint)

    def _request(self, provider: Provider, key: str, description: str, context: str, timeout: float) -> str:
        # One system message only: Gemini's OpenAI-compatible endpoint keeps just the last one.
        user = f"{context}\n\nRequest: {description}" if context else description
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
        body: dict[str, Any] = {
            "model": provider.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 1800 if provider.name == "gemini" else 3000,  # macros and batches need room
            "response_format": {"type": "json_object"},
        }
        if provider.name == "groq":
            body["reasoning_effort"] = "low"
        # Groq's edge refuses urllib's default User-Agent with 403.
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": f"Keyware/{__version__}"}
        data = self.transport(provider.url, headers, json.dumps(body).encode("utf-8"), timeout)
        try:
            return str(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError):
            raise AIBinderError("The model returned an empty reply.") from None

    def describe(self, description: str, config: Config | None = None, profile_name: str = "") -> Proposal:
        return self._ask(description, config, profile_name, parse_reply)

    def describe_batch(self, description: str, config: Config | None = None, profile_name: str = "") -> list:
        """Like describe, but one sentence may yield several bindings (Proposal and Rejected items)."""
        return self._ask(description, config, profile_name, parse_batch)

    def _ask(self, description: str, config: Config | None, profile_name: str, parse):
        text = " ".join((description or "").split())
        if not text:
            raise AIBinderError("Describe what the key should do first.")
        if len(text) > MAX_DESCRIPTION:
            raise AIBinderError(f"Keep it under {MAX_DESCRIPTION} characters.")
        if not looks_like_binding(text):
            raise AIBinderError(NOT_BINDING_MESSAGE, not_binding=True)
        if self.uses_proxy:
            return self._ask_proxy(text, config, profile_name, parse)
        keys = self._keys if self._keys is not None else load_keys()
        usable = [p for p in self.providers if keys.get(p.key_env)]
        if not usable:
            raise AIBinderError("No GEMINI_API_KEY or GROQ_API_KEY found.")
        context = _context_message(config, profile_name)
        started = self.clock()
        last = "The AI service did not answer."
        last_code = ""
        for provider in usable:
            left = self.total_budget - (self.clock() - started)
            if left <= 0.5:
                last = "Timed out. Try again."
                break
            try:
                reply = self._request(provider, keys[provider.key_env], text, context, min(self.per_call_timeout, left))
            except _HTTPFailure as exc:
                last = "Rate limited. Try again in a minute." if exc.status == 429 else f"AI service error ({exc.status})."
                last_code = ""
                if exc.status in (400, 401, 403):
                    last, last_code = f"The key was refused ({exc.status}). Check it in Settings.", "key"
                continue
            except AIBinderError as exc:
                last = str(exc)
                continue
            except (TimeoutError, OSError, ValueError) as exc:
                timed_out = isinstance(exc, TimeoutError) or "timed out" in str(exc).lower()
                last = "Timed out. Try again." if timed_out else "Could not reach the AI service."
                last_code = "timeout" if timed_out else "offline"
                continue
            try:
                result = parse(reply, config)
            except AIBinderError as exc:
                if exc.final:
                    raise  # a refusal, unsafe action or off-topic text: another model should not overrule it
                last = str(exc)
                continue
            seconds = round(self.clock() - started, 1)
            for item in result if isinstance(result, list) else [result]:
                if isinstance(item, Proposal):
                    item.model = provider.model
                    item.seconds = seconds
            return result
        raise AIBinderError(_scrub(last, keys), code=last_code)

    def _ask_proxy(self, text: str, config: Config | None, profile_name: str, parse):
        install = self.install_id or str(uuid.uuid4())
        body = json.dumps({"description": text, "context": proxy_context(config, profile_name)}).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": f"Keyware/{__version__}",
                   "X-Keyware-Install": install, "Accept": "application/json"}
        started = self.clock()
        try:
            data = self.proxy_transport(self.endpoint, headers, body, PROXY_TIMEOUT)
        except ProxyReply as exc:
            raise _proxy_error(exc.status, exc.data) from None
        except _HTTPFailure as exc:
            raise _proxy_error(exc.status, {}) from None
        except (TimeoutError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError) or "timed out" in str(exc).lower():
                raise AIBinderError("Timed out. Try again.", code="timeout") from None
            raise AIBinderError(OFFLINE_MESSAGE, code="offline") from None
        if not isinstance(data, dict) or data.get("ok") is not True or not isinstance(data.get("result"), dict):
            raise AIBinderError("The AI service sent a reply Keyware can't read.", code="busy")
        result = parse(json.dumps(data["result"]), config)
        model = str(data.get("model") or "Keyware AI")[:40]
        seconds = round(self.clock() - started, 1)
        for item in result if isinstance(result, list) else [result]:
            if isinstance(item, Proposal):
                item.model = model
                item.seconds = seconds
        return result


def _proxy_error(status: int, data: dict) -> AIBinderError:
    if status == 429:
        return AIBinderError(LIMIT_MESSAGE, code="rate_limited")
    if status in (502, 503, 504) or status >= 500:
        return AIBinderError(BUSY_MESSAGE, code="busy")
    message = " ".join(str((data or {}).get("error") or "").split())[:160]
    if status == 413:
        return AIBinderError(message or f"Keep it under {MAX_DESCRIPTION} characters.")
    return AIBinderError(message or f"AI service error ({status}).")


def binder_for_config(config: Config, **kwargs) -> DescribeBinder:
    """The binder the window uses: own key if set, else dev keys (source only), else the proxy."""
    if config.ai_use_own_key and config.ai_key_protected:
        from assist.secret_store import SecretError, unprotect

        try:
            key = unprotect(config.ai_key_protected)
        except SecretError as exc:
            raise AIBinderError(str(exc), code="key") from None
        return own_key_binder(config.ai_provider, key, **kwargs)
    dev = load_keys()
    if dev:
        return DescribeBinder(keys=dev, **kwargs)
    return DescribeBinder(endpoint=config.ai_endpoint, install_id=config.install_id, **kwargs)


def own_key_binder(provider: str, key: str, **kwargs) -> DescribeBinder:
    name = provider if provider in ("gemini", "groq") else "gemini"
    env = "GEMINI_API_KEY" if name == "gemini" else "GROQ_API_KEY"
    providers = tuple(p for p in DEFAULT_PROVIDERS if p.name == name)
    return DescribeBinder(providers=providers, keys={env: key.strip()}, **kwargs)


def check_own_key(provider: str, key: str, **kwargs) -> str:
    """One small real request with the user's key. Returns the model that answered. Raises AIBinderError."""
    if not (key or "").strip():
        raise AIBinderError("Paste a key first.", code="key")
    proposal = own_key_binder(provider, key, **kwargs).describe("F2 opens YouTube")
    return proposal.model
