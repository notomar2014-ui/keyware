'use strict';
// GET /api/release: the version the site is shipping, the x64 file, and the
// ARM64 file when the GitHub release for that same version has it.
const { VERSION, X64, publicBuild, lookupArm } = require('./_lib/release');

function createReleaseHandler({ lookup = lookupArm, log = () => {} } = {}) {
  return async function release(req, res) {
    const method = req.method || 'GET';
    res.setHeader('Cache-Control', 'public, max-age=60');
    res.setHeader('X-Robots-Tag', 'noindex');
    if (method !== 'GET' && method !== 'HEAD') {
      res.statusCode = 405;
      res.setHeader('Allow', 'GET, HEAD');
      return res.end();
    }
    const builds = [publicBuild(X64)];
    try {
      const arm = await lookup();
      if (arm && arm.version === VERSION) builds.push(publicBuild(arm));
    } catch (err) {
      log({ evt: 'release', err: 'lookup' });
    }
    const body = JSON.stringify({ version: VERSION, builds });
    res.statusCode = 200;
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    if (method === 'HEAD') return res.end();
    return res.end(body);
  };
}

module.exports = createReleaseHandler();
module.exports.createReleaseHandler = createReleaseHandler;
