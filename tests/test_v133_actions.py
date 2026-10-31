"""v1.3.3 single actions and in-app macro steps. Fully mocked: nothing opens, locks, runs, fetches or plays."""

from __future__ import annotations

import json
import random

import pytest

from assist.ai_binder import parse_batch, parse_reply
from engine.actions import ActionExecutor, find_spotify_item, spotify_web_url
from engine.models import (
    Action, Config, MacroStep, command_fingerprint, parse_media_text, step_summary,
    validate_action, validate_command, validate_path, validate_step,
)

from tests.fakes import RecordingInjector, RecordingOpener, service

TRACK = "3SMiEsSLbdssYbZjyH5f04"
DDG = "https://html.duckduckgo.com/"
SPOTIFY_PAGE = f'<a href="https://open.spotify.com/track/{TRACK}">Du bist genug</a>'
VIDEO = "dQw4w9WgXcQ"


def make(installed=None, pages=None, windows=None, confirm=None):
    opener = RecordingOpener(installed=installed, pages=pages, windows=windows)
    injector = RecordingInjector()
    sleeps: list[float] = []
    created = ActionExecutor(opener, injector, sleeps.append, random.Random(1))
    created.confirm = confirm
    return created, opener, injector, sleeps


# ---- single actions -------------------------------------------------------

def test_open_path_expands_and_opens(monkeypatch):
    monkeypatch.setenv("USERPROFILE", r"C:\Users\Test")
    created, opener, _i, _s = make()
    created.run(Action(type="open_path", path=r"%USERPROFILE%\Documents"))
    assert opener.paths == [r"C:\Users\Test\Documents"]


@pytest.mark.parametrize("path", [r"C:\x\run.exe", r"C:\x\a.bat", r"C:\x\s.ps1", "https://x.com", r"C:\a & del", r"C:\l.lnk"])
def test_open_path_refuses_programs_and_shell_text(path):
    created, opener, _i, _s = make()
    with pytest.raises(ValueError):
        created.run(Action(type="open_path", path=path))
    assert opener.paths == []


def test_media_taps_count_times_and_never_more_than_50():
    created, _o, injector, _s = make()
    created.run(Action(type="media", media="volume_up", count=5))
    assert injector.events.count(("press", "volume_up")) == 5
    assert injector.events.count(("release", "volume_up")) == 5
    with pytest.raises(ValueError):
        validate_action(Action(type="media", media="volume_up", count=51))
    with pytest.raises(ValueError):
        validate_action(Action(type="media", media="set_default_device", count=1))


def test_lock_and_screenshot():
    created, opener, injector, _s = make()
    created.run(Action(type="lock_screen"))
    created.run(Action(type="screenshot"))
    assert opener.locks == 1
    pressed = [key for kind, key in injector.events if kind == "press"]
    assert pressed == ["win", "print_screen"] or set(pressed) == {"win", "print_screen"}


def test_run_command_needs_confirmation():
    created, opener, _i, _s = make(confirm=None)
    with pytest.raises(ValueError, match="not confirmed"):
        created.run(Action(type="run_command", command="code C:\\projects"))
    created.confirm = lambda text: False
    with pytest.raises(ValueError, match="not confirmed"):
        created.run(Action(type="run_command", command="code C:\\projects"))
    asked: list[str] = []
    created.confirm = lambda text: asked.append(text) or True
    created.run(Action(type="run_command", command="code C:\\projects"))
    assert opener.commands == ["code C:\\projects"] and asked == ["code C:\\projects"]


@pytest.mark.parametrize("command", [
    "shutdown /s /t 0", "shutdown -r", "logoff", "Stop-Computer", "Restart-Computer", "del C:\\x", "erase a",
    "rd /s C:\\x", "rmdir x", "Remove-Item x", "rm -rf /", "format C:", "diskpart", "reg delete HKCU\\x",
    "powershell -enc AAAA", "iex (x)", "certutil -urlcache", "nircmd setdefaultsounddevice x", "a\nb",
    "SetDefaultSoundDevice", "x" * 501,
])
def test_dangerous_commands_are_refused_before_asking(command):
    asked: list[str] = []
    created, opener, _i, _s = make(confirm=lambda text: asked.append(text) or True)
    with pytest.raises(ValueError):
        created.run(Action(type="run_command", command=command))
    assert opener.commands == [] and asked == []
    with pytest.raises(ValueError):
        validate_command(command)


