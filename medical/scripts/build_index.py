#!/usr/bin/env python3
"""medical/sources/*.md 를 검증하고 index.csv · STATUS.md 를 다시 만든다.

사용법:  python3 medical/scripts/build_index.py [--today YYYY-MM-DD] [--strict]
  --strict : 오류가 하나라도 있으면 종료코드 1 (경고는 무시)
자료를 추가·수정한 뒤에는 반드시 실행한다. index.csv·STATUS.md 는 손으로 고치지 않는다.
"""
import csv, datetime, glob, os, re, sys
from collections import Counter
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "sources")

FIELDS = ["id", "title", "org", "jurisdiction", "category", "disease", "group", "topic", "url", "published", "revised",
          "checked", "location", "source_type", "tier", "status", "recheck_by", "reviewer",
          "drive_file", "video_use"]
OPTIONAL = {"migrated_from", "supersedes", "superseded_by", "related", "note", "effective"}
ENUM = {
    "jurisdiction": {"KR", "US", "INT"},
    "source_type": {"web_summary", "pdf_original", "law", "notice", "guideline_summary"},
    "tier": {"A", "B", "C"},
    "status": {"verified", "partial", "needs_recheck", "superseded", "rejected"},
    "group": {"암", "뇌혈관질환", "심장·혈관질환", "당뇨·대사·내분비", "호흡기질환", "소화기·간질환",
              "콩팥·비뇨기", "정신건강·신경계", "근골격·통증·재활", "감염·예방접종", "여성·임신·출산",
              "눈·귀·코·피부·구강", "혈액·면역질환", "제도·급여·공통", "검사·생활·공통"},
}
SECTIONS = ["## 핵심 근거", "## 적용 조건·한계", "## 미검증·사용 금지", "## 영상 활용 메모"]
DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
# 날짜 칸에는 'unknown' 또는 날짜만 허용 (미확인/미표기/본문 미표기 등 혼용 금지)
DATE_FIELDS = ["published", "revised", "effective", "checked", "recheck_by"]


def parse(path):
    text = open(path, encoding="utf-8").read()
    m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
    if not m:
        return None, text, ["front matter(---) 없음"]
    meta, errs = {}, []
    for ln in m.group(1).splitlines():
        if not ln.strip():
            continue
        if ":" not in ln:
            errs.append(f"front matter 형식 오류: {ln!r}")
            continue
        k, v = ln.split(":", 1)
        meta[k.strip()] = v.strip()
    return meta, m.group(2), errs


def norm_url(u):
    """같은 원문 중복 판별용: 호스트의 www 제거 + 의미 있는 쿼리만 비교."""
    p = urlparse(u)
    host = p.netloc.lower().removeprefix("www.")
    q = parse_qs(p.query)
    keep = {k: v for k, v in q.items() if k in ("cancer_seq", "menu_seq", "brdBltNo", "SEQ", "mid", "lsiSeq", "admRulSeq", "admRulId", "flSeq", "lsId", "id", "sid", "number", "fileId", "titleId", "chapter", "doi", "cntnts_sn", "articleNo", "attachNo")}
    qs = "&".join(f"{k}={','.join(v)}" for k, v in sorted(keep.items()))
    return f"{host}{p.path}?{qs}" if qs else f"{host}{p.path}"


