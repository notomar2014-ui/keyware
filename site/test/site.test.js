'use strict';
// Download counter, feedback, Ko-fi stub, admin sign-in and dashboard, Blob records.
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { Readable } = require('node:stream');

const { Stats, BlobRecords } = require('../api/_lib/stats');
const { MemoryKV, statsDay } = require('../api/_lib/kv');
const { createDownloadHandler, isBot } = require('../api/download');
const { createFeedbackHandler } = require('../api/feedback');
const { createKofiHandler } = require('../api/kofi-webhook');
const { createAdminHandler } = require('../api/admin');
const auth = require('../api/_lib/adminauth');
const { FILE_PATH } = require('../api/_lib/release');

const ROOT = path.join(__dirname, '..');
const T0 = Date.UTC(2026, 9, 31, 9, 0, 0);
const CHROME = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36';
const PW = 'test-only-password';

function mkStats(clock = { t: T0 }) {
  const now = () => clock.t;
  return new Stats({ env: {}, kv: new MemoryKV(now), now });
}

function req({ method = 'GET', url = '/', headers = {}, body = '' } = {}) {
  const r = Readable.from([Buffer.from(body)]);
  r.method = method;
  r.url = url;
  r.headers = headers;
  return r;
}

function res() {
  const out = { statusCode: 0, headers: {}, body: '' };
  out.setHeader = (k, v) => { out.headers[k.toLowerCase()] = v; };
  out.end = (t) => { out.body = t ? String(t) : ''; try { out.json = JSON.parse(out.body); } catch (_e) { out.json = null; } };
  return out;
}

async function run(handler, opts) {
  const r = res();
  await handler(req(opts), r);
  return r;
}

// ---------------------------------------------------------------- downloads
test('download: counts a browser once per 10 minutes and redirects to the versioned exe', async () => {
  const clock = { t: T0 };
  const stats = mkStats(clock);
  const h = createDownloadHandler({ stats });
  const hdr = { 'user-agent': CHROME, 'x-forwarded-for': '203.0.113.9' };
  const r1 = await run(h, { headers: hdr });
  assert.equal(r1.statusCode, 302);
  assert.equal(r1.headers.location, FILE_PATH);
  assert.equal(r1.headers['cache-control'], 'no-store');
  await run(h, { headers: hdr });
  clock.t += 11 * 60 * 1000;
  await run(h, { headers: hdr });
  await run(h, { headers: { ...hdr, 'x-forwarded-for': '203.0.113.10' } });
  const sum = await stats.summary();
  assert.equal(sum.downloads.total, 3);
  assert.equal(sum.downloads.today, 3);
  assert.equal(sum.downloads.series.length, 30);
});

test('download: bots, previews, empty agents, HEAD and resumed ranges are not counted', async () => {
  const stats = mkStats();
  const h = createDownloadHandler({ stats });
  for (const ua of ['', 'curl/8.5', 'Googlebot/2.1', 'facebookexternalhit/1.1', 'WhatsApp/2.23', 'python-requests/2.31', 'Mozilla/5.0 (compatible; Discordbot/2.0)']) {
    const r = await run(h, { headers: { 'user-agent': ua } });
    assert.equal(r.statusCode, 302);
  }
  await run(h, { method: 'HEAD', headers: { 'user-agent': CHROME } });
  await run(h, { headers: { 'user-agent': CHROME, range: 'bytes=1000-' } });
  const sum = await stats.summary();
  assert.equal(sum.downloads.total, 0);
  assert.equal(sum.botDownloads, 7);
  assert.ok(isBot('Mozilla/5.0 (compatible; bingbot/2.0)'));
  assert.ok(!isBot(CHROME));
});

test('download: a store failure still hands out the file', async () => {
  const stats = mkStats();
  stats.kv.run = async () => { throw new Error('down'); };
  const r = await run(createDownloadHandler({ stats }), { headers: { 'user-agent': CHROME } });
  assert.equal(r.statusCode, 302);
});