def test_service_asks_once_per_exact_command_and_remembers(tmp_path):
    created, opener, _injector, _audio, _hook = service(tmp_path)
    created.run_action(Action(type="run_command", command="code C:\\site"))
    assert opener.commands == []  # no confirmer: never runs
    asked: list[str] = []
    created.command_confirmer = lambda text: asked.append(text) or True
    created.run_action(Action(type="run_command", command="code C:\\site"))
    created.run_action(Action(type="run_command", command="code   C:\\site"))
    assert opener.commands == ["code C:\\site", "code   C:\\site"] and len(asked) == 1
    assert command_fingerprint("code C:\\site") in created.config.approved_commands
    created.run_action(Action(type="run_command", command="code C:\\other"))
    assert len(asked) == 2
    reloaded = Config.from_dict(created.config.to_dict())
    assert reloaded.approved_commands == created.config.approved_commands


def test_corrupt_approved_commands_are_dropped():
    raw = Config().to_dict()
    raw["approved_commands"] = ["nope", 5, None, "a" * 64, "0" * 64]
    assert Config.from_dict(raw).approved_commands == ["a" * 64, "0" * 64]
    raw["approved_commands"] = "garbage"
    assert Config.from_dict(raw).approved_commands == []


def test_confirmer_crash_means_no():
    def boom(_text):
        raise RuntimeError("dialog failed")

    created, opener, _i, _s = make()
    created.confirm = boom
    with pytest.raises(RuntimeError):
        created.run(Action(type="run_command", command="code ."))
    assert opener.commands == []


def test_service_confirmer_crash_means_no(tmp_path):
    created, opener, *_ = service(tmp_path)
    created.command_confirmer = lambda text: 1 / 0
    created.run_action(Action(type="run_command", command="code ."))
    assert opener.commands == []


# ---- web first ------------------------------------------------------------

def test_open_app_installed_passes_args():
    created, opener, _i, _s = make(installed={"chrome"})
    created.run(Action(type="open_app", app="chrome.exe", args=["https://classroom.google.com"], fallback=""))
    assert opener.apps == ["chrome.exe"] and opener.app_args == [["https://classroom.google.com"]]


def test_open_app_missing_falls_back_to_its_links_in_the_default_browser():
    created, opener, _i, _s = make(installed=set())
    created.run(Action(type="open_app", app="chrome.exe", args=["https://a.com", "https://b.com"]))
    assert opener.apps == [] and opener.urls == ["https://a.com", "https://b.com"]


def test_open_app_missing_uses_website_fallback_or_says_not_installed():
    created, opener, _i, _s = make(installed=set())
    created.run(Action(type="open_app", app="spotify.exe", fallback="https://open.spotify.com"))
    assert opener.urls == ["https://open.spotify.com"]
    with pytest.raises(ValueError, match="not installed"):
        created.run(Action(type="open_app", app="figma.exe"))


def test_spotify_links_go_to_the_web_player_without_the_app():
    created, opener, _i, _s = make(installed=set())
    created.run(Action(type="open_url", url=f"spotify:track:{TRACK}"))
    assert opener.urls == [f"https://open.spotify.com/track/{TRACK}"]
    assert spotify_web_url("spotify:search:du%20bist") == "https://open.spotify.com/search/du%20bist"


def test_spotify_link_kept_when_the_app_is_installed():
    created, opener, _i, _s = make(installed={"spotify"})
    created.run(Action(type="open_url", url=f"spotify:track:{TRACK}"))
    assert opener.urls == [f"spotify:track:{TRACK}"]


def test_play_spotify_web_player_by_default():
    created, opener, injector, _s = make(installed=set(), pages={DDG: SPOTIFY_PAGE})
    created.run(Action(type="play", service="spotify", text="du bist gueng"))
    assert opener.urls == [f"https://open.spotify.com/track/{TRACK}?autoplay=true"]
    assert opener.fetched[0].startswith(DDG) and "du+bist+gueng" in opener.fetched[0]
    assert injector.events == []  # nothing typed into the browser


def test_play_spotify_falls_back_to_bing_then_search_page():
    created, opener, _i, _s = make(installed=set(), pages={"https://www.bing.com/": SPOTIFY_PAGE})
    created.run(Action(type="play", service="spotify", text="Du bist genug"))
    assert opener.urls == [f"https://open.spotify.com/track/{TRACK}?autoplay=true"]
    created, opener, _i, _s = make(installed=set(), pages={})
    created.run(Action(type="play", service="spotify", text="Du bist genug"))
    assert opener.urls == ["https://open.spotify.com/search/Du%20bist%20genug"]


