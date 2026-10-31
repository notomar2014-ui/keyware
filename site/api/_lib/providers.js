'use strict';
// The free-tier fallback chain behind /api/bind, in order. Every entry runs on a free plan.
// A provider is skipped when its key is not configured or while it is cooling down
// (after a 429, 5xx, timeout or auth error), so an exhausted quota costs no time.
//
// Free limits (Oct 2026, per key; check the provider pages before relying on them):
//   Gemini Flash-Lite models: each model has its own requests-per-minute and per-day quota.
//   Gemma 4 on the Gemini API: about 14,400 requests/day, slower, no JSON mode.
//   Groq: per-model limits, roughly 1,000 requests/day for the larger models.

const GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions';
const GROQ_URL = 'https://api.groq.com/openai/v1/chat/completions';

const CHAIN = [
  { id: 'gemini-3.5-flash-lite', vendor: 'gemini', url: GEMINI_URL, model: 'gemini-3.5-flash-lite', keyEnv: 'GEMINI_API_KEY', json: true, system: true, maxTokens: 1800, daily: 'pacific' },
  { id: 'gemini-3.1-flash-lite', vendor: 'gemini', url: GEMINI_URL, model: 'gemini-3.1-flash-lite', keyEnv: 'GEMINI_API_KEY', json: true, system: true, maxTokens: 1800, daily: 'pacific' },
  { id: 'groq-gpt-oss-120b', vendor: 'groq', url: GROQ_URL, model: 'openai/gpt-oss-120b', keyEnv: 'GROQ_API_KEY', json: true, system: true, maxTokens: 2000, extra: { reasoning_effort: 'low' } },
  { id: 'groq-qwen3.8-27b', vendor: 'groq', url: GROQ_URL, model: 'qwen/qwen3.8-27b', keyEnv: 'GROQ_API_KEY', json: true, system: true, maxTokens: 2000, extra: { reasoning_format: 'hidden' } },
  { id: 'groq-gpt-oss-20b', vendor: 'groq', url: GROQ_URL, model: 'openai/gpt-oss-20b', keyEnv: 'GROQ_API_KEY', json: true, system: true, maxTokens: 2000, extra: { reasoning_effort: 'low' } },
  // Gemma on the same Gemini key: separate daily quota. No system role or JSON mode, so the
  // prompt goes into the user turn; it always "thinks" first (about 10 s), and the
  // <thought> block is stripped before validation. Last resort only.
  // Checked live on 31 Oct 2026: gemini-2.5-flash-lite is retired (404) and gemma-4-31b-it
  // answered 500 every time, so both are left out.
  { id: 'gemma-4-26b-a4b', vendor: 'gemini', url: GEMINI_URL, model: 'gemma-4-26b-a4b-it', keyEnv: 'GEMINI_API_KEY', json: false, system: false, maxTokens: 1800, daily: 'pacific', slow: true },
];

class ProviderError extends Error {
  constructor(kind, status, cooldownSec) {
    super(kind);
    this.kind = kind; // http | timeout | network | bad_json | empty
    this.status = status || 0;
    this.cooldownSec = cooldownSec || 0;
  }
}

function secondsUntilPacificMidnight(nowMs) {
  // Gemini daily quotas reset at midnight Pacific. 08:00 UTC is midnight PST (and 01:00 PDT),
  // so waiting for it never retries too early.
  const d = new Date(nowMs);
  let next = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), 8, 0, 0);
  if (next <= nowMs) next += 86400000;
  return Math.ceil((next - nowMs) / 1000);
}

