# Contributing to Keyware

Thanks for helping make Keyware better.

## Ground rules

- **Safety first.** Keyware sits on the keyboard hook. A change must never swallow an
  unbound key, block the panic chord (Ctrl + Alt + Shift + Esc), or do slow work
  inside the hook callback. See [docs/TECHNICAL.md](docs/TECHNICAL.md).
- **The engine stays offline.** Nothing under `engine/` may make network calls.
- **No secrets.** Never commit API keys, `.env` files, passwords, personal paths or
  e-mail addresses. Tests use obvious placeholders (`you@example.com`, fake keys built
  at runtime).
- **No new actions that can harm a PC** (shutdown, delete, registry, downloads, shells
  without the one-time confirmation).

## Development setup

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest
```

The tests are headless: they feed synthetic key events and block `subprocess`,
`os.startfile`, `webbrowser` and `winsound`. Window tests run only when a display is
available. The website tests run with `cd site && npm test` (Node 20+).

If you change the Text-mode system prompt in `assist/ai_binder.py`, run
`python tools/sync_prompt.py` so the proxy in `site/api/_lib/prompt.js` matches.

## Pull requests

1. Fork, create a branch, keep the change focused.
2. Add or update tests; `python -m pytest` must pass.
3. Describe what changed and how you tested it on Windows.
4. CI builds `Keyware.exe` on `windows-latest`; please check the run is green.

By contributing you agree that your contribution is licensed under the MIT License.
