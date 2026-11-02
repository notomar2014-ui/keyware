const test = require('node:test');
const assert = require('node:assert/strict');
const { cleanBinding, Bad } = require('../api/_lib/validate');

const base = { label: 'x', profile: { scope: 'global' }, trigger: { mode: 'chord', keys: ['ctrl', 'shift', 's'] }, action: { type: 'none' } };

test('app_rules pass through normalized', () => {
  const out = cleanBinding({ ...base, app_rules: { 'Cursor.exe': 'App', code: 'both' } });
  assert.deepEqual(out.app_rules, { 'cursor.exe': 'app', 'code.exe': 'both' });
  const list = cleanBinding({ ...base, app_rules: [{ app: 'cursor.exe', mode: 'keyware' }] });
  assert.deepEqual(list.app_rules, { 'cursor.exe': 'keyware' });
  assert.equal(cleanBinding(base).app_rules, undefined);
});

test('bad app_rules mode is rejected', () => {
  assert.throws(() => cleanBinding({ ...base, app_rules: { 'cursor.exe': 'sometimes' } }), Bad);
});
