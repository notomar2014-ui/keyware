"""Keyware 1.4.0: packaged-app launch, Tab and navigation keys, update check."""

from __future__ import annotations

import json

import pytest

from assist.ai_binder import parse_reply
from engine import __version__
from engine.applaunch import (
    APPS_PREFIX,
    Catalog,
    InstalledApp,
    canonical_app_query,
    not_installed_message,
    perform_launch,
    resolve_launch,
)
from engine.detector import InputDetector
from engine.hook_engine import HookEngine
from engine.keys import canonicalize, format_keys, insert_key, parse_key_expression
from engine.models import Action, Binding, Config
from engine.sendinput import KEYEVENTF_EXTENDEDKEY, KEYEVENTF_SCANCODE, TaggedInjector
from assist.update_check import fetch_latest_version
from engine.updates import (
    DOWNLOAD_URL,
    GITHUB_LATEST,
    STORE_URL,
    check_due,
    is_newer,
    notice_for,
    parse_release_version,
)
from tests.fakes import service
from tests.test_hook_and_safety import _Listener, _Suppress


def _catalog(*apps, protocols=(), enumerated=True) -> Catalog:
    return Catalog(apps=list(apps), protocols=set(protocols), packaged_enumerated=enumerated)


def test_version_is_current():
    assert __version__ == "1.4.1"


def test_phone_link_uses_the_store_id_not_a_missing_exe():
    apps = [InstalledApp("Phone Link", "packaged", "Microsoft.YourPhone_8wekyb3d8bbwe!App")]
    found = resolve_launch("Phone Link", _catalog(*apps), exists=lambda _p: False)
    assert found.launch is not None
    assert found.launch.kind == "packaged"
    assert found.launch.target.endswith("!App")
    # Enumeration failed (offline Start menu): the inbox id is still tried.
    inbox = resolve_launch("YourPhone.exe", Catalog(packaged_enumerated=False), exists=lambda _p: False)
    assert inbox.launch is not None and inbox.launch.kind == "packaged"
    # The list was read and Phone Link is not on this PC.
    missing = resolve_launch("Phone Link", _catalog(InstalledApp("Calculator", "packaged", "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App")))
    assert missing.launch is None


def test_a_made_up_windowsapps_path_does_not_get_launched():
    path = r"C:\Program Files\WindowsApps\Microsoft.YourPhone_1.0_x64__8wekyb3d8bbwe\PhoneExperienceHost.exe"
    result = resolve_launch(path, _catalog(), exists=lambda _p: False)
    assert result.launch is None
    assert result.missing_path == path
    message = not_installed_message(path)
    assert "does not exist" in message
    assert "Phone Link" in message
    started = []

    def boom(*_args, **_kwargs):
        started.append(True)
        raise AssertionError("a missing exe must not be started")

    with pytest.raises(ValueError, match="does not exist"):
        perform_launch(path, [], exists=lambda _p: False, startfile=boom, popen=boom, activate=boom)
    assert started == []


def test_packaged_launch_uses_the_aumid_not_a_shell_string():
    seen = []
    perform_launch(
        APPS_PREFIX + "Microsoft.YourPhone_8wekyb3d8bbwe!App",
        [],
        exists=lambda _p: False,
        startfile=lambda *_a, **_k: seen.append("start"),
        popen=lambda *_a, **_k: seen.append("popen"),
        activate=lambda aumid: seen.append(aumid),
    )
    assert seen == ["Microsoft.YourPhone_8wekyb3d8bbwe!App"]


def test_start_menu_shortcut_and_fuzzy_chrome_and_app_paths():
    shortcut = InstalledApp("Discord", "shortcut", r"C:\Menu\Discord.lnk")
    found = resolve_launch("discord", _catalog(shortcut), exists=lambda path: path.endswith(".lnk"))
    assert found.launch is not None and found.launch.kind == "shortcut"
    chrome = InstalledApp("Google Chrome", "exe", r"C:\Chrome\chrome.exe", exe="chrome.exe")
    named = resolve_launch("Google Chrome", _catalog(chrome), exists=lambda path: path.endswith("chrome.exe"))
    assert named.launch is not None and named.launch.target.endswith("chrome.exe")
    resolved = resolve_launch(
        "code",
        _catalog(),
        which=lambda name: r"C:\Code\Code.exe" if name.lower() in ("code", "code.exe") else None,
        exists=lambda path: path.endswith("Code.exe"),
    )
    assert resolved.launch is not None and resolved.launch.kind == "exe"


