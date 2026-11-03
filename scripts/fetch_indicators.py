#!/usr/bin/env python3
"""Daily fetch of 30 KR/US economic indicators into data/history.csv.

Sources (no scraping, all official/stable APIs):
  - FRED (fredgraph.csv, no key): US rate target, US CPI, US M2, US 2y yield, US unemployment,
    US high-yield credit spread.
  - Yahoo Finance chart API (no key): US 10y yield, Dow, S&P 500, Nasdaq, WTI, KOSPI,
    KOSDAQ, gold, VIX, US dollar index (DXY), copper.
  - Bank of Korea ECOS (needs BOK_ECOS_API_KEY): USD/KRW, EUR/KRW, JPY(100)/KRW, CNY/KRW
    (Seoul market base rates, 731Y001; full history backfilled once per field),
    KR base rate, KR 2y/10y bond yield, KR CPI, KR M2.
  - Korea Real Estate Board R-ONE Open API (needs REB_API_KEY): nationwide/Seoul weekly
    apartment sale price index, plus nationwide/Seoul monthly average/median apartment
    sale price per square meter (actual won amounts, not an index).
  - us_yield_spread is computed locally (us_10y - us_2y), not fetched.

If a field can't be fetched, the previous value is carried forward and a warning is recorded
in data/STATUS.md so staleness is visible instead of silently frozen.
"""
import csv
import http.client
import json
import os
import sys
import time
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
    "kr_m2", "us_m2", "vix", "dxy", "copper", "us_unemployment", "us_hy_spread",
    "us_yield_spread", "kr_apt_price_national", "kr_apt_price_seoul",
    "kr_apt_avgprice_national", "kr_apt_avgprice_seoul",
    "kr_apt_medprice_national", "kr_apt_medprice_seoul", "soybean", "corn", "wheat", "silver", "shanghai", "hsi", "nikkei", "dax", "ftse", "cac", "stoxx50", "sensex", "nifty",
    "eurkrw", "jpykrw", "cnykrw", "updated_at",
]

ECOS_KEY = os.environ.get("BOK_ECOS_API_KEY", "").strip()
REB_KEY = os.environ.get("REB_API_KEY", "").strip()
UA = "Mozilla/5.0 (compatible; econ-indicator-bot/1.0)"

warnings = []


def http_get(url, headers=None, retries=1):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                http.client.HTTPException, OSError) as e:
            if attempt < retries:
                time.sleep(2)
                continue
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


LONG_TERM_DIR = os.path.join(ROOT, "data", "long_term")

# Daily market series fetched as dated bars (true trade dates), not just "latest price".
# history.csv used to stamp whatever the latest price was with the *collection* date, so a
# row could hold the previous session's close under today's date (and which one depended on
# the time of day the job ran). Dated bars fix that: every daily value is stored under the
# date the market actually closed at that level.
YAHOO_DAILY = {
    "us_10y": "%5ETNX", "dow": "%5EDJI", "sp500": "%5EGSPC",
    "nasdaq": "%5EIXIC", "wti": "CL=F", "kospi": "%5EKS11", "kosdaq": "%5EKQ11",
    "gold": "GC=F", "vix": "%5EVIX", "dxy": "DX-Y.NYB", "copper": "HG=F",
    "soybean": "ZS=F", "corn": "ZC=F", "wheat": "ZW=F", "silver": "SI=F",
    "shanghai": "000001.SS", "hsi": "%5EHSI", "nikkei": "%5EN225", "dax": "%5EGDAXI",
    "ftse": "%5EFTSE", "cac": "%5EFCHI", "stoxx50": "%5ESTOXX50E",
    "sensex": "%5EBSESN", "nifty": "%5ENSEI",
}
THREE_DECIMALS = {"us_10y", "us_2y", "kr_2y", "kr_10y", "us_yield_spread", "copper", "dxy", "us_hy_spread"}


def fmt_value(field, value):
    return round(value, 3 if field in THREE_DECIMALS else 2)


