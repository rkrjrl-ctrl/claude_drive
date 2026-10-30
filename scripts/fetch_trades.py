"""아파트 매매·전월세 실거래가 수집기.

국토교통부 실거래가 API로 전국 시군구의 아파트 거래를 거래 월별로 받아
data/trades/apt_trade/YYYY-MM.csv, data/trades/apt_rent/YYYY-MM.csv 에 저장한다.

- 실거래 신고는 계약 후 30일 안에 하므로 최근 3개월은 매일 다시 받아 통째로 바꾼다.
- 처음엔 TRADES_BACKFILL_FROM(기본 2026-01)부터 거슬러 받는다.
- 시군구 코드는 행정안전부 법정동코드 API에서 받아 data/ref/lawd_sgg.csv 에 두고 30일마다 새로 받는다.
- 한 달치에서 실패한 시군구가 있으면 그 달 파일은 덮어쓰지 않는다 (빠진 채로 저장되는 것을 막음).

키: DATA_GO_KR_API_KEY (공공데이터포털)
"""

import csv
import io
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_housing import find_items, get_any, total_count  # noqa: E402

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
ROOT = Path(__file__).resolve().parent.parent / "data"
REF = ROOT / "ref" / "lawd_sgg.csv"
BACKFILL_FROM = os.environ.get("TRADES_BACKFILL_FROM", "2026-01")
RECENT_MONTHS = 3
WORKERS = 8

KINDS = {
    "apt_trade": {
        "label": "아파트 매매",
        "url": "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade",
        "cols": ["sggCd", "umdNm", "aptNm", "aptDong", "jibun", "excluUseAr", "floor", "buildYear",
                 "dealYear", "dealMonth", "dealDay", "dealAmount", "dealingGbn", "buyerGbn", "slerGbn",
                 "cdealType", "cdealDay", "rgstDate", "landLeaseholdGbn", "aptSeq"],
        "money": ["dealAmount"],
    },
    "apt_rent": {
        "label": "아파트 전월세",
        "url": "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent",
        "cols": ["sggCd", "umdNm", "aptNm", "jibun", "excluUseAr", "floor", "buildYear",
                 "dealYear", "dealMonth", "dealDay", "deposit", "monthlyRent", "contractTerm",
                 "contractType", "useRRRight", "preDeposit", "preMonthlyRent", "aptSeq"],
        "money": ["deposit", "monthlyRent", "preDeposit", "preMonthlyRent"],
    },
}


def log(msg):
    print(msg, flush=True)


# ---------- 시군구 코드 ----------