test('vercel.json routes the download through the counter and the exe is not at the counted path', () => {
  const v = JSON.parse(fs.readFileSync(path.join(ROOT, 'vercel.json'), 'utf8'));
  assert.ok(v.rewrites.some((r) => r.source === '/download/Keyware.exe' && r.destination === '/api/download'));
  assert.ok(!fs.existsSync(path.join(ROOT, 'download', 'Keyware.exe')), 'a real file there would bypass the counter');
  assert.ok(v.rewrites.some((r) => r.source === '/admin' && r.destination === '/api/admin'));
  const globalRule = v.headers.find((h) => h.headers.some((x) => x.key === 'Content-Security-Policy' && x.value.includes("form-action 'none'")));
  assert.ok(!new RegExp(`^${globalRule.source.replace('/((', '/((').replace(/\)\.\*\)$/, ').*)')}$`).test('/admin'), 'site CSP does not apply to /admin');
  const adminRule = v.headers.find((h) => h.source === '/admin(.*)');
  const csp = adminRule.headers.find((x) => x.key === 'Content-Security-Policy').value;
  assert.match(csp, /form-action 'self'/);
  assert.ok(adminRule.headers.some((x) => x.key === 'X-Robots-Tag' && /noindex/.test(x.value)));
});

// ---------------------------------------------------------------- feedback
const FB_HEADERS = (install = crypto.randomUUID(), ip = '198.51.100.20') => ({
  'content-type': 'application/json', 'user-agent': 'Keyware/1.3.4', 'x-keyware-install': install, 'x-forwarded-for': ip,
});
const FB = { type: 'idea', message: 'Please add a dark blue theme.', email: 'kid@example.com', app_version: '1.3.4' };

test('feedback: stores type, message, email, version and date; no raw install id', async () => {
  const stats = mkStats();
  const logs = [];
  const h = createFeedbackHandler({ stats, log: (e) => logs.push(e), now: () => T0 });
  const install = crypto.randomUUID();
  const r = await run(h, { method: 'POST', headers: FB_HEADERS(install), body: JSON.stringify(FB) });
  assert.equal(r.statusCode, 200);
  assert.deepEqual(r.json, { ok: true });
  const list = await stats.records.listFeedback();
  assert.equal(list.length, 1);
  assert.equal(list[0].type, 'idea');
  assert.equal(list[0].email, 'kid@example.com');
  assert.equal(list[0].app_version, '1.3.4');
  assert.equal(list[0].created, new Date(T0).toISOString());
  assert.ok(!JSON.stringify(list).includes(install));
  assert.ok(!JSON.stringify(logs).includes('kid@example.com') && !JSON.stringify(logs).includes('dark blue'));
});

test('feedback: validation', async () => {
  const h = createFeedbackHandler({ stats: mkStats() });
  const cases = [
    [{ ...FB, type: 'rant' }, 400], [{ ...FB, message: 'hi' }, 400], [{ ...FB, message: 'x'.repeat(2001) }, 413],
    [{ ...FB, email: '' }, 400], [{ ...FB, email: 'not-an-email' }, 400], [{ ...FB, email: 'a@b' }, 400],
    [{ ...FB, message: 'see http://a.co http://b.co http://c.co http://d.co' }, 422], [{ ...FB, message: '1234567 89' }, 422],
    [{ ...FB, message: 'cheap casino bonus here' }, 422],
  ];
  for (const [body, status] of cases) {
    const r = await run(h, { method: 'POST', headers: FB_HEADERS(), body: JSON.stringify(body) });
    assert.equal(r.statusCode, status, JSON.stringify(body).slice(0, 80));
    assert.equal(r.json.ok, false);
  }
  assert.equal((await run(h, { method: 'GET', headers: FB_HEADERS() })).statusCode, 405);
  assert.equal((await run(h, { method: 'POST', headers: { ...FB_HEADERS(), origin: 'https://evil.example' }, body: JSON.stringify(FB) })).statusCode, 403);
  assert.equal((await run(h, { method: 'POST', headers: { ...FB_HEADERS(), 'user-agent': 'Mozilla/5.0' }, body: JSON.stringify(FB) })).statusCode, 403);
  assert.equal((await run(h, { method: 'POST', headers: { ...FB_HEADERS(), 'x-keyware-install': 'nope' }, body: JSON.stringify(FB) })).statusCode, 400);
  assert.equal((await run(h, { method: 'POST', headers: FB_HEADERS(), body: '{bad' })).statusCode, 400);
});