def yahoo_daily(symbol, rng="3mo"):
    """Daily closes as [(YYYY-MM-DD in the exchange's local time, close)], oldest first."""
    text = http_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range={rng}")
    if not text:
        return []
    try:
        res = json.loads(text)["chart"]["result"][0]
        stamps = res["timestamp"]
        closes = res["indicators"]["quote"][0]["close"]
        offset = res["meta"].get("gmtoffset", 0)
    except (KeyError, IndexError, TypeError, ValueError):
        warnings.append(f"Yahoo {symbol}: unexpected daily response shape")
        return []
    rows = {}
    for ts, close in zip(stamps, closes):
        if close is None:
            continue
        day = datetime.fromtimestamp(ts + offset, timezone.utc).strftime("%Y-%m-%d")
        rows[day] = close
    return sorted(rows.items())


ECOS_PAGE = 1000


def ecos_daily(stat_code, item_code, lookback_days=120, start=None):
    """Daily ECOS series as [(YYYY-MM-DD, value)], oldest first. `start` (YYYYMMDD) fetches
    from that date instead of the lookback window, paging ECOS_PAGE rows per request."""
    if not ECOS_KEY:
        return []
    now = datetime.now(KST)
    start = start or (now - timedelta(days=lookback_days)).strftime("%Y%m%d")
    out, first = [], 1
    while True:
        url = (
            f"https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_KEY}/json/kr/{first}/{first + ECOS_PAGE - 1}/"
            f"{stat_code}/D/{start}/{now.strftime('%Y%m%d')}/{item_code}"
        )
        text = http_get(url)
        if not text:
            return [] if first == 1 else None  # a failed later page must not leave a half history
        try:
            block = json.loads(text)["StatisticSearch"]
            rows, total = block["row"], int(block.get("list_total_count", 0))
        except (ValueError, KeyError, TypeError):
            if first == 1:
                warnings.append(f"ECOS {stat_code}/{item_code}: no daily rows")
                return []
            return None
        for r in rows:
            t = r.get("TIME", "")
            try:
                out.append((f"{t[:4]}-{t[4:6]}-{t[6:8]}", float(r["DATA_VALUE"])))
            except (ValueError, KeyError):
                continue
        first += ECOS_PAGE
        if first > total or not rows:
            break
    return sorted(out)


def last_on_or_before(series, day):
    """Latest value in a dated series with date <= day (None if the series starts later)."""
    value = None
    for d, v in series:
        if d > day:
            break
        value = v
    return value


ECOS_FX = {  # field -> (ECOS 731Y001 item, first date ECOS has)
    "usdkrw": ("0000001", "19640504"),
    "jpykrw": ("0000002", "19770401"),  # 원/100엔
    "eurkrw": ("0000003", "19940411"),
    "cnykrw": ("0000053", "20160104"),
}
FX_MARK = os.path.join(LONG_TERM_DIR, "_ecos_fx.json")


