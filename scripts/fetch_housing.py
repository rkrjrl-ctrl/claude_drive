"""청약·LH 공고·공공서비스(지원 제도) 수집기.

공공데이터포털 키 하나(DATA_GO_KR_API_KEY)로 아래를 받아 data/housing/ 에 쌓는다.

- 청약홈 분양정보: APT·무순위·오피스텔/도시형·공공지원민간임대·임의공급 공고
- 청약홈 주택형별 분양가 (APT), 청약 경쟁률·당첨 가점 (APT)
- LH 분양·임대 공고
- 행정안전부 대한민국 공공서비스(혜택) 목록 (청년·주거 지원 제도 포함)
- 국토부 마이홈포털 공공임대·공공분양 모집공고 (SH·GH·지방공사 공고 포함)
- 서울시 청년안심주택 모집공고 (키 없이, 사이트가 쓰는 목록 데이터)

공고는 공고 번호별로 한 줄씩 남기고, 내용이 바뀌면 그 줄을 고친다.
처음 본 날(first_seen)과 내용이 마지막으로 바뀐 날(last_changed)을 함께 적는다.
그대로인 줄은 손대지 않아 매일 기록이 불필요하게 커지지 않는다.
한 API가 실패해도 나머지는 계속 받고, 실패한 것은 마지막에 모아 알린다.
"""

import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).date()
DATA = Path(__file__).resolve().parent.parent / "data" / "housing"

# 매일 다시 받는 기간 (정정 공고·경쟁률·가점 발표를 따라잡기 위해)
WINDOW_DAYS = 60
# 파일이 처음 생길 때 거슬러 받는 시작일
BACKFILL_FROM = os.environ.get("HOUSING_BACKFILL_FROM", "2026-01-01")

ODCLOUD = "https://api.odcloud.kr/api/"
APPLYHOME = "ApplyhomeInfoDetailSvc/v1/"
CMPET = "ApplyhomeInfoCmpetRtSvc/v1/"

# 청약홈 공고 종류: (저장 이름, 경로, 설명)
NOTICE_KINDS = [
    ("apt", "getAPTLttotPblancDetail", "APT 분양"),
    ("remndr", "getRemndrLttotPblancDetail", "APT 무순위·잔여세대"),
    ("urbty", "getUrbtyOfctlLttotPblancDetail", "오피스텔·도시형·민간임대"),
    ("pblpvt", "getPblPvtRentLttotPblancDetail", "공공지원 민간임대"),
    ("opt", "getOPTLttotPblancDetail", "임의공급"),
]

failures = []

META = ("first_seen", "last_changed", "last_seen")
# 매일 바뀌어 기록만 키우는 열
VOLATILE = {"조회수", "inqireCo"}


def log(msg):
    print(msg, flush=True)


def get(url, params, as_json=True):
    qs = urllib.parse.urlencode(params, safe=":[]")
    req = urllib.request.Request(url + "?" + qs, headers={"User-Agent": "claude_drive housing (python urllib)"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode("utf-8")
            return json.loads(body) if as_json else body
        except urllib.error.HTTPError as e:
            # 4xx 는 다시 해도 같으니 바로 넘긴다
            if 400 <= e.code < 500:
                detail = e.read().decode("utf-8", "replace")[:200]
                raise RuntimeError(f"HTTP {e.code}: {detail}")
            last = e
        except Exception as e:
            last = e
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"요청 실패: {last}")


# ---------- 저장 ----------

def load(path):
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        # 예전 형식(last_seen) 정리
        old_seen = r.pop("last_seen", None)
        if "last_changed" not in r or not r["last_changed"]:
            r["last_changed"] = r.get("first_seen") or old_seen or ""
        for c in VOLATILE:
            r.pop(c, None)
    return rows


def save(path, rows, sort_keys):
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    # 보기 좋게: 날짜 열은 맨 뒤
    for c in ("first_seen", "last_changed"):
        if c in cols:
            cols.remove(c)
            cols.append(c)
    rows = sorted(rows, key=lambda r: tuple(str(r.get(k, "")) for k in sort_keys), reverse=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in cols})
    # newline="" : 윈도우에서도 줄바꿈을 바꾸지 않게
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(buf.getvalue())


def clean(row):
    return {k: ("" if v is None else str(v).replace("\r\n", "\n").strip())
            for k, v in row.items() if k not in VOLATILE}


def body(row):
    return {k: v for k, v in row.items() if k not in META and v}