// How long to skip a provider after a failure.
function cooldownFor(provider, status, bodyText, retryAfter, nowMs) {
  if (status === 429) {
    const text = String(bodyText || '');
    const daily = /per\s*day|PerDay|requests per day|RPD|TPD|daily/i.test(text);
    if (daily) return provider.daily === 'pacific' ? secondsUntilPacificMidnight(nowMs) : 3600;
    const ra = Number(retryAfter);
    if (Number.isFinite(ra) && ra > 0) return Math.min(3600, Math.ceil(ra));
    const m = /retry(?:Delay|\s+in)["':\s]*([\d.]+)\s*s/i.exec(text);
    if (m) return Math.min(3600, Math.ceil(Number(m[1])) + 1);
    return 60;
  }
  if (status === 401 || status === 403) return 3600; // bad or revoked key: stop hammering
  if (status === 404) return 6 * 3600; // model retired
  if (status >= 500) return 30;
  return 0;
}

function buildBody(provider, systemPrompt, userText) {
  const messages = provider.system
    ? [{ role: 'system', content: systemPrompt }, { role: 'user', content: userText }]
    : [{ role: 'user', content: `${systemPrompt}\n\n---\n${userText}\n\nReply with the JSON object only.` }];
  const body = { model: provider.model, messages, temperature: 0, max_tokens: provider.maxTokens };
  if (provider.json) body.response_format = { type: 'json_object' };
  if (provider.extra) Object.assign(body, provider.extra);
  return body;
}

async function callProvider({ fetchImpl, provider, key, systemPrompt, userText, timeoutMs, appVersion, now }) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res;
  try {
    res = await fetchImpl(provider.url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${key}`,
        'User-Agent': `KeywareProxy/${appVersion || '1'}`,
      },
      body: JSON.stringify(buildBody(provider, systemPrompt, userText)),
      signal: controller.signal,
    });
    if (!res.ok) {
      let text = '';
      try { text = typeof res.text === 'function' ? await res.text() : JSON.stringify(await res.json()); } catch (_e) { /* ignore */ }
      const ra = res.headers && typeof res.headers.get === 'function' ? res.headers.get('retry-after') : undefined;
      throw new ProviderError('http', res.status, cooldownFor(provider, res.status, text, ra, now()));
    }
    let data;
    try {
      data = await res.json();
    } catch (_err) {
      throw new ProviderError('bad_json');
    }
    const msg = data && data.choices && data.choices[0] && data.choices[0].message;
    let content = msg && msg.content;
    if (Array.isArray(content)) content = content.map((p) => (p && p.text) || '').join('');
    if (!content) throw new ProviderError('empty');
    // Reasoning models leak a <think> (Qwen) or <thought> (Gemma) block before the JSON.
    return String(content)
      .replace(/<(think|thought)>[\s\S]*?<\/\1>/gi, '')
      .replace(/^\s*<(think|thought)>[\s\S]*$/i, '')
      .trim();
  } catch (err) {
    if (err instanceof ProviderError) throw err;
    if (err && err.name === 'AbortError') throw new ProviderError('timeout', 0, 30);
    throw new ProviderError('network', 0, 15);
  } finally {
    clearTimeout(timer);
  }
}

// Cooldowns live in the shared store, so one instance's 429 spares the others.
class Cooldowns {
  constructor(kv) {
    this.kv = kv;
  }
  async active(ids) {
    try {
      const [vals] = await this.kv.run([['MGET', ...ids.map((id) => `kw:cd:${id}`)]]);
      return new Set(ids.filter((_id, i) => vals && vals[i]));
    } catch (_e) {
      return new Set();
    }
  }
  async start(id, seconds) {
    if (!seconds) return;
    try {
      await this.kv.run([['SET', `kw:cd:${id}`, '1', 'EX', Math.max(1, Math.ceil(seconds))]]);
    } catch (_e) {
      /* ignore */
    }
  }
  async remaining(ids) {
    try {
      const res = await this.kv.run(ids.map((id) => ['TTL', `kw:cd:${id}`]));
      const out = {};
      ids.forEach((id, i) => { if (Number(res[i]) > 0) out[id] = Number(res[i]); });
      return out;
    } catch (_e) {
      return {};
    }
  }
}

module.exports = { CHAIN, ProviderError, Cooldowns, callProvider, cooldownFor, buildBody, secondsUntilPacificMidnight };
