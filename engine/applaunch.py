"""Find a real way to open an app, including Microsoft Store apps.

Phone Link and other packaged apps have no stable exe path. Guessing
``PhoneExperienceHost.exe`` or a ``WindowsApps`` path makes Windows report
that the file does not exist. This module resolves a name to something that
can actually be launched:

* an exe that exists (PATH, App Paths, a known install directory)
* a Start-menu shortcut (``.lnk``)
* a packaged app id (AUMID), launched with ``shell:AppsFolder``
* a registered protocol such as ``spotify:``

Nothing here starts a process. ``perform_launch`` is the only launcher, and
the Windows catalog reader is the only place that touches the registry.
"""

from __future__ import annotations

import difflib
import os
import re
import sys
from dataclasses import dataclass, field

APPS_PREFIX = "appsfolder:"

_AUMID_RE = re.compile(r"^[A-Za-z0-9][\w.]*_[A-Za-z0-9]+![A-Za-z0-9][\w.]*$")
_GENERIC_TOKENS = frozenset({"microsoft", "windows", "google", "desktop", "app", "the"})


@dataclass(frozen=True)
class InstalledApp:
    """One thing the PC can open."""

    name: str
    kind: str  # exe, shortcut, packaged, protocol
    target: str
    exe: str = ""


@dataclass(frozen=True)
class Launch:
    kind: str  # exe, shortcut, packaged, protocol
    target: str
    label: str


@dataclass(frozen=True)
class ResolveResult:
    launch: Launch | None = None
    missing_path: str = ""


@dataclass(frozen=True)
class KnownApp:
    label: str
    names: tuple[str, ...]
    exes: tuple[str, ...] = ()
    aumid: str = ""
    inbox: bool = False
    protocol: str = ""
    prefixes: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()


