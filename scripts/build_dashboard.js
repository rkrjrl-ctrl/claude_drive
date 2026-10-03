#!/usr/bin/env node
// Builds site/index.html: the full dashboard app (same chart code/UI as the
// Claude Artifact / Google Drive version) with this repo's own data baked in
// at build time. No live backend needed — pure static file for GitHub Pages.
"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const TEMPLATE = path.join(__dirname, "dashboard_template.html");
const LONG_TERM_DIR = path.join(ROOT, "data", "long_term");
const HISTORY_CSV = path.join(ROOT, "data", "history.csv");
const OUT_DIR = path.join(ROOT, "site");
const OUT_FILE = path.join(OUT_DIR, "index.html");

// file field name -> original LT object key (as used by the dashboard's chart code)
const LT_KEY = {
  kospi: "kospi_lt", usdkrw: "usdkrw_lt", kr_cpi: "kr_cpi_lt", kr_10y: "kr_10y_lt",
  us_10y: "us_10y_lt", kosdaq: "kosdaq_lt", kr_2y: "kr_2y_lt", us_m2: "us_m2_lt",
  kr_m2: "kr_m2_lt", us_2y: "us_2y_lt", kr_rate: "kr_rate_hist", us_rate: "us_rate_hist",
  dow: "dow", sp500: "sp500", us_cpi: "us_cpi", nasdaq: "nasdaq", gold: "gold", wti: "wti",
  vix: "vix", dxy: "dxy", copper: "copper", us_unemployment: "us_unemployment",
  us_hy_spread: "us_hy_spread", us_yield_spread: "us_yield_spread",
  kr_apt_price_national: "kr_apt_price_national", kr_apt_price_seoul: "kr_apt_price_seoul",
  kr_apt_avgprice_national: "kr_apt_avgprice_national", kr_apt_avgprice_seoul: "kr_apt_avgprice_seoul",
  kr_apt_medprice_national: "kr_apt_medprice_national", kr_apt_medprice_seoul: "kr_apt_medprice_seoul",
  soybean: "soybean", corn: "corn", wheat: "wheat", silver: "silver", shanghai: "shanghai", hsi: "hsi", nikkei: "nikkei", dax: "dax", ftse: "ftse", cac: "cac", stoxx50: "stoxx50",
};

function parseCsv(text) {
  const lines = text.trim().split(/\r?\n/);
  const header = lines[0].split(",");
  return lines.slice(1).map((line) => {
    const cols = line.split(",");
    const row = {};
    header.forEach((h, i) => (row[h] = cols[i]));
    return row;
  });
}

function loadLongTerm() {
  const LT = {};
  for (const [field, ltKey] of Object.entries(LT_KEY)) {
    const file = path.join(LONG_TERM_DIR, `${field}.csv`);
    if (!fs.existsSync(file)) continue;
    const rows = parseCsv(fs.readFileSync(file, "utf8"));
    LT[ltKey] = rows
      .filter((r) => r[field] !== undefined && r[field] !== "")
      .map((r) => ({ date: r.date, [field]: Number(r[field]) }));
  }
  return LT;
}

function loadRows() {
  if (!fs.existsSync(HISTORY_CSV)) return [];
  const rows = parseCsv(fs.readFileSync(HISTORY_CSV, "utf8"));
  const numeric = [
    "usdkrw", "kr_rate", "us_rate", "kr_2y", "kr_10y", "us_2y", "us_10y",
    "kospi", "kosdaq", "sp500", "dow", "nasdaq", "kr_cpi", "us_cpi", "wti", "gold",
    "kr_m2", "us_m2", "vix", "dxy", "copper", "us_unemployment", "us_hy_spread", "us_yield_spread",
    "kr_apt_price_national", "kr_apt_price_seoul",
    "kr_apt_avgprice_national", "kr_apt_avgprice_seoul",
    "kr_apt_medprice_national", "kr_apt_medprice_seoul", "soybean", "corn", "wheat", "silver", "shanghai", "hsi", "nikkei", "dax", "ftse", "cac", "stoxx50",
  ];
  return rows.map((r) => {
    const out = { date: r.date, updated_at: r.updated_at };
    for (const k of numeric) if (r[k] !== undefined && r[k] !== "") out[k] = Number(r[k]);
    return out;
  });
}

function build() {
  let html = fs.readFileSync(TEMPLATE, "utf8").replace(/\r\n/g, "\n");
  const buildAt = new Date().toISOString();

  const LT = loadLongTerm();
  const ltMarker = "var LT = {};";
  if (!html.includes(ltMarker)) throw new Error("LT marker not found in template");
  html = html.replace(ltMarker, "var LT = " + JSON.stringify(LT) + ";");

  const rows = loadRows();
  const rowsMarker = "var rows = [SEED_ROW]; // sorted ascending by date, replaced once db loads";
  if (!html.includes(rowsMarker)) throw new Error("rows marker not found in template");
  html = html.replace(rowsMarker, "var rows = " + JSON.stringify(rows) + "; // baked at build time from this repo's data/");

  html = html.replace(
    /var SEED_DATE = "[^"]*";/,
    (m) => `${m}\n  var STATIC_BUILD_AT = "${buildAt}";\n  var IS_STATIC_BUILD = true;`
  );

  const bootStart = html.indexOf("  // ---- live db wiring ----");
  const bootEnd = html.indexOf("  boot();\n})();");
  if (bootStart === -1 || bootEnd === -1) throw new Error("boot block not found in template");
  const bootEndFull = bootEnd + "  boot();\n})();".length;
  const replacement =
    `  // ---- static build notice (no live db in this GitHub Pages copy) ----
  document.getElementById("footNote").textContent =
    "GitHub Actions 자동 생성 (생성: " + STATIC_BUILD_AT.slice(0,16).replace("T"," ") + " UTC) — 매일 08:00 KST에 자동 갱신됩니다.";
})();`;
  html = html.slice(0, bootStart) + replacement + html.slice(bootEndFull);

  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.writeFileSync(OUT_FILE, html, "utf8");
  console.log(`built ${OUT_FILE}: ${rows.length} daily rows, ${Object.keys(LT).length} long-term series, ${(html.length / 1e6).toFixed(2)}MB`);
}

build();
