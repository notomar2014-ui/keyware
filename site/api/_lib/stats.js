'use strict';
// Counters and records behind the private dashboard.
//
// counters (AI requests, per-model usage, active installs, rate limits, cooldowns):
//   Upstash Redis when connected, else per-instance memory (labelled "not saved").
// records (downloads, feedback, donations):
//   Upstash Redis when connected, else Vercel Blob (private store, free on Hobby:
//   1 GB, 2,000 uploads/lists a month), else memory (labelled "not saved").
// Nothing here stores a raw IP or install ID: only salted hash prefixes.

const crypto = require('crypto');
const { kvFromEnv, statsDay, lastDays } = require('./kv');

const DAY_TTL = 60 * 60 * 24 * 400; // keep daily counters ~13 months

function hashOf(kind, value, salt) {
  return crypto.createHash('sha256').update(`${salt}|${kind}|${value}`).digest('hex').slice(0, 24);
}

function newId(nowMs) {
  // Sorts by time: 9 base36 digits of ms, then randomness.
  return `${nowMs.toString(36).padStart(9, '0')}-${crypto.randomBytes(5).toString('hex')}`;
}

function pairs(flat) {
  const out = {};
  if (Array.isArray(flat)) for (let i = 0; i + 1 < flat.length; i += 2) out[flat[i]] = flat[i + 1];
  else if (flat && typeof flat === 'object') Object.assign(out, flat);
  return out;
}

class RedisRecords {
  constructor(kv) {
    this.kv = kv;
    this.name = kv.name;
    this.persistent = kv.persistent;
  }
  async addDownload(day) {
    await this.kv.run([['INCR', 'kw:dl:total'], ['INCR', `kw:dl:d:${day}`], ['EXPIRE', `kw:dl:d:${day}`, DAY_TTL]]);
  }
  async downloadCounts(days) {
    const res = await this.kv.run([['GET', 'kw:dl:total'], ['MGET', ...days.map((d) => `kw:dl:d:${d}`)]]);
    const byDay = {};
    days.forEach((d, i) => { byDay[d] = Number((res[1] || [])[i]) || 0; });
    return { total: Number(res[0]) || 0, byDay };
  }
  async addFeedback(rec) {
    await this.kv.run([
      ['SET', `kw:fb:${rec.id}`, JSON.stringify(rec)],
      ['LPUSH', 'kw:fb:index', rec.id],
      ['LTRIM', 'kw:fb:index', 0, 1999],
    ]);
  }
  async listFeedback(limit = 200) {
    const [ids, readFlat] = await this.kv.run([['LRANGE', 'kw:fb:index', 0, limit - 1], ['HGETALL', 'kw:fb:read']]);
    if (!ids || !ids.length) return [];
    const read = pairs(readFlat);
    const [raw] = await this.kv.run([['MGET', ...ids.map((id) => `kw:fb:${id}`)]]);
    return (raw || []).map((text, i) => {
      try {
        const rec = JSON.parse(text);
        rec.read = Boolean(read[ids[i]]);
        return rec;
      } catch (_e) {
        return null;
      }
    }).filter(Boolean);
  }
  async markRead(id, read = true) {
    await this.kv.run([['HSET', 'kw:fb:read', id, read ? '1' : '']]);
  }
  async addDonation(rec, dedupeKey) {
    const [fresh] = await this.kv.run([['SET', `kw:don:seen:${dedupeKey}`, '1', 'NX', 'EX', 60 * 60 * 24 * 90]]);
    if (fresh !== 'OK') return false;
    await this.kv.run([['LPUSH', 'kw:don:list', JSON.stringify(rec)], ['LTRIM', 'kw:don:list', 0, 4999]]);
    return true;
  }
  async listDonations() {
    const [list] = await this.kv.run([['LRANGE', 'kw:don:list', 0, 4999]]);
    return (list || []).map((t) => { try { return JSON.parse(t); } catch (_e) { return null; } }).filter(Boolean);
  }
}

