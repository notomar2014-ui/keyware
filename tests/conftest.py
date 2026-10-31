"""Block every side effect the engine must never perform during tests."""

from __future__ import annotations

import os
import subprocess
import sys
import types

import pytest


def _boom(*_args, **_kwargs):
    raise AssertionError("A test tried to touch the real keyboard, an app, audio, or the network.")


@pytest.fixture(autouse=True)
def no_real_ai_keys(monkeypatch):
    """Real keys in the developer's shell must never reach a test (or its failure output)."""
    for name in ("GEMINI_API_KEY", "GROQ_API_KEY", "KEYWARE_DEV_KEYS", "ASSEMBLYAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def block_real_side_effects(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(subprocess, "call", _boom)
    monkeypatch.setattr(subprocess, "run", _boom)
    if hasattr(os, "startfile"):
        monkeypatch.setattr(os, "startfile", _boom)
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", _boom)
    monkeypatch.setattr(webbrowser, "open_new", _boom)
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _boom)  # the AI binder is always mocked in tests
    fake_audio = types.ModuleType("winsound")
    fake_audio.Beep = _boom
    fake_audio.PlaySound = _boom
    fake_audio.SND_ALIAS = 0
    monkeypatch.setitem(sys.modules, "winsound", fake_audio)


def _patch_flaky_tk_init():
    """Windows Tcl sometimes fails to start the Nth Tk() in one process ("tcl_findLibrary").

    That is a test-runner quirk (the app makes one window per process), so retry it here.
    """
    try:
        import tkinter
    except Exception:  # no Tk on this machine; GUI tests skip/fail on their own
        return
    original = tkinter.Tk.__init__
    if getattr(original, "_keyware_retry", False):
        return

    def init_with_retry(self, *args, **kwargs):
        import time

        for attempt in range(4):
            try:
                return original(self, *args, **kwargs)
            except tkinter.TclError as exc:
                if "tcl_findLibrary" not in str(exc) and "init.tcl" not in str(exc) or attempt == 3:
                    raise
                time.sleep(0.2)

    init_with_retry._keyware_retry = True
    tkinter.Tk.__init__ = init_with_retry


_patch_flaky_tk_init()