test('feedback: 5 a day per install and per IP, duplicates are stored once', async () => {
  const stats = mkStats();
  const h = createFeedbackHandler({ stats });
  const install = crypto.randomUUID();
  for (let i = 0; i < 5; i += 1) {
    const r = await run(h, { method: 'POST', headers: FB_HEADERS(install), body: JSON.stringify({ ...FB, message: `Idea number ${i} for Keyware` }) });
    assert.equal(r.statusCode, 200);
  }
  const sixth = await run(h, { method: 'POST', headers: FB_HEADERS(install), body: JSON.stringify({ ...FB, message: 'Idea number six' }) });
  assert.equal(sixth.statusCode, 429);
  const otherInstallSameIp = await run(h, { method: 'POST', headers: FB_HEADERS(crypto.randomUUID()), body: JSON.stringify({ ...FB, message: 'Different install, same IP' }) });
  assert.equal(otherInstallSameIp.statusCode, 429);
  const dupStats = mkStats();
  const h2 = createFeedbackHandler({ stats: dupStats });
  await run(h2, { method: 'POST', headers: FB_HEADERS(), body: JSON.stringify(FB) });
  const again = await run(h2, { method: 'POST', headers: FB_HEADERS(crypto.randomUUID(), '198.51.100.99'), body: JSON.stringify(FB) });
  assert.equal(again.statusCode, 200);
  assert.equal((await dupStats.records.listFeedback()).length, 1);
});

test('feedback: store down gives 503, never a crash', async () => {
  const stats = mkStats();
  stats.records.addFeedback = async () => { throw new Error('down'); };
  const r = await run(createFeedbackHandler({ stats }), { method: 'POST', headers: FB_HEADERS(), body: JSON.stringify(FB) });
  assert.equal(r.statusCode, 503);
  assert.equal(r.json.code, 'busy');
});

// ---------------------------------------------------------------- Ko-fi
function kofiBody(data) {
  return `data=${encodeURIComponent(JSON.stringify(data))}`;
}
const KOFI = { verification_token: 'kofi-test-token', message_id: 'm-1', type: 'Donation', amount: '3.00', currency: 'USD', timestamp: '2026-10-31T09:00:00Z', from_name: 'Someone', email: 'donor@example.com', message: 'hi' };

test('kofi: not connected without a token', async () => {
  const r = await run(createKofiHandler({ stats: mkStats(), env: {} }), { method: 'POST', body: kofiBody(KOFI) });
  assert.equal(r.statusCode, 503);
  assert.equal(r.json.code, 'not_connected');
});

test('kofi: checks the token, keeps amount and kind only, ignores repeats', async () => {
  const stats = mkStats();
  const h = createKofiHandler({ stats, env: { KOFI_VERIFICATION_TOKEN: 'kofi-test-token' } });
  assert.equal((await run(h, { method: 'POST', body: kofiBody({ ...KOFI, verification_token: 'wrong' }) })).statusCode, 401);
  assert.equal((await run(h, { method: 'POST', body: 'data=notjson' })).statusCode, 400);
  assert.equal((await run(h, { method: 'POST', body: kofiBody(KOFI) })).statusCode, 200);
  assert.equal((await run(h, { method: 'POST', body: kofiBody(KOFI) })).statusCode, 200);
  assert.equal((await run(h, { method: 'POST', body: kofiBody({ ...KOFI, message_id: 'm-2', type: 'Subscription', amount: '5', is_first_subscription_payment: true }) })).statusCode, 200);
  const list = await stats.records.listDonations();
  assert.equal(list.length, 2);
  assert.ok(!JSON.stringify(list).includes('donor@example.com') && !JSON.stringify(list).includes('Someone'));
  assert.deepEqual(list.map((d) => d.kind).sort(), ['monthly', 'one-time']);
});

