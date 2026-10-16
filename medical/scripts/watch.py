#!/usr/bin/env python3
"""토큰 없이 도는 의료 자료 감시. index.csv의 url을 받아 변경·기한·접속불가를 WATCH.md에 쓴다.
자료 본문(sources/)과 index.csv는 건드리지 않는다. 내용 판단·재작성은 Claude가 한다.

변경 판정: 해시가 달라지면 'pending'으로 두고, 같은 새 해시가 다음 실행에서도 나오면 '변경 확정'.
(조회수·시각이 들어간 페이지가 매일 바뀌는 오탐을 거르기 위함)
"""
import csv, datetime, hashlib, json, os, re, sys, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "watch_state.json")
OUT = os.path.join(ROOT, "WATCH.md")
TODAY = datetime.date.today()
UA = {"User-Agent": "Mozilla/5.0 (medical-watch; +https://github.com/rkrjrl-ctrl/claude_drive)"}
MAX_BYTES = 8_000_000
FAIL_LIMIT = 3  # 연속 실패가 이 횟수 이상이면 보고

# PubMed 신규 논문 검색어 (index.csv의 disease 값에 맞춤). 없는 질병은 건너뜀.
PUBMED = {
    "신장암": "renal cell carcinoma", "위암": "gastric cancer", "대장암": "colorectal cancer",
    "폐암": "lung cancer", "유방암": "breast cancer", "간암": "hepatocellular carcinoma",
    "전립선암": "prostate cancer", "갑상선암": "thyroid cancer", "췌장암": "pancreatic cancer",
    "담낭암": "gallbladder cancer", "담도암": "biliary tract cancer",
}
PUB_TYPES = '("practice guideline"[pt] OR guideline[pt] OR "randomized controlled trial"[pt] OR meta-analysis[pt])'


