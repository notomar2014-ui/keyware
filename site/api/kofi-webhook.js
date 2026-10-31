'use strict';
// POST /api/kofi-webhook — Ko-fi donation notifications (not connected yet).
// To connect: Ko-fi → Settings → API → Webhooks: URL https://keyware-app.vercel.app/api/kofi-webhook,
// copy the Verification Token into the Vercel env var KOFI_VERIFICATION_TOKEN, redeploy.
// Ko-fi posts application/x-www-form-urlencoded with one field, data = JSON. We keep only the
// amount, currency, type (Donation/Subscription), whether it is a first subscription payment and
// the date. Never names, emails or messages.
const crypto = require('crypto');
const { sharedStats } = require('./_lib/stats');
const { readBody, send, fail } = require('./_lib/core');

function sameToken(a, b) {
  const x = crypto.createHash('sha256').update(String(a)).digest();
  const y = crypto.createHash('sha256').update(String(b)).digest();
  return crypto.timingSafeEqual(x, y) && String(a).length === String(b).length;
}

function createKofiHandler({ stats, env = process.env, log = () => {} } = {}) {
  return async function kofi(req, res) {
    if (req.method !== 'POST') return fail(res, 405, 'method', 'Use POST.', { Allow: 'POST' });
    const token = env.KOFI_VERIFICATION_TOKEN;
    if (!token) return fail(res, 503, 'not_connected', 'Ko-fi is not connected yet.');
    let data;
    try {
      const raw = await readBody(req);
      if (raw.length > 16 * 1024) return fail(res, 413, 'too_long', 'Too large.');
      const form = new URLSearchParams(raw);
      data = JSON.parse(form.get('data') || (typeof req.body === 'object' && req.body && req.body.data) || '');
    } catch (_e) {
      return fail(res, 400, 'bad_request', 'Bad payload.');
    }
    if (!data || typeof data !== 'object' || !sameToken(data.verification_token || '', token)) {
      log({ evt: 'kofi', status: 401 });
      return fail(res, 401, 'forbidden', 'Bad token.');
    }
    const amount = Math.round(parseFloat(data.amount) * 100);
    const currency = String(data.currency || '').toUpperCase().replace(/[^A-Z]/g, '').slice(0, 3);
    const kind = /subscription/i.test(String(data.type || '')) ? 'monthly' : 'one-time';
    const id = String(data.message_id || data.kofi_transaction_id || '').slice(0, 100);
    if (!Number.isFinite(amount) || amount <= 0 || amount > 1e7 || !currency || !id) return fail(res, 400, 'bad_request', 'Bad payload.');
    const st = stats || sharedStats();
    try {
      const rec = { amount_cents: amount, currency, kind, first: Boolean(data.is_first_subscription_payment), at: String(data.timestamp || new Date().toISOString()).slice(0, 32) };
      const fresh = await st.records.addDonation(rec, st.hash('kofi', id));
      log({ evt: 'kofi', status: 200, dup: !fresh });
      return send(res, 200, { ok: true });
    } catch (_e) {
      return fail(res, 503, 'busy', 'Try again.');
    }
  };
}

module.exports = createKofiHandler();
module.exports.createKofiHandler = createKofiHandler;
