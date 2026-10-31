'use strict';
// Daily request counters for the AI proxy.
//
// Storage: Upstash Redis over its REST API when the Vercel Marketplace
// integration is connected (free plan: 500K commands/month, 250 MB). Each
// counted request costs 5 commands. Without it, a per-instance in-memory
// Map is used: best effort only, since every cold start or extra instance
// starts at zero.
//
// Keys never hold a raw IP or install ID, only a salted SHA-256 prefix.

const crypto = require('crypto');

const DAY_SECONDS = 60 * 60 * 24;

function dayStamp(nowMs) {
  return new Date(nowMs).toISOString().slice(0, 10).replace(/-/g, '');
}

function hashId(kind, value, salt) {
  return crypto.createHash('sha256').update(`${salt}|${kind}|${value}`).digest('hex').slice(0, 24);
}

class MemoryStore {
  constructor() {
    this.name = 'memory';
    this.counts = new Map();
    this.day = '';
  }
  async incr(keys, day) {
    if (day !== this.day) {
      this.counts.clear(); // yesterday's counters are no longer needed
      this.day = day;
    }
    return keys.map((key) => {
      const next = (this.counts.get(key) || 0) + 1;
      this.counts.set(key, next);
      return next;
    });
  }
  async decr(keys) {
    for (const key of keys) {
      const value = this.counts.get(key) || 0;
      if (value > 0) this.counts.set(key, value - 1);
    }
  }
}

class UpstashStore {
  constructor(url, token, fetchImpl) {
    this.name = 'upstash';
    this.url = url.replace(/\/+$/, '');
    this.token = token;
    this.fetch = fetchImpl;
  }
  async _pipeline(commands) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2500);
    try {
      const res = await this.fetch(`${this.url}/pipeline`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${this.token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(commands),
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`store HTTP ${res.status}`);
      const data = await res.json();
      if (!Array.isArray(data)) throw new Error('store reply');
      return data.map((item) => {
        if (item && item.error) throw new Error('store command');
        return item ? item.result : null;
      });
    } finally {
      clearTimeout(timer);
    }
  }
  async incr(keys) {
    const commands = [];
    for (const key of keys) {
      commands.push(['INCR', key]);
      commands.push(['EXPIRE', key, String(DAY_SECONDS * 2)]);
    }
    const results = await this._pipeline(commands);
    return keys.map((_key, index) => Number(results[index * 2]) || 0);
  }
  async decr(keys) {
    await this._pipeline(keys.map((key) => ['DECR', key]));
  }
}

const sharedMemory = new MemoryStore();

function storeFromEnv(env, fetchImpl) {
  const url = env.KV_REST_API_URL || env.UPSTASH_REDIS_REST_URL;
  const token = env.KV_REST_API_TOKEN || env.UPSTASH_REDIS_REST_TOKEN;
  if (url && token && /^https:\/\//.test(url)) return new UpstashStore(url, token, fetchImpl);
  return sharedMemory;
}

class Limiter {
  constructor({ store, fallback, perInstall, perIp, globalCap, salt, now }) {
    this.store = store;
    this.fallback = fallback || sharedMemory;
    this.perInstall = perInstall;
    this.perIp = perIp;
    this.globalCap = globalCap;
    this.salt = salt;
    this.now = now;
  }

  _keys(installId, ip) {
    const day = dayStamp(this.now());
    return {
      day,
      keys: [
        `kw:rl:${day}:i:${hashId('install', installId, this.salt)}`,
        `kw:rl:${day}:a:${hashId('ip', ip, this.salt)}`,
        `kw:rl:${day}:all`,
      ],
    };
  }

  // Counts one request. Returns {allowed, reason, remaining, keys, store}.
  async take(installId, ip) {
    const { day, keys } = this._keys(installId, ip);
    let store = this.store;
    let counts;
    try {
      counts = await store.incr(keys, day);
    } catch (_err) {
      store = this.fallback; // storage hiccup: keep serving with the local counter
      counts = await store.incr(keys, day);
    }
    const [install, address, all] = counts;
    let reason = '';
    if (install > this.perInstall) reason = 'install';
    else if (address > this.perIp) reason = 'ip';
    else if (all > this.globalCap) reason = 'global';
    return {
      allowed: !reason,
      reason,
      remaining: Math.max(0, Math.min(this.perInstall - install, this.perIp - address)),
      keys,
      store,
    };
  }

  // Gives the request back when the AI never answered, so "AI busy" costs nothing.
  async refund(ticket) {
    try {
      await ticket.store.decr(ticket.keys);
    } catch (_err) {
      /* best effort */
    }
  }
}

module.exports = { Limiter, MemoryStore, UpstashStore, storeFromEnv, dayStamp, hashId, sharedMemory };