def main():
    today = datetime.date.today()
    strict = "--strict" in sys.argv
    if "--today" in sys.argv:
        today = datetime.date.fromisoformat(sys.argv[sys.argv.index("--today") + 1])
    rows, errors, warns = [], [], []
    seen_ids, seen_urls = {}, {}

    for path in sorted(glob.glob(os.path.join(SRC, "*.md"))):
        fn = os.path.basename(path)
        meta, body, perrs = parse(path)
        errors += [f"{fn}: {e}" for e in perrs]
        if meta is None:
            continue
        for k in FIELDS:
            if not meta.get(k):
                errors.append(f"{fn}: 필수 항목 '{k}' 비어 있음")
        for k in meta:
            if k not in FIELDS and k not in OPTIONAL:
                errors.append(f"{fn}: 알 수 없는 항목 '{k}'")
        mid = meta.get("id", "")
        if not re.fullmatch(r"MED-\d{4}", mid):
            errors.append(f"{fn}: id 형식 오류 {mid!r}")
        elif not fn.startswith(mid + "_"):
            errors.append(f"{fn}: 파일명은 '{mid}_' 로 시작해야 함")
        if mid in seen_ids:
            errors.append(f"{fn}: id 중복 ({seen_ids[mid]})")
        seen_ids[mid] = fn
        for k, allowed in ENUM.items():
            if meta.get(k) and meta[k] not in allowed:
                errors.append(f"{fn}: {k}='{meta[k]}' (허용: {', '.join(sorted(allowed))})")
        for k in DATE_FIELDS:
            v = meta.get(k, "")
            if v and v != "unknown" and not DATE_RE.match(v):
                errors.append(f"{fn}: {k}='{v}' → 날짜(YYYY[-MM[-DD]]) 또는 unknown 만 허용")
        if meta.get("checked") == "unknown" or meta.get("recheck_by") == "unknown":
            errors.append(f"{fn}: checked/recheck_by 는 unknown 불가")
        if not str(meta.get("url", "")).startswith("http"):
            errors.append(f"{fn}: url 이 http(s) 가 아님")
        else:
            key = norm_url(meta["url"])
            if key in seen_urls:
                warns.append(f"{fn}: 같은 원문 URL 이 {seen_urls[key]} 와 중복 (버전 연결 필요)")
            seen_urls[key] = mid
        for s in SECTIONS:
            if s not in body:
                errors.append(f"{fn}: 섹션 '{s}' 없음")
        if meta.get("status") == "partial" and "미확보" not in body and "없다" not in body:
            warns.append(f"{fn}: status=partial 인데 미확보 내용이 본문에 명시되지 않음")
        if meta.get("status") == "verified" and meta.get("tier") == "C" and meta.get("jurisdiction") == "US":
            pass  # 해외 자료는 verified 여도 국내 제도 근거로 쓰지 않음 (README 규칙)
        rb = meta.get("recheck_by", "")
        if DATE_RE.match(rb) and len(rb) == 10:
            due = datetime.date.fromisoformat(rb)
            if due < today:
                warns.append(f"{fn}: 재확인 기한 경과 ({rb})")
        rows.append(meta)

    # 번호 결번 점검
    nums = sorted(int(i[4:]) for i in seen_ids if re.fullmatch(r"MED-\d{4}", i))
    if nums:
        missing = sorted(set(range(1, nums[-1] + 1)) - set(nums))
        if missing:
            warns.append("결번: " + ", ".join(f"MED-{n:04d}" for n in missing))

    rows.sort(key=lambda r: r.get("id", ""))
    with open(os.path.join(ROOT, "index.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    write_status(rows, errors, warns, today)
    write_by_disease(rows)
    print(f"자료 {len(rows)}건 / 오류 {len(errors)} / 경고 {len(warns)}")
    for e in errors:
        print("오류:", e)
    for w_ in warns:
        print("경고:", w_)
    if strict and errors:
        sys.exit(1)


def write_by_disease(rows):
    from collections import defaultdict
    g = defaultdict(lambda: defaultdict(list))
    for r in rows:
        g[r.get("group", "")][r.get("topic", "")].append(r)
    order = sorted(ENUM["group"], key=lambda k: (-sum(len(v) for v in g[k].values()), k))
    L = ["# 질병별 자료 목록 (자동 생성 — 직접 수정 금지)", "",
         "대분류(`group`) → 질병(`topic`)으로 묶은 목록. 원문 표기 질병명은 `disease` 칸에 그대로 있다. 상태 `verified`만 영상 근거로 바로 쓸 수 있고 `partial`/`needs_recheck`는 각 파일의 미검증 항목을 먼저 확인한다. 관할 KR이 아닌 자료(US·INT)는 국내 제도·급여 근거로 쓰지 않는다.", "",
         "## 목차", ""]
    for grp in order:
        n = sum(len(v) for v in g[grp].values())
        if n:
            L.append(f"- **{grp}** ({n}건): " + ", ".join(f"{t} {len(g[grp][t])}" for t in sorted(g[grp], key=lambda k: (-len(g[grp][k]), k))))
    L.append("")
    for grp in order:
        if not g[grp]:
            continue
        L += [f"# {grp}", ""]
        for t in sorted(g[grp], key=lambda k: (-len(g[grp][k]), k)):
            rs = g[grp][t]
            ver = sum(r.get("status") == "verified" for r in rs)
            kr = sum(r.get("jurisdiction") == "KR" for r in rs)
            L += [f"## {t} ({len(rs)}건 · verified {ver} · 국내 {kr})", "",
                  "| ID | 자료명 | 기관 | 관할 | 등급 | 상태 | 재확인 |", "|---|---|---|---|---|---|---|"]
            for r in sorted(rs, key=lambda r: (r.get("status") != "verified", r.get("jurisdiction") != "KR", r.get("tier", ""), r.get("id", ""))):
                L.append(f"| {r['id']} | {r['title']} | {r['org']} | {r['jurisdiction']} | {r['tier']} | {r['status']} | {r['recheck_by']} |")
            L.append("")
    with open(os.path.join(ROOT, "BY_DISEASE.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


def write_status(rows, errors, warns, today):
    st = Counter(r.get("status") for r in rows)
    tier = Counter(r.get("tier") for r in rows)
    jur = Counter(r.get("jurisdiction") for r in rows)
    L = [f"# 수집 현황 (자동 생성 — 직접 수정 금지)", "",
         f"- 기준일: {today}  /  검증 후 보관된 자료: **{len(rows)}건**",
         "- 상태: " + ", ".join(f"{k} {v}" for k, v in sorted(st.items())),
         "- 근거 등급: " + ", ".join(f"{k} {v}" for k, v in sorted(tier.items())) + "  (A 법령·고시·학회지침 / B 국내 공공기관 안내 / C 해외·보조·오래된 일반 안내)",
         "- 관할: " + ", ".join(f"{k} {v}" for k, v in sorted(jur.items())),
         f"- 검증 스크립트 결과: 오류 {len(errors)}건, 경고 {len(warns)}건", ""]
    need = [r for r in rows if r.get("status") != "verified"]
    L += ["## 영상에 바로 쓰기 전에 보완이 필요한 자료", "", "| ID | 자료명 | 상태 | 등급 | 재확인 기한 |", "|---|---|---|---|---|"]
    for r in need:
        L.append(f"| {r['id']} | {r['title']} | {r['status']} | {r['tier']} | {r['recheck_by']} |")
    L += ["", "## 재확인 기한 순", "", "| 기한 | ID | 자료명 |", "|---|---|---|"]
    for r in sorted(rows, key=lambda r: (r.get("recheck_by", ""), r.get("id", ""))):
        L.append(f"| {r['recheck_by']} | {r['id']} | {r['title']} |")
    if errors or warns:
        L += ["", "## 검증 결과"]
        L += [f"- 오류: {e}" for e in errors] + [f"- 경고: {w}" for w in warns]
    L.append("")
    with open(os.path.join(ROOT, "STATUS.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L))


if __name__ == "__main__":
    main()
