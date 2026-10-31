'use strict';
// Admin sign-in: password from env ADMIN_PASSWORD, HttpOnly signed cookie, strict lockouts.
const crypto = require('crypto');

const COOKIE = 'kw_admin';
const SESSION_HOURS = 8;
const IP_FAILS = 5; // per IP per 15 minutes
const IP_WINDOW = 15 * 60;
const GLOBAL_FAILS = 30; // all IPs per hour, then sign-in is closed for the rest of the hour
const GLOBAL_WINDOW = 60 * 60;

function sha(s) {
  return crypto.createHash('sha256').update(String(s)).digest();
}

function sameSecret(a, b) {
  return crypto.timingSafeEqual(sha(a), sha(b)) && String(a).length === String(b).length;
}

function sessionKey(env) {
  return sha(`kw-admin-v1|${env.ADMIN_PASSWORD || ''}|${env.ADMIN_SESSION_SECRET || ''}`);
}

function sign(env, payload) {
  return crypto.createHmac('sha256', sessionKey(env)).update(payload).digest('base64url');
}

function makeSession(env, nowMs) {
  const exp = Math.floor(nowMs / 1000) + SESSION_HOURS * 3600;
  const nonce = crypto.randomBytes(16).toString('base64url');
  const payload = `v1.${exp}.${nonce}`;
  return { value: `${payload}.${sign(env, payload)}`, maxAge: SESSION_HOURS * 3600 };
}

function readSession(env, cookieHeader, nowMs) {
  if (!env.ADMIN_PASSWORD) return null;
  const m = new RegExp(`(?:^|;\\s*)${COOKIE}=([^;]+)`).exec(String(cookieHeader || ''));
  if (!m) return null;
  const parts = m[1].split('.');
  if (parts.length !== 4 || parts[0] !== 'v1') return null;
  const payload = parts.slice(0, 3).join('.');
  const want = sign(env, payload);
  const got = parts[3];
  if (want.length !== got.length || !crypto.timingSafeEqual(Buffer.from(want), Buffer.from(got))) return null;
  if (Number(parts[1]) * 1000 <= nowMs) return null;
  return { nonce: parts[2], exp: Number(parts[1]) };
}

function csrfFor(env, session) {
  return crypto.createHmac('sha256', sessionKey(env)).update(`csrf|${session.nonce}`).digest('base64url').slice(0, 32);
}

function cookieHeader(value, maxAge) {
  return `${COOKIE}=${value}; Path=/admin; HttpOnly; Secure; SameSite=Strict; Max-Age=${maxAge}`;
}

// Failure counters. With Upstash they are shared by every server instance. Without it they live
// in memory per instance, and a lock marker is written to the private Blob store when an IP or
// the whole sign-in gets locked, so other instances see the lock too.
class Lockout {
  constructor({ kv, blob, now = Date.now }) {
    this.kv = kv;
    this.blob = blob; // optional {put, head}
    this.now = now;
    this.cache = new Map();
  }
  _hour() {
    return Math.floor(this.now() / 3600000);
  }
  async _blobLocked(name) {
    if (!this.blob || this.kv.persistent) return false;
    const hit = this.cache.get(name);
    if (hit && hit.until > this.now()) return hit.locked;
    let locked = false;
    try {
      locked = Boolean(await this.blob.head(`lock/${name}`));
    } catch (_e) {
      locked = false;
    }
    this.cache.set(name, { locked, until: this.now() + 60000 });
    return locked;
  }
  async _blobLock(name) {
    if (!this.blob || this.kv.persistent) return;
    this.cache.set(name, { locked: true, until: this.now() + 3600000 });
    try {
      await this.blob.put(`lock/${name}`, '1');
    } catch (_e) {
      /* already locked or store down: memory lock still applies */
    }
  }
  async locked(ipHash) {
    const [ip, all] = await this.kv.run([['GET', `kw:adm:f:${ipHash}`], ['GET', `kw:adm:fall:${this._hour()}`]]);
    if (Number(ip) >= IP_FAILS || Number(all) >= GLOBAL_FAILS) return true;
    const q = Math.floor(this.now() / (IP_WINDOW * 1000));
    if (await this._blobLocked(`all-${this._hour()}`)) return true;
    return this._blobLocked(`ip-${ipHash}-${q}`);
  }
  async fail(ipHash) {
    const hour = this._hour();
    const [ip, , all] = await this.kv.run([
      ['INCR', `kw:adm:f:${ipHash}`], ['EXPIRE', `kw:adm:f:${ipHash}`, IP_WINDOW],
      ['INCR', `kw:adm:fall:${hour}`], ['EXPIRE', `kw:adm:fall:${hour}`, GLOBAL_WINDOW],
    ]);
    const q = Math.floor(this.now() / (IP_WINDOW * 1000));
    if (Number(ip) >= IP_FAILS) await this._blobLock(`ip-${ipHash}-${q}`);
    if (Number(all) >= GLOBAL_FAILS) await this._blobLock(`all-${hour}`);
  }
  async success(ipHash) {
    await this.kv.run([['DEL', `kw:adm:f:${ipHash}`]]);
  }
}

module.exports = { COOKIE, makeSession, readSession, csrfFor, cookieHeader, sameSecret, Lockout, IP_FAILS, GLOBAL_FAILS, SESSION_HOURS };