def upsert(path, new_rows, keys, sort_keys):
    """keys 가 같은 줄은 고치고, 없던 줄은 더한다. 지난 줄은 지우지 않는다."""
    today = TODAY.isoformat()
    old = {tuple(r.get(k, "") for k in keys): r for r in load(path)}
    added = changed = 0
    for r in map(clean, new_rows):
        key = tuple(r.get(k, "") for k in keys)
        if not all(key):
            continue
        prev = old.get(key)
        if prev is None:
            r["first_seen"] = r["last_changed"] = today
            added += 1
        elif body(prev) != body(r):
            r["first_seen"] = prev.get("first_seen") or today
            r["last_changed"] = today
            changed += 1
        else:
            continue  # 그대로면 예전 줄을 그대로 둔다
        old[key] = r
    save(path, list(old.values()), sort_keys)
    return added, changed


def replace_children(path, parent_key, parent_ids, new_rows, sort_keys):
    """부모 공고 단위로 통째로 바꾼다 (주택형·경쟁률처럼 공고 아래 여러 줄인 자료)."""
    today = TODAY.isoformat()
    parent_ids = set(parent_ids)
    old = load(path)
    by_parent = {}
    for r in old:
        by_parent.setdefault(r.get(parent_key, ""), []).append(r)
    new_by_parent = {}
    for r in map(clean, new_rows):
        new_by_parent.setdefault(r.get(parent_key, ""), []).append(r)

    def sig(rows):
        return sorted(json.dumps(body(r), sort_keys=True, ensure_ascii=False) for r in rows)

    out = [r for r in old if r.get(parent_key, "") not in parent_ids]
    n = 0
    for pid in parent_ids:
        prev, cur = by_parent.get(pid, []), new_by_parent.get(pid, [])
        if sig(prev) == sig(cur):
            out += prev  # 그대로면 손대지 않는다
            continue
        first = prev[0].get("first_seen") if prev else today
        for r in cur:
            r["first_seen"] = first or today
            r["last_changed"] = today
        out += cur
        n += 1
    save(path, out, sort_keys)
    return n


# ---------- 청약홈 (odcloud) ----------

def odcloud_all(key, path, cond=None, per_page=500):
    rows, page = [], 1
    while True:
        params = {"page": page, "perPage": per_page, "returnType": "JSON", "serviceKey": key}
        for k, v in (cond or {}).items():
            params[f"cond[{k}]"] = v
        res = get(ODCLOUD + path, params)
        data = res.get("data") or []
        rows += data
        total = int(res.get("matchCount") or res.get("totalCount") or 0)
        if not data or page * per_page >= total:
            return rows
        page += 1


def collect_applyhome(key):
    apt_ids = []
    for name, ep, label in NOTICE_KINDS:
        path = DATA / f"applyhome_{name}.csv"
        since = (TODAY - timedelta(days=WINDOW_DAYS)).isoformat() if path.exists() else BACKFILL_FROM
        try:
            rows = odcloud_all(key, APPLYHOME + ep, {"RCRIT_PBLANC_DE::GTE": since})
        except Exception as e:
            failures.append(f"청약홈 {label}: {e}")
            log(f"[실패] 청약홈 {label}: {e}")
            continue
        a, c = upsert(path, rows, ["HOUSE_MANAGE_NO", "PBLANC_NO"], ["RCRIT_PBLANC_DE", "HOUSE_MANAGE_NO"])
        log(f"[청약홈 {label}] {since}부터 {len(rows)}건 받음 (새 공고 {a}, 바뀜 {c})")
        if name == "apt":
            apt_ids = sorted({str(r.get("HOUSE_MANAGE_NO")) for r in rows if r.get("HOUSE_MANAGE_NO")})
    return apt_ids


def collect_children(key, apt_ids):
    """APT 공고별 주택형(분양가)·경쟁률·당첨 가점."""
    if not apt_ids:
        return
    jobs = [
        ("applyhome_apt_models.csv", APPLYHOME + "getAPTLttotPblancMdl", "APT 주택형·분양가"),
        ("applyhome_apt_competition.csv", CMPET + "getAPTLttotPblancCmpet", "APT 경쟁률"),
        ("applyhome_apt_score.csv", CMPET + "getAptLttotPblancScore", "APT 당첨 가점"),
    ]
    for fname, ep, label in jobs:
        rows, ok_ids, err = [], [], None
        for hid in apt_ids:
            try:
                rows += odcloud_all(key, ep, {"HOUSE_MANAGE_NO::EQ": hid})
                ok_ids.append(hid)
            except Exception as e:
                err = e
                break  # 경로가 틀렸으면 공고마다 같은 오류라 바로 멈춘다
        if err is not None and not ok_ids:
            failures.append(f"청약홈 {label}: {err}")
            log(f"[실패] 청약홈 {label}: {err}")
            continue
        for r in rows:
            r["HOUSE_MANAGE_NO"] = str(r.get("HOUSE_MANAGE_NO", ""))
        n = replace_children(DATA / fname, "HOUSE_MANAGE_NO", ok_ids, rows, ["HOUSE_MANAGE_NO"])
        log(f"[청약홈 {label}] 공고 {len(ok_ids)}개 확인, 바뀐 공고 {n}개 ({len(rows)}줄)")


