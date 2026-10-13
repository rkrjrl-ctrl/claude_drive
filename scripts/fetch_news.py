#!/usr/bin/env python3
"""경제·보험·의료정책 뉴스 헤드라인 수집 (토큰 없음). 제목·언론사·시각·링크만 저장하고 본문은 저장하지 않는다.
결과: news/YYYY-MM.csv(월별 누적) · news/DIGEST.md(최근 7일 요약 목록)
선택 입력: 환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (네이버 개발자센터 검색 API 키. 없으면 건너뜀)
기사는 '소재 후보'일 뿐 근거 자료가 아니다. 영상에 쓰기 전 1차 출처(보도자료·법령)로 확인한다.
"""
import csv, datetime, email.utils, glob, hashlib, html, json, os, re, ssl, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

if os.environ.get("WATCH_INSECURE"):  # 로컬 프록시 인증서 문제 우회(테스트용)
    ssl._create_default_https_context = ssl._create_unverified_context

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "news")
DIGEST = os.path.join(ROOT, "DIGEST.md")
LEGACY = os.path.join(ROOT, "news.csv")
KST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime.now(KST)
UA = {"User-Agent": "Mozilla/5.0"}  # 일부 매체는 낯선 UA를 막는다
BLOCK_SOURCES = {"Vietnam.vn"}  # 키워드 검색에 섞여 들어오는 무관한 해외 사이트

