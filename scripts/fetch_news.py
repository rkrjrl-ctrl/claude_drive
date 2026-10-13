#!/usr/bin/env python3
"""경제·보험 뉴스 헤드라인 수집 (토큰 없음). 제목·언론사·시각·링크만 저장하고 본문은 저장하지 않는다.
결과: news/news.csv(누적, 180일 보관) · news/DIGEST.md(최근 7일 요약 목록)
선택 입력: 환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (없으면 네이버 검색은 건너뜀)
기사는 '소재 후보'일 뿐 근거 자료가 아니다. 영상에 쓰기 전 1차 출처(보도자료·법령)로 확인한다.
"""
import csv, datetime, email.utils, hashlib, html, json, os, re, ssl, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

if os.environ.get("WATCH_INSECURE"):  # 로컬 프록시 인증서 문제 우회(테스트용)
    ssl._create_default_https_context = ssl._create_unverified_context

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "news")
CSV_PATH, DIGEST = os.path.join(ROOT, "news.csv"), os.path.join(ROOT, "DIGEST.md")
KST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime.now(KST)
KEEP_DAYS = 180
UA = {"User-Agent": "Mozilla/5.0 (news-collector)"}

# (이름, url, 종류) 종류: all=전부 보관 / scored=키워드 점수 > 0만 보관 / gov=기관 보도자료(전부, 다이제스트 별도 구역)
FEEDS = [
    ("보험신보", "https://www.insnews.co.kr/rss/allArticle.xml", "all"),
    ("보험매일", "http://www.fins.co.kr/rss/allArticle.xml", "all"),
    ("파이낸셜뉴스 금융", "https://www.fnnews.com/rss/r20/fn_realnews_finance.xml", "scored"),
    ("한국경제 금융", "https://www.hankyung.com/feed/finance", "scored"),
    ("한국은행", "https://www.bok.or.kr/portal/bbs/B0000501/news.rss?menuNo=200691", "gov"),
    ("보건복지부", "https://www.mohw.go.kr/rss/board.es?mid=a10503010100&bid=0027", "gov"),
    ("금융위원회", "https://www.fsc.go.kr/about/fsc_bbs_rss/?fid=0111", "gov"),  # 점검 중이면 건너뜀
]
QUERIES = ["실손보험", "암보험", "보험료 인상", "보험 약관 분쟁", "보험사기", "보험업법", "건강보험 급여",
           "산정특례", "기준금리", "원달러 환율", "소비자물가", "변액보험", "연금보험", "퇴직연금", "금융감독원 보험"]
# 제목 키워드 가중치 (영상 소재 적합도의 거친 지표. 점수는 선별용일 뿐 판단이 아니다)
WEIGHTS = {"실손": 3, "암보험": 3, "암 ": 1, "보험료": 2, "약관": 2, "분쟁": 2, "보험금": 3, "보험사기": 2, "산정특례": 3,
           "건강보험": 2, "급여": 1, "금융감독원": 1, "금감원": 1, "보험업법": 2, "변액": 3, "연금": 2, "퇴직연금": 2,
           "기준금리": 2, "금리": 1, "환율": 1, "물가": 1, "보험": 1, "판결": 1, "대법원": 2, "손해보험": 1, "생명보험": 1}
COLS = ["date", "source", "title", "link", "score", "tags", "via"]


def get(url, headers=None, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read(6_000_000)
        except Exception:
            time.sleep(2)
    return None


def parse_date(s):
    if not s:
        return NOW
    try:
        d = email.utils.parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except Exception:
            return NOW
    if d.tzinfo is None:
        d = d.replace(tzinfo=KST)
    return d.astimezone(KST)


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def parse_rss(raw, via_kind, default_source):
    out = []
    try:
        root = ET.fromstring(raw)
    except Exception:
        return out
    for it in root.iter("item"):
        g = lambda tag: (it.findtext(tag) or "")
        title, link = clean(g("title")), g("link").strip()
        date = g("pubDate") or g("{http://purl.org/dc/elements/1.1/}date")
        src = clean(g("source")) or default_source
        if via_kind == "google" and " - " in title:  # 구글 뉴스는 '제목 - 언론사'
            title, _, tail = title.rpartition(" - ")
            src = src if src != default_source else tail
        if title and link:
            out.append({"title": title, "link": link, "date": parse_date(date), "source": src})
    return out


def naver(query):
    cid, sec = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and sec):
        return []
    q = urllib.parse.urlencode({"query": query, "display": 50, "sort": "date"})
    raw = get("https://openapi.naver.com/v1/search/news.json?" + q,
              {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec})
    if not raw:
        return []
    return [{"title": clean(x["title"]), "link": x.get("originallink") or x["link"], "date": parse_date(x.get("pubDate")),
             "source": urllib.parse.urlparse(x.get("originallink") or x["link"]).netloc.removeprefix("www.")}
            for x in json.loads(raw).get("items", [])]