def test_play_spotify_app_when_installed_presses_enter_after_window():
    created, opener, injector, sleeps = make(installed={"spotify"}, pages={DDG: SPOTIFY_PAGE})
    created.run(Action(type="play", service="spotify", text="du bist genug"))
    assert opener.urls == [f"spotify:track:{TRACK}"]
    assert opener.waits and opener.waits[0][0] == "spotify.exe"
    assert ("press", "enter") in injector.events


def test_play_youtube_opens_first_video_or_results():
    page = f'..."videoId":"{VIDEO}"...'
    created, opener, _i, _s = make(pages={"https://www.youtube.com/results": page})
    created.run(Action(type="play", service="youtube", text="lofi beats"))
    assert opener.urls == [f"https://www.youtube.com/watch?v={VIDEO}"]
    created, opener, _i, _s = make(pages={})
    created.run(Action(type="play", service="youtube", text="lofi beats"))
    assert opener.urls == ["https://www.youtube.com/results?search_query=lofi+beats"]


def test_fetch_errors_never_break_play():
    created, opener, _i, _s = make(installed=set())

    def broken(_url):
        raise OSError("offline")

    opener.fetch_text = broken
    created.run(Action(type="play", service="spotify", text="x"))
    assert opener.urls == ["https://open.spotify.com/search/x"]


def test_find_spotify_item_ignores_junk():
    assert find_spotify_item(lambda url: "open.spotify.com/track/short", "x") is None
    assert find_spotify_item(lambda url: f"open.spotify.com/playlist/{TRACK}", "x") == ("playlist", TRACK)


# ---- multi-step in-app macros ------------------------------------------------

def spotify_macro():
    return Action(type="macro", steps=[
        MacroStep(op="app", target="chrome.exe", args=[], fallback="https://www.google.com"),
        MacroStep(op="play", target="spotify", text="du bist gueng"),
    ])


def test_f5_opens_chrome_and_plays_on_spotify_web_without_chrome():
    created, opener, _i, _s = make(installed=set(), pages={DDG: SPOTIFY_PAGE})
    created.run(spotify_macro())
    assert opener.urls == ["https://www.google.com", f"https://open.spotify.com/track/{TRACK}?autoplay=true"]


def test_wait_for_window_then_type_and_guard_follows_new_window():
    created, opener, injector, _s = make(windows={"notepad.exe"})
    # The original window loses focus once notepad opens: expected, so later steps follow notepad.
    created.guard = lambda: not opener.apps
    created.run(Action(type="macro", steps=[
        MacroStep(op="app", target="notepad.exe"),
        MacroStep(op="wait_window", target="notepad.exe", ms=8000),
        MacroStep(op="text", text="hello"),
    ]))
    assert opener.waits == [("notepad.exe", "", 8.0)]
    assert ("text", "hello") in injector.events


def test_wait_for_window_timeout_stops_the_macro():
    created, opener, injector, _s = make(windows=set())
    with pytest.raises(ValueError, match="Gave up waiting"):
        created.run(Action(type="macro", steps=[
            MacroStep(op="wait_window", target="ghost.exe", ms=1000),
            MacroStep(op="text", text="never"),
        ]))
    assert injector.events == []


def test_cancel_during_macro_stops_before_next_step():
    created, opener, _i, _s = make()
    created.cancel()
    created.run(Action(type="macro", steps=[MacroStep(op="url", target="https://a.com")]))
    assert opener.urls == []


def test_work_mode_macro():
    created, opener, injector, _s = make(installed={"code"})
    created.confirm = lambda text: True
    created.run(Action(type="macro", steps=[
        MacroStep(op="app", target="code"),
        MacroStep(op="url", target="https://github.com"),
        MacroStep(op="path", target=r"C:\Users\Me\Documents"),
        MacroStep(op="media", key="volume_mute", count=1),
    ]))
    assert opener.apps == ["code"] and opener.urls == ["https://github.com"]
    assert opener.paths == [r"C:\Users\Me\Documents"]
    assert ("press", "volume_mute") in injector.events


@pytest.mark.parametrize("step", [
    MacroStep(op="url", target="javascript:alert(1)"),
    MacroStep(op="app", target="calc & del"),
    MacroStep(op="path", target=r"C:\x.exe"),
    MacroStep(op="media", key="volume_up", count=99),
    MacroStep(op="wait_window", target="", text="", ms=1000),
    MacroStep(op="wait_window", target="a.exe", ms=10),
    MacroStep(op="play", target="tidal", text="x"),
    MacroStep(op="play", target="spotify", text=""),
])
def test_bad_steps_rejected(step):
    with pytest.raises(ValueError):
        validate_step(step)


