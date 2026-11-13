"""Optional Keyware script. A small, bounded language for one binding.

The hook never compiles or runs a script. Compilation happens when a binding
is saved or loaded. Running happens on the action worker, through the same
executor that runs macros, so pause, panic, and a lost focus release every
key the script pressed.

What a script cannot do: open a shell, run a command, download, loop without
a fixed limit, or wait more than two seconds in total. ``do`` is the only way
to reach the binding's normal action, including a command the user already
confirmed in Manual mode.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Any

from engine.keys import canonicalize, is_allowed, is_trigger_only, ordered_keys
from engine.models import (
    BLOCKED_PATH_EXTENSIONS,
    app_rule_key,
    parse_media_text,
    validate_app,
    validate_path,
    validate_url,
)

SCRIPT_MAX_CHARS = 2000
SCRIPT_MAX_LINES = 80
SCRIPT_MAX_LINE = 600
SCRIPT_MAX_STEPS = 128
SCRIPT_MAX_WAIT_MS = 500
SCRIPT_MAX_WAIT_TOTAL_MS = 2000
SCRIPT_MAX_REPEAT = 8
SCRIPT_MAX_DEPTH = 4
SCRIPT_MAX_VARS = 16
SCRIPT_MAX_TEXT = 500
SCRIPT_MAX_MEDIA = 10
SCRIPT_VAR_LIMIT = 1_000_000

_RESERVED = frozenset({
    "if", "else", "end", "repeat", "set", "toggle", "type", "combo", "key", "click",
    "url", "app", "path", "media", "wait", "lock", "screenshot", "do", "stop",
    "mod", "not", "profile", "setup", "is", "and", "or",
})
_MODS = frozenset({"ctrl", "alt", "shift", "win"})
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,15}")
_SHELL = re.compile(
    r"^(?:cmd|powershell|pwsh|powershell_ise|wscript|cscript|mshta|rundll32|regsvr32|"
    r"reg|regedit|bcdedit|diskpart|shutdown|taskkill|schtasks|netsh|certutil|bitsadmin|"
    r"wmic|msiexec|cipher|sdelete|ftp|tftp|conhost|wt|bash|sh|python|python3|py)(?:\.exe)?$",
    re.IGNORECASE,
)
_SCRIPT_EXT = BLOCKED_PATH_EXTENSIONS - {".exe", ".lnk"}
_INPUT_OPS = frozenset({"type", "combo", "key", "click", "media", "shot"})
_INPUT_ACTIONS = frozenset({"type_text", "key_combo", "macro", "media", "screenshot", "auto_click"})


class ScriptLimit(ValueError):
    """A script tried to run past its step or wait budget."""


def script_sends_input(source: str, action) -> bool:
    """True when running this script can press a key or a mouse button."""
    for op in compile_script(source):
        if op[0] in _INPUT_OPS:
            return True
        if op[0] == "do" and getattr(action, "type", "") in _INPUT_ACTIONS:
            return True
    return False


def execute_script(source: str, *, executor, action, ctx, variables: dict[str, int]) -> None:
    """Run a compiled script. Releases any key the script still holds."""
    from engine.actions import Cancelled

    program = compile_script(source)
    executor._held = []
    try:
        _interpret(program, executor=executor, action=action, ctx=ctx, variables=variables)
    except Cancelled:
        return
    finally:
        for key in reversed(executor._held):
            try:
                executor.injector.release(key)
            except Exception:
                pass
        executor._held.clear()


@lru_cache(maxsize=128)
def compile_script(source: str) -> tuple:
    """Compile source to opcodes. Raises ValueError with a line number."""
    text = (source or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("Script is empty.")
    if "\x00" in text:
        raise ValueError("Script contains a character that cannot be saved.")
    if len(text) > SCRIPT_MAX_CHARS:
        raise ValueError(f"Scripts are limited to {SCRIPT_MAX_CHARS} characters.")
    lines = text.split("\n")
    if len(lines) > SCRIPT_MAX_LINES:
        raise ValueError(f"Scripts are limited to {SCRIPT_MAX_LINES} lines.")
    statements = []
    for number, raw in enumerate(lines, 1):
        if len(raw) > SCRIPT_MAX_LINE:
            raise ValueError(f"Script line {number} is too long.")
        line = _strip_comment(raw).strip()
        if not line:
            continue
        statements.append((number, *_parse_line(number, line)))
    if not statements:
        raise ValueError("Add a statement, or clear the script.")
    return tuple(_link(statements))


def _strip_comment(line: str) -> str:
    quote = False
    for index, char in enumerate(line):
        if char == '"':
            quote = not quote
        elif char == "#" and not quote:
            return line[:index]
    return line


def _parse_line(number: int, line: str) -> tuple:
    word, _, rest = line.partition(" ")
    word = word.lower()
    rest = rest.strip()
    try:
        if word == "if":
            if not rest:
                raise ValueError("if needs a condition.")
            return ("if", _condition(rest))
        if word == "else":
            if rest:
                raise ValueError("else stands on its own line.")
            return ("else",)
        if word == "end":
            if rest:
                raise ValueError("end stands on its own line.")
            return ("end",)
        if word == "repeat":
            count = _count(rest, SCRIPT_MAX_REPEAT, "repeat")
            return ("repeat", count)
        if word == "set":
            return ("set", *_assignment(rest))
        if word == "toggle":
            return ("toggle", _varname(rest))
        if word == "type":
            return ("type", _string(rest))
        if word == "combo":
            return ("combo", tuple(_script_keys(rest, several=True)))
        if word == "key":
            return ("key", _script_keys(rest, several=False)[0])
        if word == "click":
            button = rest.lower()
            if button not in ("left", "right", "middle"):
                raise ValueError("click needs left, right, or middle.")
            return ("click", button)
        if word == "url":
            return ("url", validate_url(_unquote(rest))[:2000])
        if word == "app":
            return ("app", _script_app(_unquote(rest)))
        if word == "path":
            return ("path", validate_path(_unquote(rest)))
        if word == "media":
            return ("media", *_script_media(rest))
        if word == "wait":
            return ("wait", _count(rest, SCRIPT_MAX_WAIT_MS, "wait"))
        if word in ("lock", "screenshot", "do", "stop"):
            if rest:
                raise ValueError(f"{word} takes nothing after it.")
            return ("shot",) if word == "screenshot" else (word,)
        if word in ("run", "exec", "eval", "import", "play", "command", "shell", "python", "powershell", "cmd"):
            raise ValueError("Scripts cannot run commands, shells, or network lookups.")
        raise ValueError(f"Unknown script line “{line[:80]}”.")
    except ValueError as exc:
        message = str(exc)
        if message.startswith("Script line"):
            raise
        raise ValueError(f"Script line {number}: {message}") from None


def _link(statements: list[tuple]) -> list[tuple]:
    program: list[list] = []
    stack: list[tuple] = []

    def emit(op: list) -> int:
        program.append(op)
        return len(program) - 1

    for number, kind, *payload in statements:
        if kind == "if":
            if len(stack) >= SCRIPT_MAX_DEPTH:
                raise ValueError(f"Script line {number}: too many nested blocks.")
            emit(["if", payload[0], -1, -1])
            stack.append(("if", len(program) - 1, False))
        elif kind == "else":
            if not stack or stack[-1][0] != "if" or stack[-1][2]:
                raise ValueError(f"Script line {number}: else needs an if.")
            _kind, index, _used = stack[-1]
            jump = emit(["jump", -1])
            program[index][2] = len(program)
            stack[-1] = ("if", index, True, jump)
        elif kind == "repeat":
            if len(stack) >= SCRIPT_MAX_DEPTH:
                raise ValueError(f"Script line {number}: too many nested blocks.")
            emit(["repeat", payload[0], -1])
            stack.append(("repeat", len(program) - 1))
        elif kind == "end":
            if not stack:
                raise ValueError(f"Script line {number}: end without if or repeat.")
            item = stack.pop()
            if item[0] == "if":
                index = item[1]
                if len(item) == 4:
                    program[item[3]][1] = len(program)
                else:
                    program[index][2] = len(program)
                program[index][3] = len(program)
            else:
                index = item[1]
                emit(["next", index])
                program[index][2] = len(program)
        else:
            emit([kind, *payload])
    if stack:
        raise ValueError("Script has an unclosed if or repeat. Add end.")
    frozen = []
    for op in program:
        frozen.append(tuple(tuple(part) if isinstance(part, list) else part for part in op))
    return frozen


def _condition(text: str) -> tuple:
    match = re.fullmatch(r"app\s+is\s+not\s+(\S+)", text, re.IGNORECASE)
    if match:
        return ("app", False, _exe(match.group(1)))
    match = re.fullmatch(r"app\s+is\s+(\S+)", text, re.IGNORECASE)
    if match:
        return ("app", True, _exe(match.group(1)))
    match = re.fullmatch(r"profile\s+is\s+(.+)", text, re.IGNORECASE)
    if match:
        name = " ".join(match.group(1).split())
        if not name or len(name) > 40:
            raise ValueError("Name the profile, up to 40 characters.")
        return ("profile", name.casefold())
    match = re.fullmatch(r"setup\s+is\s+(\d+)", text, re.IGNORECASE)
    if match:
        number = int(match.group(1))
        if number > 9:
            raise ValueError("setup is a number from 0 to 9.")
        return ("setup", number)
    match = re.fullmatch(r"not\s+mod\s+(ctrl|alt|shift|win)", text, re.IGNORECASE)
    if match:
        return ("mod", False, match.group(1).lower())
    match = re.fullmatch(r"mod\s+(ctrl|alt|shift|win)", text, re.IGNORECASE)
    if match:
        return ("mod", True, match.group(1).lower())
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]{0,15})\s*(==|!=|>=|<=|>|<)\s*(-?\d+)", text)
    if match:
        _varname(match.group(1))
        return ("cmp", match.group(1), match.group(2), _clamp_literal(match.group(3)))
    match = re.fullmatch(r"not\s+([A-Za-z_][A-Za-z0-9_]{0,15})", text, re.IGNORECASE)
    if match:
        return ("not", _varname(match.group(1)))
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]{0,15})", text)
    if match:
        return ("var", _varname(match.group(1)))
    raise ValueError("Condition not understood. Try: app is chrome.exe, mod ctrl, n > 1.")


def _assignment(text: str) -> tuple:
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]{0,15})\s*=\s*(.+)", text.strip())
    if not match:
        raise ValueError("set looks like: set n = n + 1")
    name = _varname(match.group(1))
    expr = match.group(2).strip()
    number = re.fullmatch(r"-?\d+", expr)
    if number:
        return name, ("num", _clamp_literal(expr))
    var = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]{0,15})", expr)
    if var:
        return name, ("var", _varname(var.group(1)))
    math = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]{0,15})\s*([+-])\s*(\d+)", expr)
    if math:
        return name, ("add", _varname(math.group(1)), _clamp_literal(math.group(3)) * (1 if math.group(2) == "+" else -1))
    raise ValueError("set can copy a number or a name, or add or subtract a number.")


def _varname(text: str) -> str:
    name = (text or "").strip()
    if not _NAME.fullmatch(name) or name.lower() in _RESERVED:
        raise ValueError(f"“{name or '?'}” cannot be a variable name.")
    return name


def _clamp_literal(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError("That number is not valid.") from exc
    if abs(value) > SCRIPT_VAR_LIMIT:
        raise ValueError(f"Numbers must stay within ±{SCRIPT_VAR_LIMIT}.")
    return value


def _count(text: str, limit: int, what: str) -> int:
    if not re.fullmatch(r"\d+", (text or "").strip()):
        raise ValueError(f"{what} needs a whole number.")
    value = int(text.strip())
    if value > limit:
        raise ValueError(f"{what} cannot be more than {limit}.")
    return value


def _string(text: str) -> str:
    if not text.startswith('"'):
        raise ValueError("type needs quotes: type \"hello\".")
    out: list[str] = []
    index = 1
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            out.append(text[index + 1])
            index += 2
            continue
        if char == '"':
            if text[index + 1:].strip():
                raise ValueError("type has extra text after the quotes.")
            value = "".join(out)
            if len(value) > SCRIPT_MAX_TEXT:
                raise ValueError(f"type is limited to {SCRIPT_MAX_TEXT} characters.")
            return value
        out.append(char)
        index += 1
    raise ValueError("type is missing its closing quote.")


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1]
    return text


def _exe(token: str) -> str:
    key = app_rule_key(_unquote(token))
    if not re.fullmatch(r"[^<>:\"/\\|?*\x00-\x1f]{1,60}\.exe", key or ""):
        raise ValueError("Name an app like chrome.exe.")
    return key


def _script_app(text: str) -> str:
    app = validate_app(text)
    base = app.replace("\\", "/").rsplit("/", 1)[-1]
    suffix = os.path.splitext(base)[1].lower()
    if suffix in _SCRIPT_EXT or _SHELL.fullmatch(base):
        raise ValueError("Scripts cannot open a shell, a script, or a system tool.")
    return app


def _script_keys(text: str, *, several: bool) -> list[str]:
    raw = [part for part in text.replace("+", " ").split() if part]
    if not raw:
        raise ValueError("Name a key.")
    if not several and len(raw) != 1:
        raise ValueError("key takes one key. Use combo for several.")
    names: list[str] = []
    for item in raw:
        name = canonicalize(item)
        if name is None or not is_allowed(name):
            raise ValueError(f"Unknown key '{item}'.")
        if is_trigger_only(name):
            raise ValueError(f"{item} can be a trigger, but a script cannot send it.")
        if name not in names:
            names.append(name)
    return ordered_keys(names)


def _script_media(text: str) -> tuple[str, int]:
    body = text.strip()
    explicit = re.fullmatch(r"(.+?)\s+(\d{1,2})", body)
    count = None
    if explicit and not re.search(r"[x×*]\s*\d+$", body, re.IGNORECASE):
        body = explicit.group(1).strip()
        count = int(explicit.group(2))
    try:
        name, parsed = parse_media_text(body)
    except ValueError as exc:
        raise ValueError("media needs a name such as volume_up, mute, or next.") from exc
    count = parsed if count is None else count
    if not 1 <= count <= SCRIPT_MAX_MEDIA:
        raise ValueError(f"A script can press a media key at most {SCRIPT_MAX_MEDIA} times.")
    return name, count


def _interpret(program: tuple, *, executor, action, ctx, variables: dict[str, int]) -> None:
    pc = 0
    steps = 0
    waited = 0
    frames: list[list[int]] = []
    while pc < len(program):
        steps += 1
        if steps > SCRIPT_MAX_STEPS:
            raise ScriptLimit(f"Script stopped after {SCRIPT_MAX_STEPS} steps.")
        op = program[pc]
        kind = op[0]
        if kind == "if":
            pc = pc + 1 if _truth(op[1], ctx, variables) else op[2]
            continue
        if kind == "jump":
            pc = op[1]
            continue
        if kind == "repeat":
            count, end_pc = op[1], op[2]
            if count <= 0:
                pc = end_pc
                continue
            frames.append([count])
            pc += 1
            continue
        if kind == "next":
            if not frames:
                raise ValueError("Script repeat was not opened.")
            frames[-1][0] -= 1
            if frames[-1][0] > 0:
                pc = op[1] + 1
            else:
                frames.pop()
                pc += 1
            continue
        if kind == "set":
            _assign(variables, op[1], op[2])
        elif kind == "toggle":
            _assign(variables, op[1], ("num", 0 if variables.get(op[1], 0) else 1))
        elif kind == "type":
            executor._type(op[1])
        elif kind == "combo":
            executor._combo(list(op[1]))
        elif kind == "key":
            executor._tap(op[1])
        elif kind == "click":
            executor._check()
            executor.injector.click(op[1])
        elif kind == "url":
            executor._open_link(op[1])
        elif kind == "app":
            executor._open_app(op[1], [], "")
        elif kind == "path":
            executor._open_path(op[1])
        elif kind == "media":
            executor._media(op[1], op[2])
        elif kind == "wait":
            executor._check()
            if waited + op[1] > SCRIPT_MAX_WAIT_TOTAL_MS:
                raise ScriptLimit(f"Script waits are limited to {SCRIPT_MAX_WAIT_TOTAL_MS} ms in total.")
            waited += op[1]
            if op[1]:
                executor.sleeper(op[1] / 1000)
            executor._check()
        elif kind == "lock":
            executor._lock()
        elif kind == "shot":
            executor._combo(["win", "print_screen"])
        elif kind == "do":
            executor._check()
            executor._run(action)
        elif kind == "stop":
            return
        else:
            raise ValueError(f"Unknown script step '{kind}'.")
        pc += 1


def _assign(variables: dict[str, int], name: str, expr: tuple) -> None:
    if name not in variables and len(variables) >= SCRIPT_MAX_VARS:
        raise ScriptLimit(f"Scripts can use {SCRIPT_MAX_VARS} variables.")
    value = _value(expr, variables)
    if abs(value) > SCRIPT_VAR_LIMIT:
        value = SCRIPT_VAR_LIMIT if value > 0 else -SCRIPT_VAR_LIMIT
    variables[name] = value


def _value(expr: tuple, variables: dict[str, int]) -> int:
    if expr[0] == "num":
        return int(expr[1])
    if expr[0] == "var":
        return int(variables.get(expr[1], 0))
    if expr[0] == "add":
        return int(variables.get(expr[1], 0)) + int(expr[2])
    raise ValueError("Script expression is not valid.")


def _truth(cond: tuple, ctx, variables: dict[str, int]) -> bool:
    kind = cond[0]
    if kind == "app":
        return (ctx.app.casefold() == cond[2]) is cond[1]
    if kind == "profile":
        return ctx.profile.casefold() == cond[1]
    if kind == "setup":
        return int(ctx.setup) == cond[1]
    if kind == "mod":
        return (cond[2] in ctx.mods) is cond[1]
    if kind == "var":
        return variables.get(cond[1], 0) != 0
    if kind == "not":
        return variables.get(cond[1], 0) == 0
    if kind == "cmp":
        left = variables.get(cond[1], 0)
        op = cond[2]
        right = cond[3]
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == ">":
            return left > right
        if op == "<":
            return left < right
        if op == ">=":
            return left >= right
        if op == "<=":
            return left <= right
    return False


def script_context(app: str, profile: str, setup: int, mods: Any) -> "ScriptContext":
    return ScriptContext(
        app=str(app or "").casefold(),
        profile=str(profile or ""),
        setup=int(setup or 0),
        mods=frozenset(name for name in mods if name in _MODS),
    )


class ScriptContext:
    __slots__ = ("app", "profile", "setup", "mods")

    def __init__(self, app: str, profile: str, setup: int, mods: frozenset[str]):
        self.app = app
        self.profile = profile
        self.setup = setup
        self.mods = mods