def fx_backfilled():
    try:
        with open(FX_MARK, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def fx_backfill_problem(field, full):
    """Why a fetched full history must not replace the long-term file (None if it is fine)."""
    if not full:
        return "받은 값 없음(중간 페이지 실패 포함)"
    if len(full) < 1000:
        return f"값이 {len(full)}건뿐"
    if any(v <= 0 for _, v in full):
        return "0 이하 값 포함"
    path = os.path.join(LONG_TERM_DIR, f"{field}.csv")
    if not os.path.exists(path):
        return None  # 새 통화는 비교할 기존 값이 없다
    with open(path, encoding="utf-8") as f:
        old = {}
        for ln in f.read().splitlines()[1:]:
            d, _, v = ln.partition(",")
            try:
                old[d] = float(v)
            except ValueError:
                continue
    cutoff = (datetime.now(KST) - timedelta(days=365)).strftime("%Y-%m-%d")
    diffs = sorted(abs(v / old[d] - 1) for d, v in full if d >= cutoff and old.get(d))
    if len(diffs) < 100:
        return f"기존 값과 겹치는 날이 {len(diffs)}일뿐"
    median = diffs[len(diffs) // 2]
    if median > 0.03:
        return f"기존 값과 중간값 {median:.1%} 차이"
    if diffs[-1] > 0.10:
        return f"기존 값과 가장 큰 차이 {diffs[-1]:.1%}"
    return None


def write_long_term_full(field, series):
    """Replace data/long_term/<field>.csv with a whole series (one-time source switch)."""
    path = os.path.join(LONG_TERM_DIR, f"{field}.csv")
    lines = [f"date,{field}"] + [f"{d},{fmt_value(field, v)}" for d, v in series]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


def update_long_term(field, series):
    """Keep data/long_term/<field>.csv current with true-dated values: rows from the first
    fetched date onward are replaced by the fetched ones, older history is left untouched."""
    path = os.path.join(LONG_TERM_DIR, f"{field}.csv")
    if not series or not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    if not lines:
        return
    first = series[0][0]
    kept = [ln for ln in lines[1:] if ln and ln.split(",")[0] < first]
    new = [f"{d},{fmt_value(field, v)}" for d, v in series]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join([lines[0]] + kept + new) + "\n")


def redate_history(daily):
    """Rewrite the daily-market columns of recent history.csv rows so each row holds the
    close of its own date (or the last close before it, on that market's holidays). Today's
    row is provisional until the next run, when today's bar is final."""
    if not daily or not os.path.exists(HISTORY_CSV):
        return
    with open(HISTORY_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for field, series in daily.items():
            if not series or r["date"] < series[0][0]:
                continue
            v = last_on_or_before(series, r["date"])
            if v is not None:
                r[field] = fmt_value(field, v)
    with open(HISTORY_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


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


def reb_latest(statbl_id, cls_id, cycle):
    """Korea Real Estate Board R-ONE: fetches the full (small) series for one statistics
    table + region/classification code and takes the last point -- simpler and more robust
    than guessing a page offset for "latest"."""
    if not REB_KEY:
        warnings.append(f"REB {statbl_id}/{cls_id}: REB_API_KEY not set")
        return None
    url = (
        f"https://www.reb.or.kr/r-one/openapi/SttsApiTblData.do?KEY={REB_KEY}&Type=json"
        f"&pIndex=1&pSize=1000&STATBL_ID={statbl_id}&DTACYCLE_CD={cycle}&CLS_ID={cls_id}"
    )
    text = http_get(url)
    if not text:
        return None
    try:
        data = json.loads(text)
        rows = data["SttsApiTblData"][1]["row"]
    except (ValueError, KeyError, IndexError):
        msg = text[:200]
        try:
            msg = json.loads(text).get("RESULT", {}).get("MESSAGE", msg)
        except ValueError:
            pass
        warnings.append(f"REB {statbl_id}/{cls_id}: {msg}")
        return None
    if not rows:
        warnings.append(f"REB {statbl_id}/{cls_id}: empty result")
        return None
    rows.sort(key=lambda r: r["WRTTIME_DESC"])
    return rows[-1]["DTA_VAL"]


def reb_weekly_latest(cls_id, statbl_id="T244183132827305"):
    """Weekly apartment sale price index, nationwide (CLS_ID 50001) or Seoul (50008)."""
    return reb_latest(statbl_id, cls_id, "WK")


def reb_monthly_price_latest(cls_id, statbl_id):
    """Monthly average/median apartment sale price per sqm (won, not an index),
    nationwide (CLS_ID 500001) or Seoul (500004). statbl_id A_2024_00188=average,
    A_2024_00189=median."""
    return reb_latest(statbl_id, cls_id, "MM")


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
    daily = {}  # field -> [(true trade date, value)], used to date history/long_term correctly
    for field, symbol in YAHOO_DAILY.items():
        series = yahoo_daily(symbol)
        if series:
            daily[field] = series
            fetched[field] = fmt_value(field, series[-1][1])
        else:
            fetched[field] = yahoo_price(symbol)  # fallback: undated latest price
    us_2y_rows = fred_series("DGS2", (now - timedelta(days=120)).strftime("%Y-%m-%d"))
    if us_2y_rows:
        daily["us_2y"] = us_2y_rows
        fetched["us_2y"] = us_2y_rows[-1][1]
    else:
        fetched["us_2y"] = fred_latest("DGS2")
    fetched["us_cpi"] = fred_yoy("CPIAUCSL")
    fetched["us_m2"] = fred_latest("M2SL", lookback_days=120)

    upper = fred_latest("DFEDTARU", lookback_days=120)
    lower = fred_latest("DFEDTARL", lookback_days=120)
    fetched["us_rate"] = round((upper + lower) / 2, 4) if upper is not None and lower is not None else None

    fetched["us_unemployment"] = fred_latest("UNRATE", lookback_days=120)
    hy_rows = fred_series("BAMLH0A0HYM2", (now - timedelta(days=120)).strftime("%Y-%m-%d"))
    if hy_rows:
        daily["us_hy_spread"] = hy_rows
        fetched["us_hy_spread"] = hy_rows[-1][1]
    else:
        fetched["us_hy_spread"] = fred_latest("BAMLH0A0HYM2", lookback_days=30)
    if fetched.get("us_10y") is not None and fetched.get("us_2y") is not None:
        fetched["us_yield_spread"] = round(fetched["us_10y"] - fetched["us_2y"], 3)
    if "us_10y" in daily and "us_2y" in daily:
        two = dict(daily["us_2y"])
        spread = [(d, round(v - two[d], 3)) for d, v in daily["us_10y"] if d in two]
        if spread:
            daily["us_yield_spread"] = spread

    # 환율은 한국은행 ECOS 매매기준율(서울외환시장 기준, 2026-11-04 변경). Yahoo KRW=X는 해외 시세라
    # 국내 고시와 몇 원씩 달랐다. 통화마다 처음 한 번은 ECOS 전체 기간을 받아 장기 파일을 통째로 바꾸고
    # (_ecos_fx.json에 기록), 그 뒤로는 최근 120일만 받는다. 원/달러는 ECOS가 비면 Yahoo로 대신 받는다.
    # 안전장치(11/4 사용자 「한번에 받으면 위험하지 않아?」): 한 번 돌 때 통화 하나만 채우고, 받은 값이
    # 적거나 기존 장기 파일의 최근 1년 값과 3% 넘게 다르면 덮어쓰지 않고 경고만 남긴다.
    done = fx_backfilled()
    backfill_used = False
    for field, (item, first_day) in ECOS_FX.items():
        full = None
        if ECOS_KEY and field not in done and not backfill_used:
            backfill_used = True
            full = ecos_daily("731Y001", item, start=first_day)
            problem = fx_backfill_problem(field, full)
            if problem:
                warnings.append(f"ECOS 731Y001/{item}: {field} 과거 값 채우기 보류({problem}), 다음에 다시 시도")
                full = None
            else:
                write_long_term_full(field, full)
                done[field] = now.strftime("%Y-%m-%d")
                print(f"{field}: ECOS full history {full[0][0]}~{full[-1][0]} ({len(full)} rows)")
        series = full or ecos_daily("731Y001", item)
        if series:
            daily[field] = series
            fetched[field] = series[-1][1]
        elif field == "usdkrw":
            warnings.append("ECOS 731Y001/0000001: usdkrw fell back to Yahoo KRW=X")
            ys = yahoo_daily("KRW=X")
            if ys:
                daily[field] = ys
                fetched[field] = fmt_value(field, ys[-1][1])
            else:
                fetched[field] = yahoo_price("KRW=X")
    if done != fx_backfilled():
        with open(FX_MARK, "w", encoding="utf-8") as f:
            json.dump(done, f, ensure_ascii=False, indent=1)

    kr_rate = ecos_latest("722Y001", "0101000", "D", lookback_periods=14)
    fetched["kr_rate"] = kr_rate[1] if kr_rate else None
    for field, item in (("kr_2y", "010195000"), ("kr_10y", "010210000")):
        series = ecos_daily("817Y002", item)
        if series:
            daily[field] = series
            fetched[field] = series[-1][1]
        else:
            latest = ecos_latest("817Y002", item, "D", lookback_periods=14)
            fetched[field] = latest[1] if latest else None
    fetched["kr_cpi"] = ecos_cpi_yoy()
    kr_m2_raw = ecos_latest("161Y006", "BBHA00", "M", lookback_periods=6)
    fetched["kr_m2"] = round(kr_m2_raw[1] / 1000, 2) if kr_m2_raw else None  # 십억원 -> 조원

    fetched["kr_apt_price_national"] = reb_weekly_latest("50001")
    fetched["kr_apt_price_seoul"] = reb_weekly_latest("50008")
    fetched["kr_apt_avgprice_national"] = reb_monthly_price_latest("500001", "A_2024_00188")
    fetched["kr_apt_avgprice_seoul"] = reb_monthly_price_latest("500004", "A_2024_00188")
    fetched["kr_apt_medprice_national"] = reb_monthly_price_latest("500001", "A_2024_00189")
    fetched["kr_apt_medprice_seoul"] = reb_monthly_price_latest("500004", "A_2024_00189")

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
    # Put every daily value under its true trade date: recent history rows and long_term files.
    redate_history(daily)
    for field, series in daily.items():
        update_long_term(field, series)
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