// ---------------------------------------------------------------- admin
function adminSetup(extraEnv = {}) {
  const clock = { t: T0 };
  const stats = mkStats(clock);
  const env = { ADMIN_PASSWORD: PW, ADMIN_SESSION_SECRET: 's3', ...extraEnv };
  const lockout = new auth.Lockout({ kv: stats.kv, now: () => clock.t });
  const h = createAdminHandler({ stats, env, now: () => clock.t, lockout, failDelayMs: 0 });
  return { h, stats, env, clock };
}
const SAME = { host: 'keyware-app.vercel.app', origin: 'https://keyware-app.vercel.app', 'content-type': 'application/x-www-form-urlencoded', 'x-forwarded-for': '192.0.2.1' };

async function login(h, pw = PW, extra = {}) {
  return run(h, { method: 'POST', url: '/api/admin?route=login', headers: { ...SAME, ...extra }, body: `password=${encodeURIComponent(pw)}` });
}
const cookieOf = (r) => String(r.headers['set-cookie']).split(';')[0];

test('admin: login page without a session, noindex, strict CSP, no data', async () => {
  const { h, stats } = adminSetup();
  await stats.records.addFeedback({ id: '0000000aa-0123456789', type: 'bug', message: 'secret msg', email: 'x@example.com', created: new Date(T0).toISOString() });
  const r = await run(h, { url: '/api/admin' });
  assert.equal(r.statusCode, 200);
  assert.match(r.body, /type="password"/);
  assert.match(r.body, /noindex/);
  assert.ok(!r.body.includes('secret msg') && !r.body.includes('x@example.com'));
  assert.match(r.headers['content-security-policy'], /form-action 'self'/);
  assert.match(r.headers['x-robots-tag'], /noindex/);
  assert.equal(r.headers['cache-control'], 'no-store');
});

test('admin: wrong password 401; right password sets an HttpOnly, Secure, SameSite=Strict signed cookie', async () => {
  const { h, env } = adminSetup();
  assert.equal((await login(h, 'nope')).statusCode, 401);
  const ok = await login(h);
  assert.equal(ok.statusCode, 303);
  const c = String(ok.headers['set-cookie']);
  assert.match(c, /^kw_admin=v1\./);
  for (const flag of ['HttpOnly', 'Secure', 'SameSite=Strict', 'Path=/admin', 'Max-Age=28800']) assert.ok(c.includes(flag), flag);
  assert.ok(!c.includes(PW));
  const dash = await run(h, { url: '/api/admin', headers: { cookie: cookieOf(ok) } });
  assert.match(dash.body, /Downloads/);
  assert.match(dash.body, /Not connected yet/);
  // tampered or expired cookies do not work
  const tampered = cookieOf(ok).replace(/.$/, (ch) => (ch === 'A' ? 'B' : 'A'));
  assert.match((await run(h, { url: '/api/admin', headers: { cookie: tampered } })).body, /type="password"/);
  assert.equal(auth.readSession(env, cookieOf(ok), T0 + 9 * 3600 * 1000), null);
  assert.equal(auth.readSession({ ...env, ADMIN_PASSWORD: 'changed' }, cookieOf(ok), T0), null, 'changing the password signs everyone out');
});

