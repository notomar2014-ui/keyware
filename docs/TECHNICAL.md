# Keyware: technical notes

Keyware is a Windows keyboard remapper and macro engine. Make your keys your own. It maps keys, chords, and hold durations to actions, and it switches those bindings by the app in front.

A short press of an unbound key is never swallowed. The listener starts only after you turn **Active** on, and **Ctrl + Alt + Shift + Esc** drops the hook immediately. Keyware does not need administrator rights and does not store secrets. The hook engine never touches the network. The app goes online only for optional Text mode (the sentence plus a short setup summary; keystrokes are not uploaded), optional feedback from Settings, and actions you create that look something up, such as a **Play** action.

## Turning it on

The big **TURN ON** button in the header is the master switch. The status next to it says **ON** (green) or **OFF** (red) and how many bindings are armed in the profile in front. While Keyware is OFF nothing fires. Saving a new binding, or switching one on, turns Keyware ON so it works right away. The tray menu has **Turn ON / Turn OFF** too, and the tray icon is dimmed with a red dot while OFF.

Everything in the log is also written to `%APPDATA%\Keyware\keyware.log`.

## What it does

- **Chords.** `Ctrl + Win + Shift + R` opens YouTube in the sample Global profile.
- **Holds.** Tap under 300 ms, tier 1 at 800 ms, tier 2 at 2000 ms. Durations are per binding, from 300 ms to 10 s. A hold-only key still types if you release it early. Tap, hold, and tap-dance apply only when no modifier is held, so Ctrl+P still types.
- **Ordered replay.** A key that is held back is replayed on one queue, before the next key, when the next key arrives within `rollover_ms` (120 ms by default, `0` turns it off). That setting lives in the config file.
- **Tap-dance.** Double-tap or triple-tap within 250 ms. A long press still types the key.
- **Leader key.** Tap a leader (F13 is the safe choice), then a follow-up key within 1.5 s. `Leader → Y` opens YouTube once a leader key is set.
- **Actions.** Open an `http` or `https` URL, open an application (with links or files, and a website to open instead if it isn't installed), open a file or folder, type text, send a multi-key combo, media and volume keys, lock the screen, take a screenshot, play a song on Spotify or a video on YouTube, run a command (asks once), auto-click, or run a macro. Injected keys get a 5–15 ms jitter between press and release. Delays you type stay exact.
- **Macros.** A macro is an ordered step list: key, combo, text, delay, mouse click, open URL/app/file, media key, wait for a window, play, lock, screenshot. The Manual editor edits every step (type and value), adds and removes steps, and reorders them with ▲ ▼. AI-made macros open in the same editor. Saving checks safety and conflicts again.
- **Web first.** If an app isn't installed, Keyware opens its website instead. **Play on Spotify** uses the Spotify app only when it is installed; otherwise it finds the track with a web search and opens it in the Spotify web player in your default browser. **Play on YouTube** opens the top video.
- **Wait for window.** A macro can wait (up to 30 s) for an app's window to appear and come to the front before it types, instead of a fixed sleep.
- **Auto-click.** Left, right or middle, 1–50 clicks per second, while the key is held or toggled, with an optional safety stop. It stops on pause, panic, Keyware off, or a profile switch.
- **Trash and undo.** The trash icon deletes a binding at once; **Undo** in the toast brings it back for 5 seconds.
- **Active switch and tray.** Closing the window leaves Keyware in the tray. The tray can pause, reload the config, open the config file, or quit. Quit in the tray fully closes Keyware. **Start with Windows** is on by default: Keyware starts silently in the tray at sign-in, with no window and hotkeys working; turn it off in Settings.
- **Pause.** `Ctrl + Alt + F12` pauses remaps and leaves the hook installed, so the same chord resumes. The tray icon switches to a pause mark.
- **Dry run.** Settings can log what would fire and let every key through.
- **Profiles.** Global bindings stay active everywhere. Gaming (`javaw.exe`, `minecraft.exe`), Browser, and Code add or replace them by the foreground executable. The foreground process is checked every 500 ms.
- **Light and dark.** The window is black and white. The choice is saved with the rest of the config.

## Safety

The low-level hook uses selective suppression. `suppress=True` is never turned on, so an unbound key is not taken from Windows. The hook callback only classifies the event and queues work. It does not sleep, draw the window, run a macro, or read key state. The filter returns false, so Windows is not asked to translate the key and dead keys stay intact.

A per-app rule of **App** or **Both** passes the physical key through, down and up. **App** skips the action and the hold beep. **Both** runs the action and still passes the key. A short hold does not inject that key a second time. Version 1.3.7 let the key-down through and swallowed the key-up, so the foreground app could see a stuck key. 1.3.8 fixes that.

Every event Keyware injects sets `dwExtraInfo` to `0x4B575245` (ASCII `KWRE`). The hook ignores that tag. Injected events with any other extra info, including 0, are passed through untouched, so another hook (for example the Pulse assistant) still sees its own keys. Keyware does not inject a key-up for Ctrl, Win, Alt, or Shift while you are physically holding that modifier. Pulse's own hotkeys are left alone when `respect_pulse` is true (the default): exactly `Ctrl + Win`, `Ctrl + Win + Space`, and `Ctrl + Shift + Win`. A longer chord such as `Ctrl + Win + Shift + R` still fires. The list is `pulse_hotkeys` in the config file.

Win and Alt chords also tap an unassigned key (virtual key `0xE8`) so the Start menu and the menu bar do not open. If the hook callback throws, or if it cannot take its lock within 20 ms, the key is passed through. If the foreground window is elevated, remaps pass through and replayed keys are released, so a modifier cannot stick under UIPI. After sleep, the lock screen, or the UAC desktop, a watchdog removes the hook and installs it again. Three failed reinstalls turn Keyware off. AltGr (right Alt, or Alt with the extended flag) does not count Ctrl as a chord modifier. Key repeat does not restart a hold.

Panic clears timers, releases keys Keyware replayed (not a modifier you are still holding), and cancels a macro between steps. A key that stays logically down for 30 seconds with no repeat is released. On Windows, the timer also drops keys the system says are already up, which covers a missed key-up after locking the screen. One process per session holds the mutex `Local\Keyware.single`. A second launch prints `Keyware is already running.` and exits. Shutdown releases replayed keys and the hook.

URLs must be `http` or `https` (or `spotify:` links, which become web-player links when the app isn't installed). Application launches do not go through a shell. **Run command** is the one exception: it runs through `cmd.exe /c` with no window, only after you confirm that exact command once (the confirmation is stored as a SHA-256 hash in the config; any edit asks again). Keyware never runs commands that shut down, restart, sign out, delete files, format or partition disks, edit the registry, download code, or change the default audio device; it never offers shutdown or sign-out actions. Media keys act on the whole system and never switch audio devices. **Open file or folder** refuses programs and scripts (`.exe`, `.bat`, `.ps1`, `.lnk` ...).

## Describe it (Text mode)

The binding editor has a small **Manual / Text** toggle. Manual is the usual
dropdown and fields. In Text, write what a key should do in plain words
("F2 opens YouTube and searches minecraft") and press Generate (or Ctrl+Enter).
A free model turns it into a binding and fills the Manual fields; a summary
card shows the result. Switch to Manual to tweak it, then press Save.
Nothing is ever saved automatically. If the sentence does not name an app, the
binding works in every window. Opening Calculator opens calc.exe. There is no
separate Calculator action, and the app being opened is not treated as a profile.

- Models, free tiers only, in order: `gemini-3.5-flash-lite`,
  `gemini-3.1-flash-lite`, Groq `openai/gpt-oss-120b`, `openai/gpt-oss-20b`.
  About 6 s per try, 14 s total; the call runs on a worker thread.
- By default the request goes to the Keyware proxy (`ai_endpoint`, see `site/api/bind.js`),
  which holds the provider keys and applies a daily limit per install. In Settings you
  can instead **use your own free key** (Gemini `AIza…` or Groq `gsk_…`); it is stored
  with Windows DPAPI and never logged. The app never reads a `.env` file. Developers
  running from source can set `KEYWARE_DEV_KEYS=1` with `GEMINI_API_KEY` / `GROQ_API_KEY`
  in the environment.
- The reply is strict JSON, validated like a hand-made binding. Unknown keys,
  actions, steps or fields, non-http links, shells/scripts (cmd, powershell,
  .bat/.ps1 ...), command-like text, Win+R then typing, Ctrl+Alt+Delete and
  the panic chord are rejected. A clashing trigger shows the normal conflict
  message with Replace.
- One sentence can make several bindings ("F9 opens YouTube, F10 locks my PC"). They are listed for review; remove any you don't want, then **Add all**. A trigger used twice, a trigger that is already bound, or the panic chord is flagged and must be removed first. Nothing is overwritten.
- Text that isn't about key bindings shows: "That doesn't look like a key binding. Describe what you want your keys to do, like “F9 opens YouTube”." Nothing is created.
- The AI knows the in-app steps (open with links, wait for a window, play on Spotify or YouTube, media keys) and fixes misspelled song names ("du bist gueng" → "Du bist genug").
- The code lives in `assist/`; the hook engine in `engine/` stays network-free.
- Live check without hooks or apps: `python tools/ai_binder_live.py`.

## Config

Settings and bindings are JSON at:

```text
%APPDATA%\Keyware\config.json
```

On a machine without `APPDATA`, that is `~/.config/keyware/config.json`. A corrupt file is copied to `config.json.bak` and replaced with the defaults on startup. A hand-edited file is canonicalized on load. A binding with an unknown key is turned off and kept, and the reason is written to the log. Saving the file while Keyware is open reloads it. If that edit does not parse, the previous config stays and the log says the reload was skipped. The hook stays off until you turn Active on.

`rollover_ms` is the overlap window in milliseconds. `0` disables it. The window is not shown in Settings.

`dry_run` logs actions and does not swallow keys. `pause_hotkey` defaults to Ctrl, Alt, and F12. `respect_pulse` and `pulse_hotkeys` are the Pulse exclusion list. `start_with_windows` is kept so older files still load. A new install turns it on once; after that the Settings choice wins. Hold P in the sample profile stays off until you enable it.

`donate_url` (an `https` link, empty by default) shows a **♥ Support Keyware** button in the footer and an occasional note on every second launch (never the first), with **Support Keyware**, **Maybe later** and **Don't ask again**. With no link, neither appears. There are no ads. `approved_commands` holds hashes of the Run commands you confirmed.

Upgrading from 1.3.2 renames the Minecraft profile to Gaming once, after copying the old config to `D:\Keyware_backups\config_before_v133_<date>.json` (or `backups\` next to the config).

## Install and run

Windows, Python 3.11 or newer. `run.bat` creates `.venv` from `requirements.txt` when it is missing, then starts Keyware with `pythonw` so no console window stays open:

```bat
run.bat
```

The same steps by hand:

```bat
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\pythonw.exe keyware.pyw
```

`python main.py` and `python main.py --minimized` also work. `--minimized` starts in the tray when a tray backend is available. The **Start with Windows** entry (on by default since v1.3.4, off in Settings) launches Keyware the same silent, tray-only way.

Turn on **Active** in the header. The panic chord is always shown there. Pause is `Ctrl + Alt + F12`.

Check the engine without installing a hook or sending input:

```bat
python main.py --self-test
```

Check that a hook can be installed and is not left running:

```bat
python main.py --diag
```

On a system that cannot install the hook, `--diag` prints that and exits 0. On Windows, a hook that stays installed exits 1.

## Tests

`tools/e2e_drive.py` is a real end-to-end check on Windows. It opens its own small window, injects keys tagged `0x4B575445` (`KWTE`, which Keyware treats like typed keys) only while that window is in front, and checks what Keyware typed back. `tools/e2e_config.py add|remove <config.json>` adds or removes the temporary `E2E test` profile (python.exe) it needs. It never sends Ctrl + Win.

```bat
pip install -r requirements-dev.txt
python -m pytest
```

The tests feed synthetic key events into the detector and the service. They do not open applications, send keystrokes, or play audio. A fixture blocks `subprocess`, `os.startfile`, `webbrowser`, and `winsound` for the whole run.

## Platforms

Remapping is Windows-only. It is not a packaging gap. Turning Keyware on calls `HookEngine.start()`, which raises `HookUnavailable` unless `sys.platform == "win32"`. The message is: "The keyboard hook is only installed on Windows. Bindings can still be edited." The mouse hook does the same.

The listener is a Win32 low-level hook (`WH_KEYBOARD_LL` / `WH_MOUSE_LL`) through pynput's `win32_event_filter`. Events are classified from `vkCode`, `scanCode`, `flags`, and `dwExtraInfo`. Swallowing is `suppress_event()` on that filter. Injection is `SendInput` in `engine/sendinput.py`, which raises off Windows. There is no Quartz, CGEvent, or IOHID path. `keyware.spec` bundles only the Win32 pynput and pystray backends.

These other behaviors are Windows APIs too, so a Mac build of the same code would not gain them:

| Behavior | Windows API | Off Windows |
| --- | --- | --- |
| Foreground app / per-app profiles | `GetForegroundWindow` and the process image name | The context reader returns nothing, so profiles stay on Global |
| Elevated window | Process access / UIPI | The elevated check is not installed |
| Stuck-key reconcile | `GetAsyncKeyState` | The probe reports every key down, so reconcile does nothing |
| Hook-removed check | `GetLastInputInfo` | No probe is installed |
| One instance | Mutex `Local\Keyware.single` | A file lock under the config directory |
| Start with Windows | HKCU `Run` | `StartupManager` keeps the setting in memory only |
| Own AI key | DPAPI | `assist.secret_store.available()` is false |
| Open URL, app, or file | `os.startfile` | `SystemOpener` raises |
| Lock screen, wait for a window | `LockWorkStation`, window enumeration | Lock raises; wait returns false |
| Run command | `cmd.exe /c` | `SystemOpener` raises before starting a process |
| Hold beep | `winsound.Beep` | The player ignores the error |
| Caps Lock light | `GetKeyState` | Reported off |
| Copilot key | Win+Shift+F23 on the Win32 hook | Not a Mac key |

What does run elsewhere: the detector, config, conflict checks, and Text-mode validation. `python main.py --self-test` never installs a hook. `python main.py --diag` prints that the hook is Windows-only and exits 0. CI runs that headless pytest suite on `windows-latest` before it builds `Keyware.exe`. The site job on `ubuntu-latest` only tests the website. The same pytest suite passes on Linux when the window tests are skipped (no display). The window can open where CustomTkinter has a display, and bindings can be saved. Activating Keyware does not remap keys.

**macOS.** Keyware does not publish a Mac download, on Apple Silicon or Intel. A `.app` or a PyInstaller bundle of this code would not remap keys. `HookEngine.start()` raises `HookUnavailable` unless `sys.platform == "win32"`, and the window then shows UNAVAILABLE with that message. pynput on macOS can observe some keys, but this code never uses that backend. macOS suppression is a CGEvent tap that needs Accessibility permission (often Input Monitoring as well). That tap does not speak VK codes, scan codes, or `dwExtraInfo`, which is how Keyware ignores its own injected events. Posting keys would be `CGEventPost`, not `SendInput`. Per-app rules match Windows executable names (`cursor.exe`), not bundle ids. The Copilot key, Caps Lock light, UIPI, and the secure desktop have no counterpart here. A Mac App Store sandbox cannot install a global event tap. A Developer ID app outside the store can ask for Accessibility, and the user has to allow it in System Settings. That is a new input stack, not a rebuild of `Keyware.exe`. CI does not run a macOS packaging job.

**Windows ARM64.** The Python sources call Win32 APIs and do not set a CPU target. The published `Keyware.exe` is x64, and that file can run on ARM64 Windows under emulation. A native ARM64 exe is a rebuild on an ARM64 Windows machine (PyInstaller does not cross-compile). CI does that rebuild on `windows-11-arm` and names the file `Keyware-arm64.exe`. That packaging work is separate from the hook design above.

**Linux.** Same hook refusal as macOS. Config falls back to `~/.config/keyware/config.json` when `APPDATA` is unset. Useful for tests and for editing a config, not for remapping.

## Build a single exe

On Windows:

```bat
pip install -r requirements-dev.txt
python build.py
```

`build.bat` does the same thing. The output is `dist\Keyware.exe`. PyInstaller cannot cross-compile that file. An x64 Windows PC produces an x64 exe. A Windows on ARM PC, including the GitHub Actions `windows-11-arm` runner, produces a native ARM64 exe. Linux and macOS stop with instructions. The first launch of the one-file exe is slower while it unpacks. Errors from a windowed build are appended to `%APPDATA%\Keyware\error.log`.

## Sample bindings

| Profile | Trigger | Action |
| --- | --- | --- |
| Global | Ctrl + Win + Shift + R | Open https://www.youtube.com |
| Global | Hold P for 2 s | Off until you enable it, so a normal P is never delayed. A short P still types. |
| Global | Leader, then Y | Open YouTube after you set a leader key |
| Gaming | Hold Q for 800 ms | Shift + 1 |
| Gaming | Hold Q for 2 s | Key 1, wait 50 ms, right click, wait 20 ms, key 2 |
| Browser | Double-tap F8 | Off until you enable it. Opens YouTube. |
| Code | Ctrl + Win + Shift + L | Types `console.log();` |

## Layout

```text
main.py                 entry point (--self-test, --diag)
keyware.pyw             pythonw entry, no console
run.bat                 create .venv if needed, then pythonw
engine/sendinput.py     KEYWARE_EXTRA and tagged SendInput
engine/hook_engine.py   Windows WH_KEYBOARD_LL adapter
engine/watchdog.py      reinstall after sleep, lock, or UAC
engine/detector.py      taps, holds, chords, tap-dance, leader, panic
engine/service.py       queue between the hook and the window
ui/app.py               CustomTkinter window
ui/tray.py              pause, reload, open config, quit
```
