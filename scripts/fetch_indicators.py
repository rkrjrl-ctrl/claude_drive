#!/usr/bin/env python3
"""Daily fetch of 18 KR/US economic indicators into data/history.csv.

Sources (no scraping, all official/stable APIs):
  - FRED (fredgraph.csv, no key): US rates, US CPI, US M2, US/KR treasury yields via DGS series,
    USD/KRW, Dow, S&P 500, Nasdaq, WTI.
  - Yahoo Finance chart API (no key): KOSPI, KOSDAQ, gold.
  - Bank of Korea ECOS (needs BOK_ECOS_API_KEY): KR base rate, KR 2y/10y bond yield, KR CPI, KR M2.

If a field can't be fetched, the previous value is carried forward and a warning is recorded
in data/STATUS.md so staleness is visible instead of silently frozen.
"""
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_CSV = os.path.join(ROOT, "data", "history.csv")
LATEST_JSON = os.path.join(ROOT, "data", "latest.json")
STATUS_MD = os.path.join(ROOT, "data", "STATUS.md")

FIELDS = [
    "date", "usdkrw", "kr_rate", "us_rate", "kr_2y", "kr_10y", "us_2y", "us_10y",
    "kospi", "kosdaq", "sp500", "dow", "nasdaq", "kr_cpi", "us_cpi", "wti", "gold",
    "kr_m2", "us_m2", "updated_at",
]

ECOS_KEY = os.environ.get("BOK_ECOS_API_KEY", "").strip()
UA = "Mozilla/5.0 (compatible; econ-indicator-bot/1.0)"

warnings = []


def http_get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.read().decode("utf-8")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        warnings.append(f"HTTP fetch failed: {url} ({e})")
        return None


def fred_series(series_id, cosd):
    text = http_get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={cosd}")
    if not text:
        return []
    rows = []
    for line in text.strip().splitlines()[1:]:
        parts = line.strip().split(",")
        if len(parts) != 2 or not parts[0] or parts[1] in ("", "."):
            continue
        rows.append((parts[0], float(parts[1])))
    return rows


def fred_latest(series_id, lookback_days=30):
    cosd = (datetime.now(KST) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    rows = fred_series(series_id, cosd)
    if not rows:
        warnings.append(f"FRED {series_id}: no data in lookback window")
        return None
    return rows[-1][1]


def fred_yoy(series_id, lookback_months=15):
    cosd = (datetime.now(KST) - timedelta(days=lookback_months * 31)).strftime("%Y-%m-%d")
    rows = fred_series(series_id, cosd)
    if len(rows) < 13:
        warnings.append(f"FRED {series_id}: not enough history for YoY calc")
        return None
    latest_date, latest_val = rows[-1]
    latest_ym = latest_date[:7]
    target_ym = f"{int(latest_ym[:4]) - 1}-{latest_ym[5:7]}"
    prior = next((v for d, v in rows if d[:7] == target_ym), None)
    if prior is None:
        warnings.append(f"FRED {series_id}: no matching prior-year month for YoY calc")
        return None
    return round((latest_val / prior - 1) * 100, 2)


def yahoo_price(symbol):
    text = http_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d")
    if not text:
        return None
    try:
        meta = json.loads(text)["chart"]["result"][0]["meta"]
        return meta["regularMarketPrice"]
    except (KeyError, IndexError, TypeError, ValueError):
        warnings.append(f"Yahoo {symbol}: unexpected response shape")
        return None


def ecos_latest(stat_code, item_code, cycle, lookback_periods):
    if not ECOS_KEY:
        warnings.append(f"ECOS {stat_code}/{item_code}: BOK_ECOS_API_KEY not set")
        return None
    now = datetime.now(KST)
    if cycle == "D":
        start = (now - timedelta(days=lookback_periods)).strftime("%Y%m%d")
        end = now.strftime("%Y%m%d")
    else:  # "M"
        start_dt = now.replace(day=1)
        for _ in range(lookback_periods):
            start_dt = (start_dt - timedelta(days=1)).replace(day=1)
        start = start_dt.strftime("%Y%m")
        end = now.strftime("%Y%m")
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_KEY}/json/kr/1/100/"
        f"{stat_code}/{cycle}/{start}/{end}/{item_code}"
    )
    text = http_get(url)
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        warnings.append(f"ECOS {stat_code}/{item_code}: invalid JSON response")
        return None
    if "StatisticSearch" not in data:
        msg = data.get("RESULT", {}).get("MESSAGE", text[:200])
        warnings.append(f"ECOS {stat_code}/{item_code}: {msg}")
        return None
    rows = data["StatisticSearch"]["row"]
    if not rows:
        warnings.append(f"ECOS {stat_code}/{item_code}: empty result")
        return None
    return rows[-1]["TIME"], float(rows[-1]["DATA_VALUE"])