def fetch(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        t0, body = time.time(), b""
        with urllib.request.urlopen(req, timeout=25) as r:
            ctype = r.headers.get("Content-Type", "")
            while len(body) < MAX_BYTES:  # 조금씩 오는 느린 서버가 전체를 붙잡지 않도록 총 시간 제한
                chunk = r.read(65536)
                if not chunk:
                    break
                body += chunk
                if time.time() - t0 > 45:
                    raise TimeoutError("총 45초 초과")
            clen = r.headers.get("Content-Length", "")
        # 큰 파일은 읽은 길이가 청크 경계에 따라 달라져 해시가 흔들리므로 정확히 잘라서 비교
        body = body[:MAX_BYTES] + clen.encode()
        if "html" in ctype or body[:100].lstrip().lower().startswith((b"<!doctype", b"<html")):
            t = body.decode("utf-8", "ignore")
            t = re.sub(r"(?is)<(script|style|noscript).*?</\1>|<!--.*?-->", " ", t)
            t = re.sub(r"(?s)<[^>]+>", " ", t)
            # 조회수·다운로드 횟수처럼 매일 바뀌는 숫자는 비교에서 뺀다
            t = re.sub(r"(조회수?|다운로드|추천|hits?|views?)\s*[:：(]?\s*[\d,]+\s*\)?", r"\1", t, flags=re.I)
            body = re.sub(r"\s+", " ", t).strip().encode()
        return url, hashlib.sha256(body).hexdigest()[:16], None
    except Exception as e:  # 네트워크·HTTP 오류 전부 '접속 실패'로 기록
        return url, None, f"{type(e).__name__}: {str(e)[:80]}"


def pubmed(term, since):
    q = urllib.parse.urlencode({
        "db": "pubmed", "retmode": "json", "retmax": 5, "sort": "date", "datetype": "edat",
        "mindate": since, "maxdate": TODAY.isoformat(),
        "term": f'"{term}"[Title/Abstract] AND {PUB_TYPES}',
    })
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    try:
        ids = json.load(urllib.request.urlopen(base + "esearch.fcgi?" + q, timeout=25))["esearchresult"]["idlist"]
        if not ids:
            return []
        s = json.load(urllib.request.urlopen(base + "esummary.fcgi?db=pubmed&retmode=json&id=" + ",".join(ids), timeout=25))["result"]
        return [(i, s[i]["title"], s[i].get("pubdate", "")) for i in ids if i in s]
    except Exception as e:
        return [("ERR", f"PubMed 조회 실패: {type(e).__name__}", "")]


def main():
    rows = list(csv.DictReader(open(os.path.join(ROOT, "index.csv"), encoding="utf-8")))
    state = json.load(open(STATE, encoding="utf-8")) if os.path.exists(STATE) else {}
    urls = state.setdefault("urls", {})
    first_run = not urls
    by_url = {}
    for r in rows:
        u = (r["url"] or "").strip()
        if u.startswith("http"):
            by_url.setdefault(u, []).append(r["id"])

    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(fetch, by_url))

    changed, failing = [], []
    for u, h, err in results:
        s = urls.setdefault(u, {"hash": None, "pending": None, "fails": 0})
        if err:
            s["fails"] += 1
            s["err"] = err
            if s["fails"] >= FAIL_LIMIT:
                failing.append((u, s["fails"], err))
            continue
        s["fails"], s["err"] = 0, None
        if s["hash"] is None or s["hash"] == h:
            s["hash"], s["pending"] = h, None
        elif s["pending"] == h:  # 두 번 연속 같은 새 해시 → 확정
            changed.append((u, s.get("since", "")))
            s["hash"], s["pending"], s["since"] = h, None, TODAY.isoformat()
        else:
            s["pending"] = h
    for u in list(urls):  # 목록에서 빠진 url 정리
        if u not in by_url:
            del urls[u]

    soon = TODAY + datetime.timedelta(days=30)
    due = []
    for r in rows:
        try:
            d = datetime.date.fromisoformat(r["recheck_by"])
        except ValueError:
            continue
        if d <= soon:
            due.append((d, r["id"], r["title"], r["status"]))
    due.sort()

    since = state.get("pubmed_since", (TODAY - datetime.timedelta(days=7)).isoformat())
    diseases = {r["disease"] for r in rows}
    papers, pub_err = {}, None
    for ko, en in PUBMED.items():
        if any(ko in d for d in diseases):
            hits = pubmed(en, since)
            if hits and hits[0][0] == "ERR":  # NCBI는 초당 3회 제한 → 잠시 쉬고 1회 재시도
                time.sleep(2)
                hits = pubmed(en, since)
            time.sleep(0.5)
            if hits and hits[0][0] == "ERR":
                pub_err = hits[0][1]
            elif hits:
                papers[ko] = hits
    state["pubmed_since"] = TODAY.isoformat()

    L = [f"# 자동 감시 결과 (자동 생성 — 직접 수정 금지)\n", f"- 실행일: {TODAY}  /  감시 url {len(by_url)}개  /  자료 {len(rows)}건",
         "- 이 파일은 **감지만** 한다. 원문 내용 확인·자료 갱신은 Claude Code가 한다.\n"]
    if first_run:
        L.append("> 첫 실행: 기준 해시만 저장했다. 변경 감지는 다음 실행부터.\n")
    L.append(f"## 원문 변경 확정 ({len(changed)}건)\n")
    L += [f"- {', '.join(by_url[u])} — {u}" for u, _ in changed] or ["- 없음"]
    L.append(f"\n## 접속 {FAIL_LIMIT}회 이상 연속 실패 ({len(failing)}건)\n")
    L += [f"- {', '.join(by_url[u])} — {n}회 — {e} — {u}" for u, n, e in failing] or ["- 없음"]
    L.append(f"\n## 재확인 기한 도래·30일 이내 ({len(due)}건)\n")
    L += [f"- {d} {i} [{st}] {t}" + ("  ⚠ 기한 지남" if d < TODAY else "") for d, i, t, st in due] or ["- 없음"]
    L.append(f"\n## PubMed 신규 가이드라인·RCT·메타분석 ({since} 이후)\n")
    for ko, hits in papers.items():
        L.append(f"**{ko}**")
        L += [f"- {t} ({p}) https://pubmed.ncbi.nlm.nih.gov/{i}/" if i != "ERR" else f"- {t}" for i, t, p in hits]
    if pub_err:
        L.append(f"- {pub_err} (알림 대상 아님)")
    elif not papers:
        L.append("- 없음")
    L.append("\n## 아직 감시하지 않는 것\n\n- 심평원 고시·법령의 **새 개정 탐지**(등록된 url의 내용 변경만 잡는다). 사이트별 목록 파서가 필요해 별도 작업.")
    open(OUT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1, sort_keys=True)
    print(f"변경 {len(changed)} / 접속실패 {len(failing)} / 기한 {len(due)} / 논문 {sum(map(len, papers.values()))}")
    # 알릴 일이 있으면 1, 없으면 0 (워크플로가 이슈를 열지 판단)
    return 1 if (changed or failing or any(d < TODAY for d, *_ in due) or papers) else 0


if __name__ == "__main__":
    sys.exit(0 if main() in (0, 1) else 2)
