'use strict';
// /admin (rewritten to /api/admin): private stats dashboard. Not linked anywhere, noindex,
// not in the sitemap. Sign-in checks ADMIN_PASSWORD on the server only.
const { sharedStats } = require('./_lib/stats');
const { readForm, clientIp } = require('./_lib/core');
const auth = require('./_lib/adminauth');
const { VERSION } = require('./_lib/release');

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const TYPE_LABEL = { idea: 'Idea', feedback: 'Feedback', bug: 'Bug' };
const CSP = "default-src 'none'; style-src 'self'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'";

// Feedback times are stored in UTC; show them in the site owner's zone (Asia/Muscat, UTC+4, no DST), like the day charts.
function muscatTime(iso) {
  const t = Date.parse(String(iso || ''));
  if (!Number.isFinite(t)) return '?';
  return new Date(t + 4 * 3600 * 1000).toISOString().replace('T', ' ').slice(0, 16) + ' GST';
}

function page(title, body) {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>${esc(title)}</title><link rel="stylesheet" href="/assets/css/admin.css"></head>
<body><main class="adm">${body}</main></body></html>`;
}

function sendHtml(res, status, html, extra = {}) {
  res.statusCode = status;
  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('X-Robots-Tag', 'noindex, nofollow');
  res.setHeader('Content-Security-Policy', CSP);
  res.setHeader('X-Frame-Options', 'DENY');
  // same-origin, not no-referrer: with no-referrer Chrome sends `Origin: null` on the login POST,
  // which the same-origin check below would reject.
  res.setHeader('Referrer-Policy', 'same-origin');
  for (const [k, v] of Object.entries(extra)) res.setHeader(k, v);
  res.end(html);
}

function redirect(res, to, extra = {}) {
  res.statusCode = 303;
  res.setHeader('Location', to);
  res.setHeader('Cache-Control', 'no-store');
  for (const [k, v] of Object.entries(extra)) res.setHeader(k, v);
  res.end();
}

function loginPage(message) {
  return page('Keyware admin', `<section class="login card"><h1>Keyware admin</h1>
${message ? `<p class="msg">${esc(message)}</p>` : ''}
<form method="post" action="/admin/login"><label for="pw">Password</label>
<input id="pw" name="password" type="password" autocomplete="current-password" required maxlength="200" autofocus>
<button type="submit">Sign in</button></form></section>`);
}

function bars(series, days, label) {
  const max = Math.max(1, ...series);
  const w = 12;
  const gap = 4;
  const h = 120;
  const rects = series.map((v, i) => {
    const bh = Math.round((v / max) * (h - 20));
    const d = days[i];
    return `<rect x="${i * (w + gap)}" y="${h - bh}" width="${w}" height="${Math.max(bh, v ? 2 : 0)}" rx="2" class="bar"><title>${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}: ${v}</title></rect>`;
  }).join('');
  return `<svg class="chart" viewBox="0 0 ${series.length * (w + gap)} ${h}" role="img" aria-label="${esc(label)}">${rects}<line x1="0" y1="${h}" x2="${series.length * (w + gap)}" y2="${h}" class="axis"/></svg>
<div class="chart-legend"><span>${days[0].slice(4, 6)}/${days[0].slice(6)}</span><span>max ${max}</span><span>today</span></div>`;
}

function num(v) {
  return v == null ? '—' : Number(v).toLocaleString('en-US');
}

function storageNote(s) {
  const notes = [];
  if (!s.recordsSaved) notes.push('Downloads, feedback and donations are kept in server memory only (no database connected), so they reset when the server restarts.');
  if (!s.countersSaved) notes.push('AI request counts, active installs, provider usage and rate limits are kept per server instance in memory: they reset on restarts and are not exact totals. Connect Upstash Redis (free) in Vercel → Storage to keep them.');
  return notes.length ? `<div class="banner">${notes.map((n) => `<p>${esc(n)}</p>`).join('')}</div>` : '';
}

function donationsCard(env, donations) {
  if (!env.KOFI_VERIFICATION_TOKEN) {
    return `<section class="card"><h2>Donations</h2><p class="big muted">Not connected yet</p>
<p class="small">To connect Ko-fi: Ko-fi → Settings → API → Webhooks. Webhook URL <code>https://keyware-app.vercel.app/api/kofi-webhook</code>. Copy the Verification Token into the Vercel env var <code>KOFI_VERIFICATION_TOKEN</code> and redeploy. One-time and monthly totals will show here.</p></section>`;
  }
  const sums = { 'one-time': {}, monthly: {} };
  let count = 0;
  for (const d of donations || []) {
    const bucket = sums[d.kind === 'monthly' ? 'monthly' : 'one-time'];
    bucket[d.currency] = (bucket[d.currency] || 0) + d.amount_cents;
    count += 1;
  }
  const fmt = (b) => Object.entries(b).map(([c, v]) => `${(v / 100).toFixed(2)} ${esc(c)}`).join(' + ') || '0';
  return `<section class="card"><h2>Donations</h2><div class="grid2"><div><p class="label">One-time</p><p class="big">${fmt(sums['one-time'])}</p></div>
<div><p class="label">Monthly</p><p class="big">${fmt(sums.monthly)}</p></div></div><p class="small muted">${count} payment${count === 1 ? '' : 's'} received through Ko-fi.</p></section>`;
}

