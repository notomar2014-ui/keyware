"""Hold threshold and app launch must not wait on the catalog, a macro, or the hook.

The old launch path called the installed-app catalog on the action thread before
CreateProcess or ActivateApplication. That catalog walked the Start menu and
ran ``Get-StartApps`` (timeout 8 seconds). These tests fail if that work comes
back onto the path from "the hold time elapsed" to "the launch was requested".
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from engine.actions import SystemOpener, resolve_installed_app
from engine.applaunch import (
    APPS_PREFIX,
    Catalog,
    InstalledApp,
    clear_launch_cache,
    perform_launch,
    windows_catalog,
)
from engine.models import Action, Binding, MacroStep
from engine.service import HOLD_TICK_SECONDS, KeywareService
from engine.config_store import ConfigStore
from engine.startup import MemoryRegistry, StartupManager
from tests.fakes import Clock, FakeHook, RecordingAudio, RecordingInjector, RecordingOpener, service
from tests.test_detector import binding, detector, fires


CALC_AUMID = "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"
TERMINAL_AUMID = "Microsoft.WindowsTerminal_8wekyb3d8bbwe!App"
SYSTEM32 = r"C:\Windows\System32"
WINDOWSAPPS = r"C:\Users\A\AppData\Local\Microsoft\WindowsApps"
# Stand-in for the old catalog read. Production PowerShell was allowed 8 seconds.
_CATALOG_BLOCK_SECONDS = 0.35


def _raise(name):
    def _called(*_args, **_kwargs):
        raise AssertionError(f"{name} ran on the fast launch path")

    return _called


def test_calc_resolves_without_the_catalog_or_a_directory_listing():
    catalog_calls: list[str] = []
    which_calls: list[str] = []
    list_calls: list[str] = []
    path_calls: list[str] = []

    def slow_catalog():
        catalog_calls.append("catalog")
        time.sleep(_CATALOG_BLOCK_SECONDS)
        return Catalog()

    started = time.perf_counter()
    token = resolve_installed_app(
        "calc.exe",
        catalog_reader=slow_catalog,
        exists=lambda _path: False,
        which=lambda name: which_calls.append(name),
        app_path=lambda name: path_calls.append(name),
        list_dir=lambda path: list_calls.append(path),
        search_dirs=[SYSTEM32, WINDOWSAPPS],
    )
    elapsed = time.perf_counter() - started
    assert token == APPS_PREFIX + CALC_AUMID
    assert catalog_calls == []
    assert which_calls == []
    assert list_calls == []
    assert path_calls == []
    # A second launch is the cached token. It does not even stat System32 again.
    exists_calls: list[str] = []
    again = resolve_installed_app(
        "CALC.EXE",
        catalog_reader=slow_catalog,
        exists=lambda path: exists_calls.append(path) or False,
        which=_raise("which"),
        app_path=_raise("app_path"),
        list_dir=_raise("list_dir"),
        search_dirs=[SYSTEM32],
    )
    assert again == token
    assert exists_calls == []
    assert elapsed < 0.05, elapsed
    assert elapsed * 5 < _CATALOG_BLOCK_SECONDS


def test_a_real_system32_calc_is_that_file_and_still_skips_the_catalog():
    catalog_calls: list[str] = []
    wanted = SYSTEM32 + r"\calc.exe"

    def exists(path: str) -> bool:
        return path.replace("/", "\\").lower() == wanted.lower()

    token = resolve_installed_app(
        "calc.exe",
        catalog_reader=lambda: catalog_calls.append("catalog"),
        exists=exists,
        which=_raise("which"),
        app_path=_raise("app_path"),
        list_dir=_raise("list_dir"),
        search_dirs=[SYSTEM32, WINDOWSAPPS],
    )
    assert token.replace("/", "\\") == wanted
    assert catalog_calls == []


def test_notepad_on_path_does_not_list_windowsapps_or_read_the_catalog():
    listed: list[str] = []
    catalogs: list[str] = []
    path = r"C:\Windows\notepad.exe"

    def which(name: str):
        return path if name.lower() == "notepad.exe" else None

    token = resolve_installed_app(
        "notepad.exe",
        catalog_reader=lambda: catalogs.append("catalog"),
        exists=lambda _path: False,
        which=which,
        app_path=_raise("app_path"),
        list_dir=lambda directory: listed.append(directory),
        search_dirs=[SYSTEM32, WINDOWSAPPS],
    )
    assert token == path
    assert listed == []
    assert catalogs == []


def test_terminal_alias_uses_the_store_id_without_the_catalog():
    catalogs: list[str] = []

    def list_dir(path: str):
        if path.replace("/", "\\").rstrip("\\").lower() == WINDOWSAPPS.lower():
            return ["wt.exe"]
        raise AssertionError(f"listed {path}")

    token = resolve_installed_app(
        "wt",
        catalog_reader=lambda: catalogs.append("catalog"),
        exists=lambda _path: False,
        which=lambda _name: None,
        app_path=lambda _name: None,
        list_dir=list_dir,
        search_dirs=[SYSTEM32, WINDOWSAPPS],
    )
    assert token == APPS_PREFIX + TERMINAL_AUMID
    assert catalogs == []


def test_an_unknown_name_reads_the_catalog_once_and_then_uses_the_cache():
    shortcut = InstalledApp("Contoso", "shortcut", r"C:\Menu\Contoso.lnk")
    reads: list[int] = []

    def reader():
        reads.append(1)
        return Catalog(apps=[shortcut], packaged_enumerated=True)

    first = resolve_installed_app(
        "Contoso",
        catalog_reader=reader,
        exists=lambda _path: False,
        which=lambda _name: None,
        app_path=lambda _name: None,
        list_dir=lambda _path: [],
        search_dirs=[SYSTEM32, WINDOWSAPPS],
    )
    second = resolve_installed_app(
        "contoso",
        catalog_reader=reader,
        exists=_raise("exists"),
        which=_raise("which"),
        app_path=_raise("app_path"),
        list_dir=_raise("list_dir"),
        search_dirs=[SYSTEM32],
    )
    assert first == r"C:\Menu\Contoso.lnk"
    assert second == first
    assert reads == [1]


def test_a_missing_windowsapps_path_is_not_guessed_from_the_catalog():
    catalogs: list[str] = []
    path = r"C:\Program Files\WindowsApps\Microsoft.YourPhone_1.0_x64__8wekyb3d8bbwe\PhoneExperienceHost.exe"
    token = resolve_installed_app(
        path,
        catalog_reader=lambda: catalogs.append("catalog") or Catalog(packaged_enumerated=False),
        exists=lambda _path: False,
        which=_raise("which"),
        app_path=_raise("app_path"),
        list_dir=_raise("list_dir"),
        search_dirs=[SYSTEM32, WINDOWSAPPS],
    )
    assert token is None
    assert catalogs == []


def test_windows_catalog_does_not_run_powershell_on_the_caller(monkeypatch):
    import engine.applaunch as launch

    packaged: list[str] = []
    scheduled: list[str] = []
    monkeypatch.setattr(launch.sys, "platform", "win32")
    monkeypatch.setattr(launch, "_read_shortcuts", lambda: [])
    monkeypatch.setattr(launch, "_read_app_paths", lambda: [])
    monkeypatch.setattr(launch, "_read_known_paths", lambda: [])
    monkeypatch.setattr(launch, "_read_protocols", lambda: set())
    monkeypatch.setattr(launch, "_read_packaged", lambda: packaged.append("powershell") or ([], False))
    monkeypatch.setattr(launch, "_schedule_refresh", lambda: scheduled.append("background"))
    clear_launch_cache()
    catalog = windows_catalog()
    assert packaged == []
    assert scheduled == ["background"]
    assert catalog.packaged_enumerated is False

    stale = Catalog(apps=[InstalledApp("Discord", "shortcut", r"C:\Menu\Discord.lnk")])
    launch._CACHE = stale
    # time.monotonic() is seconds since boot. A stamp of 0 looks fresh on a
    # runner that has been up for less than the catalog TTL.
    launch._CACHE_AT = time.monotonic() - (launch._CATALOG_TTL + 1)
    scheduled.clear()
    again = windows_catalog()
    assert again.apps[0].name == "Discord"
    assert packaged == []
    # Older than the TTL: return it now, refresh off this thread.
    assert scheduled == ["background"]


def test_opening_calc_activates_the_store_app_without_a_shell(monkeypatch):
    import os
    import subprocess
    import sys

    import engine.applaunch as launch

    activated: list[str] = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os.path, "exists", lambda _path: False)
    monkeypatch.setattr(launch, "activate_aumid", lambda aumid: activated.append(aumid))

    def no_shell(*_args, **_kwargs):
        raise AssertionError("calc.exe must not start PowerShell, cmd, or explorer")

    monkeypatch.setattr(subprocess, "run", no_shell)
    monkeypatch.setattr(subprocess, "Popen", no_shell)
    SystemOpener().open_app("calc.exe")
    assert activated == [CALC_AUMID]


def test_packaged_activator_is_created_once_per_thread(monkeypatch):
    import sys

    import engine.applaunch as launch

    loads: list[int] = []
    seen: list[str] = []

    def load():
        loads.append(1)
        return object(), lambda _manager, aumid, _args, _flags, _pid: seen.append(aumid) or 0

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(launch, "_load_activator", load)
    assert launch.prepare_activation() is True
    assert launch.prepare_activation() is True
    launch._activate_com(CALC_AUMID)
    launch._activate_com(CALC_AUMID)
    assert loads == [1]
    assert seen == [CALC_AUMID, CALC_AUMID]
    source = Path("engine/applaunch.py").read_text(encoding="utf-8")
    body = source[source.index("def _load_activator"): source.index("def _guid")]
    assert "CoUninitialize" not in body


def test_hold_fires_at_three_seconds_while_the_key_is_still_down(tmp_path):
    clock = Clock()
    created, opener, injector, _audio, _hook = service(tmp_path, clock=clock)
    created.add_binding(
        "global",
        Binding(
            id="hold-c",
            enabled=True,
            mode="hold",
            keys=["c"],
            hold_ms=3000,
            action=Action(type="open_app", app="calc.exe"),
        ),
    )
    assert created.handle_input("c", True, 0.0) is True
    assert opener.apps == []
    assert created._detector.next_wake(0.0) == 3.0
    assert created._tick_delay() == HOLD_TICK_SECONDS
    created.tick(2.5)
    assert opener.apps == []
    assert "c" in created._detector.down_keys
    clock.now = 2.995
    assert created._tick_delay() == pytest.approx(0.005)
    snapshots: list[list[str]] = []
    created.maybe_reload = lambda: snapshots.append(list(opener.apps))
    clock.now = 3.0
    assert created._tick_delay() == 0.0
    created.tick(3.0)
    assert opener.apps == ["calc.exe"]
    assert snapshots == [["calc.exe"]]
    assert "c" in created._detector.down_keys
    created.handle_input("c", False, 3.1)
    assert opener.apps == ["calc.exe"]
    assert ("press", "c") not in injector.events
    assert created._tick_delay() == HOLD_TICK_SECONDS

    created.handle_input("c", True, 4.0)
    created.tick(4.2)
    assert opener.apps == ["calc.exe"]
    created.handle_input("c", False, 4.2)
    assert ("press", "c") in injector.events


def test_detector_hold_wakes_at_the_threshold_and_not_on_key_up():
    engine, _clock = detector(binding("hold", ["c"], id="hold-c", hold_ms=3000, action=Action(type="open_app", app="calc.exe")))
    engine.handle("c", True, 0.0)
    assert engine.next_wake(0.0) == 3.0
    assert fires(engine.tick(2.5)) == []
    assert "c" in engine.down_keys
    fired = fires(engine.tick(3.0))
    assert [effect.binding_id for effect in fired] == ["hold-c"]
    assert engine.next_wake(3.0) is None
    assert "c" in engine.down_keys
    released = engine.handle("c", False, 3.2)
    assert fires(released.effects) == []


def test_an_overdue_hold_does_not_spin_the_tick_thread(tmp_path):
    clock = Clock()
    created, _opener, *_rest = service(tmp_path, clock=clock)
    created.add_binding(
        "global",
        Binding(
            id="hold-c",
            enabled=True,
            mode="hold",
            keys=["c"],
            hold_ms=3000,
            action=Action(type="open_app", app="calc.exe"),
        ),
    )
    created.handle_input("c", True, 0.0)
    clock.now = 5.0
    waits: list[float] = []

    def fake_wait(delay):
        waits.append(delay)
        if len(waits) >= 4:
            created._stop.set()
            return True
        return False

    created._stop.wait = fake_wait
    created.tick = lambda *args, **kwargs: None
    created._tick_loop()
    assert waits == [0.0, 0.0, HOLD_TICK_SECONDS, HOLD_TICK_SECONDS]


def test_a_launch_is_not_blocked_by_a_macro_or_a_foreground_probe(tmp_path):
    opener = RecordingOpener()
    created = KeywareService(
        store=ConfigStore(tmp_path / "config.json"),
        opener=opener,
        injector=RecordingInjector(),
        audio=RecordingAudio(),
        startup=StartupManager(registry=MemoryRegistry(), command="keyware"),
        hook=FakeHook(),
        synchronous=False,
    )
    created.config.check_updates = False
    inside = threading.Event()
    gate = threading.Event()
    probes: list[str] = []

    def sleeper(_seconds: float) -> None:
        inside.set()
        gate.wait(2.0)

    def probe():
        probes.append(threading.current_thread().name)
        time.sleep(0.25)
        return 1

    created.executor.sleeper = sleeper
    created._window_probe = probe
    try:
        created._start_workers()
        created._start_action(
            Action(type="macro", steps=[MacroStep(op="delay", ms=5000)]),
            from_hook=True,
        )
        # The macro thread checks the foreground window before it types.
        # That is allowed. The launch must not wait for those checks or for the delay.
        assert inside.wait(2.0)
        probes_before = len(probes)
        started = time.perf_counter()
        created._start_action(Action(type="open_app", app="calc.exe"), from_hook=True)
        created._start_action(Action(type="open_url", url="https://example.com"), from_hook=True)
        queued_in = time.perf_counter() - started
        deadline = started + 0.5
        while time.perf_counter() < deadline and (
            opener.apps != ["calc.exe"] or opener.urls != ["https://example.com"]
        ):
            time.sleep(0.01)
        elapsed = time.perf_counter() - started
        assert opener.apps == ["calc.exe"], [item.text for item in created.poll()]
        assert opener.urls == ["https://example.com"]
        assert len(probes) == probes_before
        assert not gate.is_set()
        # One foreground read is 0.25s. Queueing and running both launches stays under that.
        assert queued_in < 0.05
        assert elapsed < 0.25
    finally:
        gate.set()
        created.shutdown()


def test_perform_launch_of_a_resolved_calc_token_only_activates():
    seen: list[str] = []
    perform_launch(
        APPS_PREFIX + CALC_AUMID,
        [],
        exists=lambda _path: False,
        startfile=lambda *_a, **_k: seen.append("start"),
        popen=lambda *_a, **_k: seen.append("popen"),
        activate=lambda aumid: seen.append(aumid),
        trust=True,
    )
    assert seen == [CALC_AUMID]
