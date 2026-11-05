'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const read = (f) => fs.readFileSync(path.join(ROOT, f), 'utf8');
const BASE = 'https://keyware.site';
const ldOf = (html) => [...html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)].map((m) => JSON.parse(m[1]));

test('home: JSON-LD SoftwareApplication is valid, free, and matches the page version', () => {
  const html = read('index.html');
  const [ld] = ldOf(html);
  const app = ld['@graph'].find((n) => n['@type'] === 'SoftwareApplication');
  assert.match(app.name, /^Keyware/);
  assert.equal(app.offers.price, '0');
  assert.equal(app.isAccessibleForFree, true);
  assert.match(app.operatingSystem, /Windows 11/);
  assert.ok(app.featureList.some((f) => /Copilot/.test(f)) && app.featureList.some((f) => /Caps Lock/.test(f)));
  assert.equal(app.softwareVersion, html.match(/data-version>([^<]+)</)[1]);
  const faq = ld['@graph'].find((n) => n['@type'] === 'FAQPage');
  assert.ok(faq.mainEntity.length >= 3 && faq.mainEntity.every((q) => q.name && q.acceptedAnswer.text));
});

test('pages: canonical, Open Graph, Twitter and description tags; verification placeholders stay commented out', () => {
  for (const [file, url] of [['index.html', `${BASE}/`], ['guide.html', `${BASE}/guide`]]) {
    const html = read(file);
    assert.ok(html.includes(`<link rel="canonical" href="${url}">`), file);
    assert.ok(html.includes(`<meta property="og:url" content="${url}">`), file);
    for (const tag of ['og:title', 'og:description', 'og:image', 'og:image:alt']) assert.ok(html.includes(`property="${tag}"`), `${file} ${tag}`);
    for (const tag of ['twitter:card', 'twitter:title', 'twitter:image', 'description']) assert.ok(html.includes(`name="${tag}"`), `${file} ${tag}`);
    const live = html.replace(/<!--[\s\S]*?-->/g, '');
    assert.ok(!/PASTE_/.test(live), `${file} placeholder is not live`);
    if (file !== 'index.html') assert.ok(!/google-site-verification/.test(live), `${file} has no live Google tag`);
    if (file === 'index.html') {
      assert.ok(live.includes('<meta name="google-site-verification" content="6mMcW79VILdtw12xv5SYGEaakucM15veVjOuqF7VG04" />'), 'Google verification meta is live on the home page');
      assert.ok(live.includes('<meta name="msvalidate.01" content="1FCA120FB893FCD9A02C1D392DBF036D" />'), 'Bing verification meta is live on the home page');
      assert.equal(read('BingSiteAuth.xml'), '<?xml version="1.0"?>\n<users>\n\t<user>1FCA120FB893FCD9A02C1D392DBF036D</user>\n</users>\n');
    }
    ldOf(html); // parses
  }
});

test('robots.txt allows search and AI crawlers, hides admin and api, and points to the sitemap', () => {
  const robots = read('robots.txt');
  for (const bot of ['Googlebot', 'Bingbot', 'GPTBot', 'OAI-SearchBot', 'ClaudeBot', 'PerplexityBot', 'Google-Extended', 'Applebot-Extended', '*']) {
    const block = robots.split(`User-agent: ${bot}\n`)[1];
    assert.ok(block, bot);
    const rules = block.split('\n\n')[0];
    assert.ok(rules.includes('Allow: /') && !/^Disallow: \/$/m.test(rules), bot);
    assert.ok(rules.includes('Disallow: /admin') && rules.includes('Disallow: /api/'), bot);
  }
  assert.ok(robots.includes(`Sitemap: ${BASE}/sitemap.xml`));
});

test('sitemap lists every public page with a lastmod', () => {
  const xml = read('sitemap.xml');
  for (const p of ['/', '/guide', '/privacy', '/whats-new']) assert.ok(xml.includes(`<loc>${BASE}${p}</loc><lastmod>`), p);
});

test('llms.txt and llms-full.txt exist, follow the format, and cover the new features and every guide section', () => {
  const short = read('llms.txt');
  const full = read('llms-full.txt');
  assert.ok(short.startsWith('# Keyware\n\n> '));
  assert.ok(short.includes(`${BASE}/guide`) && short.includes(`${BASE}/llms-full.txt`));
  for (const word of ['Copilot', 'Caps Lock', 'F13', 'free', 'MIT']) assert.ok(short.includes(word), word);
  const guide = read('guide.html');
  for (const m of guide.matchAll(/<section id="[^"]+" class="doc__section">[\s\S]*?<h2>([\s\S]*?)<\/h2>/g)) {
    const h = m[1].replace(/<[^>]+>/g, '').replace(/&amp;/g, '&').trim();
    assert.ok(full.includes(`## ${h}`), `llms-full is missing "${h}"; run python3 tools/build_llms.py`);
  }
  const version = read('index.html').match(/"softwareVersion": "([^"]+)"/)[1];
  assert.ok(short.includes(`Current version: ${version}.`), 'llms.txt version is stale; run tools/build_llms.py');
});

test('IndexNow key file is served from the site root and holds its own name', () => {
  const keys = fs.readdirSync(ROOT).filter((f) => /^[0-9a-f]{32}\.txt$/.test(f));
  assert.equal(keys.length, 1);
  assert.equal(read(keys[0]).trim(), keys[0].replace('.txt', ''));
});

test('new pages copy: Copilot, Caps Lock, tap/hold and macropad sections; honest about the Windows setting', () => {
  const home = read('index.html');
  const guide = read('guide.html');
  assert.ok(home.includes('id="copilot-key"') && home.includes('id="caps-lock"'));
  for (const html of [home, guide]) assert.ok(html.includes('Customize Copilot key'));
  for (const id of ['tap-hold', 'special-keys', 'macropads']) {
    assert.ok(guide.includes(`<section id="${id}"`), id);
    assert.ok(guide.includes(`href="#${id}"`), `toc ${id}`);
  }
  assert.match(guide, /Macropads and stream decks/);
});

test('donate buttons stay hidden and no personal details appear on public pages', () => {
  for (const f of ['index.html', 'guide.html', 'privacy.html', '404.html', 'llms.txt', 'llms-full.txt']) {
    const text = read(f);
    for (const m of text.matchAll(/<a[^>]*data-support[^>]*>/g)) assert.ok(/\shidden[\s>]/.test(m[0]), `${f} support link visible`);
    assert.ok(!/years old|\bI am \d+|my school|Muscat|\bOman\b/i.test(text), `${f} personal detail`);
  }
  for (const f of ['llms.txt', 'llms-full.txt']) assert.ok(!/ko-fi|donat|notomar/i.test(read(f)), f);
});
