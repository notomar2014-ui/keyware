'use strict';
// Fallback chain: order, cooldowns in the shared store, budget, Gemma request shape, usage stats.
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { Readable } = require('node:stream');

const { createHandler } = require('../api/_lib/core');
const { MemoryStore } = require('../api/_lib/ratelimit');
const { CHAIN, cooldownFor, buildBody, secondsUntilPacificMidnight } = require('../api/_lib/providers');
const { Stats } = require('../api/_lib/stats');
const { MemoryKV } = require('../api/_lib/kv');
const prompt = require('../api/_lib/prompt');

const ENV = { GEMINI_API_KEY: 'AIzaFAKEFAKEFAKEFAKE123', GROQ_API_KEY: 'gsk_FAKEFAKEFAKEFAKE123' };
const GOOD = { ok: true, label: 'Lock', profile: { scope: 'global' }, trigger: { mode: 'tap', keys: ['f7'] }, action: { type: 'lock_screen' }, notes: '' };
const reply = (o) => ({ ok: true, status: 200, json: async () => ({ choices: [{ message: { content: typeof o === 'string' ? o : JSON.stringify(o) } }] }) });
const status = (s, body = '', retryAfter) => ({ ok: false, status: s, text: async () => body, headers: { get: (h) => (h === 'retry-after' ? retryAfter : null) } });

function setup({ answers = [], env = ENV, clock = { t: Date.UTC(2026, 9, 31, 9, 0, 0) }, kv } = {}) {
  const calls = [];
  const fetch = async (url, init) => {
    const body = JSON.parse(init.body);
    calls.push({ url, body });
    const next = answers.length ? answers.shift() : status(503);
    if (typeof next === 'function') return next(url, init, clock);
    if (next instanceof Error) throw next;
    return next;
  };
  const now = () => clock.t;
  const store = kv || new MemoryKV(now);
  const stats = new Stats({ env: {}, kv: store, now });
  const logs = [];
  const handler = createHandler({ env, fetch, now, store: new MemoryStore(), fallback: new MemoryStore(), stats, log: (e) => logs.push(e) });
  return { handler, calls, stats, kv: store, clock, logs, answers };
}

async function post(handler, description = 'F7 locks my pc') {
  const req = Readable.from([Buffer.from(JSON.stringify({ description, context: { app_version: '1.3.4' } }))]);
  req.method = 'POST';
  req.headers = { 'content-type': 'application/json', 'x-keyware-install': crypto.randomUUID(), 'x-forwarded-for': '198.51.100.4' };
  const res = { statusCode: 0, headers: {}, setHeader(k, v) { this.headers[k.toLowerCase()] = v; }, end(t) { this.json = JSON.parse(t); } };
  await handler(req, res);
  return res;
}

test('chain order: Gemini Flash-Lite models, Groq models, then Gemma on the Gemini key', () => {
  assert.deepEqual(CHAIN.map((p) => p.id), [
    'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite',
    'groq-gpt-oss-120b', 'groq-qwen3.8-27b', 'groq-gpt-oss-20b', 'gemma-4-26b-a4b',
  ]);
  assert.ok(CHAIN.every((p) => ['GEMINI_API_KEY', 'GROQ_API_KEY'].includes(p.keyEnv)));
});

test('Gemma gets the prompt in the user turn and no JSON mode', () => {
  const gemma = CHAIN.find((p) => p.id === 'gemma-4-26b-a4b');
  const body = buildBody(gemma, prompt.SYSTEM_PROMPT, 'Request: x');
  assert.equal(body.messages.length, 1);
  assert.equal(body.messages[0].role, 'user');
  assert.ok(body.messages[0].content.startsWith(prompt.SYSTEM_PROMPT));
  assert.equal(body.response_format, undefined);
  const flash = buildBody(CHAIN[0], prompt.SYSTEM_PROMPT, 'Request: x');
  assert.deepEqual(flash.response_format, { type: 'json_object' });
  assert.equal(flash.messages[0].role, 'system');
});

test('a daily 429 cools the model down until the quota resets; the next request skips it', async () => {
  const s = setup({ answers: [status(429, '{"error":{"message":"Quota exceeded for GenerateRequestsPerDayPerProjectPerModel"}}'), reply(GOOD)] });
  const first = await post(s.handler);
  assert.equal(first.json.model, 'gemini-3.1-flash-lite');
  s.answers.push(reply(GOOD));
  const second = await post(s.handler);
  assert.equal(second.json.model, 'gemini-3.1-flash-lite');
  assert.equal(s.calls.length, 3, 'the exhausted model was not called again');
  const [ttl] = await s.kv.run([['TTL', 'kw:cd:gemini-3.5-flash-lite']]);
  assert.equal(ttl, secondsUntilPacificMidnight(s.clock.t));
  s.clock.t += (ttl + 1) * 1000;
  s.answers.push(reply(GOOD));
  const later = await post(s.handler);
  assert.equal(later.json.model, 'gemini-3.5-flash-lite', 'back after the reset');
});

test('per-minute 429 uses Retry-After or retryDelay; 5xx and timeouts cool down briefly', () => {
  const p = CHAIN[0];
  assert.equal(cooldownFor(p, 429, '', '12', 0), 12);
  assert.equal(cooldownFor(p, 429, '"retryDelay": "37s"', null, 0), 38);
  assert.equal(cooldownFor(p, 429, '', null, 0), 60);
  assert.equal(cooldownFor(CHAIN.find((x) => x.vendor === 'groq'), 429, 'Rate limit reached on requests per day (RPD)', null, 0), 3600);
  assert.equal(cooldownFor(p, 503, '', null, 0), 30);
  assert.equal(cooldownFor(p, 401, '', null, 0), 3600);
  assert.equal(cooldownFor(p, 400, '', null, 0), 0);
});

