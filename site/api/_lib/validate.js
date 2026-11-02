'use strict';
// Strict check of every model reply before it reaches the app.
//
// validateReply(raw) -> {status:'ok', reply} | {status:'refusal', reply} | {status:'invalid', reason}
//   ok       a clean binding (or batch) in the exact schema the app expects; small slips
//            (key aliases, case, a missing profile, an out-of-range number) are repaired.
//   refusal  ok:false from the model, or a binding the safety rules forbid (shutdown, deleting
//            files, panic-chord trigger, ...). Returned to the app as a normal "can't do that".
//   invalid  broken JSON or schema: core.js tries the next model.
// The app re-validates everything again on its side (assist/ai_binder.py).

const MODIFIERS = new Set(['ctrl', 'alt', 'shift', 'win']);
const MEDIA = new Set(['volume_mute', 'volume_up', 'volume_down', 'media_play_pause', 'media_next', 'media_prev', 'media_stop']);
const NAMED = [
  'esc', 'enter', 'tab', 'space', 'backspace', 'delete', 'insert', 'home', 'end', 'page_up', 'page_down',
  'up', 'down', 'left', 'right', 'caps_lock', 'print_screen', 'menu',
  'numpad_add', 'numpad_subtract', 'numpad_multiply', 'numpad_divide', 'numpad_decimal',
  'semicolon', 'equals', 'comma', 'minus', 'period', 'slash', 'backtick', 'bracket_left', 'bracket_right',
  'backslash', 'quote',
];
const KEYS = new Set([...MODIFIERS, ...MEDIA, ...NAMED]);
for (let c = 97; c <= 122; c += 1) KEYS.add(String.fromCharCode(c));
for (let d = 0; d <= 9; d += 1) { KEYS.add(String(d)); KEYS.add(`numpad${d}`); }
for (let f = 1; f <= 24; f += 1) KEYS.add(`f${f}`);
// The Copilot key (Win+Shift+F23 from the keyboard) is one trigger key. It is never sent.
const TRIGGER_ONLY = new Set(['copilot']);
KEYS.add('copilot');

const ALIASES = {
  control: 'ctrl', ctl: 'ctrl', lctrl: 'ctrl', rctrl: 'ctrl', left_ctrl: 'ctrl', right_ctrl: 'ctrl',
  windows: 'win', super: 'win', meta: 'win', cmd: 'win', lwin: 'win', option: 'alt', altgr: 'alt',
  lalt: 'alt', ralt: 'alt', lshift: 'shift', rshift: 'shift', left_shift: 'shift', right_shift: 'shift',
  escape: 'esc', return: 'enter', spacebar: 'space', del: 'delete', ins: 'insert', back: 'backspace',
  pgup: 'page_up', pageup: 'page_up', prior: 'page_up', pgdn: 'page_down', pagedown: 'page_down', next: 'page_down',
  capslock: 'caps_lock', caps: 'caps_lock', prtsc: 'print_screen', printscreen: 'print_screen', print: 'print_screen',
  apps: 'menu', context_menu: 'menu', grave: 'backtick', tilde: 'backtick',
  arrow_up: 'up', arrow_down: 'down', arrow_left: 'left', arrow_right: 'right', up_arrow: 'up', down_arrow: 'down',
  left_arrow: 'left', right_arrow: 'right', mute: 'volume_mute', vol_up: 'volume_up', vol_down: 'volume_down',
  play_pause: 'media_play_pause', next_track: 'media_next', prev_track: 'media_prev', previous_track: 'media_prev',
  ';': 'semicolon', '=': 'equals', ',': 'comma', '-': 'minus', '.': 'period', '/': 'slash', '`': 'backtick',
  '[': 'bracket_left', ']': 'bracket_right', '\\': 'backslash', "'": 'quote',
  copilot_key: 'copilot', copilot_button: 'copilot', co_pilot: 'copilot', co_pilot_key: 'copilot', caps_lock_key: 'caps_lock',
};

