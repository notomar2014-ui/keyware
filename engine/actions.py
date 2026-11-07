"""Run binding actions through injected opener, keyboard, and clock seams.

The real Windows backends live here but are never constructed by tests.
They do not use a shell and they do not open a network connection.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from typing import Callable, Protocol

import difflib
import html
import re
import time
import urllib.parse

from engine.models import (
    Action,
    MacroStep,
    validate_action,
    validate_app,
    validate_command,
    validate_link,
    validate_path,
    validate_url,
)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
_SPOTIFY_TRACK = re.compile(r"open\.spotify\.com/(?:intl-[a-z-]+/)?(track|album|playlist|artist)/([A-Za-z0-9]{22})")
_YOUTUBE_ID = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')


class Cancelled(Exception):
    """The panic switch or a pause aborted the current macro."""


class Opener(Protocol):
    """Everything that reaches outside Keyware. Tests use a recording fake.

    Optional extras (checked with getattr so old fakes keep working):
    open_path, run_command, lock_screen, resolve_app, wait_window, fetch_text.
    """

    def open_url(self, url: str) -> None: ...

    def open_app(self, app: str, args: list[str] | None = None) -> None: ...


class Injector(Protocol):
    def press(self, key: str) -> None: ...

    def release(self, key: str) -> None: ...

    def type_text(self, text: str) -> None: ...

    def click(self, button: str) -> None: ...

    def mask(self) -> None: ...


class ActionExecutor:
    def __init__(
        self,
        opener: Opener,
        injector: Injector,
        sleeper: Callable[[float], None],
        rng,
        jitter_min_ms: int = 5,
        jitter_max_ms: int = 15,
    ):
        self.opener = opener
        self.injector = injector
        self.sleeper = sleeper
        self.rng = rng
        self.jitter_min_ms = int(jitter_min_ms)
        self.jitter_max_ms = int(jitter_max_ms)
        self._cancel = threading.Event()
        self._held: list[str] = []
        # Set by the service per action: returns False once another window
        # came to the front, so typing stops instead of landing there.
        self.guard: Callable[[], bool] | None = None
        # Set by the service: asks the user before a command runs the first time.
        self.confirm: Callable[[str], bool] | None = None

    def cancel(self) -> None:
        self._cancel.set()

    def reset(self) -> None:
        self._cancel.clear()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self, action: Action) -> None:
        self._held = []
        try:
            self._run(action)
        except Cancelled:
            return
        finally:
            for key in reversed(self._held):
                try:
                    self.injector.release(key)
                except Exception:
                    pass
            self._held.clear()

    def _run(self, action: Action) -> None:
        self._check()
        if action.type == "open_url":
            self._open_link(action.url)
        elif action.type == "open_app":
            self._open_app(action.app, list(action.args), action.fallback)
        elif action.type in ("open_calculator", "calculator"):
            self._open_app(action.app or "calc.exe", list(action.args), action.fallback)
        elif action.type == "type_text":
            self._type(action.text)
        elif action.type == "key_combo":
            self._combo(list(action.keys))
        elif action.type == "macro":
            for step in action.steps:
                self._step(step)
        elif action.type == "auto_click":
            raise ValueError("Auto-click runs from its trigger key, not as a one-shot action.")
        elif action.type == "open_path":
            self._open_path(action.path)
        elif action.type == "media":
            self._media(action.media, action.count)
        elif action.type == "lock_screen":
            self._lock()
        elif action.type == "screenshot":
            self._combo(["win", "print_screen"])
        elif action.type == "run_command":
            self._run_command(action.command)
        elif action.type == "play":
            self._play(action.service, action.text)
        elif action.type == "none":
            return  # the trigger key is turned off: swallowed, nothing runs
        else:
            raise ValueError(f"Unknown action '{action.type}'.")

    def _step(self, step: MacroStep) -> None:
        self._check()
        if step.op == "key":
            self._tap(step.key)
        elif step.op == "combo":
            self._combo(list(step.keys))
        elif step.op == "delay":
            self.sleeper(max(0, int(step.ms)) / 1000)
            self._check()
        elif step.op == "text":
            self._type(step.text)
        elif step.op == "mouse":
            self.injector.click(step.button or "left")
        elif step.op == "url":
            self._open_link(step.target)
        elif step.op == "app":
            self._open_app(step.target, list(step.args), step.fallback)
        elif step.op == "path":
            self._open_path(step.target)
        elif step.op == "media":
            self._media(step.key, step.count)
        elif step.op == "lock":
            self._lock()
        elif step.op == "screenshot":
            self._combo(["win", "print_screen"])
        elif step.op == "wait_window":
            self._wait_window(step.target, step.text, step.ms)
        elif step.op == "play":
            self._play(step.target, step.text)
        else:
            raise ValueError(f"Unknown macro step '{step.op}'.")

    def _tap(self, key: str) -> None:
        self._check()
        self.injector.press(key)
        self._held.append(key)
        self._jitter()
        self._check()
        self.injector.release(key)
        self._held.remove(key)

    def _combo(self, keys: list[str]) -> None:
        for key in keys:
            self._check()
            self.injector.press(key)
            self._held.append(key)
            self._jitter()
        for key in reversed(keys):
            self._check()
            self.injector.release(key)
            if key in self._held:
                self._held.remove(key)
            self._jitter()

    # --- v1.3.3: things on the computer and inside apps ----------------------

    def _focus_moves(self) -> None:
        """Opening something legitimately changes the window in front; later steps follow it."""
        self.guard = None

    def _extra(self, name: str):
        return getattr(self.opener, name, None)

    def spotify_app(self) -> bool:
        resolve = self._extra("resolve_app")
        return bool(resolve and resolve("spotify"))

    def _open_link(self, url: str) -> None:
        self._check()
        link = validate_link(url)
        if link.lower().startswith("spotify:") and not self.spotify_app():
            link = spotify_web_url(link)  # web first: the Spotify app is not installed
        self.opener.open_url(link)
        self._focus_moves()

    def _open_app(self, app: str, args: list[str], fallback: str) -> None:
        self._check()
        name = validate_app(app)
        resolve = self._extra("resolve_app")
        target = resolve(name) if resolve else name
        if target:
            if args:
                self.opener.open_app(target, args)
            else:
                self.opener.open_app(target)
            self._focus_moves()
            return
        # Not installed: open its links in the default browser, else its website.
        links = [item for item in args if item.lower().startswith(("http://", "https://"))]
        for link in links:
            self.opener.open_url(validate_url(link))
        if not links and fallback:
            self.opener.open_url(validate_url(fallback))
        if not links and not fallback:
            raise ValueError(f"{name} is not installed.")
        self._focus_moves()

    def _open_path(self, path: str) -> None:
        self._check()
        target = validate_path(_expand_vars(os.path.expanduser(path)))
        opener = self._extra("open_path")
        if opener is None:
            raise ValueError("Opening files is not available.")
        opener(target)
        self._focus_moves()

    def _media(self, key: str, count: int) -> None:
        for _ in range(max(1, min(50, int(count)))):
            self._tap(key)

    def _lock(self) -> None:
        self._check()
        lock = self._extra("lock_screen")
        if lock is None:
            raise ValueError("Locking is not available.")
        lock()

    def _run_command(self, command: str) -> None:
        self._check()
        text = validate_command(command)
        if self.confirm is None or not self.confirm(text):
            raise ValueError("Not run: the command was not confirmed.")
        self._check()
        runner = self._extra("run_command")
        if runner is None:
            raise ValueError("Running commands is not available.")
        runner(text)

    def _wait_window(self, process: str, title: str, ms: int) -> None:
        self._check()
        waiter = self._extra("wait_window")
        if waiter is None:
            return
        found = waiter(process, title, max(0.5, min(30.0, int(ms) / 1000)), lambda: self.cancelled)
        self._check()
        if not found:
            raise ValueError(f"Gave up waiting for {title or process}.")
        self._focus_moves()

    def _fetch(self, url: str) -> str:
        fetch = self._extra("fetch_text")
        if fetch is None:
            return ""
        try:
            return str(fetch(url) or "")
        except Exception:
            return ""

    def _play(self, service: str, query: str) -> None:
        """Play a song or video by name. Web first; the Spotify app only when it is installed."""
        self._check()
        words = " ".join((query or "").split())
        if service == "youtube":
            page = self._fetch("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(words))
            found = _YOUTUBE_ID.search(page)
            self._check()
            if found:
                self.opener.open_url(f"https://www.youtube.com/watch?v={found.group(1)}")
            else:
                self.opener.open_url("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(words))
            self._focus_moves()
            return
        if service != "spotify":
            raise ValueError("Play works with Spotify or YouTube.")
        kind_id = find_spotify_item(self._fetch, words)
        self._check()
        if self.spotify_app():
            link = f"spotify:{kind_id[0]}:{kind_id[1]}" if kind_id else "spotify:search:" + urllib.parse.quote(words)
            self.opener.open_url(link)
            self._focus_moves()
            if kind_id:
                # The app opens the item; Enter on its page starts it.
                self._wait_window("spotify.exe", "", 15_000)
                self.sleeper(SPOTIFY_APP_SETTLE)
                self._check()
                self.injector.press("enter")
                self.injector.release("enter")
            return
        if kind_id:
            self.opener.open_url(f"https://open.spotify.com/{kind_id[0]}/{kind_id[1]}?autoplay=true")
        else:
            self.opener.open_url("https://open.spotify.com/search/" + urllib.parse.quote(words))
        self._focus_moves()

    def _jitter(self) -> None:
        low = self.jitter_min_ms
        high = self.jitter_max_ms
        if high < low:
            low, high = high, low
        self.sleeper(self.rng.randint(low, high) / 1000)

    def _type(self, text: str) -> None:
        if not text:
            return
        if self.guard is None:
            self.injector.type_text(text)
            return
        for char in text:  # one character at a time so a focus change stops it
            self._check()
            self.injector.type_text(char)

    def _check(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()
        guard = self.guard
        if guard is not None and not guard():
            raise Cancelled()


def _expand_vars(path: str) -> str:
    """%USERPROFILE%-style variables, the same on every platform. Unknown names stay as typed."""
    return re.sub(r"%([A-Za-z0-9_()]+)%", lambda match: os.environ.get(match.group(1), match.group(0)), path)


SPOTIFY_APP_SETTLE = 2.5  # seconds for the Spotify app to show the item before Enter


def spotify_web_url(link: str) -> str:
    """spotify:track:ID -> https://open.spotify.com/track/ID ; spotify:search:q -> .../search/q"""
    parts = link.split(":")[1:]
    if len(parts) >= 2 and parts[0] == "search":
        return "https://open.spotify.com/search/" + urllib.parse.quote(urllib.parse.unquote(":".join(parts[1:])))
    return "https://open.spotify.com/" + "/".join(urllib.parse.quote(part) for part in parts)