function feedbackCard(items, filter, csrf) {
  const shown = filter ? items.filter((f) => f.type === filter) : items;
  const unread = items.filter((f) => !f.read).length;
  const tabs = ['', 'idea', 'feedback', 'bug'].map((t) => `<a class="tab${t === (filter || '') ? ' on' : ''}" href="/admin${t ? `?type=${t}` : ''}">${t ? TYPE_LABEL[t] : 'All'}</a>`).join('');
  const rows = shown.map((f) => `<article class="fb${f.read ? ' read' : ''}">
<header><span class="badge ${esc(f.type)}">${esc(TYPE_LABEL[f.type] || f.type)}</span>
<span class="meta">${esc(muscatTime(f.created))} · v${esc(f.app_version || '?')}</span>
<a class="email" href="mailto:${esc(f.email)}">${esc(f.email)}</a></header>
<p class="msgtext">${esc(f.message)}</p>
<form method="post" action="/admin/feedback"><input type="hidden" name="csrf" value="${esc(csrf)}"><input type="hidden" name="id" value="${esc(f.id)}">
<input type="hidden" name="read" value="${f.read ? '0' : '1'}"><button type="submit" class="link">${f.read ? 'Mark as unread' : 'Mark as read'}</button></form></article>`).join('');
  return `<section class="card wide"><h2>Feedback &amp; suggestions <span class="count">${unread} unread</span></h2><nav class="tabs">${tabs}</nav>
${rows || '<p class="muted">Nothing here yet.</p>'}</section>`;
}

function providerTable(ai) {
  const rows = Object.entries(ai.modelsWeek || {}).sort((a, b) => b[1] - a[1]);
  const fails = Object.entries(ai.failuresToday || {});
  return `<table class="tbl"><thead><tr><th>Model</th><th>Today</th><th>7 days</th></tr></thead><tbody>
${rows.map(([m, v]) => `<tr><td>${esc(m)}</td><td>${num((ai.modelsToday || {})[m] || 0)}</td><td>${num(v)}</td></tr>`).join('') || '<tr><td colspan="3" class="muted">No AI requests yet.</td></tr>'}
</tbody></table>${fails.length ? `<p class="small muted">Skipped today: ${fails.map(([k, v]) => `${esc(k)} ×${v}`).join(', ')}</p>` : ''}`;
}

function dashboard(env, sum, feedback, donations, filter, csrf) {
  const dl = sum.downloads;
  const ai = sum.ai;
  return page('Keyware admin', `<header class="top"><h1>Keyware admin</h1><span class="muted">v${VERSION} · days in Asia/Muscat time</span>
<form method="post" action="/admin/logout"><input type="hidden" name="csrf" value="${esc(csrf)}"><button type="submit" class="link">Sign out</button></form></header>
${storageNote(sum.storage)}
<div class="cards">
<section class="card"><h2>Downloads</h2><p class="big">${num(dl && dl.total)}</p><div class="grid2"><div><p class="label">Today</p><p class="mid">${num(dl && dl.today)}</p></div><div><p class="label">This week</p><p class="mid">${num(dl && dl.week)}</p></div></div>
${dl ? bars(dl.series, sum.days, 'Downloads per day, last 30 days') : '<p class="muted">Unavailable.</p>'}<p class="small muted">Bots and link previews are not counted${sum.botDownloads ? ` (${num(sum.botDownloads)} skipped)` : ''}.${dl && dl.unsavedToday ? ` ${num(dl.unsavedToday)} more today were counted on one server but not saved (free storage limit).` : ''}</p></section>
<section class="card"><h2>AI requests</h2><p class="big">${num(ai && ai.today)} <span class="unit">today</span></p><div class="grid2"><div><p class="label">This week</p><p class="mid">${num(ai && ai.week)}</p></div><div><p class="label">Hit the daily limit today</p><p class="mid">${num(ai && ai.limitedToday)}</p></div></div>
${ai ? bars(ai.series, sum.days, 'AI requests per day, last 30 days') : ''}</section>
<section class="card"><h2>Active installs</h2><p class="big">${num(ai && ai.installsToday)} <span class="unit">today</span></p><p class="label">Seen using Text mode, all time: ${num(ai && ai.installsAll)}</p><p class="small muted">Counted from Text mode requests only; the app sends no other usage data.</p></section>
<section class="card"><h2>AI providers</h2>${ai ? providerTable(ai) : '<p class="muted">Unavailable.</p>'}</section>
${donationsCard(env, donations)}
${feedbackCard(feedback, filter, csrf)}
</div>`);
}