test('invalid output moves on to the next model without a cooldown', async () => {
  const s = setup({ answers: [reply('Sure! Press F7.'), reply({ ...GOOD, action: { type: 'explode' } }), reply(GOOD)] });
  const res = await post(s.handler);
  assert.equal(res.json.model, 'groq-gpt-oss-120b');
  const [cd] = await s.kv.run([['MGET', 'kw:cd:gemini-3.5-flash-lite']]);
  assert.deepEqual(cd, [null]);
  const sum = await s.stats.summary();
  assert.equal(sum.ai.failuresToday['gemini-3.5-flash-lite|invalid:not json'], 1);
});

test('unsafe model output becomes a refusal, never a binding', async () => {
  const s = setup({ answers: [reply({ ...GOOD, action: { type: 'run_command', command: 'shutdown /s /t 0' } })] });
  const res = await post(s.handler, 'F7 shuts down my pc');
  assert.equal(res.statusCode, 200);
  assert.equal(res.json.result.ok, false);
  assert.match(res.json.result.error, /shut down/);
  assert.equal(s.calls.length, 1);
});

test('a provider that hangs is cut off at the per-call limit and cooled down', async () => {
  const hang = (_u, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener('abort', () => { const e = new Error('aborted'); e.name = 'AbortError'; reject(e); });
  });
  const s = setup({ env: { ...ENV, AI_PER_CALL_MS: '30' }, answers: [hang, hang, reply(GOOD)] });
  const started = Date.now();
  const res = await post(s.handler);
  assert.equal(res.json.model, 'groq-gpt-oss-120b');
  assert.ok(Date.now() - started < 2000);
  const [a, b] = await s.kv.run([['TTL', 'kw:cd:gemini-3.5-flash-lite'], ['TTL', 'kw:cd:gemini-3.1-flash-lite']]);
  assert.equal(a, 30);
  assert.equal(b, 30);
});

test('defaults: 6 s per call (12 s for Gemma, which always thinks first), 15 s total', () => {
  const src = require('node:fs').readFileSync(require.resolve('../api/_lib/core'), 'utf8');
  assert.match(src, /'AI_PER_CALL_MS', 6000/);
  assert.match(src, /'AI_SLOW_CALL_MS', 12000/);
  assert.match(src, /'AI_BUDGET_MS', 15000/);
});

test('budget exhausted: stops trying and answers 503 in time', async () => {
  const eat = (ms) => (_u, _i, clock) => { clock.t += ms; return status(503); };
  const s = setup({ answers: [eat(6000), eat(6000), eat(2500), reply(GOOD)] });
  const res = await post(s.handler);
  assert.equal(res.statusCode, 503);
  assert.equal(s.calls.length, 3, 'no fourth call once fewer than 0.8 s are left of the 15 s budget');
});

test('all models cooling down: the chain is still tried once', async () => {
  const s = setup({ answers: [reply(GOOD)] });
  for (const p of CHAIN) await s.kv.run([['SET', `kw:cd:${p.id}`, '1', 'EX', 999]]);
  const res = await post(s.handler);
  assert.equal(res.statusCode, 200);
});

test('which model served each request is counted for the dashboard, with no content', async () => {
  const s = setup({ answers: [status(429), reply(GOOD), reply(GOOD)] });
  await post(s.handler);
  await post(s.handler);
  const sum = await s.stats.summary();
  assert.deepEqual(sum.ai.modelsToday, { 'gemini-3.1-flash-lite': 2 });
  assert.equal(sum.ai.today, 2);
  assert.equal(sum.ai.installsToday, 2);
  const logged = JSON.stringify(s.logs);
  assert.ok(!logged.includes('F7 locks') && !logged.includes('198.51.100.4'));
  assert.ok(s.logs.every((l) => !l.model || CHAIN.some((p) => p.id === l.model)));
});

test('only the Gemini key: Groq models are skipped, Gemma still backs up', async () => {
  const s = setup({ env: { GEMINI_API_KEY: ENV.GEMINI_API_KEY }, answers: [status(429), status(429), reply(GOOD)] });
  const res = await post(s.handler);
  assert.equal(res.json.model, 'gemma-4-26b-a4b');
  assert.ok(s.calls.every((c) => /generativelanguage/.test(c.url)));
});

test('Gemma <thought> block (with JSON-looking notes inside) is stripped before validation', async () => {
  const thought = '<thought>* trigger: `{"mode": "tap", "keys": ["f9"]}`\n* action: `{"type": "open_url"}`</thought>';
  const s = setup({ env: { GEMINI_API_KEY: ENV.GEMINI_API_KEY }, answers: [status(429), status(429), reply(thought + JSON.stringify(GOOD))] });
  const res = await post(s.handler);
  assert.equal(res.statusCode, 200);
  assert.equal(res.json.model, 'gemma-4-26b-a4b');
  assert.equal(res.json.result.ok, true);
});

test('a reply that is only an unfinished <thought> block is invalid, not a binding', async () => {
  const s = setup({ env: { GEMINI_API_KEY: ENV.GEMINI_API_KEY }, answers: [status(429), status(429), reply('<thought>{"ok":true,"trigger":{"mode":"tap","keys":["f9"]},"action":{"type":"lock_screen"}}')] });
  const res = await post(s.handler);
  assert.equal(res.statusCode, 503);
});
