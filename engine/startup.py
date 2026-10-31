"""Start Keyware with Windows: one HKCU Run value, no admin needed.

The value runs Keyware with --background: straight into the tray, no window,
no popups, hotkeys active. On by default for new installs (Settings can turn
it off, which deletes the value). Tests use MemoryRegistry, never the real key.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Keyware"
BACKGROUND_FLAG = "--background"


class Registry(Protocol):
    def read(self, name: str) -> str | None: ...

    def write(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class MemoryRegistry:
    """Stand-in for HKCU used by tests and by non-Windows sessions."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def read(self, name: str) -> str | None:
        return self.values.get(name)

    def write(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


class WinregRegistry:
    def read(self, name: str) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
                value, _kind = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        except OSError:
            return None
        return str(value)

    def write(self, name: str, value: str) -> None:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            return


def launch_command() -> str:
    """Command line stored in the Run key. Uses pythonw when it sits beside Python."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {BACKGROUND_FLAG}'
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    executable = pythonw if pythonw.exists() else python
    main = Path(__file__).resolve().parents[1] / "main.py"
    return f'"{executable}" "{main}" {BACKGROUND_FLAG}'


class StartupManager:
    def __init__(self, registry: Registry | None = None, command: str | None = None):
        if registry is not None:
            self.registry = registry
        elif sys.platform == "win32":
            self.registry = WinregRegistry()
        else:
            self.registry = MemoryRegistry()
        self.command = command if command is not None else launch_command()

    def is_enabled(self) -> bool:
        return self.registry.read(VALUE_NAME) is not None

    def current(self) -> str | None:
        return self.registry.read(VALUE_NAME)

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.registry.write(VALUE_NAME, self.command)
        else:
            self.registry.delete(VALUE_NAME)
