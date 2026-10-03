// Columnar encoding for the baked long-term series: {key: {f: valueField, d: [day deltas], v: [values]}}.
// Days count from 1900-01-01 (UTC). The dashboard's decodeLT() inverts this at load time.
"use strict";
const BASE = Date.UTC(1900, 0, 1);

function encode(LT) {
  const out = {};
  for (const [k, pts] of Object.entries(LT)) {
    const f = Object.keys(pts[0]).find((x) => x !== "date");
    let prev = 0;
    out[k] = {
      f,
      d: pts.map((p) => { const day = Math.round((Date.parse(p.date) - BASE) / 864e5); const r = day - prev; prev = day; return r; }),
      v: pts.map((p) => p[f]),
    };
  }
  return out;
}

function decode(c) {
  const o = {};
  for (const [k, e] of Object.entries(c)) {
    let day = 0;
    o[k] = e.d.map((r, i) => {
      day += r;
      return { date: new Date(BASE + day * 864e5).toISOString().slice(0, 10), [e.f]: e.v[i] };
    });
  }
  return o;
}

module.exports = { encode, decode };
