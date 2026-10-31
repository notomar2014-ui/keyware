"""Keyware entry point."""

from __future__ import annotations

import sys
import traceback


def _install_excepthook() -> None:
    def hook(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            from engine.config_store import default_config_path

            path = default_config_path().parent / "error.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(text + "\n")
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = hook


APP_USER_MODEL_ID = "Keyware.Keyware"


def set_app_user_model_id(shell32=None) -> bool:
    """Give the process its own taskbar identity.

    Without this, a pythonw.exe window is grouped under Python and the
    taskbar shows the Python icon instead of Keyware's. Must run before any
    window is created.
    """
    if shell32 is None:
        if sys.platform != "win32":
            return False
        try:
            import ctypes

            shell32 = ctypes.windll.shell32
        except Exception:
            return False
    try:
        return int(shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)) == 0
    except Exception:
        return False


def run_startup_status(args: list[str]) -> int:
    """Write {"packaged", "backend", "enabled"} as JSON to the path after --startup-status.

    Used by the MSIX CI smoke test. Reads state only; it never turns startup on or off.
    """
    import json

    from engine.startup import StartupManager

    index = args.index("--startup-status")
    target = args[index + 1] if index + 1 < len(args) else None
    manager = StartupManager()
    report = {"packaged": manager.packaged, "backend": type(manager.registry).__name__}
    try:
        report["enabled"] = manager.is_enabled()
    except OSError as exc:
        report["enabled"] = None
        report["error"] = str(exc)
    text = json.dumps(report)
    if target:
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    set_app_user_model_id()
    if "--version" in args:
        return print_version(args)
    if "--self-test" in args:
        from engine.selftest import run

        return run()
    if "--startup-status" in args:
        return run_startup_status(args)
    if "--diag" in args:
        from engine.diag import run_diag

        return run_diag()
    if "--run-action" in args:
        return run_action_file(args)
    if "--ai-test" in args:
        return run_ai_test(args)
    from engine.instance import InstanceLock

    lock = InstanceLock()
    if not lock.acquire():
        print("Keyware is already running.")
        return 2
    try:
        _install_excepthook()
        from ui.app import run

        background = "--background" in args
        if not background and not args:
            # MSIX build: the startup task normally passes --background itself (uap10:Parameters).
            from engine.startup import is_packaged, launched_by_startup_task

            background = is_packaged() and launched_by_startup_task()
        run(minimized=background or "--minimized" in args, background=background)
        return 0
    finally:
        lock.release()


def print_version(args: list[str]) -> int:
    """Keyware.exe --version [--out file]. The exe is windowed, so --out lets a build check read it."""
    from engine import __version__

    print(__version__)
    if "--out" in args and args.index("--out") + 1 < len(args):
        from pathlib import Path

        Path(args[args.index("--out") + 1]).write_text(__version__ + "\n", encoding="utf-8")
    return 0


def run_action_file(args: list[str]) -> int:
    """Run one action from a JSON file, the same way its key would, then exit. For checking a build.

        Keyware.exe --run-action action.json [--out result.txt] [--dry]

    No hook, no window, no tray, no instance lock. --dry looks things up (songs, videos)
    but records what it would open instead of opening it. Run commands are never run here.
    """
    import json
    import random
    import time
    from pathlib import Path

    from assist.web_lookup import fetch_text
    from engine.actions import ActionExecutor, SystemOpener
    from engine.models import Action, validate_action

    def value_after(flag: str) -> str | None:
        if flag in args and args.index(flag) + 1 < len(args):
            return args[args.index(flag) + 1]
        return None

    lines: list[str] = []
    out = value_after("--out")

    def note(text: str) -> None:
        lines.append(f"{time.strftime('%H:%M:%S')} {text}")
        print(lines[-1])

    real = SystemOpener(fetcher=fetch_text)

    class Recorder:
        def __getattr__(self, name):
            target = getattr(real, name)
            if name in ("resolve_app", "fetch_text", "wait_window"):
                def passthrough(*a, **k):
                    result = target(*a, **k)
                    shown = result if name != "fetch_text" else f"{len(result or '')} chars"
                    note(f"{name}{tuple(x for x in a if not callable(x))} -> {shown}")
                    return result
                return passthrough

            def opener(*a, **k):
                note(("WOULD " if "--dry" in args else "") + f"{name}{a}")
                if "--dry" not in args:
                    return target(*a, **k)
                return None
            return opener

    status = 0
    try:
        path = value_after("--run-action")
        action = Action.from_dict(json.loads(Path(path or "").read_text(encoding="utf-8")))
        validate_action(action)
        from engine.sendinput import TaggedInjector

        executor = ActionExecutor(Recorder(), TaggedInjector(), time.sleep, random.SystemRandom())
        executor.confirm = lambda _text: False
        started = time.monotonic()
        executor.run(action)
        note(f"done in {time.monotonic() - started:.1f} s")
    except Exception as exc:
        note(f"ERROR {type(exc).__name__}: {exc}")
        status = 1
    if out:
        Path(out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return status


def run_ai_test(args: list[str]) -> int:
    """One Text mode request through the proxy, then exit. For checking a build.

        Keyware.exe --ai-test "F9 opens YouTube" [--endpoint URL] [--out result.json]

    Uses a fresh install ID and the default sample config; the user's config is
    not read or written. Prints the proposal (or the error), never a key.
    """
    import json
    import uuid
    from pathlib import Path

    from assist.ai_binder import AIBinderError, DescribeBinder, Proposal
    from engine.models import DEFAULT_AI_ENDPOINT, default_config, trigger_text

    def value_after(flag: str) -> str | None:
        if flag in args and args.index(flag) + 1 < len(args):
            return args[args.index(flag) + 1]
        return None

    text = value_after("--ai-test") or "F9 opens YouTube"
    endpoint = value_after("--endpoint") or DEFAULT_AI_ENDPOINT
    config = default_config()
    binder = DescribeBinder(endpoint=endpoint, install_id=str(uuid.uuid4()))
    report: dict = {"endpoint": endpoint, "request": text}
    status = 0
    try:
        items = binder.describe_batch(text, config, "Global")
        report["ok"] = True
        report["bindings"] = [
            {"trigger": trigger_text(item.binding, config.leader_key), "action": item.binding.action.to_dict(),
             "label": item.label, "model": item.model, "seconds": item.seconds}
            if isinstance(item, Proposal) else {"rejected": item.error}
            for item in items
        ]
    except AIBinderError as exc:
        report.update(ok=False, error=str(exc), code=getattr(exc, "code", ""))
        status = 1
    output = json.dumps(report, indent=2, ensure_ascii=False)
    print(output)
    out = value_after("--out")
    if out:
        Path(out).write_text(output + "\n", encoding="utf-8")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
