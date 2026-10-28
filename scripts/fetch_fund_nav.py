"""펀드 일별 기준가를 금융투자협회 전자공시(공개)에서 받아 data/fund_nav.csv에 쌓는다(2026-10-28 추가).

휴휴 펀드 대시보드가 엑셀을 못 받은 날의 펀드 평가액을 실제 기준가로 채울 때 쓴다.
대상 펀드는 data/fund_codes.json(협회 표준코드 → 펀드 이름). 대시보드 폴더의 kofia_map.py가 새 펀드를 넣는다.
파일 형식은 date,code,nav,chg(펀드가 늘어도 열이 바뀌지 않게). chg는 협회의 전일대비 금액으로, 결산일에 기준가가
1,000원으로 되돌아가도 그날의 실제 등락을 담고 있다. 기간 수익률은 nav/(nav-chg)를 날마다 곱해 구한다.

python scripts/fetch_fund_nav.py            매일: 펀드마다 마지막 날짜 다음부터 오늘까지
python scripts/fetch_fund_nav.py 20240101   처음 한 번: 그 날짜부터 전부(새 펀드도 이 날짜부터)
"""
import csv, json, os, re, sys, time, urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODES = os.path.join(ROOT, "data", "fund_codes.json")
OUT = os.path.join(ROOT, "data", "fund_nav.csv")
URL = "https://dis.kofia.or.kr/proframeWeb/XMLSERVICES/"
DEFAULT_START = "20240101"


def navs(std, frm, to):
    x = ('<?xml version="1.0" encoding="utf-8"?><message><proframeHeader><pfmAppName>FS-DIS2</pfmAppName>'
         '<pfmSvcName>DISFundStdPrcStutSO</pfmSvcName><pfmFnName>select</pfmFnName></proframeHeader><systemHeader></systemHeader>'
         f'<DISCondFuncDTO><tmpV30>{frm}</tmpV30><tmpV31>{to}</tmpV31><tmpV10>0</tmpV10><tmpV12>{std}</tmpV12></DISCondFuncDTO></message>')
    req = urllib.request.Request(URL, data=x.encode("utf-8"), headers={
        "Content-Type": "text/xml; charset=utf-8", "User-Agent": "Mozilla/5.0", "Referer": "https://dis.kofia.or.kr/"})
    for i in range(5):  # 응답이 중간에 끊기면 다시
        try:
            body = urllib.request.urlopen(req, timeout=60).read().decode("utf-8")
            break
        except Exception:
            if i == 4:
                raise
            time.sleep(2 + i * 3)
    return {f"{d[:4]}-{d[4:6]}-{d[6:]}": (v, c) for d, v, c in re.findall(r"<tmpV1>(\d{8})</tmpV1>\s*<tmpV2>([\d.]+)</tmpV2>\s*(?:<tmpV3>([-\d.]*)</tmpV3>|<tmpV3\s*/>)", body)}


def backfill(rows, path):
    """data/fund_backfill.json({표준코드: YYYYMMDD})의 펀드만, 그 날짜부터 지금 기록의 첫날 전까지 과거 기준가를 더 받는다.
    휴휴 대시보드가 고객의 첫 매수일부터 수익률을 보려고 쓴다(2026-10-29). 이미 받았으면 건너뜀."""
    want = json.load(open(path, encoding="utf-8"))
    first = {}
    for d, c in rows:
        if c not in first or d < first[c]:
            first[c] = d
    for std, frm in want.items():
        end = (datetime.strptime(first[std], "%Y-%m-%d") - timedelta(days=1)).strftime("%Y%m%d") if std in first else datetime.now().strftime("%Y%m%d")
        if frm > end:
            continue
        a = datetime.strptime(frm, "%Y%m%d")
        while a.strftime("%Y%m%d") <= end:
            b = min(a + timedelta(days=30), datetime.strptime(end, "%Y%m%d"))
            for d, v in navs(std, a.strftime("%Y%m%d"), b.strftime("%Y%m%d")).items():
                rows[(d, std)] = v
            a = b + timedelta(days=1)
            time.sleep(0.3)
        print("과거 기준가:", std, frm, "~", end)


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--backfill":
        rows = {}
        with open(OUT, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[(r["date"], r["code"])] = (r["nav"], r.get("chg") or "")
        backfill(rows, sys.argv[2])
        with open(OUT, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["date", "code", "nav", "chg"])
            for d, c in sorted(rows, key=lambda k: (k[1], k[0])):
                w.writerow([d, c, *rows[(d, c)]])
        print(f"fund_nav.csv: {len(rows)}줄")
        return
    start = sys.argv[1] if len(sys.argv) > 1 else None
    codes = json.load(open(CODES, encoding="utf-8"))
    rows = {}  # (date, code) -> nav
    if os.path.exists(OUT):
        with open(OUT, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[(r["date"], r["code"])] = (r["nav"], r.get("chg") or "")
    last = {}
    for d, c in rows:
        if d > last.get(c, ""):
            last[c] = d
    today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d")
    got, failed = 0, []
    for code in codes:
        frm = start or ((datetime.strptime(last[code], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d") if code in last else DEFAULT_START)
        if frm > today:
            continue
        try:
            a = datetime.strptime(frm, "%Y%m%d")
            while a.strftime("%Y%m%d") <= today:  # 응답이 커지면 자주 끊겨서 한 달씩 나눠 받는다
                b = min(a + timedelta(days=30), datetime.strptime(today, "%Y%m%d"))
                for d, v in navs(code, a.strftime("%Y%m%d"), b.strftime("%Y%m%d")).items():
                    rows[(d, code)] = v
                a = b + timedelta(days=1)
                time.sleep(0.3)
            got += 1
        except Exception as e:  # 한 펀드가 실패해도 나머지는 저장
            failed.append(f"{code}: {e}")
        time.sleep(0.3)  # 공시 서버에 부담 주지 않게
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "code", "nav", "chg"])
        for d, c in sorted(rows, key=lambda k: (k[1], k[0])):
            w.writerow([d, c, *rows[(d, c)]])
    print(f"fund_nav.csv: 펀드 {len(codes)}개 중 {got}개 받음, {len(rows)}줄, 마지막 {max(d for d, _ in rows) if rows else '-'}")
    if failed:
        print("실패:", "; ".join(failed))
    # 기준가가 오래 안 들어온 펀드(청산·이관 또는 수집 실패). 전체 최신일보다 7일 넘게 뒤처진 것
    last = {}
    for d, c in rows:
        if d > last.get(c, ""):
            last[c] = d
    newest = max(last.values()) if last else ""
    old = lambda d: (datetime.strptime(newest, "%Y-%m-%d") - datetime.strptime(d, "%Y-%m-%d")).days > 7
    stale = sorted(f"{c} {codes.get(c, '')[:30]} (마지막 {last.get(c, '없음')})" for c in codes if c not in last or old(last[c]))
    import status_note
    status_note.append("펀드 기준가 (fund_nav.csv, 금융투자협회)", [
        f"- 마지막 날짜: {newest or '-'} / 펀드 {len(codes)}개, {len(rows)}줄",
        f"- 이번에 받기 실패: {len(failed)}개" + (" — " + "; ".join(f[:80] for f in failed[:10]) if failed else ""),
        f"- 7일 넘게 기준가가 없는 펀드: {len(stale)}개" + ("".join(f"\n  - {x}" for x in stale[:20]) if stale else ""),
    ])


if __name__ == "__main__":
    main()