class BlobRecords {
  constructor(token, sdk) {
    this.name = 'vercel-blob';
    this.persistent = true;
    this.token = token;
    this.sdk = sdk || require('@vercel/blob');
  }
  _opts(extra) {
    return { access: 'private', token: this.token, addRandomSuffix: false, ...extra };
  }
  async _put(pathname, body, overwrite = false) {
    await this.sdk.put(pathname, body, this._opts({ contentType: 'application/json', allowOverwrite: overwrite }));
  }
  async _listAll(prefix, max = 10000) {
    const out = [];
    let cursor;
    do {
      const page = await this.sdk.list({ prefix, cursor, limit: 1000, token: this.token });
      out.push(...page.blobs);
      cursor = page.hasMore ? page.cursor : undefined;
    } while (cursor && out.length < max);
    return out;
  }
  async _read(pathname) {
    // Feedback and donation files never change, so cached reads are fine (and cheaper).
    const got = await this.sdk.get(pathname, { access: 'private', token: this.token });
    if (!got || got.statusCode !== 200 || !got.stream) return null;
    return new Response(got.stream).text();
  }
  async addDownload(day, nowMs) {
    await this._put(`dl/${day}/${newId(nowMs || Date.now())}`, '1');
  }
  async downloadCounts(days) {
    const blobs = await this._listAll('dl/');
    const byDay = {};
    for (const d of days) byDay[d] = 0;
    for (const b of blobs) {
      const day = b.pathname.split('/')[1];
      if (day in byDay) byDay[day] += 1;
    }
    return { total: blobs.length, byDay };
  }
  async addFeedback(rec) {
    await this._put(`fb/${rec.id}.json`, JSON.stringify(rec));
  }
  async listFeedback(limit = 200) {
    const [items, reads] = await Promise.all([this._listAll('fb/', 2000), this._listAll('fbread/', 5000)]);
    const read = new Set(reads.map((b) => b.pathname.slice('fbread/'.length)));
    const newest = items.map((b) => b.pathname).sort().reverse().slice(0, limit);
    const recs = await Promise.all(newest.map(async (p) => {
      try {
        const rec = JSON.parse(await this._read(p));
        rec.read = read.has(rec.id);
        return rec;
      } catch (_e) {
        return null;
      }
    }));
    return recs.filter(Boolean);
  }
  async markRead(id, read = true) {
    if (read) await this._put(`fbread/${id}`, '1', true);
    else {
      const all = await this._listAll(`fbread/${id}`);
      if (all.length) await this.sdk.del(all.map((b) => b.url), { token: this.token });
    }
  }
  async addDonation(rec, dedupeKey) {
    try {
      await this._put(`don/${dedupeKey}.json`, JSON.stringify(rec), false);
      return true;
    } catch (err) {
      if (/exist|already|precondition/i.test(String(err && err.message))) return false;
      throw err;
    }
  }
  async listDonations() {
    const items = await this._listAll('don/', 5000);
    const recs = await Promise.all(items.map(async (b) => {
      try { return JSON.parse(await this._read(b.pathname)); } catch (_e) { return null; }
    }));
    return recs.filter(Boolean);
  }
}

class Stats {
  constructor({ env = process.env, fetch: fetchImpl, now = Date.now, kv, records } = {}) {
    this.env = env;
    this.now = now;
    this.kv = kv || kvFromEnv(env, fetchImpl);
    if (records) this.records = records;
    else if (this.kv.persistent) this.records = new RedisRecords(this.kv);
    else if (env.BLOB_READ_WRITE_TOKEN) this.records = new BlobRecords(env.BLOB_READ_WRITE_TOKEN);
    else this.records = new RedisRecords(this.kv);
    this.salt = env.RATE_LIMIT_SALT || 'keyware-rl-v1';
  }

  day() {
    return statsDay(this.now());
  }

  hash(kind, value) {
    return hashOf(kind, value, this.salt);
  }

  // --- counters ---------------------------------------------------------------
  async incrLimit(name, ids, limit, ttl = 60 * 60 * 26) {
    // Returns true while every id is within its daily limit.
    const day = this.day();
    const cmds = [];
    for (const id of ids) {
      const key = `kw:lim:${name}:${day}:${id}`;
      cmds.push(['INCR', key], ['EXPIRE', key, ttl]);
    }
    const res = await this.kv.run(cmds);
    return ids.every((_id, i) => Number(res[i * 2]) <= limit);
  }

  async recordAi({ installId, model, ok, limited }) {
    const day = this.day();
    const cmds = [];
    if (ok) {
      cmds.push(['INCR', 'kw:ai:total'], ['INCR', `kw:ai:d:${day}`], ['EXPIRE', `kw:ai:d:${day}`, DAY_TTL]);
      if (model) cmds.push(['HINCRBY', `kw:ai:m:${day}`, model, 1], ['EXPIRE', `kw:ai:m:${day}`, DAY_TTL]);
    }
    if (limited) cmds.push(['INCR', `kw:ai:lim:${day}`], ['EXPIRE', `kw:ai:lim:${day}`, DAY_TTL]);
    if (installId) {
      const h = this.hash('install', installId);
      cmds.push(['PFADD', `kw:inst:d:${day}`, h], ['EXPIRE', `kw:inst:d:${day}`, DAY_TTL], ['PFADD', 'kw:inst:all', h]);
    }
    try {
      await this.kv.run(cmds);
    } catch (_e) {
      /* stats never break the proxy */
    }
  }

