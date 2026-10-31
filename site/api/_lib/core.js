'use strict';
// Keyware Text mode proxy: one sentence in, the model's binding JSON out.
// The Keyware app validates the JSON itself before anything can be saved.
//
// Never logs the description, an install ID, an IP, or a key.

const { Limiter, storeFromEnv, sharedMemory } = require('./ratelimit');
const { SYSTEM_PROMPT, PROMPT_VERSION } = require('./prompt');
const { CHAIN, ProviderError, Cooldowns, callProvider } = require('./providers');
const { validateReply } = require('./validate');
const { Stats } = require('./stats');

const MAX_DESCRIPTION = 500;
const MAX_BODY_BYTES = 8 * 1024;
const LIMIT_MESSAGE = 'Daily AI limit reached, try tomorrow or add your own free key';
const BUSY_MESSAGE = 'AI busy';
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const PROVIDERS = CHAIN;

function intEnv(env, name, fallback) {
  const value = parseInt(env[name], 10);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function clean(value, max) {
  return String(value == null ? '' : value).replace(/\s+/g, ' ').trim().slice(0, max);
}

function clientIp(headers) {
  const forwarded = String(headers['x-forwarded-for'] || '').split(',')[0].trim();
  return forwarded || String(headers['x-real-ip'] || '').trim() || 'unknown';
}

// Only the fields the prompt uses, each capped, so the context cannot be used to smuggle a long prompt.
function contextMessage(raw) {
  const ctx = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  const leader = clean(ctx.leader_key, 20) || 'not set';
  const current = clean(ctx.current_profile, 40) || 'Global';
  const profiles = (Array.isArray(ctx.profiles) ? ctx.profiles : []).slice(0, 20).map((item) => {
    const name = clean(item && item.name, 40) || 'Profile';
    const procs = (Array.isArray(item && item.process_names) ? item.process_names : [])
      .slice(0, 6)
      .map((p) => clean(p, 64))
      .filter((p) => /^[A-Za-z0-9_.\-]{1,64}$/.test(p));
    return `${name} (${procs.join(', ') || 'all apps'})`;
  });
  const triggers = (Array.isArray(ctx.triggers) ? ctx.triggers : []).slice(0, 60).map((t) => clean(t, 48)).filter(Boolean);
  let text = `Context: leader key is ${leader}. The editor is on profile “${current}”.`;
  if (profiles.length) text += ` Existing profiles: ${profiles.join('; ')}.`;
  if (triggers.length) text += ` Existing triggers: ${triggers.join('; ')}.`;
  return text;
}

function stripFences(text) {
  let out = String(text || '').trim();
  if (out.startsWith('```')) out = out.replace(/^```[a-zA-Z]*\s*/, '').replace(/\s*```$/, '');
  if (!out.startsWith('{')) {
    const start = out.indexOf('{');
    const end = out.lastIndexOf('}');
    if (start !== -1 && end > start) out = out.slice(start, end + 1);
  }
  return out;
}

function send(res, status, payload, extraHeaders) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'no-store');
  // No Access-Control-Allow-Origin on purpose: browsers cannot read or preflight this endpoint.
  for (const [name, value] of Object.entries(extraHeaders || {})) res.setHeader(name, value);
  res.end(JSON.stringify(payload));
}

function fail(res, status, code, error, extra) {
  send(res, status, { ok: false, code, error }, extra);
}