function blobMarkers(env) {
  if (!env.BLOB_READ_WRITE_TOKEN) return null;
  const token = env.BLOB_READ_WRITE_TOKEN;
  return {
    async head(path) {
      const sdk = require('@vercel/blob');
      try {
        return await sdk.head(path, { token });
      } catch (err) {
        if (/not.?found/i.test(String(err && (err.name || err.message)))) return null;
        throw err;
      }
    },
    async put(path, body) {
      const sdk = require('@vercel/blob');
      await sdk.put(path, body, { access: 'private', token, addRandomSuffix: false, allowOverwrite: true, contentType: 'text/plain' });
    },
  };
}

function createAdminHandler({ stats, env = process.env, now = Date.now, lockout, failDelayMs = 1200, log = () => {} } = {}) {
  return async function admin(req, res) {
    const st = stats || sharedStats();
    const lock = lockout || (createAdminHandler.lock = createAdminHandler.lock || new auth.Lockout({ kv: st.kv, blob: blobMarkers(env), now }));
    const headers = req.headers || {};
    const url = new URL(req.url || '/', 'http://x');
    const route = (url.searchParams.get('route') || '').replace(/[^a-z]/g, '');
    if (!env.ADMIN_PASSWORD) return sendHtml(res, 503, page('Keyware admin', '<section class="card"><h1>Admin is not set up</h1><p>Set ADMIN_PASSWORD in Vercel.</p></section>'));
    const session = auth.readSession(env, headers.cookie, now());
    if (req.method === 'POST') {
      // Same-origin only: forms post from /admin itself.
      const origin = String(headers.origin || '');
      const host = String(headers['x-forwarded-host'] || headers.host || '');
      const fetchSite = String(headers['sec-fetch-site'] || '');
      let sameOrigin = false;
      if (origin && origin !== 'null') {
        try { sameOrigin = new URL(origin).host === host; } catch (_e) { sameOrigin = false; }
      } else if (origin === 'null') {
        sameOrigin = fetchSite === 'same-origin'; // privacy settings can still null the Origin; the browser vouches
      }
      if (!sameOrigin || (fetchSite && fetchSite !== 'same-origin')) return sendHtml(res, 403, loginPage('Blocked: wrong origin.'));
      let form;
      try {
        form = await readForm(req, 4096);
      } catch (_e) {
        return sendHtml(res, 400, loginPage('Bad request.'));
      }
      if (route === 'login') {
        const ipHash = st.hash('admin-ip', clientIp(headers));
        if (await lock.locked(ipHash)) {
          log({ evt: 'admin', login: 'locked' });
          return sendHtml(res, 429, loginPage('Too many tries. Wait 15 minutes, then try again.'), { 'Retry-After': '900' });
        }
        const pw = String(form.get('password') || '');
        if (!pw || !auth.sameSecret(pw, env.ADMIN_PASSWORD)) {
          await lock.fail(ipHash);
          await sleep(failDelayMs);
          log({ evt: 'admin', login: 'fail' });
          return sendHtml(res, 401, loginPage('Wrong password.'));
        }
        await lock.success(ipHash);
        const s = auth.makeSession(env, now());
        log({ evt: 'admin', login: 'ok' });
        return redirect(res, '/admin', { 'Set-Cookie': auth.cookieHeader(s.value, s.maxAge) });
      }
      if (!session || form.get('csrf') !== auth.csrfFor(env, session)) return redirect(res, '/admin');
      if (route === 'logout') return redirect(res, '/admin', { 'Set-Cookie': auth.cookieHeader('', 0) });
      if (route === 'feedback') {
        const id = String(form.get('id') || '');
        if (/^[0-9a-z]{9}-[0-9a-f]{10}$/.test(id)) await st.records.markRead(id, form.get('read') === '1');
        createAdminHandler.cache = null;
        return redirect(res, '/admin');
      }
      return redirect(res, '/admin');
    }
    if (req.method !== 'GET' && req.method !== 'HEAD') return sendHtml(res, 405, loginPage(''), { Allow: 'GET, POST' });
    if (!session) return sendHtml(res, 200, loginPage(''));
    const filter = ['idea', 'feedback', 'bug'].includes(url.searchParams.get('type')) ? url.searchParams.get('type') : '';
    // A short cache keeps dashboard refreshes from spending the free storage quota.
    const cacheKey = `${filter}`;
    const cached = createAdminHandler.cache;
    const fresh = url.searchParams.get('fresh') === '1';
    if (!fresh && cached && cached.key === cacheKey && cached.until > now() && cached.store === st) {
      return sendHtml(res, 200, dashboard(env, cached.sum, cached.feedback, cached.donations, filter, auth.csrfFor(env, session)));
    }
    const [sum, feedback, donations] = await Promise.all([
      st.summary(),
      st.records.listFeedback(200).catch(() => []),
      env.KOFI_VERIFICATION_TOKEN ? st.records.listDonations().catch(() => []) : Promise.resolve([]),
    ]);
    createAdminHandler.cache = { key: cacheKey, until: now() + 30000, sum, feedback, donations, store: st };
    return sendHtml(res, 200, dashboard(env, sum, feedback, donations, filter, auth.csrfFor(env, session)));
  };
}

module.exports = createAdminHandler();
module.exports.createAdminHandler = createAdminHandler;