function canonKey(raw) {
  if (typeof raw !== 'string' && typeof raw !== 'number') return null;
  let k = String(raw).trim().toLowerCase();
  if (!k) return null;
  if (k.length > 1) k = k.replace(/[\s-]+/g, '_');
  if (ALIASES[k]) k = ALIASES[k];
  const np = /^num(?:pad)?_?(\d)$/.exec(k);
  if (np) k = `numpad${np[1]}`;
  return KEYS.has(k) ? k : null;
}

const MODES = new Set(['tap', 'hold', 'double_tap', 'triple_tap', 'chord', 'leader']);
const MODE_ALIASES = { press: 'tap', single: 'tap', single_tap: 'tap', doubletap: 'double_tap', double: 'double_tap', tripletap: 'triple_tap', triple: 'triple_tap', combo: 'chord', shortcut: 'chord', hotkey: 'chord', long_press: 'hold' };

const BLOCKED_APPS = /^(?:cmd|powershell|pwsh|wscript|cscript|mshta|rundll32|regsvr32|reg|regedit|bcdedit|diskpart|shutdown|taskkill|schtasks|netsh|certutil|bitsadmin|wmic|format|del|erase|rmdir|rd|sc|vssadmin|cipher|msiexec|logoff|tsdiscon|mmsys)(?:\.(?:exe|cpl|com))?$/i;
const BLOCKED_EXT = /\.(?:bat|cmd|ps1|psm1|vbs|vbe|js|jse|wsf|wsh|hta|msi|msp|reg|scr|pif|com|cpl|lnk|jar)$/i;
const EXEC_EXT = /\.(?:exe|bat|cmd|ps1|psm1|vbs|vbe|js|jse|wsf|wsh|hta|msi|msp|reg|scr|pif|com|cpl|lnk|jar|dll|sys)$/i;

// Anything a binding must never do, whatever action carries it.
const DANGEROUS = [
  [/\b(?:shutdown|shut\s*down|poweroff|power\s*off|restart-computer|stop-computer|reboot|logoff|log\s*off|sign\s*out|signout|tsdiscon)\b|\/s\s*\/t\b|\bshutdown\s*[-/][srlfh]/i, 'shut down, restart or sign out'],
  [/\b(?:del|erase|rd|rmdir|rm|remove-item|ri|format|format-volume|clear-disk|cipher\s+\/w|vssadmin|wbadmin|sdelete)\b\s|\bremove-item\b|\brm\s+-|\bdel\s+\/|\brmdir\s+\/|\bformat\s+[a-z]:/i, 'delete files or format drives'],
  [/\b(?:reg(?:\.exe)?\s+(?:add|delete|import)|regedit|bcdedit|diskpart|set-itemproperty\s+.*hk(?:lm|cu)|new-itemproperty|netsh|schtasks|sc\s+(?:config|delete|stop)|takeown|icacls)\b/i, 'change system settings'],
  [/\b(?:setdefaultsounddevice|set-audiodevice|audiodevicecmdlets|soundvolumeview|svcl(?:\.exe)?|nircmd(?:c)?(?:\.exe)?\s+setdefault|mmsys\.cpl|ms-settings:sound)\b/i, 'change the default audio device'],
  [/\b(?:invoke-webrequest|iwr|invoke-restmethod|irm|curl|wget|certutil|bitsadmin|start-bitstransfer|downloadstring|downloadfile|new-object\s+net\.webclient)\b/i, 'download code'],
  [/\b(?:iex|invoke-expression|encodedcommand|-enc\b|frombase64string|mshta|rundll32|regsvr32|wscript|cscript)\b/i, 'run hidden scripts'],
];

function dangerIn(text) {
  const s = String(text || '');
  for (const [re, why] of DANGEROUS) if (re.test(s)) return why;
  return null;
}

class Unsafe extends Error {}
class Bad extends Error {}

const isObj = (v) => v && typeof v === 'object' && !Array.isArray(v);
const str = (v) => (typeof v === 'string' ? v : v === undefined || v === null ? '' : String(v));