def load_sgg(key):
    if REF.exists():
        rows = list(csv.DictReader(open(REF, encoding="utf-8-sig")))
        # 파일 안에 받은 날을 적어 두고 그걸로 판단 (깃 체크아웃은 수정 시각을 바꾸므로)
        fetched = rows[0].get("fetched", "") if rows else ""
        if rows and fetched and (TODAY - date.fromisoformat(fetched)).days < 30:
            return rows
    url = "https://apis.data.go.kr/1741000/StanReginCd/getStanReginCdList"
    allrows, page = [], 1
    while True:
        res = get_any(url, {"serviceKey": key, "pageNo": page, "numOfRows": 1000, "type": "json"})
        rows = []
        for part in res.get("StanReginCd", []) if isinstance(res, dict) else []:
            if "row" in part:
                rows = part["row"]
        if not rows:
            rows = find_items(res) or []
        allrows += rows
        total = 0
        for part in res.get("StanReginCd", []) if isinstance(res, dict) else []:
            for h in part.get("head", []):
                if "totalCount" in h:
                    total = int(h["totalCount"])
        if not rows or page * 1000 >= total:
            break
        page += 1
    sgg = {}
    for r in allrows:
        if r.get("umd_cd") == "000" and r.get("ri_cd") == "00" and r.get("sgg_cd") not in ("000", None):
            code = str(r["region_cd"])[:5]
            sgg[code] = r.get("locatadd_nm", "").strip()
    # 구가 있는 시(예: 수원시 41110)는 빼고 구(41111 등)만 남긴다
    leaf = {c: n for c, n in sgg.items()
            if not (c.endswith("0") and any(o != c and o[:4] == c[:4] for o in sgg))}
    if len(leaf) < 200:
        raise RuntimeError(f"시군구 코드가 너무 적음 ({len(leaf)}개) — 법정동코드 응답 확인 필요")
    REF.parent.mkdir(parents=True, exist_ok=True)
    out = [{"code": c, "name": n, "fetched": TODAY.isoformat()} for c, n in sorted(leaf.items())]
    with open(REF, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["code", "name", "fetched"], lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    log(f"[시군구 코드] {len(out)}개 새로 받음")
    return out


# ---------- 실거래 ----------

class Stop(Exception):
    pass


def fetch_one(key, url, code, ym):
    rows, page = [], 1
    while True:
        try:
            res = get_any(url, {"serviceKey": key, "LAWD_CD": code, "DEAL_YMD": ym,
                                "numOfRows": 1000, "pageNo": page})
        except RuntimeError as e:
            if "22" in str(e)[:12] or "LIMITED" in str(e):
                raise Stop(f"하루 이용 한도 초과: {e}")
            raise
        items = find_items(res) or []
        rows += items
        total = total_count(res) or 0
        if not items or page * 1000 >= total:
            return rows
        page += 1


def clean_money(v):
    return str(v or "").replace(",", "").strip()


def month_list(folder):
    have = sorted(p.stem for p in folder.glob("????-??.csv")) if folder.exists() else []
    end = TODAY.replace(day=1)
    if have:
        start = end
        for _ in range(RECENT_MONTHS - 1):
            start = (start - timedelta(days=1)).replace(day=1)
        # 중간에 빠진 달이 있으면 그 달부터
        y, m = map(int, BACKFILL_FROM.split("-"))
        cur = date(y, m, 1)
        while cur < start:
            if cur.strftime("%Y-%m") not in have:
                start = cur
                break
            cur = (cur + timedelta(days=32)).replace(day=1)
    else:
        y, m = map(int, BACKFILL_FROM.split("-"))
        start = date(y, m, 1)
    months, cur = [], start
    while cur <= end:
        months.append(cur.strftime("%Y%m"))
        cur = (cur + timedelta(days=32)).replace(day=1)
    return months


def write_month(path, rows, cols):
    rows.sort(key=lambda r: tuple(str(r.get(c, "")) for c in ("sggCd", "umdNm", "aptNm", "dealDay", "excluUseAr", "floor")))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["지역"] + cols, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    text = buf.getvalue()
    old = path.read_text(encoding="utf-8-sig") if path.exists() else None
    if old == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(text)
    return True


def collect(key, kind, sgg, failures):
    spec = KINDS[kind]
    folder = ROOT / "trades" / kind
    names = {s["code"]: s["name"] for s in sgg}
    months = month_list(folder)
    log(f"[{spec['label']}] {months[0]}~{months[-1]} ({len(months)}개월 × 시군구 {len(sgg)}개)")
    for ym in months:
        rows, errs = [], []
        with ThreadPoolExecutor(WORKERS) as ex:
            futs = {ex.submit(fetch_one, key, spec["url"], s["code"], ym): s["code"] for s in sgg}
            for f in as_completed(futs):
                code = futs[f]
                try:
                    for it in f.result():
                        r = {c: str(it.get(c, "") or "").strip() for c in spec["cols"]}
                        for m in spec["money"]:
                            r[m] = clean_money(r[m])
                        r["sggCd"] = r["sggCd"] or code
                        r["지역"] = names.get(code, "")
                        rows.append(r)
                except Stop as e:
                    failures.append(f"{spec['label']}: {e}")
                    log(f"[멈춤] {e}")
                    ex.shutdown(cancel_futures=True)
                    return
                except Exception as e:
                    errs.append(f"{code}: {e}")
        label = f"{ym[:4]}-{ym[4:]}"
        if errs:
            failures.append(f"{spec['label']} {label}: 시군구 {len(errs)}곳 실패 (예: {errs[0][:120]})")
            log(f"[실패] {spec['label']} {label}: {len(errs)}곳 실패, 이 달 파일은 그대로 둠")
            if len(errs) == len(sgg):
                return  # 전부 실패면 API 자체 문제라 다음 달도 같음
            continue
        changed = write_month(folder / f"{label}.csv", rows, spec["cols"])
        log(f"[{spec['label']} {label}] {len(rows)}건 {'저장' if changed else '그대로'}")


def main():
    key = os.environ.get("DATA_GO_KR_API_KEY", "").strip()
    if not key:
        sys.exit("DATA_GO_KR_API_KEY 환경 변수가 없습니다.")
    only = set(filter(None, os.environ.get("TRADES_ONLY", "").split(",")))
    failures = []
    try:
        sgg = load_sgg(key)
    except Exception as e:
        sys.exit(f"시군구 코드를 못 받았습니다: {e}")
    t0 = time.time()
    for kind in KINDS:
        if not only or kind in only:
            collect(key, kind, sgg, failures)
    log(f"\n걸린 시간 {time.time() - t0:.0f}초")
    if failures:
        log("실패한 항목:\n- " + "\n- ".join(failures))
        sys.exit(1)
    log("모두 정상")


if __name__ == "__main__":
    main()
