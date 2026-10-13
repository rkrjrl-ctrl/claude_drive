#!/usr/bin/env python3
"""토큰 없이 도는 법령·고시·판례 감시 (법제처 국가법령정보 공동활용 API).
- 법령: 관련 법령의 개정(공포일·일련번호 변화)과 시행 예정 버전
- 고시: 보건복지부 등 행정규칙의 신규·개정 (요양급여·산정특례·약제·재난적의료비 등)
- 판례: 대법원 보험 관련 판례를 medical/cases/insurance_cases.csv에 누적
결과는 medical/LAWWATCH.md. 자료(sources/)는 건드리지 않는다. 해석·자료화는 Claude가 한다.

인증: 환경변수 LAW_OC(법제처 OPEN API 인증값). 없으면 'test'(공용·제한적). 안정 운영은 본인 OC 등록 권장.
"""
import csv, datetime, json, os, re, ssl, sys, time, urllib.parse, urllib.request

if os.environ.get("WATCH_INSECURE"):  # 사내망·프록시 인증서 문제가 있는 로컬 테스트용
    ssl._create_default_https_context = ssl._create_unverified_context

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "law_state.json")
OUT = os.path.join(ROOT, "LAWWATCH.md")
CASES = os.path.join(ROOT, "cases", "insurance_cases.csv")
OC = os.environ.get("LAW_OC") or "test"
TODAY = datetime.date.today()
BASE = "https://www.law.go.kr/DRF/"
CASE_FROM = "20210101"  # 판례 수집 시작일
DETAIL_CAP = 300         # 1회 실행에서 판시사항을 받아 올 최대 건수

LAWS = [
    "국민건강보험법", "국민건강보험법 시행령", "국민건강보험법 시행규칙",
    "암관리법", "암관리법 시행령", "암관리법 시행규칙",
    "호스피스·완화의료 및 임종과정에 있는 환자의 연명의료결정에 관한 법률",
    "재난적의료비 지원에 관한 법률", "재난적의료비 지원에 관한 법률 시행령",
    "의료급여법", "의료법", "보험업법", "보험업법 시행령", "상법", "약관의 규제에 관한 법률",
    "노인장기요양보험법", "생명윤리 및 안전에 관한 법률",
]
ADM_KEYWORDS = ["요양급여", "산정특례", "본인일부부담금", "암환자", "항암", "약제", "선별급여",
                "재난적의료비", "호스피스", "의료급여", "암관리", "요양비", "보험업감독규정", "실손의료보험"]
ADM_DEPTS = {"보건복지부", "질병관리청", "금융위원회", "식품의약품안전처"}
CASE_QUERIES = [("보험", 1), ("실손의료보험", 2), ("암보험", 2), ("진단비", 2), ("고지의무", 2),
                ("약관 설명의무", 2), ("요양급여", 2)]


FAILS = [0]  # 연속 실패 횟수. 3회 넘으면 API가 막힌 것으로 보고 전체를 즉시 중단


def api(path, **p):
    if FAILS[0] >= 3:
        raise RuntimeError("법제처 API 연속 실패 — 중단")
    url = BASE + path + "?" + urllib.parse.urlencode({"OC": OC, "type": "JSON", **p})
    last = None
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "medical-watch"}), timeout=20) as r:
                out = json.loads(r.read().decode("utf-8"))
            FAILS[0] = 0
            return out
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    FAILS[0] += 1
    raise RuntimeError(f"{path} {p.get('query', '')}: {type(last).__name__}")


def items(d, key):
    """응답 최상위 하나 아래 key 목록을 리스트로 (1건이면 dict로 오는 경우 대비)."""
    top = next(iter(d.values())) if d else {}
    v = top.get(key, []) if isinstance(top, dict) else []
    return v if isinstance(v, list) else [v]


