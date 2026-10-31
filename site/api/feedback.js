'use strict';
// POST /api/feedback — "Suggestions & feedback" from the Keyware app.
// Stores type, message, email, app version, date and a hashed install ID. Emails are only
// shown on the private /admin page; they are never published or logged.
const { sharedStats, newId } = require('./_lib/stats');
const { readBody, send, fail, clientIp } = require('./_lib/core');

const TYPES = new Set(['idea', 'feedback', 'bug']);
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const EMAIL_RE = /^[^\s@<>()",;:]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$/;
const MAX_MESSAGE = 2000;
const PER_INSTALL = 5;
const PER_IP = 5;
const GLOBAL_PER_DAY = 300;

function intEnv(env, name, fallback) {
  const v = parseInt(env[name], 10);
  return Number.isFinite(v) && v > 0 ? v : fallback;
}

function spamReason(message) {
  const links = (message.match(/https?:\/\/|www\./gi) || []).length;
  if (links > 3) return 'Too many links.';
  if (!/\p{L}{2,}/u.test(message)) return 'Write a few words first.';
  if (/(.)\1{24,}/.test(message)) return 'That looks like a typo.';
  if (/\b(?:viagra|casino|crypto\s*airdrop|seo\s+services|backlinks|loan\s+offer)\b/i.test(message)) return 'That looks like spam.';
  return '';
}

function createFeedbackHandler({ stats, env = process.env, log = () => {}, now = Date.now } = {}) {
  return async function feedback(req, res) {
    const st = stats || sharedStats();
    const headers = req.headers || {};
    if (req.method !== 'POST') return fail(res, 405, 'method', 'Use POST.', { Allow: 'POST' });
    if (headers.origin) return fail(res, 403, 'forbidden', 'Send feedback from the Keyware app.');
    if (!/^Keyware\/[0-9][0-9A-Za-z.\-]{0,15}$/.test(String(headers['user-agent'] || ''))) {
      return fail(res, 403, 'forbidden', 'Send feedback from the Keyware app.');
    }
    if (!String(headers['content-type'] || '').toLowerCase().startsWith('application/json')) return fail(res, 415, 'bad_request', 'Send JSON.');
    const installId = String(headers['x-keyware-install'] || '').trim();
    if (!UUID_RE.test(installId)) return fail(res, 400, 'bad_request', 'Update Keyware to send feedback.');
    let body;
    try {
      const raw = await readBody(req);
      if (Buffer.byteLength(raw, 'utf8') > 12 * 1024) return fail(res, 413, 'too_long', 'Keep it under 2000 characters.');
      body = JSON.parse(raw);
    } catch (_e) {
      return fail(res, 400, 'bad_request', 'Send JSON.');
    }
    if (!body || typeof body !== 'object' || Array.isArray(body)) return fail(res, 400, 'bad_request', 'Send JSON.');
    const type = String(body.type || '').trim().toLowerCase();
    const message = String(body.message == null ? '' : body.message).replace(/\r\n?/g, '\n').replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, '').trim();
    const email = String(body.email == null ? '' : body.email).trim();
    const version = String(body.app_version || '').replace(/[^0-9A-Za-z.\-]/g, '').slice(0, 16);
    if (!TYPES.has(type)) return fail(res, 400, 'invalid', 'Pick Idea, Feedback or Bug.');
    if (message.length < 5) return fail(res, 400, 'invalid', 'Write a few words first.');
    if (message.length > MAX_MESSAGE) return fail(res, 413, 'invalid', `Keep it under ${MAX_MESSAGE} characters.`);
    if (!email || email.length > 254 || !EMAIL_RE.test(email)) return fail(res, 400, 'invalid', "That email doesn't look right.");
    const spam = spamReason(message);
    if (spam) return fail(res, 422, 'invalid', spam);

    try {
      const ip = clientIp(headers);
      const within = await st.incrLimit('fb', [`i:${st.hash('install', installId)}`, `a:${st.hash('ip', ip)}`], Math.min(intEnv(env, 'FEEDBACK_PER_INSTALL', PER_INSTALL), intEnv(env, 'FEEDBACK_PER_IP', PER_IP)));
      const global = await st.incrLimit('fbglobal', ['all'], intEnv(env, 'FEEDBACK_GLOBAL', GLOBAL_PER_DAY));
      if (!within) return fail(res, 429, 'rate_limited', "You've sent a lot today. Try again tomorrow.", { 'Retry-After': '3600' });
      if (!global) return fail(res, 503, 'busy', "Couldn't send right now. Try again tomorrow.", { 'Retry-After': '3600' });
      const dupKey = `kw:fb:dup:${st.hash('msg', `${email.toLowerCase()}|${message.toLowerCase().replace(/\s+/g, ' ')}`)}`;
      const [fresh] = await st.kv.run([['SET', dupKey, '1', 'NX', 'EX', 86400 * 7]]);
      if (fresh !== 'OK') {
        log({ evt: 'feedback', dup: true });
        return send(res, 200, { ok: true });
      }
      const t = now();
      const rec = { id: newId(t), type, message, email, app_version: version, created: new Date(t).toISOString(), install: st.hash('install', installId).slice(0, 12) };
      await st.records.addFeedback(rec);
      log({ evt: 'feedback', type, chars: message.length, store: st.records.name });
      return send(res, 200, { ok: true });
    } catch (_e) {
      log({ evt: 'feedback', err: 'store' });
      return fail(res, 503, 'busy', "Couldn't send right now. Try again in a minute.", { 'Retry-After': '60' });
    }
  };
}

module.exports = createFeedbackHandler();
module.exports.createFeedbackHandler = createFeedbackHandler;
module.exports.spamReason = spamReason;
