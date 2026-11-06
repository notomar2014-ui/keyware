'use strict';
// x64 and ARM64 downloads share one version. ARM64 is offered only with its own SHA-256.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { Readable } = require('node:stream');

const release = require('../api/_lib/release');
const { createReleaseHandler } = require('../api/release');
const { createDownloadHandler } = require('../api/download');
const { Stats } = require('../api/_lib/stats');
const { MemoryKV } = require('../api/_lib/kv');

const ROOT = path.join(__dirname, '..');
const HASH = '9B792C19FA2D2684DFD2D64BC252A1F0DF5114084667E1A5AC930DB48063BF09';
const ARM_HASH = '84C97246CCB947643E0BD76B75921EDDA047B906A8E558A03B3BEA4142332E6D';
const CHROME = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36';

function armAsset(extra = {}) {
  return {
    name: release.ARM_FILE,
    state: 'uploaded',
    digest: 'sha256:' + 'ab'.repeat(32),
    browser_download_url: release.ARM_PAGE,
    size: 20_000_000,
    ...extra,
  };
}

function req(opts = {}) {
  const r = Readable.from([Buffer.from(opts.body || '')]);
  r.method = opts.method || 'GET';
  r.url = opts.url || '/';
  r.headers = opts.headers || {};
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

test('x64 release constants match the published 1.3.8 file', () => {
  assert.equal(release.VERSION, '1.3.8');
  assert.equal(release.X64.sha256, HASH);
  assert.equal(release.SHA256, HASH);
  assert.equal(release.X64.bytes, 21173481);
  assert.equal(release.X64.size, '20.2 MB');
  assert.equal(release.ARM_SHA256, ARM_HASH);
  assert.equal(release.ARM_BYTES, 16203491);
  assert.equal(release.formatSize(release.ARM_BYTES), '15.5 MB');
  assert.notEqual(release.ARM_SHA256, HASH);
  assert.equal(release.X64.version, release.VERSION);
  assert.equal(release.FILE_PATH, '/download/1.3.8/Keyware.exe');
  const config = fs.readFileSync(path.join(ROOT, 'assets/js/config.js'), 'utf8');
  assert.match(config, new RegExp(`VERSION: "${release.VERSION}"`));
  assert.match(config, new RegExp(`FILE_SIZE: "${release.X64.size}"`));
  assert.match(config, new RegExp(`SHA256: "${HASH}"`));
  assert.match(config, new RegExp(`ARM_SHA256: "${ARM_HASH}"`));
  assert.match(config, /ARM_FILE_SIZE: "15\.5 MB"/);
  for (const file of ['index.html', 'guide.html', 'thanks.html']) {
    const html = fs.readFileSync(path.join(ROOT, file), 'utf8');
    assert.ok(html.includes(HASH), file);
    assert.ok(html.includes(ARM_HASH), file);
  }
});

test('an ARM64 asset is accepted only for this version, this name and a real digest', () => {
  const good = release.armFromAsset(armAsset());
  assert.equal(good.version, release.VERSION);
  assert.equal(good.sha256, 'AB'.repeat(32));
  assert.equal(good.url, '/download/Keyware-arm64.exe');
  assert.equal(good.redirect, release.ARM_PAGE);
  assert.notEqual(good.sha256, HASH);
  assert.equal(release.armFromAsset(armAsset({ browser_download_url: 'https://evil.example/Keyware-arm64.exe' })), null);
  assert.equal(release.armFromAsset(armAsset({ digest: 'sha256:abcd' })), null);
  assert.equal(release.armFromAsset(armAsset({ digest: 'sha256:' + HASH.toLowerCase(), name: 'Keyware.exe' })), null);
  assert.equal(release.armFromAsset(armAsset({ size: 1000 })), null);
  assert.equal(release.armFromAsset(armAsset({ state: 'starter' })), null);
});

test('lookup uses the release tag for this version and caches one answer', async () => {
  const cache = { at: 0, arm: undefined, ok: false };
  const urls = [];
  const fetchImpl = async (url) => {
    urls.push(url);
    return { ok: true, json: async () => ({ assets: [armAsset()] }) };
  };
  const first = await release.lookupArm({ fetchImpl, now: () => 1000, cache, cacheMs: 5000 });
  const second = await release.lookupArm({ fetchImpl, now: () => 2000, cache, cacheMs: 5000 });
  assert.equal(urls.length, 1);
  assert.match(urls[0], /\/releases\/tags\/v1\.3\.8$/);
  assert.equal(first.sha256, second.sha256);
  assert.equal(first.version, '1.3.8');
});

test('the release API lists x64 always and ARM64 only when the asset is real', async () => {
  const none = await run(createReleaseHandler({ lookup: async () => null }));
  assert.equal(none.statusCode, 200);
  assert.deepEqual(none.json.builds.map((b) => b.arch), ['x64']);
  assert.equal(none.json.builds[0].sha256, HASH);
  assert.equal(none.json.version, '1.3.8');
  const listed = await run(createReleaseHandler({ lookup: async () => release.armFromAsset(armAsset()) }));
  assert.deepEqual(listed.json.builds.map((b) => b.arch), ['x64', 'arm64']);
  assert.equal(listed.json.builds[1].version, listed.json.version);
  assert.equal(listed.json.builds[1].sha256, 'AB'.repeat(32));
  assert.ok(!listed.body.includes('github.com'), 'the page downloads through this site');
  const failed = await run(createReleaseHandler({ lookup: async () => { throw new Error('down'); } }));
  assert.deepEqual(failed.json.builds.map((b) => b.arch), ['x64']);
});

test('ARM64 download redirects to that asset; a missing asset is not a file', async () => {
  const stats = new Stats({ env: {}, kv: new MemoryKV(() => 0), now: () => 0 });
  const arm = release.armFromAsset(armAsset());
  const h = createDownloadHandler({ stats, lookup: async () => arm });
  const headers = { 'user-agent': CHROME, 'x-forwarded-for': '203.0.113.8' };
  const got = await run(h, { url: '/download/Keyware-arm64.exe', headers });
  assert.equal(got.statusCode, 302);
  assert.equal(got.headers.location, release.ARM_PAGE);
  const x64 = await run(h, { url: '/download/Keyware.exe', headers });
  assert.equal(x64.headers.location, release.FILE_PATH);
  const missing = await run(createDownloadHandler({ stats, lookup: async () => null }), { url: '/api/download?arch=arm64', headers });
  assert.equal(missing.statusCode, 404);
  const down = await run(createDownloadHandler({ stats, lookup: async () => { throw new Error('down'); } }), { url: '/api/download?arch=arm64', headers });
  assert.equal(down.statusCode, 503);
});

test('the download page offers x64 and ARM64 as equal choices, with no Mac download', () => {
  const home = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
  const guide = fs.readFileSync(path.join(ROOT, 'guide.html'), 'utf8');
  const thanks = fs.readFileSync(path.join(ROOT, 'thanks.html'), 'utf8');
  for (const html of [home, guide, thanks]) {
    assert.match(html, /Download 64-bit \(x64\)/);
    assert.match(html, /Download ARM64/);
    assert.equal(html.includes('data-arm-wrap hidden'), false);
    assert.match(html, /[Nn]o Mac download/);
  }
  assert.ok(!/Keyware\.dmg|Download for Mac|macos-latest/.test(home + guide));
  assert.ok(!/code-signed yet|only need to do this once|nothing is left behind|Only Text mode connects/.test(home + guide));
  assert.match(home, /Start Keyware with Windows/);
  assert.match(guide, /Start Keyware with Windows/);
  const privacy = fs.readFileSync(path.join(ROOT, 'privacy.html'), 'utf8');
  assert.match(privacy, /48 hours/);
  assert.match(privacy, /10 minutes/);
  assert.ok(!/discarded after about a day or when the server restarts/.test(privacy));
  const vercel = JSON.parse(fs.readFileSync(path.join(ROOT, 'vercel.json'), 'utf8'));
  assert.ok(vercel.rewrites.some((r) => r.source === '/download/Keyware-arm64.exe' && r.destination === '/api/download?arch=arm64'));
  assert.ok(!fs.existsSync(path.join(ROOT, 'download', 'Keyware-arm64.exe')));
});