def ecos_cpi_yoy(stat_code="901Y009", item_code="0"):
    result = ecos_latest(stat_code, item_code, "M", lookback_periods=15)
    if result is None:
        return None
    if not ECOS_KEY:
        return None
    now = datetime.now(KST)
    start_dt = now.replace(day=1)
    for _ in range(15):
        start_dt = (start_dt - timedelta(days=1)).replace(day=1)
    url = (
        f"https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_KEY}/json/kr/1/100/"
        f"{stat_code}/M/{start_dt.strftime('%Y%m')}/{now.strftime('%Y%m')}/{item_code}"
    )
    text = http_get(url)
    if not text:
        return None
    data = json.loads(text)
    rows = data.get("StatisticSearch", {}).get("row", [])
    if not rows:
        return None
    latest_time, latest_val = rows[-1]["TIME"], float(rows[-1]["DATA_VALUE"])
    target_ym = f"{int(latest_time[:4]) - 1}{latest_time[4:6]}"
    prior = next((float(r["DATA_VALUE"]) for r in rows if r["TIME"] == target_ym), None)
    if prior is None:
        warnings.append(f"ECOS {stat_code}: no matching prior-year month for CPI YoY")
        return None
    return round((latest_val / prior - 1) * 100, 2)


def load_last_row():
    if not os.path.exists(HISTORY_CSV):
        return None
    with open(HISTORY_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[-1] if rows else None


def upsert_row(row):
    rows = []
    if os.path.exists(HISTORY_CSV):
        with open(HISTORY_CSV, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    rows = [r for r in rows if r["date"] != row["date"]]
    rows.append(row)
    rows.sort(key=lambda r: r["date"])
    os.makedirs(os.path.dirname(HISTORY_CSV), exist_ok=True)
    with open(HISTORY_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_status(row, fetched_ok, carried_forward):
    lines = [
        "# 자동 수집 상태 (매 실행마다 덮어씀)",
        "",
        f"최근 실행: {row['updated_at']} (KST)",
        "",
        "| 필드 | 상태 |",
        "|---|---|",
    ]
    for f in FIELDS:
        if f in ("date", "updated_at"):
            continue
        if f in carried_forward:
            status = "이전 값 유지 (갱신 실패)"
        elif f in fetched_ok:
            status = "정상 갱신"
        else:
            status = "-"
        lines.append(f"| {f} | {status} |")
    if warnings:
        lines += ["", "## 경고", ""]
        lines += [f"- {w}" for w in warnings]
    with open(STATUS_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    now = datetime.now(KST)
    today = now.strftime("%Y-%m-%d")
    last_row = load_last_row()

    fetched = {}
    fetched["usdkrw"] = yahoo_price("KRW=X")
    fetched["us_2y"] = fred_latest("DGS2")
    fetched["us_10y"] = fred_latest("DGS10")
    fetched["dow"] = fred_latest("DJIA")
    fetched["sp500"] = fred_latest("SP500")
    fetched["nasdaq"] = fred_latest("NASDAQCOM")
    fetched["wti"] = yahoo_price("CL=F")
    fetched["us_cpi"] = fred_yoy("CPIAUCSL")
    fetched["us_m2"] = fred_latest("M2SL", lookback_days=120)

    upper = fred_latest("DFEDTARU", lookback_days=120)
    lower = fred_latest("DFEDTARL", lookback_days=120)
    fetched["us_rate"] = round((upper + lower) / 2, 4) if upper is not None and lower is not None else None

    fetched["kospi"] = yahoo_price("%5EKS11")
    fetched["kosdaq"] = yahoo_price("%5EKQ11")
    fetched["gold"] = yahoo_price("GC=F")

    kr_rate = ecos_latest("722Y001", "0101000", "D", lookback_periods=14)
    fetched["kr_rate"] = kr_rate[1] if kr_rate else None
    kr_2y = ecos_latest("817Y002", "010195000", "D", lookback_periods=14)
    fetched["kr_2y"] = kr_2y[1] if kr_2y else None
    kr_10y = ecos_latest("817Y002", "010210000", "D", lookback_periods=14)
    fetched["kr_10y"] = kr_10y[1] if kr_10y else None
    fetched["kr_cpi"] = ecos_cpi_yoy()
    kr_m2_raw = ecos_latest("161Y006", "BBHA00", "M", lookback_periods=6)
    fetched["kr_m2"] = round(kr_m2_raw[1] / 1000, 2) if kr_m2_raw else None  # 십억원 -> 조원

    row = {"date": today, "updated_at": now.isoformat(timespec="seconds")}
    fetched_ok, carried_forward = [], []
    for field in FIELDS:
        if field in ("date", "updated_at"):
            continue
        value = fetched.get(field)
        if value is not None:
            row[field] = value
            fetched_ok.append(field)
        elif last_row and last_row.get(field):
            row[field] = last_row[field]
            carried_forward.append(field)
        else:
            row[field] = ""

    upsert_row(row)
    with open(LATEST_JSON, "w", encoding="utf-8") as f:
        json.dump(row, f, ensure_ascii=False, indent=2)
    write_status(row, fetched_ok, carried_forward)

    print(f"date={today} fetched_ok={fetched_ok}")
    if carried_forward:
        print(f"carried_forward={carried_forward}")
    for w in warnings:
        print(f"WARNING: {w}")

    if len(fetched_ok) < 5:
        print("ERROR: fewer than 5 fields fetched successfully, likely a network/source outage")
        sys.exit(1)


if __name__ == "__main__":
    main()