def test_macro_roundtrip_keeps_every_new_step():
    action = Action(type="macro", steps=[
        MacroStep(op="app", target="chrome.exe", args=["https://a.com"], fallback="https://b.com"),
        MacroStep(op="wait_window", target="chrome.exe", ms=5000),
        MacroStep(op="play", target="spotify", text="Du bist genug"),
        MacroStep(op="media", key="volume_down", count=3),
        MacroStep(op="path", target=r"C:\a"),
        MacroStep(op="lock"),
        MacroStep(op="screenshot"),
        MacroStep(op="url", target="https://c.com"),
    ])
    again = Action.from_dict(action.to_dict())
    assert again.to_dict() == action.to_dict()
    validate_action(again)
    assert "Play “Du bist genug” on Spotify" in [step_summary(step) for step in again.steps]


def test_media_text_parsing():
    assert parse_media_text("Volume up x5") == ("volume_up", 5)
    assert parse_media_text("mute") == ("volume_mute", 1)
    assert parse_media_text("Play/Pause") == ("media_play_pause", 1)
    with pytest.raises(ValueError):
        parse_media_text("set output to headphones")


def test_validate_path_and_play_helpers():
    assert validate_path(r"C:\Users\Me\Music") == r"C:\Users\Me\Music"


# ---- AI multi-step -------------------------------------------------------------

def test_ai_spotify_macro_from_reply():
    reply = {"bindings": [{
        "label": "Chrome + Du bist genug", "profile": {"scope": "global"},
        "trigger": {"mode": "tap", "keys": ["f5"]},
        "action": {"type": "macro", "steps": [
            {"op": "app", "target": "chrome.exe", "fallback": "https://www.google.com"},
            {"op": "play", "target": "spotify", "text": "Du bist genug"},
        ]},
    }]}
    proposal = parse_reply(json.dumps(reply))
    steps = proposal.binding.action.steps
    assert [step.op for step in steps] == ["app", "play"]
    assert steps[1].target == "spotify" and steps[1].text == "Du bist genug"


def test_ai_work_mode_and_refuses_power():
    reply = {"bindings": [{
        "label": "Work mode", "profile": {"scope": "global"},
        "trigger": {"mode": "chord", "keys": ["ctrl", "alt", "w"]},
        "action": {"type": "macro", "steps": [
            {"op": "app", "target": "code"},
            {"op": "url", "target": "https://github.com"},
            {"op": "media", "key": "volume_mute"},
        ]},
    }]}
    proposal = parse_reply(json.dumps(reply))
    assert len(proposal.binding.action.steps) == 3
    bad = {"bindings": [{"label": "x", "profile": {"scope": "global"}, "trigger": {"mode": "tap", "keys": ["f12"]},
                         "action": {"type": "run_command", "command": "shutdown /s /t 0"}}]}
    with pytest.raises(Exception, match="will not run"):
        parse_batch(json.dumps(bad))


DDG_RESULTS = """
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fopen.spotify.com%2Fintl-de%2Falbum%2F7A9LNd776FuCiUsPmtAcuX&amp;rut=x">Du bist genug - Single von True Voices | Spotify</a>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fopen.spotify.com%2Ftrack%2F33wNkLCLs9nedRm3b3v7l7&amp;rut=x">Du bist genug - song and lyrics by Lumbematz | Spotify</a>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fopen.spotify.com%2Fshow%2F7i288PKGKDfbOpPr9yCUKr">DU BIST GENUG - Dein Podcast | Spotify</a>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fopen.spotify.com%2Ftrack%2F4G7RfaPhLMLziClsoj4GfE">Du Bist Gut Genug Phonk - song and lyrics by HomoBeats | Spotify</a>
"""


def test_spotify_ranking_prefers_the_closest_song_even_misspelled():
    from engine.actions import best_spotify_item, spotify_candidates

    candidates = spotify_candidates(DDG_RESULTS)
    assert [kind for kind, *_ in candidates] == ["album", "track", "track"]  # the podcast "show" is skipped
    assert best_spotify_item("du bist gueng", candidates) == ("track", "33wNkLCLs9nedRm3b3v7l7")
    assert best_spotify_item("Du bist genug Lumbematz", candidates) == ("track", "33wNkLCLs9nedRm3b3v7l7")
    assert best_spotify_item("completely different song", candidates) is None


def test_spotify_ranking_reads_bing_rss():
    from engine.actions import spotify_candidates

    rss = "<item><title>Du bist genug - song by X | Spotify</title><link>https://open.spotify.com/track/33wNkLCLs9nedRm3b3v7l7</link></item>"
    assert spotify_candidates(rss) == [("track", "33wNkLCLs9nedRm3b3v7l7", "Du bist genug - song by X | Spotify")]
