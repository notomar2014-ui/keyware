"""Keyware 1.4.2: Windows app names, aliases, and the hosted UWP process."""

from __future__ import annotations

from engine import __version__
from engine.appidentity import (
    ALIAS_GROUPS,
    app_names_match,
    default_search_dirs,
    hosted_app_name,
    same_app,
    window_matches,
)
from engine.applaunch import (
    Catalog,
    InstalledApp,
    canonical_app_query,
    perform_launch,
    resolve_launch,
)
from engine.models import Action, Binding, Profile
from engine.profiles import app_rule_mode, match_profile
from engine.script import compile_script, execute_script
from tests.fakes import RecordingInjector, RecordingOpener, service
from tests.test_v141 import _Rng, _ctx


WINDOWSAPPS = r"C:\Users\A\AppData\Local\Microsoft\WindowsApps"
SYSTEM32 = r"C:\Windows\System32"
CORE = "Windows.UI.Core.CoreWindow"


def _listed(calc=True, notepad_in_system=True, wt=True):
    def list_dir(path):
        key = path.lower()
        if key == WINDOWSAPPS.lower():
            names = []
            if calc:
                names.append("calc.exe")
            if wt:
                names.append("wt.exe")
            names.append("notepad.exe")
            return names
        if key == SYSTEM32.lower() and notepad_in_system:
            return ["notepad.exe"]
        return []

    return list_dir


def _exists_system32(path):
    return path.lower().startswith(SYSTEM32.lower())


def test_version_is_142():
    from store.validate_msix import _version4

    assert __version__ == "1.4.3"
    assert _version4() == "1.4.3.0"


def test_alias_groups_do_not_overlap_and_each_is_one_app():
    from engine.applaunch import _known_for

    seen: set[str] = set()
    for group in ALIAS_GROUPS:
        assert not group & seen
        seen |= set(group)
        labels = set()
        for stem in group:
            known = _known_for(stem + ".exe")
            assert known is not None, stem
            labels.add(known.label)
        assert len(labels) == 1


def test_calc_matches_the_store_process_in_any_spelling():
    for left in ("calc.exe", "calc", "CALC.EXE", "Calc", r"C:\Windows\System32\calc.exe"):
        for right in ("CalculatorApp.exe", "calculatorapp", "CALCULATORAPP.EXE"):
            assert same_app(left, right)
            assert app_names_match(left, right)
    assert same_app("calc.exe", "notepad.exe") is False
    assert same_app("calc.exe", "ApplicationFrameHost.exe") is False
    assert same_app("chrome.exe", "msedge.exe") is False
    assert app_names_match("Microsoft Edge", "msedge.exe")
    assert app_names_match("Settings", "SystemSettings.exe")
    assert app_names_match("wt.exe", "WindowsTerminal.exe")
    assert app_names_match("Photos", "Microsoft.Photos.exe")


def test_frame_host_resolves_to_the_child_app_only():
    assert hosted_app_name("ApplicationFrameHost.exe", [(CORE, r"C:\pkg\CalculatorApp.exe")]) == "CalculatorApp.exe"
    assert hosted_app_name("APPLICATIONFRAMEHOST.EXE", [(CORE, "Microsoft.Photos.exe")]) == "Microsoft.Photos.exe"
    assert hosted_app_name("ApplicationFrameHost.exe", []) == "ApplicationFrameHost.exe"
    assert hosted_app_name("ApplicationFrameHost.exe", [("ApplicationFrameInputSinkWindow", "explorer.exe")]) == "ApplicationFrameHost.exe"
    assert hosted_app_name("ApplicationFrameHost.exe", [("Windows.UI.Core.CoreWindow", "CalculatorApp.exe"), ("other", "notepad.exe")]) == "CalculatorApp.exe"
    assert hosted_app_name("notepad.exe", [(CORE, "CalculatorApp.exe")]) == "notepad.exe"
    two = [("other", "CalculatorApp.exe"), ("other", "Microsoft.Photos.exe")]
    assert hosted_app_name("ApplicationFrameHost.exe", two) == "ApplicationFrameHost.exe"
    assert window_matches("calc.exe", "ApplicationFrameHost.exe", [(CORE, "CalculatorApp.exe")])
    assert window_matches("CALC", "ApplicationFrameHost.exe", [(CORE, "CalculatorApp.exe")])
    assert window_matches("calc.exe", "ApplicationFrameHost.exe", []) is False
    assert window_matches("notepad.exe", "ApplicationFrameHost.exe", [(CORE, "CalculatorApp.exe")]) is False
    assert window_matches("photos", "ApplicationFrameHost.exe", [(CORE, "Microsoft.Photos.exe")])