_SPOTIFY_LINK = re.compile(
    r'<a\b[^>]*href="([^"]*open\.spotify\.com[^"]*)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL
)
_SPOTIFY_PATH = re.compile(r"open\.spotify\.com/(?:intl-[a-z-]+/)?(track|album|playlist|artist)/([A-Za-z0-9]{22})")
_RSS_ITEM = re.compile(r"<item>\s*<title>(.*?)</title>\s*<link>(.*?)</link>", re.DOTALL)
_KIND_BONUS = {"track": 0.15, "album": 0.05, "playlist": 0.05, "artist": 0.0}


def _plain(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return " ".join(re.sub(r"[^\w ]+", " ", text.lower()).split())


def spotify_candidates(page: str) -> list[tuple[str, str, str]]:
    """(kind, id, title) for each Spotify result link on a search page, in page order."""
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    links = [(m.group(1), m.group(2)) for m in _SPOTIFY_LINK.finditer(page or "")]
    links += [(m.group(2), m.group(1)) for m in _RSS_ITEM.finditer(page or "")]  # Bing RSS
    for raw_link, raw_title in links:
        link = urllib.parse.unquote(html.unescape(raw_link))
        where = _SPOTIFY_PATH.search(link)
        title = html.unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
        if where and title and where.group(2) not in seen:
            seen.add(where.group(2))
            found.append((where.group(1), where.group(2), title))
    return found


def best_spotify_item(words: str, candidates: list[tuple[str, str, str]]):
    """The result whose name is closest to the words; songs before albums; earlier wins ties."""
    query = _plain(words)
    best = None
    best_score = 0.0
    for index, (kind, ident, title) in enumerate(candidates):
        name, _sep, rest = title.partition(" - ")
        artist = rest.split(" by ", 1)[1] if " by " in rest else ""
        artist = artist.split(" | ")[0]
        score = max(
            difflib.SequenceMatcher(None, query, _plain(name)).ratio(),
            difflib.SequenceMatcher(None, query, _plain(f"{name} {artist}")).ratio(),
        )
        score += _KIND_BONUS.get(kind, 0.0) - index * 0.005
        if "podcast" in title.lower() and "podcast" not in query:
            score -= 0.3
        if score > best_score:
            best, best_score = (kind, ident), score
    return best if best_score >= 0.45 else None


def find_spotify_item(fetch: Callable[[str], str], words: str):
    """(kind, id) of the best Spotify match for the words, found through a public web search. None if none."""
    query = urllib.parse.quote_plus(f"{words} site:open.spotify.com")
    for url in (
        f"https://html.duckduckgo.com/html/?q={query}",
        f"https://www.bing.com/search?format=rss&q={query}",
    ):
        page = fetch(url) or ""
        best = best_spotify_item(words, spotify_candidates(page))
        if best:
            return best
        found = _SPOTIFY_TRACK.search(page)
        if found:
            return found.group(1), found.group(2)
    return None


class SystemOpener:
    """Launch URLs, apps, files and commands on Windows. Never constructed by tests.

    fetcher(url, user_agent) -> page text is passed in by the app (the web lookup helper outside the engine);
    the engine package itself never opens a network connection.
    """

    def __init__(self, fetcher: Callable[[str, str], str] | None = None) -> None:
        self._fetcher = fetcher

    def open_url(self, url: str) -> None:
        target = validate_link(url)
        if sys.platform != "win32":
            raise OSError("Opening a URL requires Windows.")
        os.startfile(target)  # type: ignore[attr-defined]

    def open_app(self, app: str, args: list[str] | None = None) -> None:
        target = validate_app(app)
        if sys.platform != "win32":
            raise OSError("Opening an application requires Windows.")
        extra = [str(item) for item in args or []]
        if os.path.exists(target):
            resolved = target
        else:
            found = shutil.which(target)
            if found and found.lower().endswith(".exe"):
                subprocess.Popen([found, *extra], shell=False)
                return
            resolved = self.resolve_app(target) or target
        if extra:
            os.startfile(resolved, "open", subprocess.list2cmdline(extra))  # type: ignore[attr-defined]
        else:
            os.startfile(resolved)  # type: ignore[attr-defined]

    def resolve_app(self, app: str) -> str | None:
        """A launchable path for the app, or None when it is not installed."""
        return resolve_installed_app(app)

    def open_path(self, path: str) -> None:
        target = validate_path(path)
        if sys.platform != "win32":
            raise OSError("Opening files requires Windows.")
        if not os.path.exists(target):
            raise ValueError(f"Not found: {target}")
        os.startfile(target)  # type: ignore[attr-defined]

    def lock_screen(self) -> None:
        if sys.platform != "win32":
            raise OSError("Locking the screen requires Windows.")
        import ctypes

        if not ctypes.windll.user32.LockWorkStation():  # type: ignore[attr-defined]
            raise OSError("Windows did not lock the screen.")

    def run_command(self, command: str) -> None:
        text = validate_command(command)
        if sys.platform != "win32":
            raise OSError("Run command requires Windows.")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(["cmd.exe", "/d", "/c", text], shell=False, creationflags=flags,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait_window(self, process: str, title: str, timeout: float, cancelled: Callable[[], bool]) -> bool:
        return wait_for_window(process, title, timeout, cancelled)

    def fetch_text(self, url: str) -> str:
        if self._fetcher is None:
            return ""
        return self._fetcher(url, USER_AGENT)


_KNOWN_APP_PATHS = {
    "spotify": [r"%APPDATA%\Spotify\Spotify.exe"],
    "code": [r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe", r"%ProgramFiles%\Microsoft VS Code\Code.exe"],
    "chrome": [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
               r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
               r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"],
    "msedge": [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
               r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"],
    "firefox": [r"%ProgramFiles%\Mozilla Firefox\firefox.exe"],
    "steam": [r"%ProgramFiles(x86)%\Steam\steam.exe"],
    "cursor": [r"%LOCALAPPDATA%\Programs\cursor\Cursor.exe", r"%ProgramFiles%\cursor\Cursor.exe"],
    "slack": [r"%LOCALAPPDATA%\slack\slack.exe"],
    "figma": [r"%LOCALAPPDATA%\Figma\Figma.exe"],
    "obs64": [r"%ProgramFiles%\obs-studio\bin\64bit\obs64.exe"],
    "blender": [r"%ProgramFiles%\Blender Foundation\Blender\blender.exe"],
}
# Store apps register a link protocol instead of a path.
_PROTOCOL_APPS = {"spotify": "spotify"}


def resolve_installed_app(app: str) -> str | None:
    if sys.platform != "win32":
        return None
    text = os.path.expandvars(app.strip().strip('"'))
    if os.path.isabs(text):
        return text if os.path.exists(text) else None
    found = shutil.which(text)
    if found:
        return found
    stem = os.path.splitext(os.path.basename(text))[0].lower()
    exe = stem + ".exe"
    try:
        import winreg

        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as key:
                    value = winreg.QueryValue(key, None).strip('"')
                    if value and os.path.exists(value):
                        return value
            except OSError:
                pass
        protocol = _PROTOCOL_APPS.get(stem)
        if protocol:
            try:
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{protocol}\shell\open\command"):
                    return f"{protocol}:"
            except OSError:
                pass
    except ImportError:
        pass
    for candidate in _KNOWN_APP_PATHS.get(stem, []):
        path = os.path.expandvars(candidate)
        if "%" not in path and os.path.exists(path):
            return path
    return _start_menu_shortcut(stem)


_SHORTCUT_NAMES = {"code": "visual studio code", "msedge": "microsoft edge", "chrome": "google chrome",
                   "obs64": "obs studio", "winword": "word", "excel": "excel", "powerpnt": "powerpoint",
                   "cursor": "cursor", "slack": "slack", "figma": "figma"}
# Versioned Start menu names ("Adobe Photoshop 2026", "Blender 4.5").
_SHORTCUT_PREFIXES = {"photoshop": "adobe photoshop", "adobe premiere pro": "adobe premiere pro",
                      "afterfx": "adobe after effects", "blender": "blender"}


def _start_menu_shortcut(stem: str) -> str | None:
    """Installed apps without a PATH entry (Discord, games) still have a Start menu shortcut."""
    wanted = {stem, _SHORTCUT_NAMES.get(stem, stem)}
    roots = [os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
             os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs")]
    for root in roots:
        if "%" in root or not os.path.isdir(root):
            continue
        for folder, _dirs, files in os.walk(root):
            for name in files:
                base, ext = os.path.splitext(name)
                if ext.lower() != ".lnk":
                    continue
                lowered = base.lower()
                prefix = _SHORTCUT_PREFIXES.get(stem)
                if lowered in wanted or (prefix and lowered.startswith(prefix)):
                    return os.path.join(folder, name)
    return None


def wait_for_window(process: str, title: str, timeout: float, cancelled: Callable[[], bool]) -> bool:
    """Poll visible top-level windows until one belongs to `process` or has `title` in its title; focus it."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    want_exe = os.path.basename(process or "").lower()
    if want_exe and not want_exe.endswith(".exe"):
        want_exe += ".exe"
    want_title = (title or "").lower()
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def exe_of(hwnd) -> str:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return os.path.basename(buffer.value).lower()
            return ""
        finally:
            kernel32.CloseHandle(handle)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not cancelled():
        hits: list[int] = []

        def visit(hwnd, _param):
            if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
                return True
            buffer = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buffer, 512)
            name_ok = not want_title or want_title in buffer.value.lower()
            exe_ok = not want_exe or exe_of(hwnd) == want_exe
            if name_ok and exe_ok:
                hits.append(hwnd)
                return False
            return True

        user32.EnumWindows(enum_proc(visit), 0)
        if hits:
            hwnd = hits[0]
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            return True
        time.sleep(0.1)
    return False


class PynputInjector:
    """Send input with pynput. Constructed only when a real hook is in use."""

    def __init__(self) -> None:
        self._keyboard = None
        self._mouse = None

    def press(self, key: str) -> None:
        self._kb().press(_pynput_key(key))

    def release(self, key: str) -> None:
        self._kb().release(_pynput_key(key))

    def type_text(self, text: str) -> None:
        self._kb().type(text)

    def click(self, button: str) -> None:
        from pynput.mouse import Button

        mapping = {"left": Button.left, "right": Button.right, "middle": Button.middle}
        if button not in mapping:
            raise ValueError(f"Unknown mouse button '{button}'.")
        mouse = self._ms()
        mouse.press(mapping[button])
        mouse.release(mapping[button])

    def mask(self) -> None:
        from pynput.keyboard import KeyCode

        from engine.keystate import MASK_VK

        code = KeyCode.from_vk(MASK_VK)
        keyboard = self._kb()
        keyboard.press(code)
        keyboard.release(code)

    def _kb(self):
        if self._keyboard is None:
            from pynput.keyboard import Controller

            self._keyboard = Controller()
        return self._keyboard

    def _ms(self):
        if self._mouse is None:
            from pynput.mouse import Controller

            self._mouse = Controller()
        return self._mouse


def _pynput_key(name: str):
    """Map a canonical name to a pynput key.

    Letters, digits, and punctuation go out as virtual keys so the current
    layout does not change which key is pressed. Named keys stay on the Key
    enum so arrows and modifiers keep the extended-key flag.
    """
    from pynput.keyboard import Key, KeyCode

    from engine.keys import NAME_TO_VK

    special = {
        "ctrl": Key.ctrl,
        "alt": Key.alt,
        "shift": Key.shift,
        "win": Key.cmd,
        "esc": Key.esc,
        "caps_lock": Key.caps_lock,
        "enter": Key.enter,
        "space": Key.space,
        "tab": Key.tab,
        "backspace": Key.backspace,
        "delete": Key.delete,
        "insert": Key.insert,
        "home": Key.home,
        "end": Key.end,
        "page_up": Key.page_up,
        "page_down": Key.page_down,
        "up": Key.up,
        "down": Key.down,
        "left": Key.left,
        "right": Key.right,
        "print_screen": Key.print_screen,
        "menu": Key.menu,
        "f1": Key.f1,
        "f2": Key.f2,
        "f3": Key.f3,
        "f4": Key.f4,
        "f5": Key.f5,
        "f6": Key.f6,
        "f7": Key.f7,
        "f8": Key.f8,
        "f9": Key.f9,
        "f10": Key.f10,
        "f11": Key.f11,
        "f12": Key.f12,
        "f13": Key.f13,
        "f14": Key.f14,
        "f15": Key.f15,
        "f16": Key.f16,
        "f17": Key.f17,
        "f18": Key.f18,
        "f19": Key.f19,
        "f20": Key.f20,
        "f21": Key.f21,
        "f22": Key.f22,
        "f23": Key.f23,
        "f24": Key.f24,
    }
    if name in special:
        return special[name]
    from engine.keys import name_to_vk

    vk = name_to_vk(name)
    if vk is None:
        raise ValueError(f"Unknown key '{name}'.")
    return KeyCode.from_vk(vk)


def ensure_action(action: Action) -> Action:
    """Validate and return the same action. Used by the service before save."""
    validate_action(action)
    return action
