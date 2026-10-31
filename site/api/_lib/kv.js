'use strict';
// Tiny key-value layer for counters, cooldowns and records.
//
// UpstashKV: Upstash Redis over REST (Vercel Marketplace, free plan: 500K commands/month,
// 256 MB). Env: KV_REST_API_URL + KV_REST_API_TOKEN (or UPSTASH_REDIS_REST_URL/_TOKEN).
// MemoryKV: per-instance Map. NOT persistent: every cold start begins at zero. The dashboard
// says so instead of showing those numbers as real totals.
//
// Both take Redis-style command arrays through run([...commands]) and return one result each.

class MemoryKV {
  constructor(now = Date.now) {
    this.name = 'memory';
    this.persistent = false;
    this.now = now;
    this.data = new Map(); // key -> {v, exp}
  }
  _get(key) {
    const item = this.data.get(key);
    if (!item) return undefined;
    if (item.exp && item.exp <= this.now()) {
      this.data.delete(key);
      return undefined;
    }
    return item.v;
  }
  _set(key, v, exp) {
    const old = this.data.get(key);
    this.data.set(key, { v, exp: exp === undefined ? (old && old.exp) || 0 : exp });
  }
  _one(cmd) {
    const op = String(cmd[0]).toUpperCase();
    const key = cmd[1];
    const cur = this._get(key);
    switch (op) {
      case 'GET': return cur === undefined || typeof cur === 'object' ? null : String(cur);
      case 'MGET': return cmd.slice(1).map((k) => { const v = this._get(k); return v === undefined || typeof v === 'object' ? null : String(v); });
      case 'SET': {
        const rest = cmd.slice(3).map((x) => String(x).toUpperCase());
        if (rest.includes('NX') && cur !== undefined) return null;
        const exIdx = rest.indexOf('EX');
        const exp = exIdx >= 0 ? this.now() + Number(cmd[3 + exIdx + 1]) * 1000 : 0;
        this.data.set(key, { v: String(cmd[2]), exp });
        return 'OK';
      }
      case 'DEL': { let n = 0; for (const k of cmd.slice(1)) if (this.data.delete(k)) n += 1; return n; }
      case 'INCR': case 'INCRBY': case 'DECR': {
        const by = op === 'INCRBY' ? Number(cmd[2]) : op === 'DECR' ? -1 : 1;
        const next = (Number(cur) || 0) + by;
        this._set(key, String(next));
        return next;
      }
      case 'EXPIRE': {
        const item = this.data.get(key);
        if (!item) return 0;
        item.exp = this.now() + Number(cmd[2]) * 1000;
        return 1;
      }
      case 'TTL': {
        const item = this.data.get(key);
        if (!item || this._get(key) === undefined) return -2;
        return item.exp ? Math.ceil((item.exp - this.now()) / 1000) : -1;
      }
      case 'PFADD': case 'SADD': {
        const set = cur instanceof Set ? cur : new Set();
        const before = set.size;
        for (const m of cmd.slice(2)) set.add(String(m));
        this._set(key, set);
        return set.size > before ? 1 : 0;
      }
      case 'PFCOUNT': case 'SCARD': return cur instanceof Set ? cur.size : 0;
      case 'HINCRBY': {
        const h = cur instanceof Map ? cur : new Map();
        const next = (Number(h.get(String(cmd[2]))) || 0) + Number(cmd[3]);
        h.set(String(cmd[2]), String(next));
        this._set(key, h);
        return next;
      }
      case 'HSET': {
        const h = cur instanceof Map ? cur : new Map();
        let added = 0;
        for (let i = 2; i + 1 < cmd.length; i += 2) {
          if (!h.has(String(cmd[i]))) added += 1;
          h.set(String(cmd[i]), String(cmd[i + 1]));
        }
        this._set(key, h);
        return added;
      }
      case 'HGETALL': {
        if (!(cur instanceof Map)) return [];
        const out = [];
        for (const [f, v] of cur) out.push(f, v);
        return out;
      }
      case 'LPUSH': {
        const list = Array.isArray(cur) ? cur : [];
        for (const v of cmd.slice(2)) list.unshift(String(v));
        this._set(key, list);
        return list.length;
      }
      case 'LTRIM': {
        if (!Array.isArray(cur)) return 'OK';
        const stop = Number(cmd[3]);
        this._set(key, cur.slice(Number(cmd[2]), stop < 0 ? cur.length + stop + 1 : stop + 1));
        return 'OK';
      }
      case 'LRANGE': {
        if (!Array.isArray(cur)) return [];
        const stop = Number(cmd[3]);
        return cur.slice(Number(cmd[2]), stop < 0 ? cur.length + stop + 1 : stop + 1);
      }
      default:
        throw new Error(`MemoryKV: unsupported ${op}`);
    }
  }
  async run(commands) {
    return commands.map((cmd) => this._one(cmd));
  }
}

class UpstashKV {
  constructor(url, token, fetchImpl, timeoutMs = 2500) {
    this.name = 'upstash';
    this.persistent = true;
    this.url = url.replace(/\/+$/, '');
    this.token = token;
    this.fetch = fetchImpl;
    this.timeoutMs = timeoutMs;
  }
  async run(commands) {
    if (!commands.length) return [];
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const res = await this.fetch(`${this.url}/pipeline`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${this.token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(commands.map((c) => c.map(String))),
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
}

const sharedMemoryKV = new MemoryKV();

function kvFromEnv(env, fetchImpl) {
  const url = env.KV_REST_API_URL || env.UPSTASH_REDIS_REST_URL;
  const token = env.KV_REST_API_TOKEN || env.UPSTASH_REDIS_REST_TOKEN;
  if (url && token && /^https:\/\//.test(url)) return new UpstashKV(url, token, fetchImpl || globalThis.fetch);
  return sharedMemoryKV;
}

// Asia/Muscat is UTC+4 all year (no DST): the dashboard's days follow that zone.
const STATS_OFFSET_MS = 4 * 60 * 60 * 1000;
function statsDay(nowMs) {
  return new Date(nowMs + STATS_OFFSET_MS).toISOString().slice(0, 10).replace(/-/g, '');
}
function lastDays(nowMs, count) {
  const out = [];
  for (let i = count - 1; i >= 0; i -= 1) out.push(statsDay(nowMs - i * 86400000));
  return out;
}

module.exports = { MemoryKV, UpstashKV, kvFromEnv, sharedMemoryKV, statsDay, lastDays, STATS_OFFSET_MS };