# ---------- LH ----------

def collect_lh(key):
    path = DATA / "lh_notices.csv"
    start = (TODAY - timedelta(days=WINDOW_DAYS)) if path.exists() else datetime.strptime(BACKFILL_FROM, "%Y-%m-%d").date()
    rows, page = [], 1
    try:
        while True:
            res = get("https://apis.data.go.kr/B552555/lhLeaseNoticeInfo1/lhLeaseNoticeInfo1", {
                "serviceKey": key, "PG_SZ": 500, "PAGE": page,
                "PAN_ST_DT": start.strftime("%Y%m%d"), "PAN_ED_DT": TODAY.strftime("%Y%m%d"),
            })
            lst = []
            for part in res if isinstance(res, list) else [res]:
                if isinstance(part, dict) and "dsList" in part:
                    lst = part["dsList"] or []
            rows += lst
            total = int(lst[0].get("ALL_CNT", 0)) if lst else 0
            if not lst or page * 500 >= total:
                break
            page += 1
    except Exception as e:
        failures.append(f"LH 공고: {e}")
        log(f"[실패] LH 공고: {e}")
        return
    for r in rows:
        r.pop("RNUM", None)
        r.pop("ALL_CNT", None)
    a, c = upsert(path, rows, ["PAN_ID"], ["PAN_NT_ST_DT", "PAN_ID"])
    log(f"[LH 공고] {start}부터 {len(rows)}건 받음 (새 공고 {a}, 바뀜 {c})")


# ---------- 공공서비스(지원 제도) ----------

def collect_gov24(key):
    path = DATA / "gov24_services.csv"
    try:
        rows = odcloud_all(key, "gov24/v3/serviceList", per_page=1000)
    except Exception as e:
        failures.append(f"공공서비스 목록: {e}")
        log(f"[실패] 공공서비스 목록: {e}")
        return
    if not rows:
        failures.append("공공서비스 목록: 받은 것이 없음")
        return
    id_col = "서비스ID" if "서비스ID" in rows[0] else next(iter(rows[0]))
    a, c = upsert(path, rows, [id_col], ["수정일시", id_col] if "수정일시" in rows[0] else [id_col])
    log(f"[공공서비스] {len(rows)}건 받음 (새 제도 {a}, 바뀜 {c})")


# ---------- 마이홈포털 (SH·GH·LH 등 공공임대·분양 공고) ----------

def xml_to_obj(text):
    import xml.etree.ElementTree as ET

    def conv(el):
        kids = list(el)
        if not kids:
            return (el.text or "").strip()
        out = {}
        for k in kids:
            v = conv(k)
            if k.tag in out:
                if not isinstance(out[k.tag], list):
                    out[k.tag] = [out[k.tag]]
                out[k.tag].append(v)
            elif k.tag == "item":
                out[k.tag] = [v]
            else:
                out[k.tag] = v
        return out

    return conv(ET.fromstring(text))


def get_any(url, params):
    """JSON 이든 XML 이든 dict 로 돌려준다. 오류 응답이면 결과 코드를 담아 예외."""
    body = get(url, params, as_json=False)
    s = body.lstrip()
    obj = json.loads(s) if s[:1] in "{[" else xml_to_obj(s)
    code = None
    def walk(o):
        nonlocal code
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("resultCode", "returnReasonCode") and isinstance(v, str):
                    code = code or (k, v, o.get("resultMsg") or o.get("returnAuthMsg") or "")
                walk(v)
    walk(obj)
    # 00/000 정상, 03 데이터 없음, INFO-0 정상·INFO-3 데이터 없음(행정표준코드)
    if code and code[1] not in ("00", "000", "0", "03", "INFO-0", "INFO-000", "INFO-3", "INFO-200"):
        raise RuntimeError(f"API 오류 {code[1]}: {code[2]}")
    return obj


