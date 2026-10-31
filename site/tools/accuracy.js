'use strict';
// Live accuracy check: sends the same 5 requests to every model in the fallback chain
// (bypassing the chain so each one is tested on its own) and scores the validated result.
//   GEMINI_API_KEY=... GROQ_API_KEY=... node tools/accuracy.js [model-id ...]
// Prints only model ids, pass/fail and short reasons. Never prints keys or raw replies.
const { CHAIN, callProvider, ProviderError } = require('../api/_lib/providers');
const { validateReply } = require('../api/_lib/validate');
const { SYSTEM_PROMPT } = require('../api/_lib/prompt');
const { contextMessage } = require('../api/_lib/core');

const one = (r) => (r.bindings ? r.bindings[0] : r);
const CASES = [
  { text: 'F9 opens YouTube', check: (r) => r.ok && one(r).trigger.mode === 'tap' && one(r).trigger.keys.join() === 'f9' && one(r).action.type === 'open_url' && /youtube\.com/.test(one(r).action.url) },
  { text: 'ctrl alt n opens notepad', check: (r) => r.ok && one(r).trigger.mode === 'chord' && [...one(r).trigger.keys].sort().join() === 'alt,ctrl,n' && /notepad/.test(JSON.stringify(one(r).action)) },
  { text: 'hold Q for 2 seconds in gaming to type gg then press enter', check: (r) => r.ok && one(r).trigger.mode === 'hold' && one(r).trigger.keys.join() === 'q' && one(r).trigger.hold_ms === 2000 && one(r).profile.name === 'Gaming' && /gg/.test(JSON.stringify(one(r).action)) && /enter/.test(JSON.stringify(one(r).action)) },
  { text: 'F12 shuts down my computer', check: (r) => r.ok === false && !r.not_binding },
  { text: 'what is the capital of france', check: (r) => r.ok === false && r.not_binding === true },
];

async function ask(provider, c) {
  const started = Date.now();
  try {
    const text = await callProvider({
      fetchImpl: fetch, provider, key: process.env[provider.keyEnv], systemPrompt: SYSTEM_PROMPT,
      userText: `${contextMessage({ app_version: '1.3.4', profiles: ['Global', 'Gaming'] })}\n\nRequest: ${c.text}`,
      timeoutMs: provider.slow ? 20000 : 12000, appVersion: '1.3.4', now: Date.now,
    });
    const v = validateReply(text);
    const ms = Date.now() - started;
    if (v.status === 'invalid') return { pass: false, note: `invalid (${v.reason}) ${ms}ms` };
    return c.check(v.reply) ? { pass: true, note: `ok ${ms}ms` } : { pass: false, note: `wrong ${ms}ms` };
  } catch (err) {
    return { pass: false, answered: false, note: err instanceof ProviderError ? `${err.kind}${err.status ? `:${err.status}` : ''}` : 'error' };
  }
}

async function main() {
  // Round-robin (every model gets case 1, then case 2, ...) with a pause between rounds, because
  // Groq's free tier allows only about one request per model per minute with this prompt size.
  const only = process.argv.slice(2);
  const pause = Number(process.env.ACC_ROUND_PAUSE_MS || 50000);
  const models = CHAIN.filter((p) => process.env[p.keyEnv] && (!only.length || only.includes(p.id)));
  const score = Object.fromEntries(models.map((p) => [p.id, { pass: 0, answered: 0, of: CASES.length, notes: [] }]));
  for (const [i, c] of CASES.entries()) {
    await Promise.all(models.map(async (p) => {
      const r = await ask(p, c);
      if (r.pass) score[p.id].pass += 1;
      if (r.answered !== false) score[p.id].answered += 1;
      score[p.id].notes.push(`"${c.text}": ${r.note}`);
    }));
    if (i < CASES.length - 1) await new Promise((r) => setTimeout(r, pause));
  }
  for (const p of models) {
    const s = score[p.id];
    console.log(`${p.id}: ${s.pass}/${s.of} correct (${s.answered} answered)`);
    for (const n of s.notes) console.log(`   ${n}`);
  }
}

main();