def test_search_dirs_check_system32_before_windowsapps():
    env = {
        "WINDIR": r"C:\Windows",
        "LOCALAPPDATA": r"C:\Users\A\AppData\Local",
        "USERPROFILE": r"C:\Users\A",
        # PATH is left empty. On Windows it is split on ';', but this suite also
        # runs on Linux, where os.pathsep is ':' and would cut a drive letter in two.
        "PATH": "",
    }
    dirs = default_search_dirs(env)

    def norm(path: str) -> str:
        return path.replace("/", "\\").rstrip("\\").lower()

    assert norm(dirs[0]) == norm(SYSTEM32)
    assert norm(dirs[1]) == norm(r"C:\Windows\SysWOW64")
    windowsapps = [item for item in dirs if norm(item).endswith("windowsapps")]
    assert [norm(item) for item in windowsapps] == [norm(WINDOWSAPPS)]


def test_calc_alias_is_recognized_when_exists_says_no():
    catalog = Catalog(packaged_enumerated=True)  # Start menu was read, and Calculator was not in it
    for query in ("calc.exe", "calc", "CALC.EXE", "Calc"):
        found = resolve_launch(
            query,
            catalog,
            which=lambda _name: None,
            exists=lambda _path: False,
            list_dir=_listed(),
            search_dirs=[SYSTEM32, WINDOWSAPPS],
        )
        assert found.launch is not None, query
        assert found.launch.kind == "packaged"
        assert found.launch.target == "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"
        assert found.missing_path == ""


def test_which_alias_is_accepted_even_when_exists_is_false():
    alias = WINDOWSAPPS + r"\calc.exe"

    def which(name):
        return alias if name.lower() in {"calc.exe", "calc"} else None

    found = resolve_launch(
        "calc.exe",
        Catalog(packaged_enumerated=True),
        which=which,
        exists=lambda _path: False,
    )
    assert found.launch is not None
    assert found.launch.kind == "packaged"
    assert "WindowsCalculator" in found.launch.target


def test_real_system32_exe_is_not_replaced_by_a_store_id():
    path = SYSTEM32 + r"\calc.exe"
    found = resolve_launch(
        "calc.exe",
        Catalog(packaged_enumerated=True),
        which=lambda name: path if name.lower() == "calc.exe" else None,
        exists=lambda _path: False,
    )
    assert found.launch is not None
    assert found.launch.kind == "exe"
    assert found.launch.shell is False
    assert found.launch.target == path


def test_system32_notepad_wins_over_the_windowsapps_alias():
    found = resolve_launch(
        "NOTEPAD.EXE",
        Catalog(packaged_enumerated=True),
        which=lambda _name: None,
        exists=_exists_system32,
        list_dir=_listed(),
        search_dirs=[SYSTEM32, WINDOWSAPPS],
    )
    assert found.launch is not None
    assert found.launch.kind == "exe"
    assert found.launch.shell is False
    assert found.launch.target.replace("/", "\\").lower().endswith(r"system32\notepad.exe")


def test_terminal_alias_opens_the_store_app_when_the_file_check_fails():
    found = resolve_launch(
        "wt",
        Catalog(packaged_enumerated=True),
        which=lambda _name: None,
        exists=lambda _path: False,
        list_dir=_listed(),
        search_dirs=[SYSTEM32, WINDOWSAPPS],
    )
    assert found.launch is not None
    assert found.launch.kind == "packaged"
    assert found.launch.target == "Microsoft.WindowsTerminal_8wekyb3d8bbwe!App"


def test_unknown_windowsapps_alias_is_launched_through_the_shell():
    alias = WINDOWSAPPS + r"\Contoso.exe"

    def which(name):
        return alias if name.lower() == "contoso.exe" else None

    found = resolve_launch("contoso", Catalog(packaged_enumerated=True), which=which, exists=lambda _path: False)
    assert found.launch is not None
    assert found.launch.kind == "exe" and found.launch.shell is True
    seen = []
    perform_launch(
        found.launch.target,
        [],
        exists=lambda _path: False,
        startfile=lambda *_a, **_k: seen.append("start"),
        popen=lambda *_a, **_k: seen.append("popen"),
        activate=lambda *_a, **_k: seen.append("activate"),
        trust=True,
    )
    assert seen == ["start"]
    with __import__("pytest").raises(ValueError):
        perform_launch(
            alias, [], exists=lambda _path: False,
            startfile=lambda *_a, **_k: seen.append("start"),
            popen=lambda *_a, **_k: seen.append("popen"),
            activate=lambda *_a, **_k: seen.append("activate"),
            trust=False,
        )


def test_a_missing_name_is_still_missing():
    found = resolve_launch(
        "not-a-real-app",
        Catalog(packaged_enumerated=True),
        which=lambda _name: None,
        exists=lambda _path: False,
        list_dir=_listed(),
        search_dirs=[SYSTEM32, WINDOWSAPPS],
    )
    assert found.launch is None


