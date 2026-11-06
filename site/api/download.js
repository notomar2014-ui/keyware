'use strict';
// GET /download/Keyware.exe (rewritten here): count the download, then 302 to the versioned x64 file.
// GET /download/Keyware-arm64.exe: same counter, then 302 to the ARM64 release asset when that
// asset exists for this version and its digest is a SHA-256. Otherwise 404.
// Bots, link previews and HEAD requests are not counted; the same IP + browser counts once per
// 10 minutes. Only a salted hash of IP + user agent is kept, for those 10 minutes.
const { sharedStats } = require('./_lib/stats');
const { clientIp } = require('./_lib/core');
const { FILE_PATH, lookupArm } = require('./_lib/release');

const BOT_UA = /bot\b|bot\/|crawl|spider|slurp|scrap|preview|facebookexternalhit|embedly|whatsapp|telegram|discord|slack|skype|vkshare|curl|wget|python|httpclient|okhttp|go-http|java\/|libwww|axios|node-fetch|undici|headless|lighthouse|pagespeed|pingdom|uptime|monitor|checker|scanner|virustotal|archive\.org/i;

function isBot(ua) {
  return !ua || ua.length < 12 || BOT_UA.test(ua);
}

function wantsArm(req) {
  const url = String((req && req.url) || '');
  return /(?:\?|&)arch=arm64(?:&|$)/.test(url) || /Keyware-arm64\.exe(?:\?|$)/.test(url);
}

function createDownloadHandler({ stats, log = () => {}, lookup = lookupArm } = {}) {
  return async function download(req, res) {
    const method = req.method || 'GET';
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Robots-Tag', 'noindex');
    if (method !== 'GET' && method !== 'HEAD') {
      res.statusCode = 405;
      res.setHeader('Allow', 'GET, HEAD');
      return res.end();
    }
    const arm = wantsArm(req);
    let target = FILE_PATH;
    if (arm) {
      let build;
      try {
        build = await lookup();
      } catch (_e) {
        log({ evt: 'download', arch: 'arm64', err: 'lookup' });
        res.statusCode = 503;
        res.setHeader('Content-Type', 'text/plain; charset=utf-8');
        res.setHeader('Retry-After', '60');
        return res.end('The ARM64 build could not be checked. Try again in a minute.');
      }
      if (!build || !build.redirect) {
        res.statusCode = 404;
        res.setHeader('Content-Type', 'text/plain; charset=utf-8');
        return res.end('The ARM64 build is not published for this version.');
      }
      target = build.redirect;
    }
    const headers = req.headers || {};
    const ua = String(headers['user-agent'] || '');
    const st = stats || sharedStats();
    let counted = false;
    if (method === 'GET' && !String(headers.range || '').match(/^bytes=[1-9]/)) {
      try {
        if (isBot(ua)) await st.recordBotDownload();
        else counted = await st.recordDownload({ ip: clientIp(headers), ua });
      } catch (_e) {
        log({ evt: 'download', err: 'store' });
      }
    }
    log({ evt: 'download', counted, arch: arm ? 'arm64' : 'x64' });
    res.statusCode = 302;
    res.setHeader('Location', target);
    return res.end();
  };
}

module.exports = createDownloadHandler();
module.exports.createDownloadHandler = createDownloadHandler;
module.exports.isBot = isBot;
module.exports.wantsArm = wantsArm;
