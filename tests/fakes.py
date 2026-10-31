"""In-memory doubles. They record intent and never touch the OS."""

from __future__ import annotations

from engine.audio_cue import AudioCue
from engine.config_store import ConfigStore
from engine.service import KeywareService
from engine.startup import MemoryRegistry, StartupManager


class Clock:
    def __init__(self, now: float = 0.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, ms: float) -> float:
        self.now += ms / 1000
        return self.now


class RecordingOpener:
    """Records what would be opened. Never launches, locks, runs or fetches anything for real.

    installed: None = every app counts as installed; otherwise the set of installed app stems.
    pages: canned web pages for fetch_text, by URL prefix.
    windows: window names that "appear" for wait_window (None = every wait succeeds).
    """

    def __init__(self, installed: set[str] | None = None, pages: dict[str, str] | None = None,
                 windows: set[str] | None = None):
        self.urls: list[str] = []
        self.apps: list[str] = []
        self.app_args: list[list[str]] = []
        self.paths: list[str] = []
        self.commands: list[str] = []
        self.locks = 0
        self.waits: list[tuple[str, str, float]] = []
        self.fetched: list[str] = []
        self.installed = installed
        self.pages = pages or {}
        self.windows = windows

    def open_url(self, url: str) -> None:
        self.urls.append(url)

    def open_app(self, app: str, args: list[str] | None = None) -> None:
        self.apps.append(app)
        self.app_args.append(list(args or []))

    def resolve_app(self, app: str) -> str | None:
        if self.installed is None:
            return app
        stem = app.lower().replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".exe")
        return app if stem in self.installed else None

    def open_path(self, path: str) -> None:
        self.paths.append(path)

    def run_command(self, command: str) -> None:
        self.commands.append(command)

    def lock_screen(self) -> None:
        self.locks += 1

    def wait_window(self, process: str, title: str, timeout: float, cancelled) -> bool:
        self.waits.append((process, title, timeout))
        if self.windows is None:
            return True
        return any(name and name.lower() in self.windows for name in (process, title))

    def fetch_text(self, url: str) -> str:
        self.fetched.append(url)
        for prefix, page in self.pages.items():
            if url.startswith(prefix):
                return page
        return ""


class RecordingInjector:
    def __init__(self):
        self.events: list[tuple] = []

    def press(self, key: str) -> None:
        self.events.append(("press", key))

    def release(self, key: str) -> None:
        self.events.append(("release", key))

    def type_text(self, text: str) -> None:
        self.events.append(("text", text))

    def click(self, button: str) -> None:
        self.events.append(("click", button))

    def mouse_down(self, button: str) -> None:
        self.events.append(("mouse_down", button))

    def mouse_up(self, button: str) -> None:
        self.events.append(("mouse_up", button))

    def mask(self) -> None:
        self.events.append(("mask",))


class RecordingAudio(AudioCue):
    def __init__(self):
        self.played: list[int] = []
        super().__init__(self._play, enabled=True)

    def _play(self, hz: int, _duration_ms: int) -> None:
        self.played.append(hz)


class FakeHook:
    def __init__(self):
        self.running = False
        self.starts = 0
        self.stops = 0

    def start(self) -> None:
        self.starts += 1
        self.running = True

    def stop(self) -> None:
        self.stops += 1
        self.running = False

    def is_running(self) -> bool:
        return self.running


def service(tmp_path, **kwargs) -> tuple[KeywareService, RecordingOpener, RecordingInjector, RecordingAudio, FakeHook]:
    opener = RecordingOpener()
    injector = RecordingInjector()
    audio = RecordingAudio()
    hook = FakeHook()
    clock = kwargs.pop("clock", Clock())
    registry = MemoryRegistry()
    startup = StartupManager(registry=registry, command=r'"C:\Keyware\Keyware.exe"')
    store = ConfigStore(tmp_path / "config.json")
    rng = kwargs.pop("rng", None)
    created = KeywareService(
        store=store,
        opener=opener,
        injector=injector,
        audio=audio,
        startup=startup,
        hook=hook,
        clock=clock,
        rng=rng,
        synchronous=True,
    )
    created.executor.sleeper = kwargs.pop("sleeper", created.executor.sleeper)
    return created, opener, injector, audio, hook
