'use strict';
// Download buttons open the thanks page and name the file. The thanks page
// starts that file without sending the marketing buttons straight at the exe.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { isBot } = require('../api/download');
const release = require('../api/_lib/release');

const ROOT = path.join(__dirname, '..');
const X64 = '9B792C19FA2D2684DFD2D64BC252A1F0DF5114084667E1A5AC930DB48063BF09';
const ARM = '84C97246CCB947643E0BD76B75921EDDA047B906A8E558A03B3BEA4142332E6D';
const KOFI = 'https://ko-fi.com/keyware_donate';

function read(name) {
  return fs.readFileSync(path.join(ROOT, name), 'utf8');
}

function attrs(html, attr) {
  const out = [];
  const re = new RegExp(`<a\\b[^>]*\\b${attr}\\b[^>]*>`, 'g');
  for (const tag of html.match(re) || []) {
    const href = (tag.match(/\bhref="([^"]*)"/) || [])[1] || '';
    out.push({ href, direct: /\bdata-dl-direct\b/.test(tag), arm: /\bdata-arm\b/.test(tag) });
  }
  return out;
}

test('x64 and ARM64 buttons open the thanks page for that file', () => {
  for (const name of ['index.html', 'guide.html']) {
    const html = read(name);
    const x64 = attrs(html, 'data-download').filter((a) => !a.direct);
    const arm = attrs(html, 'data-arm').filter((a) => !a.direct);
    assert.ok(x64.length >= 1, name);
    assert.ok(arm.length >= 1, name);
    for (const a of x64) assert.equal(a.href, '/thanks?arch=x64', name);
    for (const a of arm) assert.equal(a.href, '/thanks?arch=arm64', name);
    assert.ok(!html.includes('href="/download/Keyware.exe" data-download'), name);
    assert.ok(!html.includes('href="/download/Keyware-arm64.exe" data-arm>'), name);
  }
});

test('the thanks page keeps both files, the published hashes, and an optional Ko-fi link', () => {
  const html = read('thanks.html');
  const direct = attrs(html, 'data-dl-direct');
  assert.deepEqual(direct.map((a) => a.href).sort(), ['/download/Keyware-arm64.exe', '/download/Keyware.exe']);
  assert.equal(attrs(html, 'data-download').filter((a) => !a.direct).length, 0);
  assert.equal(attrs(html, 'data-arm').filter((a) => !a.direct).length, 0);
  assert.equal(html.includes('download="Keyware'), false, 'a download attribute can skip the counting redirect');
  assert.ok(html.includes(X64));
  assert.ok(html.includes(ARM));
  assert.match(html, /No Mac download/);
  const donate = html.match(/<a\b[^>]*\bdata-donate\b[^>]*>/);
  assert.ok(donate, 'donate link');
  assert.match(donate[0], new RegExp(`href="${KOFI.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"`));
  assert.match(donate[0], /\btarget="_blank"/);
  assert.match(html, /does not start, stop, or delay the file/);
  assert.equal(release.X64_SHA256, X64);
  assert.equal(release.ARM_SHA256, ARM);
  assert.equal(release.VERSION, '1.3.9');
  const cfg = read('assets/js/config.js');
  assert.match(cfg, new RegExp(`DONATE_URL: "${KOFI}"`));
  assert.match(cfg, new RegExp(`SHA256: "${X64}"`));
  assert.match(cfg, new RegExp(`ARM_SHA256: "${ARM}"`));
});

test('page script sends people to thanks and does not navigate the thanks page away', () => {
  const main = read('assets/js/main.js');
  const thanks = read('assets/js/thanks.js');
  assert.match(main, /data-download\]:not\(\[data-dl-direct\]\)/);
  assert.match(main, /data-arm\]:not\(\[data-dl-direct\]\)/);
  assert.match(main, /arch=" \+ arch/);
  assert.doesNotMatch(main, /setAttribute\("href", cfg\.DOWNLOAD_URL\)/);
  assert.doesNotMatch(main, /setAttribute\("href", arm\.url\)/);
  assert.doesNotMatch(thanks, /location\.assign|location\.href|window\.open/);
  assert.match(thanks, /iframe/);
  assert.match(thanks, /officialUrl/);
  assert.match(thanks, /\/download\/Keyware\.exe/);
  assert.match(thanks, /\/download\/Keyware-arm64\.exe/);
});

test('a headless browser check is still excluded from the download total', () => {
  assert.equal(isBot('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/131.0.0.0 Safari/537.36'), true);
  assert.equal(isBot('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'), false);
});