def test_ai_rewrites_bad_store_exe_guesses_and_keeps_real_ones():
    assert canonical_app_query("PhoneExperienceHost.exe") == "Phone Link"
    assert canonical_app_query(r"C:\Program Files\WindowsApps\YourPhone.exe") == "Phone Link"
    assert canonical_app_query("WhatsApp") == "WhatsApp"
    assert canonical_app_query("calc.exe") == "calc.exe"
    assert canonical_app_query("notepad.exe") == "notepad.exe"
    reply = parse_reply(json.dumps({
        "ok": True, "label": "Phone",
        "profile": {"scope": "global"},
        "trigger": {"mode": "tap", "keys": ["f8"]},
        "action": {"type": "open_app", "app": "PhoneExperienceHost.exe"},
    }))
    assert reply.binding.action.app == "Phone Link"
    calc = parse_reply(json.dumps({
        "ok": True, "label": "Calc",
        "profile": {"scope": "global"},
        "trigger": {"mode": "tap", "keys": ["f7"]},
        "action": {"type": "open_calculator"},
    }))
    assert calc.binding.action.app == "calc.exe"


def test_tab_and_navigation_keys_parse_and_send_with_the_right_scan():
    assert parse_key_expression("Ctrl + Tab") == ["ctrl", "tab"]
    assert parse_key_expression("Ctrl+Page Down") == ["ctrl", "page_down"]
    assert canonicalize("Scroll Lock") == "scroll_lock"
    assert canonicalize("Pause/Break") == "pause"
    assert canonicalize("PrtSc") == "print_screen"
    assert insert_key("Ctrl", "tab") == "Ctrl + Tab"
    assert format_keys(["home", "end", "delete", "insert", "print_screen", "pause", "scroll_lock"])
    sent = []
    injector = TaggedInjector(send=sent.extend, scan_for=lambda _vk: 0)
    for name in ("tab", "page_up", "page_down", "home", "end", "delete", "insert", "print_screen", "scroll_lock", "pause"):
        injector.press(name)
        injector.release(name)
    by_vk = {}
    for event in sent:
        by_vk.setdefault(event.vk, event)
    tab = by_vk[0x09]
    assert tab.scan == 0x0F and tab.flags & KEYEVENTF_SCANCODE and not tab.flags & KEYEVENTF_EXTENDEDKEY
    page = by_vk[0x21]
    assert page.scan == 0x49 and page.flags & KEYEVENTF_SCANCODE and page.flags & KEYEVENTF_EXTENDEDKEY
    assert by_vk[0x22].flags & KEYEVENTF_EXTENDEDKEY and by_vk[0x24].scan == 0x47
    assert by_vk[0x23].scan == 0x4F and by_vk[0x2E].scan == 0x53 and by_vk[0x2D].scan == 0x52
    assert by_vk[0x2C].scan == 0x37 and by_vk[0x2C].flags & KEYEVENTF_EXTENDEDKEY
    assert by_vk[0x91].scan == 0x46 and not by_vk[0x91].flags & KEYEVENTF_EXTENDEDKEY
    pause = by_vk[0x13]
    assert pause.scan == 0 and not pause.flags & KEYEVENTF_SCANCODE and not pause.flags & KEYEVENTF_EXTENDEDKEY


def test_ctrl_tab_chord_fires_and_the_key_up_is_swallowed():
    config = Config(enabled=True)
    binding = Binding(
        id="tab", enabled=True, mode="chord", keys=["ctrl", "tab"],
        action=Action(type="open_url", url="https://example.com"),
    )
    config.global_profile().bindings.append(binding)
    detector = InputDetector(config)
    detector.set_enabled(True)
    detector.set_profiles(config.global_profile(), config.global_profile())
    assert detector.handle("ctrl", True, 0.0).suppress is False
    down = detector.handle("tab", True, 0.0)
    assert down.suppress is True
    assert any(effect.kind == "fire" for effect in down.effects)
    up = detector.handle("tab", False, 0.05)
    assert up.suppress is True
    assert "tab" not in detector.held