def plain(s, n=400):
    s = re.sub(r"<br\s*/?>", " ", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", s).strip()[:n]


def watch_laws(state, alerts, errs):
    st = state.setdefault("laws", {})
    changed, upcoming = [], []
    for name in LAWS:
        try:
            cur = [x for x in items(api("lawSearch.do", target="law", query=name, display=20), "law")
                   if x.get("법령명한글") == name]
            time.sleep(0.3)
            eff = [x for x in items(api("lawSearch.do", target="eflaw", query=name, nw=2, display=20), "law")
                   if x.get("법령명한글") == name and x.get("시행일자", "") > TODAY.strftime("%Y%m%d")]
        except Exception as e:
            errs.append(str(e)); continue
        if cur:
            c = cur[0]
            sig = [c["공포일자"], c["법령일련번호"]]
            old = st.get(c["법령ID"])
            if old and old["sig"] != sig:
                changed.append(f"{name} — {c['제개정구분명']} (공포 {c['공포일자']}, 시행 {c['시행일자']}, 공포번호 {c['공포번호']})")
            st[c["법령ID"]] = {"name": name, "sig": sig}
        for e in eff:
            upcoming.append(f"{name} — 시행 예정 {e['시행일자']} ({e.get('제개정구분명', '')}, 공포 {e['공포일자']})")
        time.sleep(0.3)
    alerts[0] += len(changed)
    return changed, sorted(set(upcoming))


def watch_admrul(state, alerts, errs, first_run):
    st = state.setdefault("admrul", {})
    new = []
    for kw in ADM_KEYWORDS:
        try:
            rows = items(api("lawSearch.do", target="admrul", query=kw, display=100, sort="ddes"), "admrul")
        except Exception as e:
            errs.append(str(e)); continue
        for x in rows:
            if x.get("소관부처명") not in ADM_DEPTS or x.get("현행연혁구분") != "현행":
                continue
            if kw == "약제" and x["소관부처명"] != "보건복지부":
                continue
            rid, sig = x["행정규칙ID"], x["행정규칙일련번호"]
            old = st.get(rid)
            if old is None or old["sig"] != sig:
                recent = x["발령일자"] >= (TODAY - datetime.timedelta(days=60)).strftime("%Y%m%d")
                if (not first_run) or recent:
                    new.append((x["발령일자"], f"{x['행정규칙명']} — {x['소관부처명']} {x['행정규칙종류']} {x.get('발령번호', '')} "
                                f"({x.get('제개정구분명', '')}, 발령 {x['발령일자']}, 시행 {x.get('시행일자', '')}) "
                                f"https://www.law.go.kr/DRF/lawService.do?OC={OC}&target=admrul&ID={sig}&type=HTML"))
            st[rid] = {"sig": sig, "name": x["행정규칙명"], "date": x["발령일자"]}
        time.sleep(0.3)
    new = sorted(set(new), reverse=True)
    alerts[0] += len(new)
    return [t for _, t in new]


def load_cases():
    if not os.path.exists(CASES):
        return {}
    with open(CASES, encoding="utf-8-sig", newline="") as f:
        return {r["판례일련번호"]: r for r in csv.DictReader(f)}


def watch_cases(state, alerts, errs, first_run):
    cases = load_cases()
    seen_new = []
    for kw, mode in CASE_QUERIES:
        for page in range(1, 6):
            try:
                d = api("lawSearch.do", target="prec", query=kw, search=mode, org="400201", display=100,
                        page=page, prncYd=f"{CASE_FROM}~{TODAY.strftime('%Y%m%d')}", sort="ddes")
            except Exception as e:
                errs.append(str(e)); break
            rows = items(d, "prec")
            for x in rows:
                pid = x["판례일련번호"]
                if pid in cases:
                    if kw not in cases[pid]["검색어"].split("|"):
                        cases[pid]["검색어"] += "|" + kw
                    continue
                cases[pid] = {"판례일련번호": pid, "사건번호": x["사건번호"], "선고일자": x["선고일자"], "사건명": x["사건명"],
                              "판결유형": x.get("판결유형", ""), "검색어": kw, "판시사항": "",
                              "링크": f"https://www.law.go.kr/precInfoP.do?precSeq={pid}"}
                seen_new.append(pid)
            total = int(next(iter(d.values())).get("totalCnt", 0) or 0) if d else 0
            if page * 100 >= total or not rows:
                break
            time.sleep(0.3)
        time.sleep(0.3)
    todo = [p for p, r in sorted(cases.items(), key=lambda kv: kv[1]["선고일자"], reverse=True) if not r["판시사항"]][:DETAIL_CAP]
    for pid in todo:
        try:
            d = api("lawService.do", target="prec", ID=pid)
        except Exception as e:
            errs.append(str(e)); break
        v = next(iter(d.values()), None) if d else None
        # 상세가 없는 건(요약 판례 등)은 '-'로 표시해 매번 다시 받지 않는다
        cases[pid]["판시사항"] = plain(v.get("판시사항") or v.get("판결요지")) if isinstance(v, dict) else ""
        cases[pid]["판시사항"] = cases[pid]["판시사항"] or "-"
        time.sleep(0.2)
    os.makedirs(os.path.dirname(CASES), exist_ok=True)
    cols = ["판례일련번호", "사건번호", "선고일자", "사건명", "판결유형", "검색어", "판시사항", "링크"]
    with open(CASES, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        for r in sorted(cases.values(), key=lambda r: (r["선고일자"], r["판례일련번호"]), reverse=True):
            w.writerow(r)
    shown = seen_new[:10] if first_run else seen_new
    lines = [f"{cases[p]['선고일자']} {cases[p]['사건번호']} {cases[p]['사건명']} — {cases[p]['판시사항'][:140]} {cases[p]['링크']}"
             for p in sorted(shown, key=lambda p: cases[p]["선고일자"], reverse=True)]
    if not first_run:
        alerts[0] += len(seen_new)
    return lines, len(cases), len(seen_new)


def main():
    state = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
    first_run = not state
    alerts, errs = [0], []
    changed, upcoming = watch_laws(state, alerts, errs)
    adm = watch_admrul(state, alerts, errs, first_run)
    cases, ncases, nnew = watch_cases(state, alerts, errs, first_run)
    L = ["# 법령·고시·판례 감시 결과 (자동 생성 — 직접 수정 금지)\n",
         f"- 실행일: {TODAY}  /  법령 {len(LAWS)}개·고시 키워드 {len(ADM_KEYWORDS)}개 감시  /  보험 대법원 판례 누적 {ncases}건 (`medical/cases/insurance_cases.csv`)",
         "- 이 파일은 **감지만** 한다. 해석·자료 등록은 Claude Code가 한다. 판례는 요약 일부(판시사항)만 보여 주며 원문 확인이 필요하다.\n"]
    if first_run:
        L.append("> 첫 실행: 법령·고시는 기준값만 저장(고시는 최근 60일 발령분만 표시), 판례는 2021년 이후를 모두 수집하고 최신 10건만 표시.\n")
    L.append(f"## 법령 개정 감지 ({len(changed)}건)\n")
    L += [f"- {t}" for t in changed] or ["- 없음"]
    L.append(f"\n## 시행 예정 법령 ({len(upcoming)}건)\n")
    L += [f"- {t}" for t in upcoming] or ["- 없음"]
    L.append(f"\n## 고시·행정규칙 신규·개정 ({len(adm)}건)\n")
    L += [f"- {t}" for t in adm] or ["- 없음"]
    L.append(f"\n## 신규 대법원 보험 판례 ({nnew}건{', 첫 실행은 최신 10건 표시' if first_run else ''})\n")
    L += [f"- {t}" for t in cases] or ["- 없음"]
    L.append("\n## 수집 오류\n")
    L += [f"- {e}" for e in sorted(set(errs))[:10]] or ["- 없음"]
    L.append("\n## 아직 감시하지 않는 것\n\n- 심평원(hira.or.kr) 공고 게시판: 목록이 스크립트로 읽히지 않아 제외. 공고의 근거가 되는 보건복지부 고시는 위 '고시' 항목으로 잡힌다.")
    L.append(f"\n<!-- alerts: {alerts[0]} -->")
    open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    print(f"법령변경 {len(changed)} / 시행예정 {len(upcoming)} / 고시 {len(adm)} / 신규판례 {nnew} / 누적 {ncases} / 오류 {len(errs)}")


if __name__ == "__main__":
    main()
