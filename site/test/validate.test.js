'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { validateReply, canonKey, dangerIn } = require('../api/_lib/validate');
const FIXTURES = require('./fixtures/model_outputs');

for (const f of FIXTURES) {
  test(`validator [${f.model}] ${f.name} -> ${f.expect}`, () => {
    const out = validateReply(f.text);
    assert.equal(out.status, f.expect, JSON.stringify(out));
    if (f.check) {
      const r = out.reply.bindings ? out.reply : out.reply;
      assert.ok(f.check(r), JSON.stringify(out.reply));
    }
    if (out.status === 'ok') assert.equal(out.reply.ok, true);
    if (out.status === 'refusal') assert.equal(out.reply.ok, false);
  });
}

test('key aliases map to the canonical names', () => {
  assert.equal(canonKey('PgUp'), 'page_up');
  assert.equal(canonKey('Num 5'), 'numpad5');
  assert.equal(canonKey('Windows'), 'win');
  assert.equal(canonKey('fn'), null);
});

test('danger patterns', () => {
  assert.ok(dangerIn('shutdown /r /t 0'));
  assert.ok(dangerIn('Stop-Computer'));
  assert.equal(dangerIn('code C:\\projects'), null);
  assert.equal(dangerIn('notepad.exe'), null);
});

test('fuzz: random and hostile replies never throw and never pass unsafe shapes', () => {
  const pieces = ['{', '}', '"ok":true', '"trigger":', '{"mode":"tap","keys":["f9"]}', '"action":', '{"type":"run_command","command":"shutdown /s"}',
    '{"type":"open_app","app":"powershell"}', ',', '[', ']', 'null', '"bindings":', '"ok":false', '<think>', '```', '\u0000', '"\\u202e"'];
  let seed = 7;
  const rnd = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
  for (let i = 0; i < 3000; i += 1) {
    let s = '';
    const n = 1 + Math.floor(rnd() * 12);
    for (let k = 0; k < n; k += 1) s += pieces[Math.floor(rnd() * pieces.length)];
    const out = validateReply(s);
    assert.ok(['ok', 'refusal', 'invalid'].includes(out.status));
    if (out.status === 'ok') {
      const text = JSON.stringify(out.reply);
      assert.ok(!/shutdown|powershell/i.test(text), text);
    }
  }
});

test('writes the parity fixture the app suite checks (tests/test_server_parity.py)', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const rows = FIXTURES.map((f) => {
    const out = validateReply(f.text);
    return { model: f.model, name: f.name, raw: f.text, status: out.status, reply: out.reply ? JSON.stringify(out.reply) : null };
  });
  fs.writeFileSync(path.join(__dirname, 'fixtures', 'validated.json'), JSON.stringify(rows, null, 1));
});