test('admin: 5 wrong tries per IP lock that IP for 15 minutes, even with the right password', async () => {
  const { h, clock } = adminSetup();
  for (let i = 0; i < 5; i += 1) assert.equal((await login(h, `guess${i}`)).statusCode, 401);
  assert.equal((await login(h)).statusCode, 429);
  assert.equal((await login(h, PW, { 'x-forwarded-for': '192.0.2.2' })).statusCode, 303, 'other IPs unaffected');
  clock.t += 16 * 60 * 1000;
  assert.equal((await login(h)).statusCode, 303);
});

test('admin: 30 wrong tries across IPs close sign-in for the hour', async () => {
  const { h } = adminSetup();
  for (let i = 0; i < 30; i += 1) await login(h, 'x', { 'x-forwarded-for': `10.0.${Math.floor(i / 4)}.${i}` });
  assert.equal((await login(h, PW, { 'x-forwarded-for': '10.9.9.9' })).statusCode, 429);
});

test('admin: cross-site posts are refused; actions need the CSRF token', async () => {
  const { h, stats } = adminSetup();
  assert.equal((await login(h, PW, { origin: 'https://evil.example' })).statusCode, 403);
  assert.equal((await login(h, PW, { origin: '' })).statusCode, 403);
  assert.equal((await login(h, PW, { origin: 'null' })).statusCode, 403, 'null origin without Sec-Fetch-Site');
  assert.equal((await login(h, PW, { origin: 'null', 'sec-fetch-site': 'cross-site' })).statusCode, 403);
  assert.equal((await login(h, PW, { 'sec-fetch-site': 'cross-site' })).statusCode, 403, 'right origin but browser says cross-site');
  assert.equal((await login(h, PW, { origin: 'not a url' })).statusCode, 403);
  const ok = await login(h);
  const cookie = cookieOf(ok);
  const id = '0000000ab-0123456789';
  await stats.records.addFeedback({ id, type: 'bug', message: 'It crashes on F13', email: 'x@example.com', app_version: '1.3.4', created: new Date(T0).toISOString() });
  await run(h, { method: 'POST', url: '/api/admin?route=feedback', headers: { ...SAME, cookie }, body: `id=${id}&read=1&csrf=wrong` });
  assert.equal((await stats.records.listFeedback())[0].read, false);
  const dash = await run(h, { url: '/api/admin', headers: { cookie } });
  const csrf = /name="csrf" value="([^"]+)"/.exec(dash.body)[1];
  await run(h, { method: 'POST', url: '/api/admin?route=feedback', headers: { ...SAME, cookie }, body: `id=${id}&read=1&csrf=${csrf}` });
  assert.equal((await stats.records.listFeedback())[0].read, true);
});

test('admin dashboard: feedback newest first with type badge, filter, escaping; honest storage note', async () => {
  const { h, stats } = adminSetup();
  const fb = createFeedbackHandler({ stats, now: () => T0 });
  await run(fb, { method: 'POST', headers: FB_HEADERS(), body: JSON.stringify({ ...FB, type: 'bug', message: 'TEST <script>alert(1)</script> crash' }) });
  await new Promise((r) => setTimeout(r, 2));
  const fb2 = createFeedbackHandler({ stats, now: () => T0 + 1000 });
  await run(fb2, { method: 'POST', headers: FB_HEADERS(crypto.randomUUID(), '198.51.100.77'), body: JSON.stringify({ ...FB, message: 'TEST newer idea' }) });
  const cookie = cookieOf(await login(h));
  const dash = await run(h, { url: '/api/admin', headers: { cookie } });
  assert.ok(dash.body.indexOf('TEST newer idea') < dash.body.indexOf('crash'), 'newest first');
  assert.ok(!dash.body.includes('<script>alert'), 'escaped');
  assert.match(dash.body, /class="badge bug"/);
  assert.match(dash.body, /\d{4}-\d{2}-\d{2} \d{2}:\d{2} GST/, "feedback times in Muscat time");
  assert.doesNotMatch(dash.body, / UTC · v/);
  assert.match(dash.body, /2 unread/);
  assert.match(dash.body, /kept in server memory only/);
  const bugs = await run(h, { url: '/api/admin?type=bug', headers: { cookie } });
  assert.ok(!bugs.body.includes('TEST newer idea'));
  assert.ok(!dash.body.includes('delete'), 'no delete button');
});

