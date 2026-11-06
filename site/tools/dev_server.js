'use strict';
// Local stand-in for Vercel on 127.0.0.1 only: /api/bind, /api/feedback, /api/kofi-webhook,
// /download/Keyware.exe (counter) and /admin. Static files are served from the site folder.
//   GEMINI_API_KEY=... GROQ_API_KEY=... ADMIN_PASSWORD=... node tools/dev_server.js [port]
// Keys come from this process's environment only and are never printed.
const http = require('http');
const fs = require('fs');
const path = require('path');
const { createHandler } = require('../api/_lib/core');

const ROOT = path.join(__dirname, '..');
const port = parseInt(process.argv[2] || process.env.PORT || '3000', 10);
const bind = createHandler();
const routes = {
  '/api/bind': bind,
  '/api/feedback': require('../api/feedback'),
  '/api/kofi-webhook': require('../api/kofi-webhook'),
  '/download/Keyware.exe': require('../api/download'),
  '/download/Keyware-arm64.exe': require('../api/download'),
  '/api/download': require('../api/download'),
  '/api/release': require('../api/release'),
};
const admin = require('../api/admin');
const TYPES = { '.html': 'text/html; charset=utf-8', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml', '.exe': 'application/vnd.microsoft.portable-executable', '.ico': 'image/x-icon', '.woff2': 'font/woff2' };

http
  .createServer((req, res) => {
    const pathname = req.url.split('?')[0];
    let handler = routes[pathname];
    if (!handler && /^\/admin(\/(login|logout|feedback))?$/.test(pathname)) {
      const route = pathname.split('/')[2] || '';
      req.url = `/api/admin${route ? `?route=${route}` : ''}${req.url.includes('?') && !route ? `?${req.url.split('?')[1]}` : ''}`;
      handler = admin;
    }
    if (handler) {
      return Promise.resolve(handler(req, res)).catch(() => {
        res.statusCode = 500;
        res.end('{"ok":false,"error":"server error"}');
      });
    }
    let file = path.normalize(path.join(ROOT, pathname === '/' ? 'index.html' : pathname));
    if (!file.startsWith(ROOT) || /[\\/](api|test|tools|node_modules)[\\/]|\.admin_password/.test(file)) file = '';
    if (file && !path.extname(file) && fs.existsSync(`${file}.html`)) file += '.html';
    if (!file || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.statusCode = 404;
      return res.end('Not found');
    }
    res.setHeader('Content-Type', TYPES[path.extname(file)] || 'application/octet-stream');
    return fs.createReadStream(file).pipe(res);
  })
  .listen(port, '127.0.0.1', () => {
    const have = ['GEMINI_API_KEY', 'GROQ_API_KEY'].filter((n) => process.env[n]).map((n) => n.split('_')[0]);
    console.log(`Keyware dev server on http://127.0.0.1:${port} (AI providers: ${have.join(', ') || 'none'}; admin: ${process.env.ADMIN_PASSWORD ? 'on' : 'off'})`);
  });
