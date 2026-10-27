/*
 * GEO & MTA quantitative engine (browser + Node).
 * A faithful JavaScript port of src/services/*.py so the GitHub Pages sandbox runs the
 * real mathematics client-side. Parity with Python is verified by tests/test_pages_build.py.
 *
 * Journeys are passed as weighted records: { p: [channels...], c: 0|1, n: count, v: totalValueUsd }.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.GeoMtaEngine = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const START = "(start)";
  const CONV = "(conversion)";
  const NULL = "(null)";
  const SEP = "\u241f";
  const SOM_MAX = 1.5;
  const Z95 = 1.959963984540054;

  // ---------------------------------------------------------------- utilities
  function sum(xs) { let s = 0; for (const x of xs) s += x; return s; }

  function normalize(obj) {
    const keys = Object.keys(obj);
    const total = sum(keys.map((k) => Math.max(0, obj[k])));
    const out = {};
    for (const k of keys) out[k] = total > 0 ? Math.max(0, obj[k]) / total : (keys.length ? 1 / keys.length : 0);
    return out;
  }

  /** Solves A x = b (A is n*n row-major Float64Array) by Gaussian elimination with partial pivoting. */
  function solve(A, b, n) {
    const M = Float64Array.from(A);
    const x = Float64Array.from(b);
    for (let col = 0; col < n; col++) {
      let piv = col, best = Math.abs(M[col * n + col]);
      for (let r = col + 1; r < n; r++) {
        const v = Math.abs(M[r * n + col]);
        if (v > best) { best = v; piv = r; }
      }
      if (best < 1e-300) continue;
      if (piv !== col) {
        for (let c = 0; c < n; c++) { const t = M[col * n + c]; M[col * n + c] = M[piv * n + c]; M[piv * n + c] = t; }
        const t = x[col]; x[col] = x[piv]; x[piv] = t;
      }
      const d = M[col * n + col];
      for (let r = col + 1; r < n; r++) {
        const f = M[r * n + col] / d;
        if (f === 0) continue;
        for (let c = col; c < n; c++) M[r * n + c] -= f * M[col * n + c];
        x[r] -= f * x[col];
      }
    }
    for (let r = n - 1; r >= 0; r--) {
      let s = x[r];
      for (let c = r + 1; c < n; c++) s -= M[r * n + c] * x[c];
      const d = M[r * n + r];
      x[r] = Math.abs(d) < 1e-300 ? 0 : s / d;
    }
    return x;
  }

  /** Deterministic PRNG (mulberry32). */
  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // ---------------------------------------------------------------- quant metrics
  function wilson(k, n, z = Z95) {
    if (n <= 0) return [0, 0];
    const p = Math.min(1, Math.max(0, k / n));
    const z2 = z * z, den = 1 + z2 / n;
    const center = (p + z2 / (2 * n)) / den;
    const half = (z * Math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))) / den;
    return [Math.max(0, center - half), Math.min(1, center + half)];
  }
  function hhi(shares) {
    const s = shares.map((x) => Math.max(0, x)); const t = sum(s);
    return t > 0 ? 10000 * sum(s.map((x) => (x / t) ** 2)) : 0;
  }
  function entropyBits(ws) {
    const w = ws.map((x) => Math.max(0, x)); const t = sum(w);
    if (t <= 0) return 0;
    return -sum(w.filter((x) => x > 0).map((x) => (x / t) * Math.log2(x / t)));
  }
  function jsdBits(p, q) {
    const keys = [...new Set([...Object.keys(p), ...Object.keys(q)])].sort();
    const pn = normalize(Object.fromEntries(keys.map((k) => [k, p[k] || 0])));
    const qn = normalize(Object.fromEntries(keys.map((k) => [k, q[k] || 0])));
    let d = 0;
    for (const k of keys) {
      const m = (pn[k] + qn[k]) / 2;
      if (pn[k] > 0) d += 0.5 * pn[k] * Math.log2(pn[k] / m);
      if (qn[k] > 0) d += 0.5 * qn[k] * Math.log2(qn[k] / m);
    }
    return Math.max(0, d);
  }
  function totalVariation(p, q) {
    const keys = [...new Set([...Object.keys(p), ...Object.keys(q)])];
    const pn = normalize(Object.fromEntries(keys.map((k) => [k, p[k] || 0])));
    const qn = normalize(Object.fromEntries(keys.map((k) => [k, q[k] || 0])));
    return 0.5 * sum(keys.map((k) => Math.abs(pn[k] - qn[k])));
  }
  function percentile(sorted, pct) {
    if (!sorted.length) return 0;
    if (sorted.length === 1) return sorted[0];
    const rank = (pct / 100) * (sorted.length - 1);
    const lo = Math.floor(rank), hi = Math.ceil(rank);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (rank - lo);
  }

  // ---------------------------------------------------------------- GEO / Share of Model
  const positionDecay = (rank) => (rank <= 0 ? 1 : 1 / Math.log2(rank + 1));
  const sentimentMultiplier = (s) => 1 + 0.5 * Math.max(-1, Math.min(1, s));
  function somScore(citations, totalPrompts) {
    if (totalPrompts <= 0) return 0;
    return sum(citations.map((c) => positionDecay(c.rank) * sentimentMultiplier(c.sentiment))) / totalPrompts;
  }
  /** Expected-value form: SoM = citationRate * E[decay(rank)] * multiplier(sentiment). */
  function expectedSom(citationRate, rank, sentiment) {
    return citationRate * positionDecay(rank) * sentimentMultiplier(sentiment);
  }

  // ---------------------------------------------------------------- journeys
  function channelsOf(records) {
    const s = new Set();
    for (const r of records) for (const c of r.p) s.add(c);
    return [...s].sort();
  }
  function totals(records) {
    let n = 0, conv = 0, rev = 0, touches = 0;
    for (const r of records) { n += r.n; touches += r.n * r.p.length; if (r.c) { conv += r.n; rev += r.v; } }
    return { journeys: n, conversions: conv, revenue: rev, touches };
  }

  // ---------------------------------------------------------------- order-k Markov chain
  function fitMarkov(records, channels, order = 1) {
    const chSet = new Set(channels);
    const states = [[START]];
    const index = new Map([[START, 0]]);
    const trans = []; // [recordIdx, from, toCode] where toCode: >=0 state, -1 conv, -2 null
    records.forEach((r, ri) => {
      const path = r.p.filter((c) => chSet.has(c));
      const seq = [[START]];
      for (let i = 0; i < path.length; i++) seq.push(path.slice(Math.max(0, i - order + 1), i + 1));
      const ids = seq.map((s) => {
        const key = s.join(SEP);
        if (!index.has(key)) { index.set(key, states.length); states.push(s); }
        return index.get(key);
      });
      for (let i = 0; i + 1 < ids.length; i++) trans.push([ri, ids[i], ids[i + 1]]);
      trans.push([ri, ids[ids.length - 1], r.c ? -1 : -2]);
    });
    return { order, states, trans, weights: records.map((r) => r.n) };
  }

  function transitionProbs(model, weights) {
    const T = model.states.length, W = T + 2;
    const P = new Float64Array(T * W);
    for (const [ri, a, b] of model.trans) {
      const col = b === -1 ? T : b === -2 ? T + 1 : b;
      P[a * W + col] += weights[ri];
    }
    for (let i = 0; i < T; i++) {
      let s = 0;
      for (let j = 0; j < W; j++) s += P[i * W + j];
      if (s > 0) for (let j = 0; j < W; j++) P[i * W + j] /= s;
    }
    return P;
  }

  function conversionProbability(model, P, removed) {
    const T = model.states.length, W = T + 2;
    const A = new Float64Array(T * T);
    const b = new Float64Array(T);
    for (let i = 0; i < T; i++) {
      A[i * T + i] = 1;
      if (removed !== undefined && removed !== null && model.states[i].includes(removed)) continue;
      for (let j = 0; j < T; j++) A[i * T + j] -= P[i * W + j];
      b[i] = P[i * W + T];
    }
    const x = solve(A, b, T);
    return Math.min(1, Math.max(0, x[0]));
  }

  function removalEffects(model, channels, P) {
    const base = conversionProbability(model, P, null);
    const effects = {};
    for (const ch of channels) {
      effects[ch] = base > 1e-12 ? Math.max(0, 1 - conversionProbability(model, P, ch) / base) : 0;
    }
    return { effects, base };
  }

  function sharesFromEffects(effects, channels) {
    const total = sum(channels.map((c) => effects[c]));
    const out = {};
    for (const c of channels) out[c] = total > 1e-12 ? effects[c] / total : 1 / channels.length;
    return out;
  }

  function markovAttribution(records, channels, order = 1) {
    const model = fitMarkov(records, channels, order);
    const P = transitionProbs(model, model.weights);
    const { effects, base } = removalEffects(model, channels, P);
    return { shares: sharesFromEffects(effects, channels), effects, base, model, P };
  }

  function expectedVisits(model, P, channels) {
    const T = model.states.length, W = T + 2;
    // Solve (I - Q)^T y = e_start
    const A = new Float64Array(T * T);
    for (let i = 0; i < T; i++) {
      A[i * T + i] += 1;
      for (let j = 0; j < T; j++) A[j * T + i] -= P[i * W + j];
    }
    const e = new Float64Array(T); e[0] = 1;
    const y = solve(A, e, T);
    const visits = Object.fromEntries(channels.map((c) => [c, 0]));
    let length = 0;
    for (let i = 1; i < T; i++) {
      const s = model.states[i];
      visits[s[s.length - 1]] += y[i];
      length += y[i];
    }
    return { visits, length };
  }

  function bootstrapMarkov(records, channels, order, samples, seed = 20261027) {
    if (!samples) return {};
    const model = fitMarkov(records, channels, order);
    const total = sum(model.weights);
    const cdf = []; let acc = 0;
    for (const w of model.weights) { acc += w; cdf.push(acc / total); }
    const rnd = mulberry32(seed);
    const draws = Object.fromEntries(channels.map((c) => [c, []]));
    const w = new Float64Array(model.weights.length);
    for (let s = 0; s < samples; s++) {
      w.fill(0);
      for (let k = 0; k < total; k++) {
        const u = rnd();
        let lo = 0, hi = cdf.length - 1;
        while (lo < hi) { const mid = (lo + hi) >> 1; if (cdf[mid] < u) lo = mid + 1; else hi = mid; }
        w[lo] += 1;
      }
      const P = transitionProbs(model, w);
      const { effects } = removalEffects(model, channels, P);
      const sh = sharesFromEffects(effects, channels);
      for (const c of channels) draws[c].push(sh[c]);
    }
    const out = {};
    for (const c of channels) {
      const sorted = draws[c].sort((a, b) => a - b);
      out[c] = [percentile(sorted, 2.5), percentile(sorted, 97.5)];
    }
    return out;
  }

  // ---------------------------------------------------------------- Shapley
  function convertedCoalitions(records, channels) {
    const pos = new Map(channels.map((c, i) => [c, i]));
    const byMask = new Map();
    for (const r of records) {
      if (!r.c) continue;
      let mask = 0;
      for (const c of r.p) if (pos.has(c)) mask |= 1 << pos.get(c);
      if (!mask) continue;
      const value = r.v > 0 ? r.v : r.n;
      byMask.set(mask, (byMask.get(mask) || 0) + value);
    }
    return byMask;
  }

  /** Exact Shapley via 2^N enumeration with a zeta transform for v(S) = sum_{T subset S} value(T). */
  function shapleyExact(records, channels) {
    const n = channels.length;
    if (n === 0) return {};
    if (n > 16) throw new Error("Exact Shapley enumeration capped at 16 channels in the browser");
    const size = 1 << n;
    const v = new Float64Array(size);
    for (const [mask, value] of convertedCoalitions(records, channels)) v[mask] += value;
    for (let i = 0; i < n; i++) for (let m = 0; m < size; m++) if (m & (1 << i)) v[m] += v[m ^ (1 << i)];
    const fact = [1]; for (let i = 1; i <= n; i++) fact.push(fact[i - 1] * i);
    const weight = []; for (let s = 0; s < n; s++) weight.push((fact[s] * fact[n - s - 1]) / fact[n]);
    const popcount = (m) => { let c = 0; while (m) { m &= m - 1; c++; } return c; };
    const phi = {};
    channels.forEach((ch, i) => {
      const bit = 1 << i;
      let total = 0;
      for (let m = 0; m < size; m++) if (!(m & bit)) total += weight[popcount(m)] * (v[m | bit] - v[m]);
      phi[ch] = total;
    });
    return phi;
  }

  /** Unanimity-game closed form: phi_k = sum_{T contains k} value(T) / |T|. */
  function shapleyClosedForm(records, channels) {
    const phi = Object.fromEntries(channels.map((c) => [c, 0]));
    for (const [mask, value] of convertedCoalitions(records, channels)) {
      const members = channels.filter((_, i) => mask & (1 << i));
      for (const m of members) phi[m] += value / members.length;
    }
    return phi;
  }

  function shapleyShares(records, channels) {
    const phi = channels.length <= 12 ? shapleyExact(records, channels) : shapleyClosedForm(records, channels);
    return { phi, shares: normalize(phi) };
  }

  // ---------------------------------------------------------------- rule-based models
  function positionBasedWeights(L, first = 0.4, last = 0.4) {
    if (L <= 0) return [];
    if (L === 1) return [1];
    if (L === 2) return [0.5, 0.5];
    const mid = (1 - first - last) / (L - 2);
    return [first, ...Array(L - 2).fill(mid), last];
  }
  function timeDecayWeights(L, halfLife = 2) {
    const raw = Array.from({ length: L }, (_, i) => Math.pow(2, -(L - 1 - i) / halfLife));
    const t = sum(raw);
    return raw.map((w) => w / t);
  }
  function ruleBased(records, channels, halfLife = 2) {
    const z = () => Object.fromEntries(channels.map((c) => [c, 0]));
    const lc = z(), ft = z(), lin = z(), pb = z(), td = z();
    const chSet = new Set(channels);
    for (const r of records) {
      if (!r.c) continue;
      const path = r.p.filter((c) => chSet.has(c));
      if (!path.length) continue;
      ft[path[0]] += r.n;
      lc[path[path.length - 1]] += r.n;
      for (const c of path) lin[c] += r.n / path.length;
      positionBasedWeights(path.length).forEach((w, i) => { pb[path[i]] += r.n * w; });
      timeDecayWeights(path.length, halfLife).forEach((w, i) => { td[path[i]] += r.n * w; });
    }
    return {
      "Last-Click": normalize(lc), "First-Touch": normalize(ft), Linear: normalize(lin),
      "Position-Based": normalize(pb), "Time-Decay": normalize(td),
    };
  }

  // ---------------------------------------------------------------- budget optimizer
  function optimizeBudget(attributedRevenue, currentSpend, totalBudget, beta = 0.5, trust = 0.5) {
    const channels = Object.keys(currentSpend).sort();
    const S = sum(channels.map((c) => Math.max(0, currentSpend[c])));
    const zero = Object.fromEntries(channels.map((c) => [c, 0]));
    if (!channels.length || totalBudget <= 0 || S <= 0) {
      return { allocation: zero, mroasNow: zero, mroasOpt: zero, lambda: 0, revenueNow: 0, revenueOpt: 0, liftPct: 0, lo: zero, hi: zero };
    }
    const scale = totalBudget / S, a = {}, lo = {}, hi = {};
    for (const c of channels) {
      const s = Math.max(0, currentSpend[c]);
      a[c] = s > 0 ? Math.max(0, attributedRevenue[c] || 0) / Math.pow(s, beta) : 0;
      lo[c] = Math.max(0, 1 - trust) * s * scale;
      hi[c] = (1 + trust) * s * scale;
    }
    const expo = 1 / (1 - beta);
    const alloc = (lam) => {
      const out = {};
      for (const c of channels) {
        if (a[c] <= 0) { out[c] = lo[c]; continue; }
        out[c] = Math.min(hi[c], Math.max(lo[c], Math.pow((beta * a[c]) / lam, expo)));
      }
      return out;
    };
    let l = Math.log(1e-12), h = Math.log(1e12);
    for (let it = 0; it < 300 && h - l >= 1e-10; it++) {
      const mid = 0.5 * (l + h);
      if (sum(Object.values(alloc(Math.exp(mid)))) > totalBudget) l = mid; else h = mid;
    }
    const lambda = Math.exp(0.5 * (l + h));
    const allocation = alloc(lambda);
    const residual = totalBudget - sum(Object.values(allocation));
    const free = channels.filter((c) => lo[c] + 1e-9 < allocation[c] && allocation[c] < hi[c] - 1e-9);
    if (free.length && residual !== 0) {
      const ft = sum(free.map((c) => allocation[c]));
      for (const c of free) allocation[c] += residual * (ft > 0 ? allocation[c] / ft : 1 / free.length);
    }
    const rev = (c, x) => (x > 0 ? a[c] * Math.pow(x, beta) : 0);
    const mroas = (c, x) => (a[c] <= 0 ? 0 : x <= 0 ? Infinity : beta * a[c] * Math.pow(x, beta - 1));
    const revenueNow = sum(channels.map((c) => rev(c, Math.max(0, currentSpend[c]) * scale)));
    const revenueOpt = sum(channels.map((c) => rev(c, allocation[c])));
    return {
      allocation, lambda, revenueNow, revenueOpt, lo, hi,
      liftPct: revenueNow > 0 ? 100 * (revenueOpt / revenueNow - 1) : 0,
      mroasNow: Object.fromEntries(channels.map((c) => [c, mroas(c, Math.max(0, currentSpend[c]))])),
      mroasOpt: Object.fromEntries(channels.map((c) => [c, mroas(c, allocation[c])])),
    };
  }

  // ---------------------------------------------------------------- full pipeline
  function runPipeline(records, opts = {}) {
    const order = opts.order || 1;
    const halfLife = opts.halfLife || 2;
    const channels = channelsOf(records);
    const t = totals(records);
    const revenue = t.revenue > 0 ? t.revenue : t.conversions * 150;
    const mk = markovAttribution(records, channels, order);
    const ev = expectedVisits(mk.model, mk.P, channels);
    const sh = shapleyShares(records, channels);
    const rules = ruleBased(records, channels, halfLife);
    const models = { ...rules, Markov: mk.shares, Shapley: sh.shares };
    const divergence = {};
    for (const name of ["Last-Click", "First-Touch", "Linear", "Position-Based", "Time-Decay", "Shapley"]) {
      divergence[name] = { jsd: jsdBits(models[name], mk.shares), tv: totalVariation(models[name], mk.shares) };
    }
    return {
      channels, totals: t, revenue, order,
      base: mk.base, empirical: t.journeys ? t.conversions / t.journeys : 0,
      effects: mk.effects, models, phi: sh.phi, visits: ev.visits, pathLength: ev.length,
      divergence, model: mk.model, P: mk.P,
    };
  }

  // ---------------------------------------------------------------- security helpers
  const FORBIDDEN_URL_CHARS = /[\x00-\x20\x7f\\<>"'`{}|^]/;
  const BLOCKED_HOSTS = ["localhost", "metadata.google.internal", "metadata", "instance-data"];
  const BLOCKED_SUFFIXES = [".localhost", ".local", ".internal", ".localdomain", ".home.arpa"];
  const V4_BLOCKED = [
    ["0.0.0.0", 8, "\"this network\" 0.0.0.0/8"], ["10.0.0.0", 8, "RFC1918 private 10.0.0.0/8"],
    ["100.64.0.0", 10, "carrier-grade NAT 100.64.0.0/10"], ["127.0.0.0", 8, "loopback 127.0.0.0/8"],
    ["169.254.0.0", 16, "link-local / cloud metadata 169.254.0.0/16"], ["172.16.0.0", 12, "RFC1918 private 172.16.0.0/12"],
    ["192.0.0.0", 24, "IETF protocol assignments 192.0.0.0/24"], ["192.0.2.0", 24, "documentation TEST-NET-1"],
    ["192.168.0.0", 16, "RFC1918 private 192.168.0.0/16"], ["198.18.0.0", 15, "benchmarking 198.18.0.0/15"],
    ["198.51.100.0", 24, "documentation TEST-NET-2"], ["203.0.113.0", 24, "documentation TEST-NET-3"],
    ["224.0.0.0", 4, "multicast 224.0.0.0/4"], ["240.0.0.0", 4, "reserved 240.0.0.0/4 + broadcast"],
  ];
  function v4ToInt(ip) {
    const parts = ip.split(".").map(Number);
    if (parts.length !== 4 || parts.some((p) => !Number.isInteger(p) || p < 0 || p > 255)) return null;
    return ((parts[0] << 24) >>> 0) + (parts[1] << 16) + (parts[2] << 8) + parts[3];
  }
  function intToV4(n) { return [n >>> 24, (n >>> 16) & 255, (n >>> 8) & 255, n & 255].join("."); }
  function v4Reason(ip) {
    const n = v4ToInt(ip);
    if (n === null) return "invalid IPv4";
    for (const [net, bits, label] of V4_BLOCKED) {
      const mask = bits === 0 ? 0 : (0xffffffff << (32 - bits)) >>> 0;
      if (((n & mask) >>> 0) === ((v4ToInt(net) & mask) >>> 0)) return label;
    }
    return null;
  }
  function parseV6(text) {
    let s = text.replace(/^\[|\]$/g, "").toLowerCase();
    const v4tail = s.match(/(\d+\.\d+\.\d+\.\d+)$/);
    if (v4tail) {
      const n = v4ToInt(v4tail[1]);
      if (n === null) return null;
      s = s.slice(0, -v4tail[1].length) + ((n >>> 16).toString(16)) + ":" + ((n & 0xffff).toString(16));
    }
    const halves = s.split("::");
    if (halves.length > 2) return null;
    const head = halves[0] ? halves[0].split(":") : [];
    const tail = halves.length === 2 && halves[1] ? halves[1].split(":") : [];
    const fill = halves.length === 2 ? 8 - head.length - tail.length : 0;
    const groups = [...head, ...Array(fill).fill("0"), ...tail];
    if (groups.length !== 8 || groups.some((g) => !/^[0-9a-f]{1,4}$/.test(g))) return null;
    return groups.map((g) => parseInt(g, 16));
  }
  function v6Reason(g) {
    const zeroPrefix = (k) => g.slice(0, k).every((x) => x === 0);
    const embedded = (hi, lo) => intToV4(((hi << 16) >>> 0) + lo);
    if (g.every((x) => x === 0)) return "IPv6 unspecified ::";
    if (zeroPrefix(7) && g[7] === 1) return "IPv6 loopback ::1";
    if (zeroPrefix(5) && g[5] === 0xffff) {
      const v4 = embedded(g[6], g[7]); const r = v4Reason(v4);
      return r ? `IPv4-mapped ${v4}: ${r}` : "IPv4-mapped address";
    }
    if (g[0] === 0x64 && g[1] === 0xff9b && g.slice(2, 6).every((x) => x === 0)) {
      const v4 = embedded(g[6], g[7]); const r = v4Reason(v4);
      return r ? `NAT64-embedded ${v4}: ${r}` : null;
    }
    if (g[0] === 0x2002) {
      const v4 = embedded(g[1], g[2]); const r = v4Reason(v4);
      return r ? `6to4-embedded ${v4}: ${r}` : "6to4 relay prefix 2002::/16";
    }
    if (g[0] === 0xfd00 && g[1] === 0x0ec2 && g.slice(2, 7).every((x) => x === 0) && g[7] === 0x254) return "AWS IMDS IPv6 fd00:ec2::254";
    if ((g[0] & 0xfe00) === 0xfc00) return "IPv6 unique-local fc00::/7";
    if ((g[0] & 0xffc0) === 0xfe80) return "IPv6 link-local fe80::/10";
    if ((g[0] & 0xff00) === 0xff00) return "IPv6 multicast ff00::/8";
    if (g[0] === 0x2001 && g[1] === 0x0db8) return "IPv6 documentation 2001:db8::/32";
    if (g[0] === 0x2001 && g[1] < 0x0200) return "IETF special-purpose 2001::/23 (incl. Teredo)";
    if (zeroPrefix(6)) return "IPv4-compatible / reserved ::/96";
    return null;
  }

  /**
   * Client-side replica of src/security.py::resolve_and_validate_url.
   * Returns { verdict: 'blocked' | 'allowed' | 'needs-dns', steps: [{rule, ok, detail}] }.
   * WHATWG URL parsing normalizes decimal / hex / octal IPv4 (http://2130706433 -> 127.0.0.1).
   */
  function ssrfCheck(raw) {
    const steps = [];
    const fail = (rule, detail) => { steps.push({ rule, ok: false, detail }); return { verdict: "blocked", steps }; };
    const pass = (rule, detail) => steps.push({ rule, ok: true, detail });
    const input = String(raw || "").trim();
    if (!input) return fail("Non-empty input", "URL string cannot be empty");
    if (input.length > 2048) return fail("Length <= 2048", `${input.length} characters`);
    pass("Length <= 2048", `${input.length} characters`);
    const schemeMatch = input.match(/^([a-zA-Z][a-zA-Z0-9+.-]*):/);
    const scheme = schemeMatch ? schemeMatch[1].toLowerCase() : "";
    if (scheme !== "https") return fail("Scheme is https", `'${scheme || "(none)"}' is not permitted`);
    pass("Scheme is https", "TLS enforced");
    if (FORBIDDEN_URL_CHARS.test(input)) return fail("No forbidden characters", "whitespace, quotes, angle brackets, backslash or control characters");
    pass("No forbidden characters", "RFC 3986 safe");
    let url;
    try { url = new URL(input); } catch (e) { return fail("Parsable URL", "malformed URL"); }
    if (url.username || url.password || /^https:\/\/[^/?#]*@/i.test(input)) return fail("No embedded credentials", "userinfo (user:pass@host) is rejected");
    pass("No embedded credentials", "no userinfo");
    if (url.port && url.port !== "443") return fail("Port is 443", `port ${url.port} is not permitted`);
    pass("Port is 443", "default HTTPS port");
    const host = url.hostname.replace(/\.$/, "").toLowerCase();
    if (BLOCKED_HOSTS.includes(host) || BLOCKED_SUFFIXES.some((s) => host.endsWith(s))) return fail("Not an internal hostname", `'${host}' refers to an internal service`);
    pass("Not an internal hostname", host);
    if (host.startsWith("[")) {
      const g = parseV6(host);
      if (!g) return fail("Valid IP literal", "unparsable IPv6");
      const r = v6Reason(g);
      if (r) return fail("Globally routable IP", r);
      pass("Globally routable IP", `${host} is public`);
      return { verdict: "allowed", steps };
    }
    if (/^\d+\.\d+\.\d+\.\d+$/.test(host)) {
      const r = v4Reason(host);
      if (r) return fail("Globally routable IP", `${host}: ${r}`);
      pass("Globally routable IP", `${host} is public`);
      return { verdict: "allowed", steps };
    }
    steps.push({ rule: "DNS answers are all public", ok: null, detail: "Server-side: socket.getaddrinfo, every A/AAAA record must be global; IPs are pinned against rebinding" });
    return { verdict: "needs-dns", steps };
  }

  const DANGEROUS_BLOCK = /<\s*(script|style|iframe|object|embed|template|noscript|svg|math)\b[^>]*>[\s\S]*?<\s*\/\s*\1\s*>/gi;
  const INVISIBLE = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]/g;
  function decodeEntities(s) {
    return s.replace(/&(#x[0-9a-f]+|#\d+|lt|gt|amp|quot|apos|#39);?/gi, (m, e) => {
      const k = e.toLowerCase();
      if (k === "lt") return "<"; if (k === "gt") return ">"; if (k === "amp") return "&";
      if (k === "quot") return '"'; if (k === "apos" || k === "#39") return "'";
      const code = k.startsWith("#x") ? parseInt(k.slice(2), 16) : parseInt(k.slice(1), 10);
      return Number.isFinite(code) && code > 0 && code < 0x110000 ? String.fromCodePoint(code) : "";
    });
  }
  /** Mirrors src/security.py::sanitize_text: reduce untrusted input to inert plain text. */
  function sanitizeText(text) {
    let value = String(text);
    for (let i = 0; i < 4; i++) {
      const prev = value;
      value = decodeEntities(value).replace(DANGEROUS_BLOCK, "").replace(/<!--[\s\S]*?-->/g, "").replace(/<\/?[a-zA-Z][^>]*>/g, "");
      if (value === prev) break;
    }
    return value.replace(/[<>]/g, "").replace(INVISIBLE, "").trim();
  }
  /** JSON safe for <script type="application/ld+json"> embedding (OWASP XSS rule 3.1). */
  function safeJsonForScript(obj, indent = 2) {
    return JSON.stringify(obj, null, indent)
      .replace(/</g, "\\u003c").replace(/>/g, "\\u003e").replace(/&/g, "\\u0026")
      .replace(/[\u2028\u2029\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]/g,
        (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"));
  }

  return {
    START, CONV, NULL, SOM_MAX,
    solve, mulberry32, normalize,
    wilson, hhi, entropyBits, jsdBits, totalVariation, percentile,
    positionDecay, sentimentMultiplier, somScore, expectedSom,
    channelsOf, totals, fitMarkov, transitionProbs, conversionProbability, removalEffects,
    markovAttribution, expectedVisits, bootstrapMarkov,
    shapleyExact, shapleyClosedForm, shapleyShares,
    positionBasedWeights, timeDecayWeights, ruleBased,
    optimizeBudget, runPipeline,
    ssrfCheck, sanitizeText, safeJsonForScript,
  };
});