test('admin dashboard: AI provider usage and donations when connected', async () => {
  const { h, stats } = adminSetup({ KOFI_VERIFICATION_TOKEN: 'k' });
  await stats.recordAi({ installId: crypto.randomUUID(), model: 'gemini-3.5-flash-lite', ok: true });
  await stats.recordAi({ installId: crypto.randomUUID(), model: 'groq-gpt-oss-120b', ok: true });
  await stats.records.addDonation({ amount_cents: 300, currency: 'USD', kind: 'one-time' }, 'a');
  await stats.records.addDonation({ amount_cents: 500, currency: 'USD', kind: 'monthly' }, 'b');
  const cookie = cookieOf(await login(h));
  const dash = await run(h, { url: '/api/admin', headers: { cookie } });
  assert.match(dash.body, /gemini-3\.5-flash-lite/);
  assert.match(dash.body, /groq-gpt-oss-120b/);
  assert.match(dash.body, /3\.00 USD/);
  assert.match(dash.body, /5\.00 USD/);
});

test('admin: not configured without ADMIN_PASSWORD', async () => {
  const stats = mkStats();
  const r = await run(createAdminHandler({ stats, env: {}, lockout: new auth.Lockout({ kv: stats.kv }) }), { url: '/api/admin' });
  assert.equal(r.statusCode, 503);
});

// ---------------------------------------------------------------- Blob records
function fakeBlob() {
  const files = new Map();
  return {
    files,
    async put(pathname, body, opts) {
      assert.equal(opts.access, 'private');
      if (files.has(pathname) && !opts.allowOverwrite) throw new Error('This blob already exists');
      files.set(pathname, String(body));
      return { pathname, url: `https://blob/${pathname}` };
    },
    async list({ prefix, cursor }) {
      const all = [...files.keys()].filter((p) => p.startsWith(prefix)).sort();
      const start = cursor ? Number(cursor) : 0;
      const page = all.slice(start, start + 1000);
      return { blobs: page.map((p) => ({ pathname: p, url: `https://blob/${p}` })), hasMore: start + 1000 < all.length, cursor: String(start + 1000) };
    },
    async get(pathname) {
      if (!files.has(pathname)) return { statusCode: 404 };
      return { statusCode: 200, stream: Readable.toWeb(Readable.from([Buffer.from(files.get(pathname))])) };
    },
    async del(urls) {
      for (const u of [].concat(urls)) files.delete(u.replace('https://blob/', ''));
    },
  };
}

test('blob records: downloads per day, feedback with read marks, donation dedupe', async () => {
  const sdk = fakeBlob();
  const rec = new BlobRecords('tok', sdk);
  const day = statsDay(T0);
  await rec.addDownload(day, T0);
  await rec.addDownload(day, T0 + 1);
  await rec.addDownload('20261001', T0 - 30 * 86400000);
  const c = await rec.downloadCounts([day]);
  assert.equal(c.total, 3);
  assert.equal(c.byDay[day], 2);
  await rec.addFeedback({ id: '00000000a-aaaaaaaaaa', type: 'idea', message: 'one' });
  await rec.addFeedback({ id: '00000000b-bbbbbbbbbb', type: 'bug', message: 'two' });
  await rec.markRead('00000000a-aaaaaaaaaa');
  const list = await rec.listFeedback();
  assert.deepEqual(list.map((f) => [f.message, f.read]), [['two', false], ['one', true]]);
  await rec.markRead('00000000a-aaaaaaaaaa', false);
  assert.equal((await rec.listFeedback())[1].read, false);
  assert.equal(await rec.addDonation({ amount_cents: 1 }, 'x'), true);
  assert.equal(await rec.addDonation({ amount_cents: 1 }, 'x'), false);
  assert.equal((await rec.listDonations()).length, 1);
  assert.ok([...sdk.files.keys()].every((p) => !p.includes('@')), 'no emails in blob names');
});