function clampInt(v, lo, hi, dflt) {
  const n = Number(v);
  if (!Number.isFinite(n)) return dflt;
  return Math.min(hi, Math.max(lo, Math.round(n)));
}

function cleanUrl(raw) {
  let u = str(raw).trim().replace(/ /g, '+');
  if (/^javascript:|^data:|^file:|^vbscript:/i.test(u)) throw new Unsafe('open script links');
  if (/^www\./i.test(u) || /^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?:[/?#]|$)/i.test(u)) u = `https://${u}`;
  if (!/^https?:\/\/[^\s/]+\.[^\s]*$|^https?:\/\/[^\s/]+\.[a-z]{2,}$/i.test(u) || u.length > 2000) throw new Bad('url');
  if (/^https?:\/\/[^/]*@/i.test(u)) throw new Unsafe('a link with a hidden login');
  return u;
}

function cleanApp(raw) {
  const a = str(raw).trim().replace(/^"|"$/g, '');
  if (!a || a.length > 260) throw new Bad('app');
  const base = a.split(/[\\/]/).pop();
  if (/\s[-/]\w/.test(a) && !/\\/.test(a)) throw new Unsafe('run apps with switches');
  if (BLOCKED_APPS.test(base) || BLOCKED_EXT.test(base)) throw new Unsafe(`open ${base}`);
  return a;
}

function cleanPath(raw) {
  const p = str(raw).trim();
  if (!p || p.length > 260) throw new Bad('path');
  if (EXEC_EXT.test(p)) throw new Unsafe('open programs or scripts as files');
  if (/^\\\\/.test(p) || /[<>|]/.test(p)) throw new Unsafe('open that path');
  return p;
}

function cleanKeys(list, what) {
  if (!Array.isArray(list)) list = typeof list === 'string' ? list.split(/\s*\+\s*/) : [];
  const out = [];
  for (const raw of list) {
    const k = canonKey(raw);
    if (!k) throw new Bad(`${what} key`);
    if (what !== 'trigger' && TRIGGER_ONLY.has(k)) throw new Bad(`${what} key`);
    if (!out.includes(k)) out.push(k);
  }
  if (!out.length) throw new Bad(`${what} keys`);
  return out;
}

function isPanic(keys) {
  const s = new Set(keys);
  const has = (...k) => k.every((x) => s.has(x));
  return (has('ctrl', 'alt', 'shift', 'esc') && s.size === 4) || (has('ctrl', 'alt', 'delete') && s.size === 3);
}

function cleanTrigger(t, tapHold) {
  if (!isObj(t)) throw new Bad('trigger');
  let mode = str(t.mode).trim().toLowerCase().replace(/[\s-]+/g, '_');
  mode = MODE_ALIASES[mode] || mode;
  let keys = cleanKeys(t.keys, 'trigger');
  // A recorded Win+Shift+F23 is the Copilot key.
  if (keys.includes('copilot') || (keys.length === 3 && ['win', 'shift', 'f23'].every((k) => keys.includes(k)))) {
    keys = ['copilot'];
    if (!['tap', 'hold', 'double_tap', 'triple_tap'].includes(mode)) mode = 'tap';
  }
  const mods = keys.filter((k) => MODIFIERS.has(k));
  const plain = keys.filter((k) => !MODIFIERS.has(k));
  if (!MODES.has(mode)) {
    if (mods.length && plain.length === 1) mode = 'chord';
    else throw new Bad('trigger mode');
  }
  if (mode !== 'chord' && mods.length && plain.length === 1 && mode === 'tap') mode = 'chord';
  if (isPanic(keys)) throw new Unsafe('use the panic shortcut as a trigger');
  const out = { mode, keys: [] };
  if (mode === 'chord') {
    if (!mods.length || plain.length !== 1) throw new Bad('chord shape');
    const order = ['ctrl', 'alt', 'shift', 'win'];
    out.keys = [...order.filter((m) => mods.includes(m)), plain[0]];
  } else {
    if (plain.length !== 1 || mods.length) throw new Bad(`${mode} needs one key`);
    out.keys = [plain[0]];
  }
  if (mode === 'hold') {
    // Tap vs hold on one key uses a short threshold (default 200 ms).
    const dual = tapHold === true;
    out.hold_ms = clampInt(t.hold_ms, dual ? 100 : 300, 5000, dual ? 200 : 800);
    if (t.keep_held === true || t.keep_held === 'true') out.keep_held = true;
  }
  return out;
}

const KNOWN_PROC = /^[A-Za-z0-9_.\- ]{1,64}\.exe$/i;

// Apps the prompt names, so a profile the model gave only a name for still gets its process.
const KNOWN_APPS = {
  gaming: ['javaw.exe', 'minecraft.exe'], games: ['javaw.exe', 'minecraft.exe'], game: ['javaw.exe', 'minecraft.exe'],
  chrome: ['chrome.exe'], 'google chrome': ['chrome.exe'], edge: ['msedge.exe'], 'microsoft edge': ['msedge.exe'],
  firefox: ['firefox.exe'], code: ['code.exe'], 'vs code': ['code.exe'], vscode: ['code.exe'], 'visual studio code': ['code.exe'],
  discord: ['discord.exe'], spotify: ['spotify.exe'], notepad: ['notepad.exe'], roblox: ['robloxplayerbeta.exe'],
  valorant: ['valorant.exe'], fortnite: ['fortniteclient-win64-shipping.exe'],
  cursor: ['cursor.exe'], slack: ['slack.exe'], word: ['winword.exe'], 'microsoft word': ['winword.exe'],
  excel: ['excel.exe'], 'microsoft excel': ['excel.exe'], powerpoint: ['powerpnt.exe'],
  photoshop: ['photoshop.exe'], 'adobe photoshop': ['photoshop.exe'],
  premiere: ['adobe premiere pro.exe'], 'premiere pro': ['adobe premiere pro.exe'], 'adobe premiere pro': ['adobe premiere pro.exe'],
  'after effects': ['afterfx.exe'], obs: ['obs64.exe'], 'obs studio': ['obs64.exe'],
  figma: ['figma.exe'], blender: ['blender.exe'],
};

function cleanProfile(p) {
  if (!isObj(p) || str(p.scope).toLowerCase() !== 'app') return { scope: 'global' };
  let name = str(p.name).replace(/\s+/g, ' ').trim().slice(0, 40);
  let procs = Array.isArray(p.process_names) ? p.process_names : typeof p.process_names === 'string' ? [p.process_names] : [];
  procs = procs.map((x) => str(x).trim().toLowerCase()).map((x) => (x && !x.endsWith('.exe') ? `${x}.exe` : x)).filter((x) => KNOWN_PROC.test(x));
  if (/^minecraft$/i.test(name) || /^(gaming|games?)$/i.test(name)) name = 'Gaming';
  if (!procs.length && KNOWN_APPS[name.toLowerCase()]) procs = KNOWN_APPS[name.toLowerCase()];
  if (!name && procs.length) name = procs[0].slice(0, -4).replace(/^./, (c) => c.toUpperCase()).slice(0, 40);
  // A name alone is fine: the app matches it to the user's existing profile by name.
  if (!name) throw new Bad('app profile');
  return { scope: 'app', name, process_names: [...new Set(procs)].slice(0, 6) };
}

function cleanText(v, max) {
  const t = str(v);
  if (!t.length) throw new Bad('empty text');
  if (t.length > max) throw new Bad('text too long');
  return t;
}

function cleanMedia(v) {
  const m = canonKey(v);
  if (!m || !MEDIA.has(m)) throw new Bad('media key');
  return m;
}

function cleanStep(s) {
  if (!isObj(s)) throw new Bad('step');
  const op = str(s.op).trim().toLowerCase();
  switch (op) {
    case 'key': {
      const k = canonKey(s.key !== undefined ? s.key : Array.isArray(s.keys) ? s.keys[0] : '');
      if (!k) throw new Bad('step key');
      return { op, key: k };
    }
    case 'combo': {
      const keys = cleanKeys(s.keys, 'combo');
      if (isPanic(keys)) throw new Unsafe('press the panic shortcut');
      return { op, keys };
    }
    case 'text': {
      const text = cleanText(s.text, 8000);
      // Typing words is fine; typing a destructive command line (to paste into a shell) is not.
      if (/^\s*(?:cmd|powershell|pwsh)(?:\.exe)?\b|\bshutdown(?:\.exe)?\s+[-/]|\bremove-item\s|\bformat-volume\b|\brm\s+-rf\b|\bdel\s+\/[sfq]/i.test(text)) {
        throw new Unsafe('type destructive commands');
      }
      return { op, text };
    }
    case 'delay': case 'wait': return { op: 'delay', ms: clampInt(s.ms, 0, 60000, 50) };
    case 'mouse': case 'click': {
      const b = str(s.button || 'left').toLowerCase();
      if (!['left', 'right', 'middle'].includes(b)) throw new Bad('mouse button');
      return { op: 'mouse', button: b };
    }
    case 'url': return { op, target: cleanUrl(s.target || s.url) };
    case 'app': {
      const out = { op, target: cleanApp(s.target || s.app) };
      if (s.args !== undefined) {
        const args = (Array.isArray(s.args) ? s.args : [s.args]).map((a) => str(a).trim()).filter(Boolean);
        for (const a of args) {
          if (/^[-/]/.test(a)) throw new Unsafe('run apps with switches');
          if (dangerIn(a)) throw new Unsafe(dangerIn(a));
        }
        if (args.length) out.args = args.slice(0, 8);
      }
      if (s.fallback) {
        try { out.fallback = cleanUrl(s.fallback); } catch (_e) { /* drop a broken fallback */ }
      }
      return out;
    }
    case 'path': return { op, target: cleanPath(s.target || s.path) };
    case 'media': return { op, key: cleanMedia(s.key || s.media), count: clampInt(s.count, 1, 50, 1) };
    case 'lock': return { op };
    case 'screenshot': return { op };
    case 'wait_window': {
      const target = str(s.target).trim();
      const text = str(s.text).slice(0, 200);
      if (!target && !text) throw new Bad('wait_window target');
      return { op, target, text, ms: clampInt(s.ms, 500, 30000, 10000) };
    }
    case 'play': {
      const target = str(s.target || s.service || 'spotify').toLowerCase();
      if (!['spotify', 'youtube'].includes(target)) throw new Bad('play target');
      return { op, target, text: cleanText(s.text || s.query, 200) };
    }
    default:
      throw new Bad(`step op ${op || '?'}`);
  }
}

function cleanAction(a, trigger) {
  if (!isObj(a)) throw new Bad('action');
  const type = str(a.type).trim().toLowerCase();
  switch (type) {
    case 'open_url': return { type, url: cleanUrl(a.url) };
    case 'open_app': return { type, app: cleanApp(a.app || a.target) };
    case 'type_text': return { type, text: cleanText(a.text, 8000) };
    case 'key_combo': {
      const keys = cleanKeys(a.keys, 'combo');
      if (isPanic(keys)) throw new Unsafe('press the panic shortcut');
      return { type, keys };
    }
    case 'macro': {
      if (!Array.isArray(a.steps) || !a.steps.length) throw new Bad('macro steps');
      if (a.steps.length > 64) throw new Bad('too many steps');
      return { type, steps: a.steps.map(cleanStep) };
    }
    case 'auto_click': {
      const button = str(a.button || 'left').toLowerCase();
      if (!['left', 'right', 'middle'].includes(button)) throw new Bad('auto_click button');
      let mode = str(a.click_mode || 'hold').toLowerCase();
      if (!['hold', 'toggle'].includes(mode)) throw new Bad('click_mode');
      if (trigger && ['double_tap', 'triple_tap', 'leader'].includes(trigger.mode)) mode = 'toggle';
      return { type, button, cps: clampInt(a.cps, 1, 50, 10), click_mode: mode, max_seconds: clampInt(a.max_seconds, 0, 3600, 0) };
    }
    case 'open_path': return { type, path: cleanPath(a.path || a.target) };
    case 'media': return { type, media: cleanMedia(a.media || a.key), count: clampInt(a.count, 1, 50, 1) };
    case 'lock_screen': return { type };
    case 'screenshot': return { type };
    case 'none': return { type };
    case 'run_command': {
      const command = str(a.command).trim();
      if (!command || command.length > 500 || /[\r\n]/.test(command)) throw new Bad('command');
      const why = dangerIn(command);
      if (why) throw new Unsafe(why);
      if (/^\s*(?:cmd|powershell|pwsh)(?:\.exe)?\b/i.test(command)) throw new Unsafe('run shell commands');
      // Keyware always asks the user to confirm a command before it first runs.
      return { type, command };
    }
    case 'play': {
      const service = str(a.service || a.target || 'spotify').toLowerCase();
      if (!['spotify', 'youtube'].includes(service)) throw new Bad('play service');
      return { type, service, query: cleanText(a.query || a.text, 200) };
    }
    default:
      throw new Bad(`action type ${type || '?'}`);
  }
}

// Belt and braces: whatever the shape, these words never pass in a command-like field.
function scanAction(action) {
  const fields = [];
  if (action.type === 'open_app') fields.push(action.app);
  if (action.type === 'macro') for (const s of action.steps) if (s.op === 'app') fields.push(s.target, ...(s.args || []));
  for (const f of fields) {
    const why = dangerIn(f);
    if (why) throw new Unsafe(why);
  }
}

const LABEL_FALLBACK = {
  open_url: 'Open link', open_app: 'Open app', type_text: 'Type text', key_combo: 'Shortcut', macro: 'Macro',
  auto_click: 'Auto-click', open_path: 'Open folder', media: 'Media key', lock_screen: 'Lock PC',
  screenshot: 'Screenshot', run_command: 'Run command', play: 'Play', none: 'Key off',
};

// Per-app rules: exe -> keyware | app | both. Dict or [{app, mode}]; bad modes are a schema error.
const APP_RULE_MODES = new Set(['keyware', 'app', 'both']);
function cleanAppRules(raw) {
  if (raw === undefined || raw === null) return null;
  let items;
  if (Array.isArray(raw)) items = raw.filter(isObj).map((r) => [r.app, r.mode]);
  else if (isObj(raw)) items = Object.entries(raw);
  else throw new Bad('app_rules');
  const out = {};
  for (const [app, mode] of items.slice(0, 20)) {
    let name = str(app).trim().replace(/\\/g, '/').split('/').pop().trim().toLowerCase();
    if (!name) continue;
    if (!name.endsWith('.exe')) name += '.exe';
    if (!/^[a-z0-9 _.\-]{1,60}\.exe$/.test(name)) throw new Bad('app_rules app');
    const m = str(mode || 'keyware').trim().toLowerCase();
    if (!APP_RULE_MODES.has(m)) throw new Bad('app_rules mode');
    out[name] = m;
  }
  return Object.keys(out).length ? out : null;
}

function cleanBinding(b) {
  if (!isObj(b)) throw new Bad('binding');
  const t = isObj(b.trigger) ? b.trigger : {};
  const tapRaw = isObj(b.tap_action) ? b.tap_action : isObj(t.tap_action) ? t.tap_action : null;
  const dual = Boolean(tapRaw) || t.keep_held === true || t.keep_held === 'true';
  const trigger = cleanTrigger(b.trigger, dual);
  if (trigger.keep_held !== undefined) delete trigger.keep_held;
  const action = cleanAction(b.action, trigger);
  scanAction(action);
  let tapAction = null;
  if (trigger.mode === 'hold' && dual) {
    if ((t.keep_held === true || t.keep_held === 'true') && action.type === 'key_combo') trigger.keep_held = true;
    if (tapRaw) {
      tapAction = cleanAction(tapRaw, { mode: 'tap' });
      scanAction(tapAction);
      if (tapAction.type === 'auto_click' && tapAction.click_mode === 'hold') tapAction.click_mode = 'toggle';
    }
    if (!tapAction && !trigger.keep_held) trigger.hold_ms = Math.max(300, trigger.hold_ms);
  }
  if (action.type === 'auto_click' && trigger.mode === 'hold') {
    // A hold trigger would wait hold_ms before clicking; the prompt asks for tap + click_mode.
    trigger.mode = 'tap';
    delete trigger.hold_ms;
  }
  const profile = cleanProfile(b.profile);
  const label = str(b.label).replace(/\s+/g, ' ').trim().slice(0, 40) || LABEL_FALLBACK[action.type];
  const notes = str(b.notes).replace(/\s+/g, ' ').trim().slice(0, 300);
  const out = { label, profile, trigger, action, notes };
  if (tapAction) out.tap_action = tapAction;
  const appRules = cleanAppRules(b.app_rules);
  if (appRules) out.app_rules = appRules;
  return out;
}

function refusal(error, notBinding) {
  const reply = { ok: false, error: str(error).replace(/\s+/g, ' ').trim().slice(0, 200) || "Keyware can't make that binding." };
  if (notBinding) {
    reply.not_binding = true;
    reply.error = 'Not a key binding.';
  }
  return { status: 'refusal', reply };
}

function parseLoose(raw) {
  if (isObj(raw)) return raw;
  let text = str(raw).trim();
  if (!text) return null;
  text = text.replace(/^```[a-zA-Z]*\s*/, '').replace(/\s*```\s*$/, '');
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start === -1 || end <= start) return null;
  try {
    const v = JSON.parse(text.slice(start, end + 1));
    return isObj(v) ? v : null;
  } catch (_e) {
    return null;
  }
}

function validateReply(raw) {
  const obj = parseLoose(raw);
  if (!obj) return { status: 'invalid', reason: 'not json' };
  if (obj.ok === false || obj.ok === 'false') {
    if (obj.not_binding === true || obj.not_binding === 'true') return refusal('', true);
    if (!str(obj.error).trim()) return { status: 'invalid', reason: 'refusal without reason' };
    return refusal(obj.error);
  }
  if (obj.ok !== true && obj.ok !== 'true' && obj.ok !== undefined) return { status: 'invalid', reason: 'ok flag' };
  const list = Array.isArray(obj.bindings) ? obj.bindings : obj.trigger || obj.action ? [obj] : null;
  if (!list || !list.length) return { status: 'invalid', reason: 'no binding' };
  if (list.length > 8) return { status: 'invalid', reason: 'too many bindings' };
  const kept = [];
  const refused = [];
  for (const item of list) {
    try {
      kept.push(cleanBinding(item));
    } catch (err) {
      if (err instanceof Unsafe) refused.push(err.message);
      else return { status: 'invalid', reason: err.message || 'schema' };
    }
  }
  if (!kept.length) return refusal(`Keyware won't ${refused[0]}.`);
  if (refused.length) {
    const note = `Left out ${refused.length === 1 ? 'one binding' : `${refused.length} bindings`}: Keyware won't ${refused[0]}.`;
    kept[0].notes = kept[0].notes ? `${kept[0].notes} ${note}`.slice(0, 300) : note;
  }
  if (!Array.isArray(obj.bindings)) return { status: 'ok', reply: { ok: true, ...kept[0] } };
  return { status: 'ok', reply: { ok: true, bindings: kept } };
}

module.exports = { validateReply, canonKey, cleanBinding, dangerIn, parseLoose, KEYS, Unsafe, Bad };
