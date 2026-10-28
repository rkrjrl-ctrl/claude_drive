"""섹터·지역·자산배분 ETF 일별 종가를 data/sector_daily.csv에 쌓는다(2026-10-28 추가).

휴휴 펀드 대시보드가 엑셀을 못 받은 날을 추정할 때, 고객 펀드마다 비슷하게 움직이는 지표로 쓴다
(펀드 기준가를 직접 긁으면 FunETF가 보안 확인으로 막아서, 공개 시세로 대신한다).
경제 대시보드(history.csv)와 섞지 않으려고 파일을 따로 둔다.

python scripts/fetch_sector.py          매일: 최근 1개월만 받아 합친다
python scripts/fetch_sector.py 2y       처음 한 번: 과거 2년치를 받아 둔다
"""
import csv, json, os, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "sector_daily.csv")

# 열 이름 → 야후 심볼. 분배금 반영 가격(adjclose)을 쓴다(펀드 기준가도 분배금을 재투자한 값이라서).
SYMBOLS = {
    "sox": "^SOX",    # 필라델피아 반도체
    "ita": "ITA",     # 미국 항공우주·방산
    "driv": "DRIV",   # 전기차·자율주행
    "ixn": "IXN",     # 글로벌 기술주
    "reet": "REET",   # 글로벌 리츠
    "gdx": "GDX",     # 금광주
    "ewz": "EWZ",     # 브라질
    "smin": "SMIN",   # 인도 중소형
    "mchi": "MCHI",   # 중국
    "ashr": "ASHR",   # 중국 본토 A주(CSI300)
    "vug": "VUG",     # 미국 대형 성장주
    "acwi": "ACWI",   # 글로벌 주식
    "acwv": "ACWV",   # 글로벌 저변동성
    "hyg": "HYG",     # 미국 하이일드 채권
    "bndx": "BNDX",   # 글로벌 채권(달러 환헤지)
    "aoa": "AOA",     # 자산배분 주식 80%
    "aor": "AOR",     # 자산배분 주식 60%
    "aom": "AOM",     # 자산배분 주식 40%
}


def fetch(symbol, rng):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(symbol)}?interval=1d&range={rng}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)["chart"]["result"][0]
    off = res["meta"].get("gmtoffset", 0)
    ts = res.get("timestamp") or []
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or res["indicators"]["quote"][0]["close"]
    out = {}
    for t, v in zip(ts, adj):
        if v is not None and v > 0:
            out[datetime.fromtimestamp(t + off, tz=timezone.utc).strftime("%Y-%m-%d")] = round(v, 4)
    return out


def main():
    rng = sys.argv[1] if len(sys.argv) > 1 else "1mo"
    rows = {}
    if os.path.exists(OUT):
        with open(OUT, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r["date"]] = {k: v for k, v in r.items() if k != "date" and v != ""}
    failed = []
    for col, sym in SYMBOLS.items():
        try:
            for d, v in fetch(sym, rng).items():
                rows.setdefault(d, {})[col] = v
        except Exception as e:  # 한 종목이 실패해도 나머지는 저장
            failed.append(f"{sym}: {e}")
        time.sleep(0.5)
    cols = list(SYMBOLS)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date"] + cols)
        for d in sorted(rows):
            w.writerow([d] + [rows[d].get(c, "") for c in cols])
    print(f"sector_daily.csv: {len(rows)}일, 마지막 {max(rows) if rows else '-'}")
    if failed:
        print("실패:", "; ".join(failed))
    import status_note
    status_note.append("섹터·지역 ETF (sector_daily.csv)", [
        f"- 마지막 날짜: {max(rows) if rows else '-'}",
        f"- 종목 {len(SYMBOLS)}개 중 실패 {len(failed)}개" + (": " + "; ".join(f[:80] for f in failed) if failed else ""),
    ])


if __name__ == "__main__":
    main()
