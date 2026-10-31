"""In-process smoke test. It never opens an app, types, or plays a sound."""

from __future__ import annotations

import tempfile
from pathlib import Path

from engine.audio_cue import AudioCue
from engine.config_store import ConfigStore
from engine.service import KeywareService
from engine.startup import MemoryRegistry, StartupManager


class _Opener:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.apps: list[str] = []

    def open_url(self, url: str) -> None:
        self.urls.append(url)

    def open_app(self, app: str) -> None:
        self.apps.append(app)


class _Injector:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def press(self, key: str) -> None:
        self.events.append(("press", key))

    def release(self, key: str) -> None:
        self.events.append(("release", key))

    def type_text(self, text: str) -> None:
        self.events.append(("text", text))

    def click(self, button: str) -> None:
        self.events.append(("click", button))

    def mask(self) -> None:
        self.events.append(("mask",))


class _Hook:
    def __init__(self) -> None:
        self.running = False

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def is_running(self) -> bool:
        return self.running


def run() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as folder:
        opener = _Opener()
        injector = _Injector()
        service = KeywareService(
            store=ConfigStore(Path(folder) / "config.json"),
            opener=opener,
            injector=injector,
            audio=AudioCue(lambda *_args: None),
            startup=StartupManager(registry=MemoryRegistry(), command="keyware"),
            hook=_Hook(),
            synchronous=True,
        )
        message = service.set_enabled(True)
        if message:
            failures.append(f"enable: {message}")
        for key in ("ctrl", "win", "shift"):
            if service.handle_input(key, True, 0.0):
                failures.append(f"modifier swallowed: {key}")
        if not service.handle_input("r", True, 0.0):
            failures.append("YouTube chord was not swallowed")
        if opener.urls != ["https://www.youtube.com"]:
            failures.append(f"youtube url: {opener.urls}")
        if service.handle_input("z", True, 0.2):
            failures.append("unbound key was swallowed")
        for key in ("r", "z", "ctrl", "win", "shift"):
            service.handle_input(key, False, 0.3)
        service.set_foreground("javaw.exe")
        service.handle_input("q", True, 1.0)
        service.tick(1.8)
        if ("press", "shift") not in injector.events:
            failures.append("gaming tier 1 did not press shift")
        service.tick(3.0)
        if ("click", "right") not in injector.events:
            failures.append("gaming tier 2 did not right-click")
        for key in ("ctrl", "alt", "shift"):
            service.handle_input(key, True, 4.0)
        service.handle_input("esc", True, 4.0)
        if service.hook.is_running():
            failures.append("panic left the hook running")
        if service.handle_input("r", True, 5.0):
            failures.append("key swallowed after panic")
    if failures:
        print("Keyware self-test failed:")
        for item in failures:
            print(f"  {item}")
        return 1
    print("Keyware self-test passed.")
    return 0
