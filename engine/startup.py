"""Start Keyware with Windows: one HKCU Run value, no admin needed.

The value runs Keyware with --background: straight into the tray, no window,
no popups, hotkeys active. On by default for new installs (Settings can turn
it off, which deletes the value). Tests use MemoryRegistry, never the real key.

Microsoft Store (MSIX) build: a packaged app must not use the Run key. When
Keyware runs with package identity (GetCurrentPackageFullName succeeds), the
switch drives the manifest's ``windows.startupTask`` (TaskId "KeywareStartup")
through the WinRT StartupTask API instead. The manifest passes --background via
uap10:Parameters and declares the task Enabled="true", so the default stays on
and silent. The plain Keyware.exe never takes this path.
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


# --- Microsoft Store (MSIX) package -------------------------------------------

STARTUP_TASK_ID = "KeywareStartup"  # must match <desktop:StartupTask TaskId=...> in AppxManifest.xml
PACKAGED_COMMAND = f"startupTask:{STARTUP_TASK_ID} {BACKGROUND_FLAG}"

APPMODEL_ERROR_NO_PACKAGE = 15700
ERROR_INSUFFICIENT_BUFFER = 122

# Windows.ApplicationModel.StartupTaskState
TASK_DISABLED = 0
TASK_DISABLED_BY_USER = 1
TASK_ENABLED = 2
TASK_DISABLED_BY_POLICY = 3
TASK_ENABLED_BY_POLICY = 4
ENABLED_STATES = (TASK_ENABLED, TASK_ENABLED_BY_POLICY)

DISABLED_BY_USER_MESSAGE = (
    "Windows has Keyware's startup turned off. Turn it on in Windows Settings > Apps > Startup "
    "(or Task Manager > Startup apps), then try again."
)
DISABLED_BY_POLICY_MESSAGE = "Startup apps are turned off by your organisation's policy on this PC."
ENABLED_BY_POLICY_MESSAGE = "Your organisation's policy keeps Keyware starting with Windows on this PC."


def is_packaged(kernel32=None) -> bool:
    """True when this process has MSIX package identity (installed from the Store or an .msix)."""
    if kernel32 is None:
        if sys.platform != "win32":
            return False
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
        except Exception:
            return False
    try:
        import ctypes

        length = ctypes.c_uint32(0)
        result = int(kernel32.GetCurrentPackageFullName(ctypes.byref(length), None))
    except Exception:
        return False
    # No package: APPMODEL_ERROR_NO_PACKAGE. Packaged: the size probe asks for a bigger buffer.
    return result in (ERROR_INSUFFICIENT_BUFFER, 0)


class StartupTaskApi(Protocol):
    def state(self) -> int: ...

    def request_enable(self) -> int: ...

    def disable(self) -> None: ...


def _wait(operation):
    """Block for a WinRT IAsyncOperation. pywinrt runs in the MTA by default, where .get() is safe."""
    getter = getattr(operation, "get", None)
    if callable(getter):
        return getter()
    import asyncio

    async def wait():
        return await operation

    return asyncio.run(wait())


class WinRTStartupTask:
    """Windows.ApplicationModel.StartupTask through pywinrt. Imported lazily: Store build only."""

    def __init__(self, task_id: str = STARTUP_TASK_ID):
        self.task_id = task_id

    def _task(self):
        from winrt.windows.applicationmodel import StartupTask  # winrt-Windows.ApplicationModel

        return _wait(StartupTask.get_async(self.task_id))

    def state(self) -> int:
        return int(self._task().state)

    def request_enable(self) -> int:
        return int(_wait(self._task().request_enable_async()))

    def disable(self) -> None:
        self._task().disable()


class StartupTaskRegistry:
    """Registry-shaped adapter so StartupManager and the service work unchanged in the MSIX build.

    read() returns PACKAGED_COMMAND while the task is enabled, else None. write() asks Windows to
    enable the task; delete() disables it. Anything Windows refuses is raised as OSError, which the
    service turns into the Settings note.
    """

    def __init__(self, api: StartupTaskApi | None = None):
        self.api = api if api is not None else WinRTStartupTask()

    def _state(self) -> int:
        try:
            return int(self.api.state())
        except OSError:
            raise
        except Exception as exc:  # pywinrt missing or the task id not in the manifest
            raise OSError(f"the Windows startup task is unavailable ({exc})") from exc

    def read(self, name: str) -> str | None:
        return PACKAGED_COMMAND if self._state() in ENABLED_STATES else None

    def write(self, name: str, value: str) -> None:
        state = self._state()
        if state in ENABLED_STATES:
            return
        if state == TASK_DISABLED_BY_USER:
            raise OSError(DISABLED_BY_USER_MESSAGE)
        if state == TASK_DISABLED_BY_POLICY:
            raise OSError(DISABLED_BY_POLICY_MESSAGE)
        try:
            state = int(self.api.request_enable())
        except Exception as exc:
            raise OSError(f"Windows did not enable the startup task ({exc})") from exc
        if state == TASK_DISABLED_BY_USER:
            raise OSError(DISABLED_BY_USER_MESSAGE)
        if state not in ENABLED_STATES:
            raise OSError(DISABLED_BY_POLICY_MESSAGE)

    def delete(self, name: str) -> None:
        state = self._state()
        if state == TASK_ENABLED_BY_POLICY:
            raise OSError(ENABLED_BY_POLICY_MESSAGE)
        if state in ENABLED_STATES:
            try:
                self.api.disable()
            except Exception as exc:
                raise OSError(f"Windows did not turn off the startup task ({exc})") from exc


def launched_by_startup_task(app_instance=None) -> bool:
    """Packaged only: True when Windows started Keyware from the startup task.

    Belt and braces for the manifest's uap10:Parameters="--background": if a Windows build ever
    drops the parameter, the sign-in launch still goes straight to the tray.
    """
    try:
        if app_instance is None:
            import winrt.windows.applicationmodel.activation  # noqa: F401  (projects the returned args)
            from winrt.windows.applicationmodel import AppInstance

            app_instance = AppInstance
        args = app_instance.get_activated_event_args()
        if args is None:
            return False
        return int(args.kind) == 1020  # ActivationKind.StartupTask
    except Exception:
        return False


class StartupManager:
    def __init__(
        self,
        registry: Registry | None = None,
        command: str | None = None,
        packaged: bool | None = None,
    ):
        if packaged is None:
            packaged = registry is None and is_packaged()
        self.packaged = bool(packaged)
        if registry is not None:
            self.registry = registry
        elif self.packaged:
            self.registry = StartupTaskRegistry()
        elif sys.platform == "win32":
            self.registry = WinregRegistry()
        else:
            self.registry = MemoryRegistry()
        if command is not None:
            self.command = command
        else:
            self.command = PACKAGED_COMMAND if self.packaged else launch_command()

    def is_enabled(self) -> bool:
        return self.registry.read(VALUE_NAME) is not None

    def current(self) -> str | None:
        return self.registry.read(VALUE_NAME)

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self.registry.write(VALUE_NAME, self.command)
        else:
            self.registry.delete(VALUE_NAME)
