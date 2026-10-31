"""Action executor. Sleep and input are fakes, so nothing is pressed or opened."""

from __future__ import annotations

import random

import pytest

from engine.actions import ActionExecutor, Cancelled, SystemOpener
from engine.models import Action, MacroStep

from tests.fakes import RecordingInjector, RecordingOpener


def executor(rng=None, sleeper=None, **kwargs):
    opener = RecordingOpener()
    injector = RecordingInjector()
    sleeps: list[float] = []

    def record(seconds: float) -> None:
        sleeps.append(seconds)
        if sleeper is not None:
            sleeper(seconds)

    created = ActionExecutor(
        opener,
        injector,
        record,
        rng or random.Random(1),
        jitter_min_ms=kwargs.get("jitter_min_ms", 5),
        jitter_max_ms=kwargs.get("jitter_max_ms", 15),
    )
    return created, opener, injector, sleeps


def test_url_app_and_text_use_the_seams():
    created, opener, injector, _sleeps = executor()
    created.run(Action(type="open_url", url="https://www.youtube.com"))
    created.run(Action(type="open_app", app="notepad.exe"))
    created.run(Action(type="type_text", text="console.log();"))
    assert opener.urls == ["https://www.youtube.com"]
    assert opener.apps == ["notepad.exe"]
    assert injector.events == [("text", "console.log();")]


def test_url_and_app_reject_unsafe_targets():
    created, opener, _injector, _sleeps = executor()
    for url in ("javascript:alert(1)", "file:///C:/Windows", "https://", "notes"):
        with pytest.raises(ValueError):
            created.run(Action(type="open_url", url=url))
    with pytest.raises(ValueError):
        created.run(Action(type="open_app", app="calc.exe & notepad"))
    assert opener.urls == []
    assert opener.apps == []


def test_key_combo_holds_keys_together_with_bounded_jitter():
    created, _opener, injector, sleeps = executor(rng=random.Random(2))
    created.run(Action(type="key_combo", keys=["shift", "1"]))
    assert injector.events == [
        ("press", "shift"),
        ("press", "1"),
        ("release", "1"),
        ("release", "shift"),
    ]
    assert len(sleeps) == 4
    assert all(0.005 <= pause <= 0.015 for pause in sleeps)


def test_macro_keeps_explicit_delays_and_step_order():
    created, _opener, injector, sleeps = executor(rng=random.Random(3))
    created.run(
        Action(
            type="macro",
            steps=[
                MacroStep(op="key", key="1"),
                MacroStep(op="delay", ms=50),
                MacroStep(op="mouse", button="right"),
                MacroStep(op="delay", ms=20),
                MacroStep(op="key", key="2"),
            ],
        )
    )
    assert injector.events == [
        ("press", "1"),
        ("release", "1"),
        ("click", "right"),
        ("press", "2"),
        ("release", "2"),
    ]
    assert 0.05 in sleeps
    assert 0.02 in sleeps
    jitters = [pause for pause in sleeps if pause != 0.05 and pause != 0.02]
    assert jitters
    assert all(0.005 <= pause <= 0.015 for pause in jitters)


def test_panic_cancels_the_rest_of_a_macro_and_releases_held_keys():
    created, _opener, injector, _sleeps = executor()

    def cancel_on_delay(seconds: float) -> None:
        if seconds >= 0.05:
            created.cancel()

    created.sleeper = cancel_on_delay
    # The replacement sleeper skipped recording. Wrap both.
    pauses: list[float] = []

    def both(seconds: float) -> None:
        pauses.append(seconds)
        cancel_on_delay(seconds)

    created.sleeper = both
    created.run(
        Action(
            type="macro",
            steps=[
                MacroStep(op="key", key="1"),
                MacroStep(op="delay", ms=50),
                MacroStep(op="mouse", button="right"),
                MacroStep(op="key", key="2"),
            ],
        )
    )
    assert ("click", "right") not in injector.events
    assert ("press", "2") not in injector.events
    assert injector.events[0] == ("press", "1")
    assert ("release", "1") in injector.events


def test_cancelled_combo_does_not_leave_a_key_down():
    created, _opener, injector, _sleeps = executor()

    def cancel_immediately(_seconds: float) -> None:
        created.cancel()

    created.sleeper = cancel_immediately
    created.run(Action(type="key_combo", keys=["w", "ctrl"]))
    presses = [key for kind, key in injector.events if kind == "press"]
    releases = [key for kind, key in injector.events if kind == "release"]
    assert presses == ["w"]
    assert releases == ["w"]


def test_system_opener_uses_startfile_and_never_a_shell(monkeypatch):
    import os
    import subprocess
    import sys

    calls: list[str] = []
    popens: list[tuple] = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "startfile", lambda target: calls.append(target), raising=False)
    monkeypatch.setattr(os.path, "exists", lambda _path: False)
    monkeypatch.setattr("engine.actions.shutil.which", lambda name: r"C:\Windows\notepad.exe" if name == "notepad.exe" else None)

    def fake_popen(args, **kwargs):
        popens.append((args, kwargs))
        return None

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    opener = SystemOpener()
    opener.open_url("https://www.youtube.com")
    opener.open_app("notepad.exe")
    assert calls == ["https://www.youtube.com"]
    assert popens[0][0] == [r"C:\Windows\notepad.exe"]
    assert popens[0][1]["shell"] is False


def test_cancelled_exception_is_internal():
    assert issubclass(Cancelled, Exception)


def test_letters_inject_by_virtual_key_not_by_character():
    from pathlib import Path

    source = Path("engine/actions.py").read_text(encoding="utf-8")
    start = source.index("def _pynput_key")
    body = source[start:]
    assert "KeyCode.from_char" not in body
    assert "KeyCode.from_vk" in body