def test_print_screen_up_alone_and_pause_down_complete_the_pair():
    seen = []

    def on_event(name, pressed):
        seen.append((name, pressed))
        return name == "print_screen"

    engine = HookEngine(on_event)
    engine._listener = _Listener()

    class Data:
        def __init__(self, vk):
            self.vkCode = vk
            self.flags = 0
            self.dwExtraInfo = 0
            self.scanCode = 0

    with pytest.raises(_Suppress):
        engine._filter(0x0101, Data(0x2C))  # Print Screen often arrives as key-up only
    assert seen == [("print_screen", True), ("print_screen", False)]
    seen.clear()
    engine._filter(0x0100, Data(0x13))  # Pause down, no matching up
    assert seen == [("pause", True), ("pause", False)]


def test_app_rule_passthrough_still_releases_the_key(tmp_path):
    """The 1.3.8 stuck-key fix: App passes the down and the up."""
    created, opener, *_ = service(tmp_path)
    created.set_enabled(True)
    created.set_foreground("cursor.exe")
    created.add_binding("global", Binding(
        id="s", enabled=True, mode="chord", keys=["ctrl", "tab"],
        action=Action(type="open_url", url="https://example.com"),
        app_rules={"cursor.exe": "app"},
    ))
    assert created.handle_input("ctrl", True, 0.0) is False
    assert created.handle_input("tab", True, 0.0) is False
    assert created.handle_input("tab", False, 0.1) is False
    assert opener.urls == []


def test_update_source_and_notice_rules():
    assert "notomar2014-ui/keyware" in GITHUB_LATEST
    assert parse_release_version({"tag_name": "v1.4.1"}) == "1.4.1"
    assert parse_release_version({"tag_name": "nope"}) is None
    assert is_newer("1.4.1", "1.4.0") and not is_newer("1.4.0", "1.4.0") and not is_newer("1.3.9", "1.4.0")
    offer = notice_for("1.4.0", "v1.5.0", now=100, packaged=False)
    assert offer is not None and offer.url == DOWNLOAD_URL and offer.latest == "1.5.0"
    store = notice_for("1.4.0", "1.5.0", now=100, packaged=True)
    assert store is not None and store.url == STORE_URL and "9ngdp5bl0105" in store.url
    assert notice_for("1.4.0", "1.5.0", enabled=False, now=100) is None
    assert notice_for("1.4.0", "1.5.0", skipped="1.5.0", now=100) is None
    assert notice_for("1.4.0", "1.5.0", later_until=200, now=100) is None
    assert notice_for("1.4.0", "1.5.0", later_until=50, now=100) is not None
    assert check_due(0, 10) is False
    assert check_due(0, 24 * 60 * 60) is True

    class Body:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return json.dumps(self.payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def opener(request, timeout=0):
        assert "Keyware/" in request.headers["User-agent"] or request.get_header("User-agent")
        return Body({"tag_name": "v1.6.0"})

    assert fetch_latest_version(opener=opener) == "1.6.0"
    assert fetch_latest_version(opener=lambda *_a, **_k: (_ for _ in ()).throw(OSError("offline"))) is None


def test_update_worker_is_quiet_offline_and_posts_a_banner_message(tmp_path):
    created, *_ = service(tmp_path)
    assert created.config.check_updates is True
    created.consider_update()  # synchronous tests never start a network thread
    assert created._update_inflight is False
    created._update_worker(lambda: None)
    assert created.config.update_checked_at == 0
    assert [message.kind for message in created.poll()] == []
    created._update_worker(lambda: "v1.9.0")
    message = next(item for item in created.poll() if item.kind == "update")
    assert message.text == "1.9.0" and message.mode == DOWNLOAD_URL
    created.startup.packaged = True
    created._update_worker(lambda: "1.9.1")
    store = next(item for item in created.poll() if item.kind == "update")
    assert store.mode == STORE_URL
    created.snooze_update()
    created._update_worker(lambda: "1.9.2")
    assert [item for item in created.poll() if item.kind == "update"] == []
    created.skip_update("2.0.0")
    created.config.update_later_until = 0
    created._update_worker(lambda: "2.0.0")
    assert [item for item in created.poll() if item.kind == "update"] == []
    created._update_worker(lambda: "2.1.0")
    assert any(item.kind == "update" and item.text == "2.1.0" for item in created.poll())
    created.set_check_updates(False)
    created._update_worker(lambda: "3.0.0")
    assert [item for item in created.poll() if item.kind == "update"] == []
    assert created.config.check_updates is False