# Spoken names and the bad exe guesses models invent. ``inbox`` apps ship with
# Windows, so the AUMID is still worth trying when the Start-menu list could
# not be read. Desktop apps are not launched from a guessed AUMID.
KNOWN_APPS: tuple[KnownApp, ...] = (
    KnownApp("Phone Link", ("phone link", "your phone", "yourphone", "phonelink", "phoneexperiencehost"),
             ("phoneexperiencehost.exe", "yourphone.exe"),
             aumid="Microsoft.YourPhone_8wekyb3d8bbwe!App", inbox=True),
    KnownApp("Settings", ("settings", "windows settings", "ms settings"),
             aumid="windows.immersivecontrolpanel_cw5n1h2txyewy!microsoft.windows.immersivecontrolpanel",
             inbox=True),
    KnownApp("Calculator", ("calculator", "calc"), ("calc.exe", "calculator.exe"),
             aumid="Microsoft.WindowsCalculator_8wekyb3d8bbwe!App", inbox=True),
    KnownApp("Photos", ("photos", "microsoft photos"), ("photos.exe",),
             aumid="Microsoft.Windows.Photos_8wekyb3d8bbwe!App", inbox=True),
    KnownApp("Snipping Tool", ("snipping tool", "snip", "screensketch"),
             aumid="Microsoft.ScreenSketch_8wekyb3d8bbwe!App", inbox=True),
    KnownApp("Notepad", ("notepad",), ("notepad.exe",),
             aumid="Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"),
    KnownApp("Paint", ("paint", "mspaint"), ("mspaint.exe",),
             aumid="Microsoft.Paint_8wekyb3d8bbwe!App"),
    KnownApp("Terminal", ("terminal", "windows terminal"), ("wt.exe", "windowsterminal.exe"),
             aumid="Microsoft.WindowsTerminal_8wekyb3d8bbwe!App"),
    KnownApp("Clock", ("clock", "alarms"),
             aumid="Microsoft.WindowsAlarms_8wekyb3d8bbwe!App", inbox=True),
    KnownApp("Microsoft Store", ("microsoft store", "store"),
             aumid="Microsoft.WindowsStore_8wekyb3d8bbwe!App", inbox=True),
    KnownApp("Camera", ("camera",),
             aumid="Microsoft.WindowsCamera_8wekyb3d8bbwe!App", inbox=True),
    KnownApp("Media Player", ("media player", "groove", "movies"),
             aumid="Microsoft.ZuneMusic_8wekyb3d8bbwe!App"),
    KnownApp("Xbox", ("xbox",),
             aumid="Microsoft.GamingApp_8wekyb3d8bbwe!Microsoft.Xbox.App"),
    KnownApp("Microsoft Edge", ("microsoft edge", "edge", "msedge"), ("msedge.exe",),
             paths=(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
                    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe")),
    KnownApp("Google Chrome", ("google chrome", "chrome"), ("chrome.exe",),
             paths=(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
                    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")),
    KnownApp("Firefox", ("firefox", "mozilla firefox"), ("firefox.exe",),
             paths=(r"%ProgramFiles%\Mozilla Firefox\firefox.exe",)),
    KnownApp("Visual Studio Code", ("visual studio code", "vs code", "vscode", "code"), ("code.exe",),
             paths=(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
                    r"%ProgramFiles%\Microsoft VS Code\Code.exe")),
    KnownApp("Cursor", ("cursor",), ("cursor.exe",),
             paths=(r"%LOCALAPPDATA%\Programs\cursor\Cursor.exe", r"%ProgramFiles%\cursor\Cursor.exe")),
    KnownApp("Spotify", ("spotify",), ("spotify.exe",), protocol="spotify",
             paths=(r"%APPDATA%\Spotify\Spotify.exe",)),
    KnownApp("WhatsApp", ("whatsapp", "whatsapp desktop"), ("whatsapp.exe",),
             aumid="5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"),
    KnownApp("Discord", ("discord",), ("discord.exe",)),
    KnownApp("Teams", ("microsoft teams", "teams", "msteams"), ("ms-teams.exe", "teams.exe"),
             aumid="MSTeams_8wekyb3d8bbwe!MSTeams"),
    KnownApp("Slack", ("slack",), ("slack.exe",), paths=(r"%LOCALAPPDATA%\slack\slack.exe",)),
    KnownApp("Steam", ("steam",), ("steam.exe",), paths=(r"%ProgramFiles(x86)%\Steam\steam.exe",)),
    KnownApp("OBS Studio", ("obs", "obs studio"), ("obs64.exe",), prefixes=("obs studio",),
             paths=(r"%ProgramFiles%\obs-studio\bin\64bit\obs64.exe",)),
    KnownApp("Figma", ("figma",), ("figma.exe",), paths=(r"%LOCALAPPDATA%\Figma\Figma.exe",)),
    KnownApp("Blender", ("blender",), ("blender.exe",), prefixes=("blender",),
             paths=(r"%ProgramFiles%\Blender Foundation\Blender\blender.exe",)),
    KnownApp("Word", ("word", "microsoft word", "winword"), ("winword.exe",)),
    KnownApp("Excel", ("excel", "microsoft excel"), ("excel.exe",)),
    KnownApp("PowerPoint", ("powerpoint", "powerpnt"), ("powerpnt.exe",)),
    KnownApp("Photoshop", ("photoshop", "adobe photoshop"), ("photoshop.exe",), prefixes=("adobe photoshop",)),
)


# Names the model should store. Bad exe guesses become the Start-menu name.
_QUERY_LABELS = {name: app.label for app in KNOWN_APPS for name in app.names}
for _app in KNOWN_APPS:
    for _exe in _app.exes:
        stem = _exe[:-4] if _exe.endswith(".exe") else _exe
        # Keep a real, well-known exe. Rewrite only the guesses that are not that exe.
        if stem not in _QUERY_LABELS:
            _QUERY_LABELS[stem] = _app.label
_KEEP_EXES = {
    "calc.exe": "calc.exe",
    "notepad.exe": "notepad.exe",
    "mspaint.exe": "mspaint.exe",
    "explorer.exe": "explorer.exe",
    "chrome.exe": "chrome.exe",
    "msedge.exe": "msedge.exe",
    "firefox.exe": "firefox.exe",
    "code.exe": "code",
    "cursor.exe": "cursor.exe",
    "spotify.exe": "spotify.exe",
    "discord.exe": "discord.exe",
    "slack.exe": "slack.exe",
    "steam.exe": "steam.exe",
    "obs64.exe": "obs64.exe",
    "winword.exe": "winword.exe",
    "excel.exe": "excel.exe",
    "powerpnt.exe": "powerpnt.exe",
    "figma.exe": "figma.exe",
    "blender.exe": "blender.exe",
}


def normalize_name(text: str) -> str:
    text = (text or "").strip().strip('"').lower().replace("_", " ")
    text = text.replace("/", " ")
    text = re.sub(r"[^a-z0-9.+\\: -]+", " ", text)
    return " ".join(text.split())


def canonical_app_query(app: str) -> str:
    """A stable app string for a binding. Real exes stay; bad guesses become Start-menu names."""
    raw = (app or "").strip().strip('"')
    if not raw or raw.lower().startswith(APPS_PREFIX) or _AUMID_RE.match(raw):
        return raw
    # os.path is POSIX here, so a Windows path must be split on backslash too.
    base = raw.replace("/", "\\").rsplit("\\", 1)[-1]
    key = normalize_name(base or raw)
    compact = key.replace(" ", "")
    if compact.endswith(".exe") and compact in _KEEP_EXES:
        return _KEEP_EXES[compact]
    if key in _KEEP_EXES:
        return _KEEP_EXES[key]
    # PhoneExperienceHost.exe, YourPhone.exe, Calculator.exe, WhatsApp.exe, ms-teams.exe
    for alias, label in _QUERY_LABELS.items():
        if key == alias or compact == alias.replace(" ", "") or compact == alias.replace(" ", "") + ".exe":
            if label in ("Notepad", "Calculator") and compact in ("notepad.exe", "calc.exe", "mspaint.exe"):
                continue
            if compact in _KEEP_EXES:
                return _KEEP_EXES[compact]
            return label
    return raw


def not_installed_message(query: str) -> str:
    """What the log should say when nothing launchable was found."""
    text = (query or "").strip().strip('"') or "That app"
    expanded = os.path.expandvars(text)
    if os.path.isabs(expanded) or ":\\" in expanded or expanded.lower().startswith("\\\\"):
        return (
            f"{expanded} does not exist. Microsoft Store apps such as Phone Link have no normal exe path. "
            "Use the name from the Start menu (Phone Link) and Keyware will open the installed app."
        )
    return (
        f"{text} is not installed. Keyware looked for a program on PATH, an App Paths entry, "
        "a Start menu shortcut, and a Microsoft Store app."
    )


def launch_token(launch: Launch) -> str:
    if launch.kind == "packaged":
        return APPS_PREFIX + launch.target
    return launch.target


def is_packaged_token(text: str) -> bool:
    raw = (text or "").strip()
    lower = raw.lower()
    return lower.startswith(APPS_PREFIX) or lower.startswith("shell:appsfolder\\") or bool(_AUMID_RE.match(raw))


def packaged_aumid(text: str) -> str:
    raw = text.strip()
    lower = raw.lower()
    if lower.startswith(APPS_PREFIX):
        return raw.split(":", 1)[1]
    if lower.startswith("shell:appsfolder\\"):
        return raw.split("\\", 1)[1]
    return raw


@dataclass
class Catalog:
    apps: list[InstalledApp] = field(default_factory=list)
    protocols: set[str] = field(default_factory=set)
    packaged_enumerated: bool = False


def resolve_launch(
    query: str,
    catalog: Catalog | None = None,
    *,
    which=None,
    exists=None,
    app_path=None,
) -> ResolveResult:
    """Pick a launch target. ``which`` / ``exists`` / ``app_path`` are injected in tests."""
    catalog = catalog or Catalog()
    exists = exists or (lambda _path: False)
    which = which or (lambda _name: None)
    app_path = app_path or (lambda _exe: None)
    raw = (query or "").strip().strip('"')
    if not raw:
        return ResolveResult()
    if is_packaged_token(raw):
        aumid = packaged_aumid(raw)
        return ResolveResult(Launch("packaged", aumid, aumid))

    expanded = os.path.expandvars(raw)
    if os.path.isabs(expanded):
        if exists(expanded):
            kind = "shortcut" if expanded.lower().endswith(".lnk") else "exe"
            return ResolveResult(Launch(kind, expanded, os.path.basename(expanded)))
        # A versioned WindowsApps path is not a program. Keep looking by name.
        named = _resolve_name(os.path.splitext(os.path.basename(expanded))[0], catalog, which, exists, app_path)
        if named.launch is not None:
            return named
        return ResolveResult(missing_path=expanded)

    if "\\" in expanded or "/" in expanded:
        return ResolveResult(missing_path=expanded)

    return _resolve_name(raw, catalog, which, exists, app_path)


def _resolve_name(raw: str, catalog: Catalog, which, exists, app_path) -> ResolveResult:
    direct = _file_hit(raw, which, exists, app_path)
    if direct is not None:
        return ResolveResult(direct)
    known = _known_for(raw)
    if known is not None:
        found = _from_known(known, catalog, which, exists, app_path)
        if found is not None:
            return ResolveResult(found)
    hit = _from_catalog(normalize_name(raw), catalog)
    if hit is not None:
        return ResolveResult(hit)
    return ResolveResult()


def _file_hit(raw: str, which, exists, app_path) -> Launch | None:
    base = raw.replace("/", "\\").rsplit("\\", 1)[-1]
    exe = base if base.lower().endswith(".exe") else base + ".exe"
    # which() already found a program. Do not ask exists() again: a test (and
    # some app-execution aliases) report the bare name as missing.
    found = which(base) or which(exe)
    if found:
        return Launch("exe", found, base)
    located = app_path(exe)
    if located and exists(located):
        return Launch("exe", located, base)
    for known in KNOWN_APPS:
        if _known_for(raw) is not known:
            continue
        for path in known.paths:
            full = os.path.expandvars(path)
            if "%" not in full and exists(full):
                return Launch("exe", full, known.label)
    return None


def _known_for(raw: str) -> KnownApp | None:
    key = normalize_name(os.path.basename(raw.replace("/", "\\")))
    if key.endswith(".exe"):
        key = key[:-4]
    compact = key.replace(" ", "")
    for app in KNOWN_APPS:
        names = {name.replace(" ", "") for name in app.names}
        exes = {exe[:-4] if exe.endswith(".exe") else exe for exe in app.exes}
        if compact in names or compact in exes or key in app.names:
            return app
    return None


def _from_known(known: KnownApp, catalog: Catalog, which, exists, app_path) -> Launch | None:
    for exe in known.exes:
        for candidate in (which(exe), app_path(exe)):
            if candidate and exists(candidate):
                return Launch("exe", candidate, known.label)
        for path in known.paths:
            full = os.path.expandvars(path)
            if "%" not in full and exists(full):
                return Launch("exe", full, known.label)
    norm = normalize_name(known.label)
    for app in catalog.apps:
        if app.kind == "packaged" and known.aumid and app.target.lower() == known.aumid.lower():
            return Launch("packaged", app.target, app.name or known.label)
        if _name_score(norm, app.name) >= 0.99 or _prefix_hit(known, app.name):
            return _launch_from_installed(app, known.label)
        exe_stem = os.path.splitext(app.exe)[0]
        if exe_stem and exe_stem in {item[:-4] if item.endswith(".exe") else item for item in known.exes}:
            return _launch_from_installed(app, known.label)
    if known.protocol and known.protocol.lower() in {item.lower() for item in catalog.protocols}:
        return Launch("protocol", known.protocol + ":", known.label)
    if known.aumid and _inbox_allowed(known, catalog):
        return Launch("packaged", known.aumid, known.label)
    return None


def _inbox_allowed(known: KnownApp, catalog: Catalog) -> bool:
    if not known.inbox or not known.aumid:
        return False
    if not catalog.packaged_enumerated:
        return True
    # The packaged list was read and this app is absent: do not invent a launch.
    wanted = known.aumid.lower()
    return any(app.kind == "packaged" and app.target.lower() == wanted for app in catalog.apps)


def _prefix_hit(known: KnownApp, name: str) -> bool:
    norm = normalize_name(name)
    return any(norm.startswith(prefix) for prefix in known.prefixes)


def _from_catalog(norm: str, catalog: Catalog) -> Launch | None:
    best: InstalledApp | None = None
    best_score = 0.0
    for app in catalog.apps:
        score = max(_name_score(norm, app.name), _name_score(norm, os.path.splitext(app.exe)[0]))
        if score > best_score:
            best, best_score = app, score
    if best is not None and best_score >= 0.86:
        return _launch_from_installed(best, best.name)
    return None


def _launch_from_installed(app: InstalledApp, label: str) -> Launch:
    kind = app.kind if app.kind in ("exe", "shortcut", "packaged", "protocol") else "exe"
    target = app.target
    if kind == "protocol" and not target.endswith(":"):
        target += ":"
    return Launch(kind, target, label or app.name)


def _name_score(query: str, candidate: str) -> float:
    left = normalize_name(query)
    right = normalize_name(candidate)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    q_tokens = left.split()
    r_tokens = right.split()
    if len(q_tokens) == 1 and q_tokens[0] in r_tokens and len(q_tokens[0]) >= 4:
        rest = [token for token in r_tokens if token != q_tokens[0]]
        if rest and all(token in _GENERIC_TOKENS or token.isdigit() for token in rest):
            return 0.93
    if len(left) < 3:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


def perform_launch(token: str, args: list[str] | None, *, exists, startfile, popen, activate, trust: bool = False) -> None:
    """Run one already-resolved target. Callers pass the OS functions.

    ``trust`` means resolve_launch already accepted the path (PATH or App Paths).
    A raw path that does not exist is refused so it is never passed to Windows.
    """
    extra = [str(item) for item in args or []]
    text = (token or "").strip()
    if not text:
        raise ValueError(not_installed_message(token))
    if is_packaged_token(text):
        activate(packaged_aumid(text))
        return
    if text.endswith(":") and not os.path.isabs(text) and "\\" not in text:
        startfile(text)
        return
    if not trust and not exists(text):
        raise ValueError(not_installed_message(text))
    if text.lower().endswith(".lnk"):
        if extra:
            startfile(text, extra)
        else:
            startfile(text)
        return
    popen([text, *extra])


def activate_aumid(aumid: str) -> None:
    """Start a packaged app. COM first, then ``explorer.exe shell:AppsFolder`` (not cmd)."""
    if sys.platform != "win32":
        raise OSError("Opening an application requires Windows.")
    try:
        _activate_com(aumid)
        return
    except Exception:
        pass
    flags = getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0)
    __import__("subprocess").Popen(
        ["explorer.exe", f"shell:AppsFolder\\{aumid}"],
        shell=False,
        creationflags=flags,
        stdin=__import__("subprocess").DEVNULL,
        stdout=__import__("subprocess").DEVNULL,
        stderr=__import__("subprocess").DEVNULL,
    )


def _activate_com(aumid: str) -> None:
    import ctypes
    from ctypes import wintypes

    ole32 = ctypes.windll.ole32
    if ole32.CoInitializeEx(None, 2) not in (0, 1):  # COINIT_APARTMENTTHREADED, S_FALSE
        pass
    clsid = _guid("{45BA127D-10A8-46EA-8AB7-56EA9078943C}")
    iid = _guid("{2E941141-7F97-4756-BA1D-9DECDE894A3D}")
    manager = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(manager))
    if hr < 0 or not manager.value:
        raise OSError(f"CoCreateInstance failed ({hr})")
    vtable = ctypes.cast(manager, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    activate = ctypes.WINFUNCTYPE(
        ctypes.HRESULT, ctypes.c_void_p, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.DWORD)
    )(vtable[3])
    pid = wintypes.DWORD()
    hr = activate(manager, aumid, None, 0, ctypes.byref(pid))
    ole32.CoUninitialize()
    if hr < 0:
        raise OSError(f"ActivateApplication failed ({hr})")


def _guid(text: str):
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = (
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        )

    value = GUID()
    if ctypes.windll.ole32.CLSIDFromString(text, ctypes.byref(value)) < 0:
        raise OSError("bad guid")
    return value


_CACHE: Catalog | None = None
_CACHE_AT = 0.0


def windows_catalog(force: bool = False) -> Catalog:
    """Installed shortcuts, App Paths, known folders, and packaged apps. Cached briefly."""
    global _CACHE, _CACHE_AT
    import time

    if sys.platform != "win32":
        return Catalog()
    now = time.monotonic()
    if not force and _CACHE is not None and now - _CACHE_AT < 30:
        return _CACHE
    apps: list[InstalledApp] = []
    apps.extend(_read_shortcuts())
    apps.extend(_read_app_paths())
    apps.extend(_read_known_paths())
    packaged, enumerated = _read_packaged()
    apps.extend(packaged)
    protocols = _read_protocols()
    _CACHE = Catalog(apps=apps, protocols=protocols, packaged_enumerated=enumerated)
    _CACHE_AT = now
    return _CACHE


def _read_shortcuts() -> list[InstalledApp]:
    found: list[InstalledApp] = []
    roots = [
        os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
    ]
    for root in roots:
        if "%" in root or not os.path.isdir(root):
            continue
        for folder, _dirs, files in os.walk(root):
            for name in files:
                if not name.lower().endswith(".lnk"):
                    continue
                found.append(InstalledApp(os.path.splitext(name)[0], "shortcut", os.path.join(folder, name)))
    return found


def _read_app_paths() -> list[InstalledApp]:
    found: list[InstalledApp] = []
    try:
        import winreg
    except ImportError:
        return found
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            key = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\App Paths")
        except OSError:
            continue
        try:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(key, name) as sub:
                        value = str(winreg.QueryValue(sub, None) or "").strip().strip('"')
                except OSError:
                    continue
                if value and os.path.exists(value):
                    found.append(InstalledApp(os.path.splitext(name)[0], "exe", value, exe=name.lower()))
        finally:
            winreg.CloseKey(key)
    return found


def _read_known_paths() -> list[InstalledApp]:
    found: list[InstalledApp] = []
    for app in KNOWN_APPS:
        for path in app.paths:
            full = os.path.expandvars(path)
            if "%" not in full and os.path.exists(full):
                found.append(InstalledApp(app.label, "exe", full, exe=os.path.basename(full).lower()))
    return found


def _read_packaged() -> tuple[list[InstalledApp], bool]:
    """``Get-StartApps`` is the installed packaged-app list. Failure is not an empty install."""
    import subprocess

    command = (
        "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=8, creationflags=flags, shell=False,
        )
    except (OSError, subprocess.TimeoutExpired, Exception):
        return [], False
    if completed.returncode != 0 or not (completed.stdout or "").strip():
        return [], False
    import json

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return [], False
    rows = payload if isinstance(payload, list) else [payload]
    found: list[InstalledApp] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("Name") or "").strip()
        app_id = str(row.get("AppID") or "").strip()
        if name and app_id:
            found.append(InstalledApp(name, "packaged", app_id))
    return found, True


def _read_protocols() -> set[str]:
    found: set[str] = set()
    try:
        import winreg
    except ImportError:
        return found
    for name in ("spotify",):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{name}\shell\open\command"):
                found.add(name)
        except OSError:
            pass
    return found


def windows_which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


def windows_app_path(exe: str) -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"Software\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as key:
                value = str(winreg.QueryValue(key, None) or "").strip().strip('"')
        except OSError:
            continue
        if value and os.path.exists(value):
            return value
    return None