# 종류: all=전부 보관 / scored=키워드 점수 > 0만 보관 / gov=기관 보도자료(전부, 다이제스트 별도 구역)
FEEDS = [
    # 보험 전문지
    ("보험신보", "https://www.insnews.co.kr/rss/allArticle.xml", "all"),
    ("보험매일", "http://www.fins.co.kr/rss/allArticle.xml", "all"),
    ("보험저널", "https://www.insjournal.co.kr/rss/allArticle.xml", "all"),
    ("한국보험신문", "https://www.insweek.co.kr/rss/allArticle.xml", "all"),
    # 금융·경제 매체
    ("연합뉴스 경제", "https://www.yna.co.kr/rss/economy.xml", "scored"),
    ("연합뉴스 최신", "https://www.yna.co.kr/rss/news.xml", "scored"),
    ("연합뉴스TV 경제", "https://www.yonhapnewstv.co.kr/category/news/economy/feed/", "scored"),
    ("한겨레 경제", "https://www.hani.co.kr/rss/economy/", "scored"),
    ("경향 경제", "https://www.khan.co.kr/rss/rssdata/economy_news.xml", "scored"),
    ("조선 경제", "https://www.chosun.com/arc/outboundfeeds/rss/category/economy/?outputType=xml", "scored"),
    ("동아 경제", "https://rss.donga.com/economy.xml", "scored"),
    ("서울경제 금융", "https://www.sedaily.com/rss/finance", "scored"),
    ("서울경제 경제", "https://www.sedaily.com/rss/economy", "scored"),
    ("머니투데이", "https://rss.mt.co.kr/mt_news.xml", "scored"),
    ("이데일리", "http://rss.edaily.co.kr/edaily_news.xml", "scored"),
    ("아시아경제", "https://www.asiae.co.kr/rss/all.htm", "scored"),
    ("뉴시스 경제", "https://www.newsis.com/RSS/economy.xml", "scored"),
    ("뉴시스 금융", "https://www.newsis.com/RSS/bank.xml", "scored"),
    ("한국경제 금융", "https://www.hankyung.com/feed/finance", "scored"),
    ("한국경제 경제", "https://www.hankyung.com/feed/economy", "scored"),
    ("파이낸셜뉴스 전체", "https://www.fnnews.com/rss/r20/fn_realnews_all.xml", "scored"),
    ("파이낸셜뉴스 금융", "https://www.fnnews.com/rss/r20/fn_realnews_finance.xml", "scored"),
    ("SBS 경제", "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=02&plink=RSSREADER", "scored"),
    ("JTBC 경제", "https://news-ex.jtbc.co.kr/v1/get/rss/section/economy", "scored"),
    ("노컷뉴스 경제", "https://rss.nocutnews.co.kr/category/economy.xml", "scored"),
    ("오마이뉴스 경제", "https://rss.ohmynews.com/rss/economy.xml", "scored"),
    ("서울파이낸스", "https://www.seoulfn.com/rss/allArticle.xml", "scored"),
    ("이코노믹리뷰", "https://www.econovill.com/rss/allArticle.xml", "scored"),
    ("구글뉴스 비즈니스", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ko&gl=KR&ceid=KR:ko", "scored"),
    # 의료·건강정책 전문지 (보험 급여·신약·제도 소재)
    ("청년의사", "https://www.docdocdoc.co.kr/rss/allArticle.xml", "scored"),
    ("메디파나", "https://www.medipana.com/rss/allArticle.xml", "scored"),
    ("의협신문", "https://www.doctorsnews.co.kr/rss/allArticle.xml", "scored"),
    ("히트뉴스", "https://www.hitnews.co.kr/rss/allArticle.xml", "scored"),
    ("코리아바이오메드", "https://www.koreabiomed.com/rss/allArticle.xml", "scored"),
    ("구글뉴스 건강", "https://news.google.com/rss/headlines/section/topic/HEALTH?hl=ko&gl=KR&ceid=KR:ko", "scored"),
    # 기관 보도자료
    ("보건복지부", "https://www.mohw.go.kr/rss/board.es?mid=a10503010100&bid=0027", "gov"),
    ("한국은행 보도자료", "https://www.bok.or.kr/portal/bbs/B0000501/news.rss?menuNo=200691", "gov"),
    ("한국은행 통화정책", "https://www.bok.or.kr/portal/bbs/P0000559/news.rss?menuNo=200690", "gov"),
    ("한국은행 통계", "https://www.bok.or.kr/portal/bbs/B0000169/news.rss?menuNo=200692", "gov"),
    ("금융위원회", "https://www.fsc.go.kr/about/fsc_bbs_rss/?fid=0111", "gov"),  # 점검 중이면 건너뜀
    ("생명보험협회", "https://www.klia.or.kr/rss/board.do", "gov"),
]
QUERIES = [
    # 보험 상품·제도
    "실손보험", "4세대 실손", "실손 청구 간소화", "암보험", "치매보험", "간병보험", "종신보험", "어린이보험", "자동차보험",
    "화재보험", "변액보험", "연금보험", "저축성보험", "보험료 인상", "보험료 인하", "보험금 지급", "보험금 분쟁", "보험 약관",
    "보험사기", "보험설계사", "GA 보험대리점", "보험 민원", "보험업법", "금융소비자보호법", "금융감독원 보험", "금감원 제재 보험",
    "보험사 실적", "K-ICS 킥스", "IFRS17", "보험 해지", "보험 리모델링", "비급여", "도수치료",
    # 건강보험·의료 정책
    "건강보험 급여", "건강보험료 인상", "건강보험 재정", "본인부담상한제", "산정특례", "중증질환 지원", "재난적의료비",
    "항암제 급여", "신약 건강보험 급여", "암 환자 지원", "암 치료비", "호스피스 완화의료", "국가암검진", "연명의료",
    "의료법 개정", "의료 대란", "필수의료", "요양병원", "장기요양보험",
    # 연금·노후
    "국민연금 개혁", "국민연금 수급", "퇴직연금 의무화", "퇴직연금 수익률", "기초연금", "개인연금 IRP", "연금저축", "주택연금",
    # 거시·금융시장
    "기준금리", "한국은행 금융통화위원회", "연준 FOMC 금리", "원달러 환율", "소비자물가", "가계부채", "주택담보대출 DSR",
    "예금금리", "대출금리", "국채금리", "코스피", "미국 증시", "달러 인덱스", "국제 유가", "금값", "경기 침체", "경상수지", "무역수지",
    # 부동산·세금
    "부동산 대책", "아파트 매매가격", "전세 시장", "종합부동산세", "상속세 증여세", "연말정산", "ISA 계좌",
]
# 제목 키워드 가중치 (영상 소재 적합도의 거친 지표. 점수는 선별용일 뿐 판단이 아니다)
WEIGHTS = {
    "실손": 3, "암보험": 3, "암 ": 1, "항암": 2, "암환자": 2, "보험료": 2, "약관": 2, "분쟁": 2, "보험금": 3, "보험사기": 2,
    "산정특례": 3, "건강보험": 2, "건보": 2, "급여": 1, "비급여": 2, "금융감독원": 1, "금감원": 1, "보험업법": 2, "변액": 3,
    "연금": 2, "퇴직연금": 2, "국민연금": 2, "기초연금": 2, "주택연금": 2, "IRP": 2, "치매": 1, "간병": 2, "종신": 2, "자동차보험": 2,
    "기준금리": 3, "금통위": 2, "금융통화위원회": 2, "연준": 2, "FOMC": 2, "금리": 1, "환율": 2, "물가": 2, "가계부채": 2, "DSR": 1,
    "대출": 1, "예금": 1, "코스피": 1, "국채": 1, "달러": 1, "유가": 1, "경기침체": 2, "경기 침체": 2, "보험": 1, "판결": 1, "대법원": 2,
    "손해보험": 1, "생명보험": 1, "손보": 1, "생보": 1, "보험사": 1, "킥스": 2, "K-ICS": 2, "IFRS17": 2, "재난적의료비": 3,
    "본인부담": 2, "호스피스": 2, "연명의료": 2, "신약": 1, "의료법": 1, "필수의료": 1, "요양": 1, "상속": 1, "증여": 1, "부동산": 1,
}
CHECK_CAP = 6_000_000
COLS = ["date", "source", "title", "link", "score", "tags", "via"]


def get(url, headers=None, tries=2):
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read(CHECK_CAP)
        except Exception:
            time.sleep(2)
    return None


def parse_date(s):
    if not s:
        return NOW
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return NOW
    if d.tzinfo is None:
        d = d.replace(tzinfo=KST)
    return d.astimezone(KST)


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def raw_items(raw):
    """표준 XML 파서로 읽고, 태그 오류가 있는 피드는 정규식으로 항목만 뽑는다."""
    try:
        root = ET.fromstring(raw)
        for it in list(root.iter("item")) + list(root.iter("{http://www.w3.org/2005/Atom}entry")):
            g = lambda *tags: next((it.findtext(t) for t in tags if it.findtext(t)), "") or ""
            link = g("link") or (it.find("{http://www.w3.org/2005/Atom}link").get("href", "") if it.find("{http://www.w3.org/2005/Atom}link") is not None else "")
            yield (g("title", "{http://www.w3.org/2005/Atom}title"), link,
                   g("pubDate", "{http://purl.org/dc/elements/1.1/}date", "{http://www.w3.org/2005/Atom}updated"), g("source"))
        return
    except Exception:
        pass
    text = raw.decode("utf-8", "ignore")
    for blk in re.findall(r"<item[ >].*?</item>", text, re.S):
        f = lambda t: (re.search(rf"<{t}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{t}>", blk, re.S) or [None, ""])[1]
        yield f("title"), f("link"), f("pubDate") or f("dc:date"), f("source")


def parse_rss(raw, via_kind, default_source):
    out = []
    for title, link, date, src in raw_items(raw):
        title, link = clean(title), clean(link)
        src = clean(src) or default_source
        if via_kind == "google" and " - " in title:  # 구글 뉴스는 '제목 - 언론사'
            title, _, tail = title.rpartition(" - ")
            src = tail if src == default_source else src
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
    hit = [k.strip() for k in WEIGHTS if k in title]
    return sum(WEIGHTS[k] for k in WEIGHTS if k in title), hit


def key(title):
    return hashlib.sha1(re.sub(r"[\W_]+", "", title).encode()).hexdigest()[:16]


def month_files(back=2):
    """이번 달과 이전 back개월 파일(중복 판별·다이제스트용)."""
    names, y, m = [], NOW.year, NOW.month
    for _ in range(back + 1):
        names.append(f"{y}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return names


def read_csv(path):
    if not os.path.exists(path):
        return []
    return list(csv.DictReader(open(path, encoding="utf-8-sig", newline="")))


def main():
    os.makedirs(ROOT, exist_ok=True)
    rows = {}
    pool = read_csv(LEGACY)  # 옛 단일 파일이 있으면 월별 파일로 옮긴다
    for mf in month_files():
        pool += read_csv(os.path.join(ROOT, mf + ".csv"))
    for r in pool:
        rows[key(r["title"])] = r
    seen_before = set(rows)

    jobs = [(n, u, k, "rss") for n, u, k in FEEDS]
    jobs += [(f"구글뉴스:{q}", "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{q} when:2d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"}), "scored", "google") for q in QUERIES]

    def run(j):
        raw = get(j[1])
        return j, (parse_rss(raw, j[3], j[0]) if raw else None)

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(run, jobs))
    if os.environ.get("NAVER_CLIENT_ID"):
        res += [((f"네이버:{q}", "", "scored", "naver"), naver(q)) for q in QUERIES]

    failed = []
    for (n, _u, kind, via), items in res:
        if items is None or (not items and via == "rss"):
            failed.append(n); continue
        for x in items:
            s, tags = score(x["title"])
            if x["source"] in BLOCK_SOURCES or (kind == "scored" and s == 0):
                continue
            k = key(x["title"])
            if k not in rows:
                rows[k] = {"date": x["date"].isoformat(timespec="minutes"), "source": x["source"], "title": x["title"],
                           "link": x["link"], "score": s, "tags": "|".join(tags), "via": "gov" if kind == "gov" else f"{via}:{n}"}

    by_month = {}
    for r in rows.values():
        by_month.setdefault(r["date"][:7], []).append(r)
    for mon, rs in by_month.items():
        if mon not in month_files(8):  # 너무 오래된 기사는 새로 만들지 않는다
            continue
        with open(os.path.join(ROOT, mon + ".csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, COLS)
            w.writeheader()
            w.writerows(sorted(rs, key=lambda r: (r["date"], r["link"]), reverse=True))
    if os.path.exists(LEGACY):
        os.remove(LEGACY)

    ordered = sorted(rows.values(), key=lambda r: r["date"], reverse=True)
    week = (NOW - datetime.timedelta(days=7)).isoformat(timespec="minutes")
    recent = [r for r in ordered if r["date"] >= week]
    gov = [r for r in recent if r["via"] == "gov"][:40]
    ins = [r for r in recent if r["via"].endswith(("보험신보", "보험매일", "보험저널", "한국보험신문"))][:25]
    top = sorted((r for r in recent if r["via"] != "gov"), key=lambda r: (int(r["score"]), r["date"]), reverse=True)[:60]
    new_n = len(set(rows) - seen_before)
    cnt = Counter(r["source"] for r in recent if r["via"] != "gov")
    L = ["# 경제·보험·의료정책 뉴스 요약 (자동 생성 — 직접 수정 금지)\n",
         f"- 갱신: {NOW:%Y-%m-%d %H:%M} KST  /  최근 7일 {len(recent)}건  /  이번 신규 {new_n}건  /  소스 {len(FEEDS)}곳 + 구글뉴스 키워드 {len(QUERIES)}개"
         f"  /  네이버 검색 {'사용' if os.environ.get('NAVER_CLIENT_ID') else '미사용'}",
         "- 제목·링크만 저장한다. **소재 후보**이며 근거 자료가 아니다. 영상에 쓰기 전 1차 출처로 확인한다.",
         f"- 수집 실패 소스: {', '.join(failed) if failed else '없음'}\n",
         "## 최근 7일 — 기관 보도자료\n"]
    L += [f"- {r['date'][5:10]} [{r['source']}] [{r['title']}]({r['link']})" for r in gov] or ["- 없음"]
    L.append("\n## 최근 7일 — 보험 전문지\n")
    L += [f"- {r['date'][5:10]} {r['source']} — [{r['title']}]({r['link']})" for r in ins] or ["- 없음"]
    L.append("\n## 최근 7일 — 키워드 점수 상위 60건\n")
    L += [f"- ({r['score']}) {r['date'][5:10]} {r['source']} — [{r['title']}]({r['link']})" for r in top] or ["- 없음"]
    L.append("\n## 최근 7일 — 언론사별 건수 상위 15\n")
    L.append(", ".join(f"{s} {c}" for s, c in cnt.most_common(15)))
    open(DIGEST, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"신규 {new_n} / 최근7일 {len(recent)} / 실패 {failed}")


if __name__ == "__main__":
    main()