def score(title):
    tags = [k.strip() for k in WEIGHTS if k in title]
    return sum(WEIGHTS[k] for k in WEIGHTS if k in title), tags


def key(title):
    return hashlib.sha1(re.sub(r"[\W_]+", "", title).encode()).hexdigest()[:16]


def main():
    jobs = [(n, u, k, "rss") for n, u, k in FEEDS]
    jobs += [(f"구글뉴스:{q}", "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{q} when:2d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"}), "scored", "google") for q in QUERIES]

    def run(j):
        n, u, k, via = j
        raw = get(u)
        return j, (parse_rss(raw, via, n) if raw else None)

    with ThreadPoolExecutor(6) as ex:
        res = list(ex.map(run, jobs))
    naver_items = [(f"네이버:{q}", naver(q)) for q in QUERIES] if os.environ.get("NAVER_CLIENT_ID") else []

    rows = {}
    if os.path.exists(CSV_PATH):
        for r in csv.DictReader(open(CSV_PATH, encoding="utf-8-sig", newline="")):
            rows[key(r["title"])] = r
    seen_before, failed = set(rows), []
    for (n, u, kind, via), items in res + [((n, "", "scored", "naver"), i) for n, i in naver_items]:
        if items is None:
            failed.append(n); continue
        for x in items:
            s, tags = score(x["title"])
            if kind == "scored" and s == 0:
                continue
            k = key(x["title"])
            if k in rows:
                continue
            rows[k] = {"date": x["date"].isoformat(timespec="minutes"), "source": x["source"], "title": x["title"],
                       "link": x["link"], "score": s, "tags": "|".join(tags), "via": kind if kind == "gov" else via + ":" + n}
    cutoff = (NOW - datetime.timedelta(days=KEEP_DAYS)).isoformat(timespec="minutes")
    rows = {k: r for k, r in rows.items() if r["date"] >= cutoff}
    os.makedirs(ROOT, exist_ok=True)
    ordered = sorted(rows.values(), key=lambda r: r["date"], reverse=True)
    with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, COLS)
        w.writeheader()
        w.writerows(ordered)

    week = (NOW - datetime.timedelta(days=7)).isoformat(timespec="minutes")
    recent = [r for r in ordered if r["date"] >= week]
    gov = [r for r in recent if r["via"] == "gov"][:30]
    top = sorted((r for r in recent if r["via"] != "gov"), key=lambda r: (int(r["score"]), r["date"]), reverse=True)[:40]
    new_n = len(set(rows) - seen_before)
    L = ["# 경제·보험 뉴스 요약 (자동 생성 — 직접 수정 금지)\n",
         f"- 갱신: {NOW:%Y-%m-%d %H:%M} KST  /  누적 {len(rows)}건 (`news/news.csv`)  /  이번 신규 {new_n}건  /  네이버 검색 {'사용' if naver_items else '미사용(키 없음)'}",
         "- 제목·링크만 저장한다. **소재 후보**이며 근거 자료가 아니다. 영상에 쓰기 전 1차 출처로 확인한다.",
         f"- 수집 실패 소스: {', '.join(failed) if failed else '없음'}\n",
         "## 최근 7일 — 기관 보도자료\n"]
    L += [f"- {r['date'][5:10]} [{r['source']}] [{r['title']}]({r['link']})" for r in gov] or ["- 없음"]
    L.append("\n## 최근 7일 — 키워드 점수 상위 40건\n")
    L += [f"- ({r['score']}) {r['date'][5:10]} {r['source']} — [{r['title']}]({r['link']})" for r in top] or ["- 없음"]
    open(DIGEST, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"신규 {new_n} / 누적 {len(rows)} / 실패 {failed}")


if __name__ == "__main__":
    main()
