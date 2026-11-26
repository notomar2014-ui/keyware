"""Windows app names that do not match the file or the process one-for-one.

Calculator is the case that shows both gaps:

* The launch name is ``calc.exe``, but on Windows 10 and 11 that file is an
  App Execution Alias under ``%LOCALAPPDATA%\\Microsoft\\WindowsApps``. Python's
  ``os.path.exists`` and ``shutil.which`` report the alias as missing
  (``WinError 1920``), so a strict existence check says the app is not installed.
* The window's process is ``CalculatorApp.exe``. On older builds the frame is
  ``ApplicationFrameHost.exe`` and the real app is a child window. Comparing
  the typed name to the process name with a plain string match fails.

The groups below are explicit. A name matches another name only when both
belong to the same group, or they are the same name aside from case and
``.exe``. ``ApplicationFrameHost.exe`` is a host for many apps, so it is not
an alias for Calculator.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

# Compact stems (letters and digits only). Each frozenset is one app.
ALIAS_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"calc", "calculator", "calculatorapp"}),
    frozenset({"mspaint", "paint", "paintstudioview"}),
    frozenset({"wt", "windowsterminal", "terminal"}),
    frozenset({"photos", "microsoftphotos"}),
    frozenset({"systemsettings", "settings", "windowssettings"}),
    frozenset({"xbox", "xboxpcapp", "xboxapp"}),
    frozenset({"snippingtool", "screensketch", "snip"}),
    frozenset({"winstoreapp", "windowsstore", "store"}),
    frozenset({"windowscamera", "camera"}),
    frozenset({"time", "windowsalarms", "clock"}),
    frozenset({"phoneexperiencehost", "yourphone", "phonelink"}),
    frozenset({"videoui", "mediaplayer", "groove"}),
    frozenset({"msteams", "teams", "microsoftteams"}),
    frozenset({"msedge", "microsoftedge", "edge"}),
)

_GROUP_OF: dict[str, frozenset[str]] = {}
for _group in ALIAS_GROUPS:
    for _stem in _group:
        if _stem in _GROUP_OF:
            raise RuntimeError(f"app alias {_stem!r} is in two groups")
        _GROUP_OF[_stem] = _group

FRAME_HOST = "applicationframehost"
_CORE_CLASSES = frozenset({
    "windows.ui.core.corewindow",
    "windows.ui.input.inputsite.windowclass",
})
# Children of the frame that are not the app the user is looking at.
_FRAME_NOISE = frozenset({FRAME_HOST, "dwm", "explorer", "shellexperiencehost", "textinputhost"})


def app_stem(name: str) -> str:
    """File stem: no directory, no quotes, lower case, without a trailing ``.exe``."""
    text = str(name or "").strip().strip('"').replace("\\", "/")
    text = text.rsplit("/", 1)[-1].strip().lower()
    if text.endswith(".exe"):
        text = text[:-4]
    return text


def _compact(stem: str) -> str:
    return re.sub(r"[^a-z0-9]", "", stem)


def identity_keys(name: str) -> frozenset[str]:
    """Every stem that should be treated as this app, including aliases."""
    stem = app_stem(name)
    if not stem:
        return frozenset()
    compact = _compact(stem)
    keys = {stem}
    if compact:
        keys.add(compact)
    extra: set[str] = set()
    for key in keys:
        group = _GROUP_OF.get(key)
        if group:
            extra |= set(group)
    return frozenset(keys | extra)


def same_app(expected: str, actual: str) -> bool:
    """True when both names are the same app, ignoring case, ``.exe``, and known aliases."""
    left = identity_keys(expected)
    right = identity_keys(actual)
    return bool(left and right and left & right)


# Filled by engine.applaunch so "Microsoft Edge" matches msedge.exe without a second table.
_EXTRA_MATCH = None


def register_extra_match(fn) -> None:
    global _EXTRA_MATCH
    _EXTRA_MATCH = fn


def app_names_match(expected: str, actual: str) -> bool:
    """``same_app``, plus the installed-app catalog when that module is loaded."""
    if same_app(expected, actual):
        return True
    extra = _EXTRA_MATCH
    return bool(extra and extra(expected, actual))


def is_execution_alias(path: str) -> bool:
    """True for an App Execution Alias path (``...\\Microsoft\\WindowsApps\\...``)."""
    folded = str(path or "").replace("/", "\\").lower()
    return "\\windowsapps\\" in folded


def is_frame_host(name: str) -> bool:
    return _compact(app_stem(name)) == FRAME_HOST


def _basename(name: str) -> str:
    """File name. Splits on ``\\`` and ``/`` so a Windows path is a name on any OS."""
    text = str(name or "").strip().strip('"').replace("/", "\\")
    return text.rsplit("\\", 1)[-1]


def hosted_app_name(host_exe: str, children: list[tuple[str, str]] | None = None) -> str:
    """Process name to match against.

    A normal window keeps its own exe. ``ApplicationFrameHost.exe`` is replaced
    by the UWP child (the ``CoreWindow`` process, such as ``CalculatorApp.exe``).
    If that child cannot be read, the host name is returned unchanged.
    """
    host = _basename(host_exe)
    if not is_frame_host(host):
        return host
    preferred: list[str] = []
    other: list[str] = []
    for class_name, exe in children or []:
        base = _basename(exe)
        if not base or _compact(app_stem(base)) in _FRAME_NOISE:
            continue
        if str(class_name or "").strip().lower() in _CORE_CLASSES:
            preferred.append(base)
        else:
            other.append(base)
    if preferred:
        return preferred[0]
    if len(other) == 1:
        return other[0]
    return host


def window_matches(want: str, top_exe: str, children: list[tuple[str, str]] | None = None) -> bool:
    """True when ``want`` is the top-level process or the app hosted inside it."""
    if not app_stem(want):
        return False
    if app_names_match(want, top_exe):
        return True
    return app_names_match(want, hosted_app_name(top_exe, children))


def default_search_dirs(env: Mapping[str, str] | None = None) -> list[str]:
    """System32, SysWOW64, then WindowsApps, then PATH. Earlier entries win."""
    source = os.environ if env is None else env
    windir = source.get("WINDIR") or source.get("SystemRoot") or r"C:\Windows"
    ordered = [
        os.path.join(windir, "System32"),
        os.path.join(windir, "SysWOW64"),
    ]
    local = source.get("LOCALAPPDATA") or ""
    if local:
        ordered.append(os.path.join(local, "Microsoft", "WindowsApps"))
    profile = source.get("USERPROFILE") or ""
    if profile:
        ordered.append(os.path.join(profile, "AppData", "Local", "Microsoft", "WindowsApps"))
    for item in source.get("PATH", "").split(os.pathsep):
        item = item.strip().strip('"')
        if item:
            ordered.append(item)
    seen: set[str] = set()
    unique: list[str] = []
    for directory in ordered:
        key = directory.replace("/", "\\").rstrip("\\").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(directory)
    return unique


def safe_list_dir(path: str) -> list[str]:
    try:
        return list(os.listdir(path))
    except OSError:
        return []


def listed_exe(name: str, list_dir, dirs: list[str]) -> str | None:
    """Find ``name`` by listing directories.

    ``os.path.exists`` is false for App Execution Aliases, but the file name
    still appears in the directory listing. The first directory that contains
    it wins, so System32 is used ahead of WindowsApps when both have the file.
    """
    base = _basename(name)
    if not base:
        return None
    exe = base if base.lower().endswith(".exe") else base + ".exe"
    wanted = exe.lower()
    seen: set[str] = set()
    for directory in dirs:
        if not directory:
            continue
        key = directory.replace("/", "\\").rstrip("\\").lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            entries = list_dir(directory) or []
        except Exception:
            continue
        for entry in entries:
            if str(entry).lower() == wanted:
                return os.path.join(directory, str(entry))
    return None


def path_is_listed(path: str, list_dir=safe_list_dir) -> bool:
    directory, name = os.path.split(str(path or ""))
    if not directory or not name:
        return False
    try:
        entries = list_dir(directory) or []
    except Exception:
        return False
    return name.lower() in {str(entry).lower() for entry in entries}
