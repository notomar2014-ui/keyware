# Changelog

## 1.4.2

- Windows app names are recognized. `calc.exe`, `calc`, `notepad.exe`, `mspaint.exe`, and `wt.exe` are accepted in any case, with or without `.exe`. App Execution Aliases under `WindowsApps` are not invisible just because `os.path.exists` cannot open them. A real program in `System32`, `SysWOW64`, or on `PATH` is used when it is there. Store apps such as Calculator, Photos, Paint, Settings, Terminal, and Xbox still open by app id.
- Per-app rules, profiles, and scripts match the process Windows actually shows. Calculator's window is `CalculatorApp.exe`, sometimes hosted by `ApplicationFrameHost.exe`. A rule or script for `calc.exe` matches that window. The same alias list covers the other built-in apps above.

## 1.4.1

- Optional scripting for power users. Settings has **Advanced scripting**, off by default. Manual and Text mode are unchanged until it is turned on. A script can branch on the app in front, the profile, the setup, and the modifiers that were held, then type, click, or run the binding's normal action with `do`. It cannot open a shell, run a command, or wait more than 2 seconds. The hook never runs a script. Pause and the panic chord stop it between steps and release keys it pressed.
- The hook timer no longer stats the config file on every 20 ms tick, log lines from the hook are written off that thread, and the mouse hook remembers which buttons are bound instead of scanning every binding.

## 1.4.0

- Open Microsoft Store apps such as Phone Link by Start-menu name and app id, plus shortcuts, App Paths, and PATH. A missing exe path is not launched.
- Text mode matches Phone Link, Settings, Calculator, Edge, Chrome, VS Code, Spotify, WhatsApp, Discord, Teams, and other installed names on this PC.
- Tab works as part of a shortcut, pressed or sent.
- Scroll Lock, Page Up, Page Down, Home, End, Delete, Insert, Print Screen, and Pause/Break work as triggers and as output, including in the key picker.
- Quiet update check against the GitHub release `notomar2014-ui/keyware`. The banner opens the Microsoft Store (product `9NGDP5BL0105`) for the Store build, or keyware.site for the portable build. No download. Later, Skip this version, and a Settings switch. Offline stays silent.

## 1.3.9

- Text mode accepts a normal sentence. A binding that names no app works in every window.
- Opening Calculator opens calc.exe. An old `open_calculator` action does too.
