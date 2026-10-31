'use strict';
// GET /download/Keyware.exe (rewritten here): count the download, then 302 to the versioned file.
// Bots, link previews and HEAD requests are not counted; the same IP + browser counts once per
// 10 minutes. Only a salted hash of IP + user agent is kept, for those 10 minutes.
const { sharedStats } = require('./_lib/stats');
const { clientIp } = require('./_lib/core');
const { FILE_PATH } = require('./_lib/release');

const BOT_UA = /bot\b|bot\/|crawl|spider|slurp|scrap|preview|facebookexternalhit|embedly|whatsapp|telegram|discord|slack|skype|vkshare|curl|wget|python|httpclient|okhttp|go-http|java\/|libwww|axios|node-fetch|undici|headless|lighthouse|pagespeed|pingdom|uptime|monitor|checker|scanner|virustotal|archive\.org/i;

function isBot(ua) {
  return !ua || ua.length < 12 || BOT_UA.test(ua);
}

function createDownloadHandler({ stats, log = () => {} } = {}) {
  return async function download(req, res) {
    const method = req.method || 'GET';
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('X-Robots-Tag', 'noindex');
    if (method !== 'GET' && method !== 'HEAD') {
      res.statusCode = 405;
      res.setHeader('Allow', 'GET, HEAD');
      return res.end();
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
    log({ evt: 'download', counted });
    res.statusCode = 302;
    res.setHeader('Location', FILE_PATH);
    return res.end();
  };
}

module.exports = createDownloadHandler();
module.exports.createDownloadHandler = createDownloadHandler;
module.exports.isBot = isBot;