def find_items(obj):
    """응답 어디에 있든 item 목록을 찾아 꺼낸다."""
    if isinstance(obj, dict):
        for k in ("item", "items"):
            v = obj.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                inner = find_items(v)
                return inner if inner is not None else [v]
        for v in obj.values():
            r = find_items(v)
            if r is not None:
                return r
    return None


def total_count(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() == "totalcount":
                return int(v or 0)
            r = total_count(v)
            if r is not None:
                return r
    return None


def collect_myhome(key):
    base = "https://apis.data.go.kr/1613000/HWSPR02/"
    for name, ep, label in (("myhome_rent", "rsdtRcritNtcList", "마이홈 공공임대 공고"),
                            ("myhome_sale", "ltRsdtRcritNtcList", "마이홈 공공분양 공고")):
        path = DATA / f"{name}.csv"
        start = (TODAY - timedelta(days=WINDOW_DAYS)) if path.exists() else datetime.strptime(BACKFILL_FROM, "%Y-%m-%d").date()
        rows, page = [], 1
        try:
            while True:
                res = get_any(base + ep, {"serviceKey": key, "numOfRows": 500, "pageNo": page,
                                      "yearMtBegin": start.strftime("%Y%m"), "yearMtEnd": TODAY.strftime("%Y%m")})
                items = find_items(res) or []
                rows += items
                total = total_count(res) or 0
                if not items or page * 500 >= total:
                    break
                page += 1
        except Exception as e:
            failures.append(f"{label}: {e}")
            log(f"[실패] {label}: {e}")
            continue
        a, c = upsert(path, rows, ["pblancId", "houseSn"], ["rcritPblancDe", "pblancId"])
        sh = sum(1 for r in rows if "서울주택" in str(r.get("suplyInsttNm", "")))
        log(f"[{label}] {start:%Y-%m}부터 {len(rows)}건 받음, SH {sh}건 (새 공고 {a}, 바뀜 {c})")


# ---------- 서울 청년안심주택 ----------

def collect_youth():
    path = DATA / "seoul_youth_housing.csv"
    url = "https://soco.seoul.go.kr/youth/pgm/home/yohome/bbsListJson.json"
    max_pages = 3 if path.exists() else 200
    rows = []
    try:
        for page in range(1, max_pages + 1):
            data = urllib.parse.urlencode({"bbsId": "BMSR00015", "pageIndex": page}).encode()
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "claude_drive housing (python urllib)"})
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.loads(r.read().decode("utf-8"))
            lst = res.get("resultList") or []
            for x in lst:
                rows.append({
                    "boardId": x.get("boardId"),
                    "공고명": x.get("nttSj"),
                    "공고일": x.get("optn1"),
                    "구분": {"1": "공공", "2": "민간"}.get(str(x.get("optn2")), x.get("optn2")),
                    "모집": {"1": "최초", "2": "추가"}.get(str(x.get("optn5")), x.get("optn5")),
                    "사업자": x.get("optn3"),
                    "청약일": x.get("optn4"),
                    "링크": f"https://soco.seoul.go.kr/youth/bbs/BMSR00015/view.do?boardId={x.get('boardId')}&menuNo=400008",
                })
            if page >= int(res.get("pagingInfo", {}).get("totPage") or 0) or not lst:
                break
            time.sleep(0.5)
    except Exception as e:
        failures.append(f"청년안심주택: {e}")
        log(f"[실패] 청년안심주택: {e}")
        return
    a, c = upsert(path, rows, ["boardId"], ["공고일", "boardId"])
    log(f"[청년안심주택] {len(rows)}건 받음 (새 공고 {a}, 바뀜 {c})")


def main():
    key = os.environ.get("DATA_GO_KR_API_KEY", "").strip()
    if not key:
        sys.exit("DATA_GO_KR_API_KEY 환경 변수가 없습니다.")
    only = set(filter(None, os.environ.get("HOUSING_ONLY", "").split(",")))

    if not only or "applyhome" in only:
        collect_children(key, collect_applyhome(key))
    if not only or "lh" in only:
        collect_lh(key)
    if not only or "gov24" in only:
        collect_gov24(key)
    if not only or "myhome" in only:
        collect_myhome(key)
    if not only or "youth" in only:
        collect_youth()

    if failures:
        log("\n실패한 항목:\n- " + "\n- ".join(failures))
        sys.exit(1)
    log("\n모두 정상")


if __name__ == "__main__":
    main()