test('stats: memory counters are labelled as not saved', async () => {
  const sum = await mkStats().summary();
  assert.equal(sum.storage.countersSaved, false);
  assert.equal(sum.storage.recordsSaved, false);
});

// ---------------------------------------------------------------- repo hygiene
test('no secrets or the admin password in anything that gets deployed', () => {
  const ignore = fs.readFileSync(path.join(ROOT, '.vercelignore'), 'utf8').split(/\r?\n/).filter(Boolean);
  assert.ok(ignore.includes('.admin_password'));
  const pwFile = path.join(ROOT, '.admin_password');
  const realPw = fs.existsSync(pwFile) ? fs.readFileSync(pwFile, 'utf8').trim() : null;
  const skip = new Set(['node_modules', 'test', 'tools', '.git']);
  const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
    if (skip.has(d.name) || d.name === '.admin_password') return [];
    const p = path.join(dir, d.name);
    return d.isDirectory() ? walk(p) : [p];
  });
  for (const file of walk(ROOT)) {
    if (/\.(png|ico|exe|woff2?|ttf|jpg|webp)$/i.test(file)) continue;
    const text = fs.readFileSync(file, 'utf8');
    assert.ok(!/AIza[0-9A-Za-z_-]{30,}|gsk_[0-9A-Za-z]{40,}/.test(text), `key-like string in ${file}`);
    if (realPw) assert.ok(!text.includes(realPw), `admin password in ${path.relative(ROOT, file)}`);
  }
});

test('admin: login works when a browser sends Origin: null with Sec-Fetch-Site same-origin', async () => {
  const { h } = adminSetup();
  const r = await login(h, PW, { origin: 'null', 'sec-fetch-site': 'same-origin' });
  assert.equal(r.statusCode, 303);
  assert.match(String(r.headers['set-cookie']), /HttpOnly; Secure; SameSite=Strict/);
  const page = await run(h, { url: '/api/admin' });
  assert.equal(page.headers['referrer-policy'], 'same-origin');
});

test('admin: works when Vercel has already parsed the form into req.body (live bug 17)', async () => {
  const { h, stats } = adminSetup();
  const parsed = (fields) => { const r = req({ method: 'POST', url: '/api/admin?route=login', headers: SAME, body: '' }); r.body = fields; return r; };
  let r = res(); await h(parsed({ password: 'nope' }), r); assert.equal(r.statusCode, 401);
  r = res(); await h(parsed({ password: PW }), r);
  assert.equal(r.statusCode, 303);
  const cookie = cookieOf(r);
  // A parsed body that is too big is still refused.
  r = res(); await h(parsed({ password: 'x'.repeat(5000) }), r); assert.equal(r.statusCode, 400);
  // CSRF-protected form posts also read parsed bodies.
  await stats.records.addFeedback({ id: '0000000bb-0123456789', type: 'bug', message: 'parsed body check', email: 'x@example.com', created: new Date(T0).toISOString() });
  const page = await run(h, { url: '/api/admin', headers: { cookie } });
  const csrf = (page.body.match(/name="csrf" value="([^"]+)"/) || [])[1];
  assert.ok(csrf);
  const fr = req({ method: 'POST', url: '/api/admin?route=feedback', headers: { ...SAME, cookie }, body: '' });
  fr.body = { csrf, id: '0000000bb-0123456789', read: '1' };
  r = res(); await h(fr, r);
  assert.ok([303, 302].includes(r.statusCode), String(r.statusCode));
});