def test_store_names_resolve_from_the_catalog_and_from_inbox_ids():
    photos = InstalledApp("Photos", "packaged", "Microsoft.Windows.Photos_8wekyb3d8bbwe!App")
    found = resolve_launch("photos", Catalog(apps=[photos], packaged_enumerated=True), exists=lambda _path: False)
    assert found.launch is not None and found.launch.kind == "packaged"
    settings = resolve_launch("Settings", Catalog(packaged_enumerated=False), exists=lambda _path: False)
    assert settings.launch is not None
    assert settings.launch.target.startswith("windows.immersivecontrolpanel_")
    terminal = InstalledApp("Windows Terminal", "packaged", "Microsoft.WindowsTerminal_8wekyb3d8bbwe!App")
    named = resolve_launch("terminal", Catalog(apps=[terminal], packaged_enumerated=True), exists=lambda _path: False)
    assert named.launch is not None and named.launch.kind == "packaged"


def test_canonical_names_keep_calc_and_rewrite_the_real_process():
    assert canonical_app_query("calc.exe") == "calc.exe"
    assert canonical_app_query("CALC.EXE") == "calc.exe"
    assert canonical_app_query("CalculatorApp.exe") == "Calculator"
    assert canonical_app_query("notepad.exe") == "notepad.exe"
    assert canonical_app_query("WindowsTerminal.exe") == "Terminal"


def test_profile_and_app_rule_follow_calculator_and_ignore_the_bare_host():
    profiles = [
        Profile(id="global", name="Global", process_names=[], enabled=True, bindings=[]),
        Profile(id="calc", name="Calculator", process_names=["calc.exe"], enabled=True, bindings=[]),
    ]
    assert match_profile(profiles, "CalculatorApp.exe").id == "calc"
    assert match_profile(profiles, "CALC.EXE").id == "calc"
    assert match_profile(profiles, "calculator").id == "calc"
    assert match_profile(profiles, "ApplicationFrameHost.exe").id == "global"
    assert match_profile(profiles, "notepad.exe").id == "global"
    rules = {"calc.exe": "app", "wt.exe": "both"}
    assert app_rule_mode(rules, "CalculatorApp.exe") == "app"
    assert app_rule_mode(rules, r"C:\Program Files\WindowsApps\CalculatorApp.exe") == "app"
    assert app_rule_mode(rules, "WindowsTerminal.exe") == "both"
    assert app_rule_mode(rules, "WT") == "both"
    assert app_rule_mode(rules, "notepad.exe") == "keyware"
    assert app_rule_mode(rules, "ApplicationFrameHost.exe") == "keyware"


def test_script_says_yes_for_calc_and_no_for_a_different_app():
    for source in (
        'if app is calc.exe\n  type "yes"\nelse\n  type "no"\nend',
        'if app is calc\n  type "yes"\nelse\n  type "no"\nend',
        'if app is CALC.EXE\n  type "yes"\nelse\n  type "no"\nend',
    ):
        compile_script(source)
        for app in ("CalculatorApp.exe", "calc.exe", "CALC", "calc"):
            opener, injector = RecordingOpener(), RecordingInjector()
            executor = _executor(opener, injector)
            execute_script(source, executor=executor, action=Action(type="none"), ctx=_ctx(app=app), variables={})
            assert ("text", "yes") in injector.events, (source, app)
            assert ("text", "no") not in injector.events
    opener, injector = RecordingOpener(), RecordingInjector()
    executor = _executor(opener, injector)
    execute_script(source, executor=executor, action=Action(type="none"), ctx=_ctx(app="notepad.exe"), variables={})
    assert ("text", "no") in injector.events
    opener, injector = RecordingOpener(), RecordingInjector()
    executor = _executor(opener, injector)
    execute_script(
        source, executor=executor, action=Action(type="none"),
        ctx=_ctx(app="ApplicationFrameHost.exe"), variables={},
    )
    assert ("text", "no") in injector.events
    with __import__("pytest").raises(ValueError):
        compile_script("app wt.exe")


def test_script_matches_terminal_by_either_name():
    source = 'if app is wt.exe\n  type "yes"\nelse\n  type "no"\nend'
    opener, injector = RecordingOpener(), RecordingInjector()
    execute_script(
        source, executor=_executor(opener, injector), action=Action(type="none"),
        ctx=_ctx(app="WindowsTerminal.exe"), variables={},
    )
    assert ("text", "yes") in injector.events


def test_running_service_treats_calc_as_the_foreground_calculator(tmp_path):
    created, opener, *_rest = service(tmp_path)
    created.add_profile("Calculator", ["calc.exe"])
    created.set_foreground("CalculatorApp.exe")
    assert created.snapshot()["profile_name"] == "Calculator"
    created.set_enabled(True)
    created.add_binding("global", Binding(
        id="open", enabled=True, mode="tap", keys=["f8"],
        action=Action(type="open_url", url="https://example.com/calc"),
        app_rules={"calc.exe": "app"},
    ))
    created.handle_input("f8", True, 0.0)
    created.handle_input("f8", False, 0.05)
    assert opener.urls == []
    created.set_foreground("notepad.exe")
    assert created.snapshot()["profile_name"] == "Global"
    created.handle_input("f8", True, 1.0)
    created.handle_input("f8", False, 1.05)
    assert opener.urls == ["https://example.com/calc"]


def _executor(opener, injector):
    from engine.actions import ActionExecutor

    return ActionExecutor(opener, injector, lambda _s: None, _Rng())