async function readBody(req) {
  if (req.body !== undefined && req.body !== null) {
    if (typeof req.body === 'string') return req.body;
    if (Buffer.isBuffer(req.body)) return req.body.toString('utf8');
    return JSON.stringify(req.body); // Vercel already parsed it
  }
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new Error('too_big'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

// Form posts (admin): on Vercel, req.body is already parsed into an object for
// application/x-www-form-urlencoded, so JSON-stringifying it would lose every field.
async function readForm(req, maxBytes = 4096) {
  const b = req.body;
  if (b !== undefined && b !== null && typeof b === 'object' && !Buffer.isBuffer(b)) {
    const form = new URLSearchParams();
    for (const [k, v] of Object.entries(b)) form.append(k, Array.isArray(v) ? String(v[0]) : String(v == null ? '' : v));
    if (form.toString().length > maxBytes) throw new Error('too_big');
    return form;
  }
  const raw = await readBody(req);
  if (raw.length > maxBytes) throw new Error('too_big');
  return new URLSearchParams(raw);
}

function createHandler(options = {}) {
  const env = options.env || process.env;
  const fetchImpl = options.fetch || globalThis.fetch;
  const now = options.now || Date.now;
  const log = options.log || ((entry) => console.log(JSON.stringify(entry)));
  const limiter = options.limiter || new Limiter({
    store: options.store || storeFromEnv(env, fetchImpl),
    fallback: options.fallback || sharedMemory,
    perInstall: intEnv(env, 'AI_LIMIT_PER_INSTALL', 20),
    perIp: intEnv(env, 'AI_LIMIT_PER_IP', 60),
    globalCap: intEnv(env, 'AI_LIMIT_GLOBAL', 1000),
    salt: env.RATE_LIMIT_SALT || 'keyware-rl-v1',
    now,
  });
  const perCallMs = intEnv(env, 'AI_PER_CALL_MS', 6000);
  const slowCallMs = intEnv(env, 'AI_SLOW_CALL_MS', 12000);
  const budgetMs = intEnv(env, 'AI_BUDGET_MS', 15000);
  const stats = options.stats || new Stats({ env, fetch: fetchImpl, now });
  const cooldowns = options.cooldowns || new Cooldowns(stats.kv);
  const chain = options.chain || PROVIDERS;

  return async function handler(req, res) {
    const started = now();
    const headers = req.headers || {};
    if (req.method === 'OPTIONS') return fail(res, 405, 'method', 'Use POST.', { Allow: 'POST' });
    if (req.method !== 'POST') return fail(res, 405, 'method', 'Use POST.', { Allow: 'POST' });
    if (headers.origin) return fail(res, 403, 'forbidden', 'Not available from a browser.');
    if (!String(headers['content-type'] || '').toLowerCase().startsWith('application/json')) {
      return fail(res, 415, 'bad_request', 'Send JSON.');
    }
    const installId = String(headers['x-keyware-install'] || '').trim();
    if (!UUID_RE.test(installId)) return fail(res, 400, 'bad_request', 'Missing install ID. Update Keyware.');

    let body;
    try {
      const raw = await readBody(req);
      if (Buffer.byteLength(raw, 'utf8') > MAX_BODY_BYTES) throw new Error('too_big');
      body = JSON.parse(raw);
    } catch (err) {
      if (err && err.message === 'too_big') return fail(res, 413, 'too_long', 'Request too large.');
      return fail(res, 400, 'bad_request', 'Send JSON.');
    }
    if (!body || typeof body !== 'object' || Array.isArray(body)) return fail(res, 400, 'bad_request', 'Send JSON.');
    const description = String(body.description == null ? '' : body.description).replace(/\s+/g, ' ').trim();
    if (!description) return fail(res, 400, 'bad_request', 'Describe what the key should do first.');
    if (description.length > MAX_DESCRIPTION) {
      return fail(res, 413, 'too_long', `Keep it under ${MAX_DESCRIPTION} characters.`);
    }
    const context = body.context && typeof body.context === 'object' ? body.context : {};
    const appVersion = clean(context.app_version || body.app_version, 16).replace(/[^0-9A-Za-z.\-]/g, '');

    const ticket = await limiter.take(installId, clientIp(headers));
    if (!ticket.allowed) {
      log({ evt: 'bind', status: ticket.reason === 'global' ? 503 : 429, limit: ticket.reason, store: ticket.store.name });
      if (ticket.reason === 'install' || ticket.reason === 'ip') await stats.recordAi({ installId, limited: true });
      if (ticket.reason === 'global') {
        return fail(res, 503, 'busy', BUSY_MESSAGE, { 'Retry-After': '3600' });
      }
      return fail(res, 429, 'rate_limited', LIMIT_MESSAGE, { 'Retry-After': '3600' });
    }

    const configured = chain.filter((p) => env[p.keyEnv]);
    const cooling = configured.length ? await cooldowns.active(configured.map((p) => p.id)) : new Set();
    // If everything is cooling down, still try the chain once rather than fail without asking.
    let usable = configured.filter((p) => !cooling.has(p.id));
    if (!usable.length) usable = configured;
    const userText = `${contextMessage(context)}\n\nRequest: ${description}`;
    let lastKind = configured.length ? 'none' : 'no_keys';
    const tried = [];
    for (const provider of usable) {
      const left = budgetMs - (now() - started);
      if (left < 800) {
        lastKind = 'budget';
        break;
      }
      let why;
      try {
        const text = await callProvider({
          fetchImpl, provider, key: env[provider.keyEnv], systemPrompt: SYSTEM_PROMPT, userText,
          timeoutMs: Math.min(provider.slow ? slowCallMs : perCallMs, left), appVersion, now,
        });
        const checked = validateReply(text);
        if (checked.status !== 'invalid') {
          log({ evt: 'bind', status: 200, model: provider.id, kind: checked.status, tried: tried.length, ms: now() - started, chars: description.length });
          await stats.recordAi({ installId, model: provider.id, ok: true });
          return send(res, 200, {
            ok: true,
            prompt_version: PROMPT_VERSION,
            model: provider.id,
            result: checked.reply,
            remaining: ticket.remaining,
          });
        }
        why = `invalid:${checked.reason}`.slice(0, 60);
      } catch (err) {
        if (!(err instanceof ProviderError)) throw err;
        why = `${err.kind}${err.status ? `:${err.status}` : ''}`;
        await cooldowns.start(provider.id, err.cooldownSec);
      }
      tried.push(`${provider.id}=${why}`);
      lastKind = why;
      await stats.recordModelFailure(provider.id, why.split(':').slice(0, 2).join(':'));
    }
    await limiter.refund(ticket);
    log({ evt: 'bind', status: 503, why: lastKind, tried, ms: now() - started });
    return fail(res, 503, 'busy', BUSY_MESSAGE, { 'Retry-After': '30' });
  };
}

module.exports = {
  createHandler, contextMessage, stripFences, readBody, readForm, send, fail, clientIp, PROVIDERS, MAX_DESCRIPTION, LIMIT_MESSAGE, BUSY_MESSAGE,
  PROMPT_VERSION,
};