  async recordModelFailure(model, why) {
    const day = this.day();
    try {
      await this.kv.run([['HINCRBY', `kw:ai:fail:${day}`, `${model}|${why}`, 1], ['EXPIRE', `kw:ai:fail:${day}`, DAY_TTL]]);
    } catch (_e) {
      /* ignore */
    }
  }

  // --- downloads ----------------------------------------------------------------
  async recordDownload({ ip, ua }) {
    const day = this.day();
    const who = this.hash('dl', `${ip}|${ua}`);
    // Same browser + IP within 10 minutes counts once (double clicks, retries, range requests).
    const [fresh] = await this.kv.run([['SET', `kw:dl:seen:${who}`, '1', 'NX', 'EX', 600]]);
    if (fresh !== 'OK') return false;
    if (this.records instanceof BlobRecords) {
      // Blob's free plan allows about 2,000 uploads a month and blocks the store for 30 days
      // past that, so each server instance writes at most BLOB_DOWNLOADS_PER_DAY a day and only
      // counts the rest in memory (shown as "extra, not saved" on the dashboard).
      const cap = Number(this.env.BLOB_DOWNLOADS_PER_DAY) || 40;
      const [n] = await this.kv.run([['INCR', `kw:dl:blobcap:${day}`], ['EXPIRE', `kw:dl:blobcap:${day}`, 90000]]);
      if (Number(n) > cap) {
        await this.kv.run([['INCR', `kw:dl:over:${day}`], ['EXPIRE', `kw:dl:over:${day}`, 90000]]);
        return true;
      }
    }
    await this.records.addDownload(day, this.now());
    return true;
  }

  async recordBotDownload() {
    try {
      await this.kv.run([['INCR', 'kw:dl:bots'], ['INCR', `kw:dl:bots:d:${this.day()}`]]);
    } catch (_e) {
      /* ignore */
    }
  }

  // --- dashboard ------------------------------------------------------------------
  async summary() {
    const days = lastDays(this.now(), 30);
    const today = days[days.length - 1];
    const week = days.slice(-7);
    const out = {
      storage: {
        counters: this.kv.name,
        countersSaved: Boolean(this.kv.persistent),
        records: this.records.name,
        recordsSaved: Boolean(this.records.persistent),
      },
      days,
      errors: [],
    };
    try {
      const dl = await this.records.downloadCounts(days);
      out.downloads = {
        total: dl.total,
        today: dl.byDay[today] || 0,
        week: week.reduce((s, d) => s + (dl.byDay[d] || 0), 0),
        series: days.map((d) => dl.byDay[d] || 0),
      };
      if (this.records instanceof BlobRecords) {
        const [over] = await this.kv.run([['GET', `kw:dl:over:${today}`]]);
        out.downloads.unsavedToday = Number(over) || 0;
      }
    } catch (_e) {
      out.errors.push('downloads');
      out.downloads = null;
    }
    try {
      const cmds = [
        ['GET', 'kw:ai:total'],
        ['MGET', ...days.map((d) => `kw:ai:d:${d}`)],
        ['HGETALL', `kw:ai:m:${today}`],
        ['PFCOUNT', `kw:inst:d:${today}`],
        ['PFCOUNT', 'kw:inst:all'],
        ['GET', `kw:ai:lim:${today}`],
        ['GET', 'kw:dl:bots'],
        ['HGETALL', `kw:ai:fail:${today}`],
      ];
      for (const d of week) cmds.push(['HGETALL', `kw:ai:m:${d}`]);
      const r = await this.kv.run(cmds);
      const series = days.map((_d, i) => Number((r[1] || [])[i]) || 0);
      const modelsWeek = {};
      for (let i = 0; i < week.length; i += 1) {
        for (const [m, v] of Object.entries(pairs(r[8 + i]))) modelsWeek[m] = (modelsWeek[m] || 0) + Number(v);
      }
      out.ai = {
        total: Number(r[0]) || 0,
        today: series[series.length - 1],
        week: series.slice(-7).reduce((s, v) => s + v, 0),
        series,
        modelsToday: Object.fromEntries(Object.entries(pairs(r[2])).map(([k, v]) => [k, Number(v)])),
        modelsWeek,
        installsToday: Number(r[3]) || 0,
        installsAll: Number(r[4]) || 0,
        limitedToday: Number(r[5]) || 0,
        failuresToday: Object.fromEntries(Object.entries(pairs(r[7])).map(([k, v]) => [k, Number(v)])),
      };
      out.botDownloads = Number(r[6]) || 0;
    } catch (_e) {
      out.errors.push('ai');
      out.ai = null;
    }
    return out;
  }
}

let shared = null;
function sharedStats() {
  if (!shared) shared = new Stats();
  return shared;
}

module.exports = { Stats, RedisRecords, BlobRecords, sharedStats, hashOf, newId, pairs };
