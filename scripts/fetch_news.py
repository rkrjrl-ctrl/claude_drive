#!/usr/bin/env python3
"""경제·보험·의료정책 뉴스 헤드라인 수집 (토큰 없음). 제목·언론사·시각·링크만 저장하고 본문은 저장하지 않는다.
결과: news/YYYY-MM.csv(월별 누적) · news/DIGEST.md(최근 7일 요약 목록)
선택 입력: 환경변수 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (네이버 개발자센터 검색 API 키. 없으면 건너뜀)
기사는 '소재 후보'일 뿐 근거 자료가 아니다. 영상에 쓰기 전 1차 출처(보도자료·법령)로 확인한다.
"""
import csv, datetime, email.utils, glob, hashlib, html, json, math, os, re, ssl, time, urllib.parse, urllib.request
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

# 종류: all=전부 보관 / scored=키워드 점수 > 0만 보관 / gov=기관 보도자료(전부) / intl=해외 영문 헤드라인(전부, 별도 구역)
# 세 번째 칸 뒤의 트랙은 점수로 분류가 안 될 때의 기본값: INS=보험·의료, INV=투자·경제
FEEDS = [
    # ── 보험·의료·연금 ──
    ("보험신보", "https://www.insnews.co.kr/rss/allArticle.xml", "all", "INS"),
    ("보험매일", "http://www.fins.co.kr/rss/allArticle.xml", "all", "INS"),
    ("보험저널", "https://www.insjournal.co.kr/rss/allArticle.xml", "all", "INS"),
    ("한국보험신문", "https://www.insweek.co.kr/rss/allArticle.xml", "all", "INS"),
    ("청년의사", "https://www.docdocdoc.co.kr/rss/allArticle.xml", "scored", "INS"),
    ("메디파나", "https://www.medipana.com/rss/allArticle.xml", "scored", "INS"),
    ("의협신문", "https://www.doctorsnews.co.kr/rss/allArticle.xml", "scored", "INS"),
    ("히트뉴스", "https://www.hitnews.co.kr/rss/allArticle.xml", "scored", "INS"),
    ("코리아바이오메드", "https://www.koreabiomed.com/rss/allArticle.xml", "scored", "INS"),
    ("구글뉴스 건강", "https://news.google.com/rss/headlines/section/topic/HEALTH?hl=ko&gl=KR&ceid=KR:ko", "scored", "INS"),
    ("보건복지부", "https://www.mohw.go.kr/rss/board.es?mid=a10503010100&bid=0027", "gov", "INS"),
    ("생명보험협회", "https://www.klia.or.kr/rss/board.do", "gov", "INS"),
    # ── 투자·증권·거시경제 ──
    ("인포맥스", "https://news.einfomax.co.kr/rss/allArticle.xml", "scored", "INV"),
    ("인포스탁데일리", "http://www.infostockdaily.co.kr/rss/allArticle.xml", "scored", "INV"),
    ("이데일리 증권", "http://rss.edaily.co.kr/stock_news.xml", "scored", "INV"),
    ("이데일리 글로벌", "http://rss.edaily.co.kr/world_news.xml", "scored", "INV"),
    ("파이낸셜뉴스 증권", "https://www.fnnews.com/rss/r20/fn_realnews_stock.xml", "scored", "INV"),
    ("파이낸셜뉴스 국제", "https://www.fnnews.com/rss/r20/fn_realnews_international.xml", "scored", "INV"),
    ("연합뉴스 마켓", "https://www.yna.co.kr/rss/market.xml", "scored", "INV"),
    ("연합뉴스 국제", "https://www.yna.co.kr/rss/international.xml", "scored", "INV"),
    ("뉴시스 국제", "https://www.newsis.com/RSS/international.xml", "scored", "INV"),
    ("아시아경제 증권", "https://www.asiae.co.kr/rss/stock.htm", "scored", "INV"),
    ("한국경제 국제", "https://www.hankyung.com/feed/international", "scored", "INV"),
    ("조선비즈", "https://biz.chosun.com/arc/outboundfeeds/rss/?outputType=xml", "scored", "INV"),
    ("SBS 국제", "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=07&plink=RSSREADER", "scored", "INV"),
    ("구글뉴스 세계", "https://news.google.com/rss/headlines/section/topic/WORLD?hl=ko&gl=KR&ceid=KR:ko", "scored", "INV"),
    ("토큰포스트", "https://www.tokenpost.kr/rss", "scored", "INV"),
    ("한국은행 보도자료", "https://www.bok.or.kr/portal/bbs/B0000501/news.rss?menuNo=200691", "gov", "INV"),
    ("한국은행 통화정책", "https://www.bok.or.kr/portal/bbs/P0000559/news.rss?menuNo=200690", "gov", "INV"),
    ("한국은행 통계", "https://www.bok.or.kr/portal/bbs/B0000169/news.rss?menuNo=200692", "gov", "INV"),
    ("금융위원회", "https://www.fsc.go.kr/about/fsc_bbs_rss/?fid=0111", "gov", "INV"),  # 점검 중이면 건너뜀
    ("FRB 보도자료", "https://www.federalreserve.gov/feeds/press_all.xml", "intl", "INV"),
    ("ECB 보도자료", "https://www.ecb.europa.eu/rss/press.html", "intl", "INV"),
    ("CNBC", "https://www.cnbc.com/id/10001147/device/rss/rss.html", "intl", "INV"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "intl", "INV"),
    # ── 양쪽에 걸친 종합 경제지 (점수로 트랙 분류) ──
    ("연합뉴스 경제", "https://www.yna.co.kr/rss/economy.xml", "scored", ""),
    ("연합뉴스 최신", "https://www.yna.co.kr/rss/news.xml", "scored", ""),
    ("연합뉴스TV 경제", "https://www.yonhapnewstv.co.kr/category/news/economy/feed/", "scored", ""),
    ("한겨레 경제", "https://www.hani.co.kr/rss/economy/", "scored", ""),
    ("경향 경제", "https://www.khan.co.kr/rss/rssdata/economy_news.xml", "scored", ""),
    ("조선 경제", "https://www.chosun.com/arc/outboundfeeds/rss/category/economy/?outputType=xml", "scored", ""),
    ("동아 경제", "https://rss.donga.com/economy.xml", "scored", ""),
    ("서울경제 금융", "https://www.sedaily.com/rss/finance", "scored", ""),
    ("서울경제 경제", "https://www.sedaily.com/rss/economy", "scored", ""),
    ("서울경제 부동산", "https://www.sedaily.com/rss/realestate", "scored", "INV"),
    ("머니투데이", "https://rss.mt.co.kr/mt_news.xml", "scored", ""),
    ("이데일리", "http://rss.edaily.co.kr/edaily_news.xml", "scored", ""),
    ("아시아경제", "https://www.asiae.co.kr/rss/all.htm", "scored", ""),
    ("아시아경제 부동산", "https://www.asiae.co.kr/rss/realestate.htm", "scored", "INV"),
    ("뉴시스 경제", "https://www.newsis.com/RSS/economy.xml", "scored", ""),
    ("뉴시스 금융", "https://www.newsis.com/RSS/bank.xml", "scored", ""),
    ("한국경제 금융", "https://www.hankyung.com/feed/finance", "scored", ""),
    ("한국경제 경제", "https://www.hankyung.com/feed/economy", "scored", ""),
    ("한국경제 부동산", "https://www.hankyung.com/feed/realestate", "scored", "INV"),
    ("파이낸셜뉴스 전체", "https://www.fnnews.com/rss/r20/fn_realnews_all.xml", "scored", ""),
    ("파이낸셜뉴스 금융", "https://www.fnnews.com/rss/r20/fn_realnews_finance.xml", "scored", ""),
    ("파이낸셜뉴스 부동산", "https://www.fnnews.com/rss/r20/fn_realnews_realestate.xml", "scored", "INV"),
    ("SBS 경제", "https://news.sbs.co.kr/news/SectionRssFeed.do?sectionId=02&plink=RSSREADER", "scored", ""),
    ("JTBC 경제", "https://news-ex.jtbc.co.kr/v1/get/rss/section/economy", "scored", ""),
    ("노컷뉴스 경제", "https://rss.nocutnews.co.kr/category/economy.xml", "scored", ""),
    ("오마이뉴스 경제", "https://rss.ohmynews.com/rss/economy.xml", "scored", ""),
    ("서울파이낸스", "https://www.seoulfn.com/rss/allArticle.xml", "scored", ""),
    ("이코노믹리뷰", "https://www.econovill.com/rss/allArticle.xml", "scored", ""),
    ("구글뉴스 비즈니스", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ko&gl=KR&ceid=KR:ko", "scored", ""),
]
# 구글 뉴스 키워드 — 두 트랙을 같은 규모로 둔다 (각 45개)
QUERIES_INS = [
    "실손보험", "4세대 실손", "실손 청구 간소화", "도수치료 비급여", "암보험", "치매보험", "간병보험", "종신보험", "어린이보험",
    "자동차보험", "화재보험", "변액보험", "연금보험", "보험료 인상", "보험금 지급", "보험금 분쟁", "보험 약관", "보험사기",
    "보험설계사", "GA 보험대리점", "금융감독원 보험", "보험사 실적", "킥스 지급여력", "IFRS17", "보험 해지 환급금",
    "건강보험 급여", "건강보험료 인상", "건강보험 재정", "본인부담상한제", "산정특례", "재난적의료비", "항암제 급여",
    "신약 급여 등재", "암 환자 지원", "암 치료비", "국가암검진", "호스피스 연명의료", "필수의료", "장기요양보험",
    "국민연금 개혁", "국민연금 수급", "퇴직연금 의무화", "퇴직연금 수익률", "기초연금", "주택연금",
]
QUERIES_INV = [
    "코스피 전망", "코스닥", "외국인 순매도", "사이드카 서킷브레이커", "증시 폭락", "반도체 주가", "삼성전자 SK하이닉스",
    "엔비디아", "나스닥 마감", "뉴욕증시 마감", "S&P500", "미국 국채금리", "연준 금리 인하", "연준 의장 발언", "FOMC",
    "한국은행 금융통화위원회", "기준금리", "원달러 환율", "달러 강세", "엔화 위안화", "금값 사상 최고", "은값 구리값",
    "국제 유가", "ETF 순자산", "배당주", "리츠", "공모주 IPO", "목표주가 증권사", "공매도", "개인투자자 순매수",
    "비트코인", "스테이블코인", "회사채 금리", "미국 고용지표", "미국 CPI", "중국 경제 둔화", "일본 금리 인상",
    "무역 관세", "수출 증가율", "경상수지", "성장률 전망", "스태그플레이션", "가계부채 DSR", "아파트 거래", "종합부동산세 양도세",
]
QUERIES = [(q, "INS") for q in QUERIES_INS] + [(q, "INV") for q in QUERIES_INV]
# 제목 키워드 가중치 — 트랙별로 따로 센다. 점수는 선별용 거친 지표일 뿐 판단이 아니다.
# 두 트랙의 최고 가중치·키워드 규모를 비슷하게 맞춰 한쪽으로 쏠리지 않게 한다.
INS_W = {
    "실손": 3, "암보험": 3, "암 ": 1, "항암": 2, "암환자": 2, "보험료": 2, "약관": 2, "분쟁": 2, "보험금": 3, "보험사기": 2,
    "산정특례": 3, "건강보험": 2, "건보": 2, "급여": 1, "비급여": 2, "금융감독원": 1, "금감원": 1, "보험업법": 2, "변액": 3,
    "연금": 2, "퇴직연금": 2, "국민연금": 2, "기초연금": 2, "주택연금": 2, "IRP": 2, "치매": 1, "간병": 2, "종신": 2, "자동차보험": 2,
    "보험": 1, "판결": 1, "대법원": 2, "손해보험": 1, "생명보험": 1, "손보": 1, "생보": 1, "보험사": 1, "킥스": 2, "K-ICS": 2,
    "IFRS17": 2, "재난적의료비": 3, "본인부담": 2, "호스피스": 2, "연명의료": 2, "신약": 1, "의료법": 1, "필수의료": 1, "요양": 1,
}
INV_W = {
    "코스피": 3, "코스닥": 2, "증시": 2, "주가": 1, "나스닥": 3, "S&P": 2, "다우": 2, "뉴욕증시": 3, "월가": 1, "반도체": 2,
    "삼성전자": 2, "하이닉스": 2, "엔비디아": 3, "외국인": 1, "순매도": 2, "순매수": 2, "사이드카": 3, "서킷브레이커": 3,
    "폭락": 2, "급락": 2, "급등": 2, "ETF": 3, "배당": 2, "리츠": 2, "공모주": 2, "IPO": 2, "목표주가": 1, "공매도": 2,
    "금값": 3, "은값": 2, "구리": 1, "유가": 2, "환율": 3, "원달러": 3, "달러": 2, "엔화": 2, "위안화": 2, "국채": 2, "채권": 2,
    "회사채": 1, "기준금리": 3, "금리": 2, "금통위": 3, "금융통화위원회": 3, "연준": 3, "FOMC": 3, "파월": 2, "물가": 2, "CPI": 2,
    "성장률": 2, "GDP": 2, "고용지표": 2, "경기침체": 2, "경기 침체": 2, "스태그플레이션": 2, "수출": 1, "무역수지": 2, "경상수지": 2,
    "관세": 2, "가계부채": 2, "DSR": 1, "대출금리": 1, "부동산": 1, "아파트": 1, "전세": 1, "종부세": 1, "양도세": 1,
    "비트코인": 2, "가상자산": 2, "스테이블코인": 2, "이더리움": 1,
}
TRACK_NAME = {"INS": "보험·의료", "INV": "투자·경제"}
CHECK_CAP = 6_000_000
COLS = ["date", "source", "title", "link", "score", "tags", "via", "track"]


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


def classify(title, hint=""):
    """(점수, 키워드, 트랙). 두 트랙 중 점수가 높은 쪽. 같거나 둘 다 0이면 피드의 기본 트랙(hint)."""
    hi = [k.strip() for k in INS_W if k in title]
    hv = [k.strip() for k in INV_W if k in title]
    si = sum(INS_W[k] for k in INS_W if k in title)
    sv = sum(INV_W[k] for k in INV_W if k in title)
    if si > sv or (si == sv and si and hint != "INV"):
        return si, hi, "INS"
    if sv > si or (si == sv and sv):
        return sv, hv, "INV"
    return 0, [], hint


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


# 세부 주제 — 한 사건에 쏠리지 않게 주제별로 상위 3개 사건씩 보여 주기 위한 분류 (먼저 맞는 주제에 들어간다)
SUBTOPICS = {
    "INS": [
        ("실손·비급여", ["실손", "비급여", "도수치료", "관리급여"]),
        ("암·질병 보장 상품", ["암보험", "암 진단", "암보장", "치매", "간병", "종신", "진단비", "선지급", "납입면제", "어린이보험"]),
        ("건강보험·의료제도", ["건강보험", "건보", "산정특례", "본인부담", "재난적의료비", "급여", "위고비", "필수의료", "요양", "호스피스", "연명의료", "신약"]),
        ("국민연금·퇴직연금", ["국민연금", "퇴직연금", "기초연금", "주택연금", "IRP", "연금저축", "연금"]),
        ("보험금 분쟁·판결", ["분쟁", "보험금", "약관", "판결", "대법원", "보험사기", "민원", "금감원", "금융감독원"]),
        ("보험업계·상품 출시", ["보험사", "손보", "생보", "생명", "손해보험", "킥스", "K-ICS", "IFRS17", "GA", "설계사", "인수", "출시", "보험"]),
    ],
    "INV": [
        ("가상자산", ["비트코인", "이더리움", "가상자산", "스테이블코인", "코인", "BTC"]),
        ("미국·해외 증시", ["뉴욕증시", "나스닥", "S&P", "다우", "엔비디아", "월가", "마이크론", "美증시", "미국증시", "유럽증시", "일본증시", "중국증시", "상하이"]),
        ("국내 증시", ["코스피", "코스닥", "사이드카", "서킷브레이커", "순매도", "순매수", "공매도", "증시", "삼성전자", "하이닉스", "특징주"]),
        ("금리·중앙은행", ["기준금리", "금통위", "금융통화위원회", "연준", "FOMC", "파월", "ECB", "일본은행", "금리"]),
        ("환율·달러", ["환율", "원달러", "달러", "엔화", "위안화"]),
        ("원자재·금·유가", ["금값", "은값", "유가", "구리", "원유", "WTI"]),
        ("채권·ETF·배당", ["국채", "채권", "회사채", "ETF", "배당", "리츠", "공모주", "IPO"]),
        ("부동산·가계부채", ["부동산", "아파트", "전세", "가계부채", "DSR", "대출", "종부세", "양도세", "주택"]),
        ("거시지표·무역", ["물가", "CPI", "성장률", "GDP", "고용", "수출", "무역", "경상수지", "관세", "경기침체", "스태그플레이션"]),
    ],
}


def subtopic(track, title):
    for name, kws in SUBTOPICS.get(track, []):
        if any(k in title for k in kws):
            return name
    return "기타"


STOP = {"속보", "단독", "종합", "영상", "포토", "사진", "오늘", "내일", "올해", "이번", "관련", "대한", "위해", "통해"}


def tokens(title):
    return {t for t in re.findall(r"[가-힣A-Za-z0-9]{2,}", title) if t not in STOP}


def cluster(items, limit):
    """제목이 비슷한 기사를 한 사건으로 묶는다(단어 겹침 비율 0.3 이상 또는 4단어 이상 공유). 대표 1건 + 건수."""
    groups = []
    for r in items:
        t = tokens(r["title"])
        for g in groups:
            u = g["tok"] | t
            inter = len(g["tok"] & t)
            if u and (inter / len(u) >= 0.3 or inter >= 4):
                g["n"] += 1
                g["src"].add(r["source"])
                break
        else:
            groups.append({"rep": r, "tok": t, "n": 1, "src": {r["source"]}})
    for g in groups:
        g["rank"] = int(g["rep"]["score"]) + 2 * math.log2(g["n"]) + min(len(g["src"]), 8) * 0.3
    return sorted(groups, key=lambda g: g["rank"], reverse=True)[:limit]


def fmt(g):
    r = g["rep"]
    more = f" — **외 {g['n'] - 1}건**({len(g['src'])}개 매체)" if g["n"] > 1 else ""
    return f"- ({r['score']}) {r['date'][5:10]} {r['source']} — [{r['title']}]({r['link']}){more}"


def main():
    os.makedirs(ROOT, exist_ok=True)
    hint_by_via = {f"rss:{n}": h for n, _u, _k, h in FEEDS}
    hint_by_via.update({f"google:구글뉴스:{q}": h for q, h in QUERIES})
    rows = {}
    pool = read_csv(LEGACY)  # 옛 단일 파일이 있으면 월별 파일로 옮긴다
    for mf in month_files():
        pool += read_csv(os.path.join(ROOT, mf + ".csv"))
    for r in pool:
        if r["via"] not in ("gov", "intl"):  # 기존 기사도 새 점수표로 다시 계산한다
            sc, tags, tr = classify(r["title"], hint_by_via.get(r["via"], ""))
            r["score"], r["tags"], r["track"] = sc, "|".join(tags), tr
        else:
            r["track"] = r.get("track") or "INS"
        rows[key(r["title"])] = r
    seen_before = set(rows)

    jobs = [(n, u, k, "rss", h) for n, u, k, h in FEEDS]
    jobs += [(f"구글뉴스:{q}", "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{q} when:2d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"}), "scored", "google", h) for q, h in QUERIES]

    def run(j):
        raw = get(j[1])
        return j, (parse_rss(raw, j[3], j[0]) if raw else None)

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(run, jobs))
    if os.environ.get("NAVER_CLIENT_ID"):
        res += [((f"네이버:{q}", "", "scored", "naver", h), naver(q)) for q, h in QUERIES]

    failed = []
    for (n, _u, kind, via, hint), items in res:
        if items is None or (not items and via == "rss"):
            failed.append(n)
            continue
        for x in items:
            sc, tags, tr = classify(x["title"], hint)
            if x["source"] in BLOCK_SOURCES or (kind == "scored" and sc == 0):
                continue
            k = key(x["title"])
            if k not in rows:
                v = kind if kind in ("gov", "intl") else f"{via}:{n}"
                rows[k] = {"date": x["date"].isoformat(timespec="minutes"), "source": x["source"], "title": x["title"],
                           "link": x["link"], "score": sc, "tags": "|".join(tags), "via": v, "track": tr or hint or "INV"}

    by_month = {}
    for r in rows.values():
        by_month.setdefault(r["date"][:7], []).append(r)
    for mon, rs in by_month.items():
        if mon not in month_files(8):  # 너무 오래된 기사는 새로 만들지 않는다
            continue
        with open(os.path.join(ROOT, mon + ".csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, COLS, extrasaction="ignore")
            w.writeheader()
            w.writerows(sorted(rs, key=lambda r: (r["date"], r["link"]), reverse=True))
    if os.path.exists(LEGACY):
        os.remove(LEGACY)

    ordered = sorted(rows.values(), key=lambda r: r["date"], reverse=True)
    week = (NOW - datetime.timedelta(days=7)).isoformat(timespec="minutes")
    recent = [r for r in ordered if r["date"] >= week]
    news = [r for r in recent if r["via"] not in ("gov", "intl")]
    new_n = len(set(rows) - seen_before)
    cnt = {t: sum(1 for r in news if r["track"] == t) for t in ("INS", "INV")}
    by_score = lambda t: sorted((r for r in news if r["track"] == t), key=lambda r: (int(r["score"]), r["date"]), reverse=True)
    govs = lambda t: [r for r in recent if r["via"] == "gov" and r["track"] == t][:20]
    ins_trade = [r for r in recent if r["via"].endswith(("보험신보", "보험매일", "보험저널", "한국보험신문"))][:15]
    intl = [r for r in recent if r["via"] == "intl"][:12]
    srcs = Counter(r["source"] for r in news)
    L = ["# 경제·보험 뉴스 요약 (자동 생성 — 직접 수정 금지)\n",
         f"- 갱신: {NOW:%Y-%m-%d %H:%M} KST  /  최근 7일 {len(news)}건 (보험·의료 {cnt['INS']} · 투자·경제 {cnt['INV']})  /  이번 신규 {new_n}건",
         f"- 소스 {len(FEEDS)}곳 + 구글뉴스 키워드 {len(QUERIES)}개(보험·의료 {len(QUERIES_INS)} · 투자·경제 {len(QUERIES_INV)})  /  네이버 {'사용' if os.environ.get('NAVER_CLIENT_ID') else '미사용'}",
         "- 제목·링크만 저장한다. **소재 후보**이며 근거 자료가 아니다. 영상에 쓰기 전 1차 출처로 확인한다. 비슷한 제목은 한 사건으로 묶어 보여 준다.",
         f"- 수집 실패 소스: {', '.join(failed) if failed else '없음'}\n"]
    for t in ("INS", "INV"):
        L.append(f"## [{TRACK_NAME[t]}] 최근 7일 — 주제별 상위 사건 (사건마다 비슷한 기사를 묶음)\n")
        buckets = {}
        for g in cluster(by_score(t)[:600], 200):
            buckets.setdefault(subtopic(t, g["rep"]["title"]), []).append(g)
        order = [n for n, _ in SUBTOPICS[t]] + ["기타"]
        for name in order:
            gs = buckets.get(name, [])
            if gs:
                L.append(f"**{name}** (사건 {len(gs)}개)")
                L += [fmt(g) for g in gs[:3]]
                L.append("")
        if not buckets:
            L.append("- 없음")
        L.append(f"\n### [{TRACK_NAME[t]}] 기관 보도자료\n")
        L += [f"- {r['date'][5:10]} [{r['source']}] [{r['title']}]({r['link']})" for r in govs(t)] or ["- 없음"]
        L.append("")
    L.append("## 보험 전문지 최신\n")
    L += [f"- {r['date'][5:10]} {r['source']} — [{r['title']}]({r['link']})" for r in ins_trade] or ["- 없음"]
    L.append("\n## 해외 원문 헤드라인 (영문, 참고용)\n")
    L += [f"- {r['date'][5:10]} {r['source']} — [{r['title']}]({r['link']})" for r in intl] or ["- 없음"]
    L.append("\n## 언론사별 건수 상위 15\n")
    L.append(", ".join(f"{s} {c}" for s, c in srcs.most_common(15)))
    open(DIGEST, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"신규 {new_n} / 최근7일 보험의료 {cnt['INS']} 투자경제 {cnt['INV']} / 실패 {failed}")


if __name__ == "__main__":
    main()
