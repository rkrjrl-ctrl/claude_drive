"""휴대폰 알림(ntfy). 11/3 사용자: 헤드라인으로 이슈를 계속 훑으려는 알림이라 좀 많아도 괜찮다.

  python scripts/notify.py news            # 수집 직후(news-collect 액션): 새 단독·속보·주목·관련 보도자료
  python scripts/notify.py picks <md 파일>  # 추천 글이 올라오면(news-picks-notify 액션): 1차 추천·2차 보충

받는 주제 이름은 깃허브 비밀값 NTFY_TOPIC에만 둔다(공개 저장소라 코드에 적으면 누구나 구독한다).
알림 하나에 헤드라인 하나, 누르면 기사가 열린다. 같은 사건은 한 번만 보낸다(news/notified.json).
속보는 한 번에 들어온 것 중 같은 소식끼리 한 통으로 합치고, 본문에 합친 기사 제목을 모두 적는다(11/4).
보낸 뒤 3시간 안에 들어온 같은 소식 기사는 다시 알리지 않는다(11/5, FOLLOW_H).
"""
import csv
import datetime
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NEWS = os.path.join(ROOT, "news")
STATE = os.path.join(NEWS, "notified.json")
LIFE_MIN = 3   # 생활·사회 알림은 돈 낱말 점수 3 이상만(병원·신청 같은 약한 말 하나로는 안 보냄, 11/5)
BOARD_URL = "https://rkrjrl-ctrl.github.io/claude_drive/news.html"
KST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime.now(KST)
TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")

WINDOW_H = int(os.environ.get("NTFY_WINDOW_H", 8))     # 이 시간 안에 들어온 것만 본다(액션이 2시간마다지만 예약이 몇 시간 늦기도 해서 넉넉히)
HOT_MEDIA = 3     # 이만큼 많은 매체가 다룬 사건이면 「주목」 알림(보드 표시는 8곳, 11/4 사용자 요청으로 5→3)
FOLLOW_H = 3      # 속보를 보낸 뒤 이 시간 안에 들어온 같은 소식 기사는 다시 알리지 않는다(11/5)
MAX_PER_RUN = int(os.environ.get("NTFY_MAX", 30)) # 한 번에 이보다 많으면 나머지는 「외 N건」 한 통으로
TAG = re.compile(r"^\s*[\[【(<]\s*(단독|속보)[^\]】)>]*[\]】)>]?\s*")
# 시장이 크게 흔들린 것(주목 알림에서 매일 나오는 시황을 거를 때 쓴다)
BIG = re.compile(r"사이드카|서킷\s*브레이커|기준\s*금리|금통위|금리\s*(인하|인상|동결)|"
                 r"(코스피|코스닥|나스닥|다우|S&P).{0,25}?([3-9]|\d{2})(\.\d+)?\s*%\s*대?\s*(급락|폭락|급등|폭등)")
MARKET = re.compile(r"증시|코스피|코스닥|나스닥|다우|S&P|국제유가|유가|환율|금값|국채|채권|외환|비트코인|프리뷰|시황|마감|출발")
TRACK = {"INS": "보험·의료", "INV": "투자·경제", "LIFE": "생활·사회", "ETC": "일반"}
STOP = {"속보", "단독", "종합", "영상", "포토", "사진", "오늘", "내일", "올해", "이번", "관련", "대한", "위해", "통해"}


def tokens(title):
    return {t for t in re.findall(r"[가-힣A-Za-z0-9]{2,}", title) if t not in STOP}


def same(a, b):
    """fetch_news.cluster와 같은 기준: 단어 겹침 0.3 이상이거나 4단어 이상 공유."""
    u, i = a | b, len(a & b)
    return bool(u) and (i / len(u) >= 0.3 or i >= 4)


def ts(s):
    s = s.strip()
    if len(s) == 10:
        s += "T00:00+09:00"
    try:
        d = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=KST)


def send(title, message, click, tags=(), priority=3, actions=None):
    if os.environ.get("NTFY_DRY"):   # 시험: 보내지 않고 화면에만
        print(f"[{title}] {message} → {click[:60]}")
        for a in actions or []:
            print(f"  버튼 「{a['label']}」 → {a['url'][:60]}")
        return
    body = {"topic": TOPIC, "title": title[:250], "message": message[:1300], "click": click,
            "tags": list(tags), "priority": priority}
    if actions:
        body["actions"] = actions[:3]   # ntfy는 버튼 3개까지
    req = urllib.request.Request(SERVER, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


# ---------------- 속보 묶음(11/4 시험) ----------------
# 한 번에 들어온 속보를 알림 한 통에 줄 목록으로 담는다. 같은 사건은 한 줄로 합치고 「외 N건」을 붙인다.
# 잘못 합쳐져도 첫 줄과 건수는 남으니, 같은 사건을 빼는 방식보다 잃는 게 적다.
DIGEST_LINES = 12
RARE_DF = 0.006   # 최근 기사 제목에서 이 비율보다 드물게 나온 단어만 「사건을 가리키는 단어」로 본다


def word_df():
    """이번 달·지난달 기사 제목의 단어별 출현 비율(흔한 단어를 거를 때 쓴다)."""
    df, n = {}, 0
    for m in sorted({NOW.strftime("%Y-%m"), (NOW - datetime.timedelta(days=20)).strftime("%Y-%m")}):
        p = os.path.join(NEWS, m + ".csv")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                n += 1
                for t in tokens(TAG.sub("", r.get("title", ""))):
                    df[t] = df.get(t, 0) + 1
    return {t: c / max(n, 1) for t, c in df.items()}


def words(title):
    return {t for t in tokens(title) if not any(ch.isdigit() for ch in t)}   # 숫자 조각은 사건과 상관없이 자주 겹친다


# 같은 묶음 안에서 값이 다르면 다른 소식이다(11/4 미국 중간선거: 「민주당 승리 예측」만 겹쳐 텍사스·캔자스·아이오와가 한 통이 됐다)
KINDS = [
    ("지역", "앨라배마 알래스카 애리조나 아칸소 캘리포니아 콜로라도 코네티컷 델라웨어 플로리다 조지아 하와이 아이다호 일리노이 "
             "인디애나 아이오와 캔자스 켄터키 루이지애나 메인주 메릴랜드 매사추세츠 미시간 미네소타 미시시피 미주리 몬태나 네브래스카 "
             "네바다 뉴햄프셔 뉴저지 뉴멕시코 뉴욕주 노스캐롤라이나 노스다코타 오하이오 오클라호마 오리건 펜실베이니아 로드아일랜드 "
             "사우스캐롤라이나 사우스다코타 테네시 텍사스 유타 버몬트 버지니아 워싱턴주 웨스트버지니아 위스콘신 와이오밍".split()),
    ("의회", ["상원", "하원", "주지사"]),
    ("시장", ["코스피", "코스닥", "나스닥", "다우", "환율", "국채", "유가", "금값", "비트코인"]),
]


def marks(ws):
    out = {}
    for kind, keys in KINDS:
        hit = {k for k in keys if any(k in t for t in ws)}
        if "버지니아" in hit and "웨스트버지니아" in hit:
            hit.discard("버지니아")
        if hit:
            out[kind] = hit
    return out


def conflict(a, b):
    ma, mb = marks(a), marks(b)
    return any(k in mb and ma[k] != mb[k] for k in ma)


def same_event(a, b, df):
    """같은 사건인가: 4단어 이상 겹치거나, 3단어 이상이면서 겹침 0.3 이상이거나, 드문 단어가 2개 이상 겹치면.
    (짧은 시황 제목은 「오른·마감」 두 단어만으로 0.3을 넘어 환율과 코스닥이 묶였다)
    단, 두 제목에 같은 종류(주 이름·상원/하원·지수)의 다른 값이 있으면 다른 소식으로 본다."""
    if conflict(a, b):
        return False
    u, i = a | b, len(a & b)
    if i >= 4 or (i >= 3 and i / len(u) >= 0.3):
        return True
    return sum(1 for t in a & b if df.get(t, 0) <= RARE_DF) >= 2


# 시황 속보: 지수·환율의 개장·마감·장중 등락. 한 번에 들어온 것은 내용이 달라도 「시황」 한 통으로 합친다
# (11/4 사용자 「코스피로 묶인 거면 한 번에 봐도 문제 될 건 없어」 「그래도 괜찮을 거 같아」)
MARKET_BREAK = re.compile(r"(코스피|코스닥|환율|나스닥|다우|S&P|뉴욕증시|니케이|닛케이|항셍|상하이)"
                          r".{0,40}?(개장|출발|마감|장중|하락\s*전환|상승\s*전환|붕괴|돌파|회복|하회|상회|혼조|급락|급등|\d+\s*(p|포인트|원|%))")


def is_market(msg):
    return bool(MARKET_BREAK.search(msg)) and "상장" not in msg   # 개별 종목 상장 소식은 시황이 아님


def nodigit(ws):
    return {t for t in ws if not any(ch.isdigit() for ch in t)}


def similar(w, t, y, ty, df):
    """같은 소식인가(묶을 때·이미 보냈는지 볼 때 같이 쓴다). w·y는 숫자 뺀 단어, t·ty는 숫자 포함 단어."""
    return same_event(w, y, df) or same(t, ty)


def fits(w, t, members, df):
    """묶음(members=[(단어, 단어+숫자), ...])에 붙일 수 있나: 어느 기사와든 비슷하고, 어느 기사와도 주 이름 등이 다르지 않으면.
    (11/5 사용자 「뉴스 합치기가 안 되고 있다」: 첫 기사와만 견주어 「경상흑자 600억달러 첫 돌파」가 「9월 경상수지 614억달러」와 따로 갔다.
    하루치를 꼬리 물기로 다 잇는 방식은 미국 선거 속보 48건이 한 묶음이 되어 쓰지 않는다)"""
    return (any(similar(w, t, y, ty, df) for y, ty in members)
            and not any(conflict(w, y) for y, _ in members))


def group_events(cands, df=None):
    """같은 소식끼리 묶는다. 반환: [[(후보, 단어집합), ...], ...] (첫 기사가 대표).
    시황 속보는 모두 한 묶음(묶음 첫 항목에 market=True 표시)."""
    df = df if df is not None else word_df()
    groups, market = [], None
    for c in cands:
        w = words(c["msg"])
        if is_market(c["msg"]):
            if market is None:
                market = [(dict(c, market=True), w)]
                groups.append(market)
            else:
                market.append((c, w))
            continue
        t = tokens(c["msg"])
        for g in groups:
            if g is market:
                continue
            if fits(w, t, [(y, tokens(x["msg"])) for x, y in g], df):
                g.append((c, w))
                break
        else:
            groups.append([(c, w)])
    # 시황이 하나뿐이면 같은 소식 묶음에 붙인다(11/5: 「코스피 4% 급락에 매도 사이드카」만 시황으로 빠져 사이드카 묶음과 따로 갔다)
    if market is not None and len(market) == 1:
        c0, w0 = market[0]
        for g in groups:
            if g is not market and fits(w0, tokens(c0["msg"]), [(y, tokens(x["msg"])) for x, y in g], df):
                g.append((dict(c0, market=False), w0))
                groups.remove(market)
                break
    return groups


def event_message(kind, g):
    """같은 소식 묶음 하나 → 알림 한 통. 합친 기사 제목을 본문에 모두 적는다(11/4 사용자 「한 알림에서 제목은 확인 가능하게」).
    반환: (제목, 본문, 누르면 열 주소, 버튼)"""
    c = g[0][0]
    track = TRACK.get(c.get("track"), "")
    if len(g) == 1:
        return f"[{kind}] {track} · {c['source']}", c["msg"], c["link"], None
    lines, size = [], 0
    for x, _ in g:
        line = f"· {x['msg']} ({x['source']})"
        if size + len(line.encode("utf-8")) > 3600:   # ntfy 본문은 4KB까지
            lines.append(f"… 외 {len(g) - len(lines)}건")
            break
        lines.append(line)
        size += len(line.encode("utf-8")) + 1
    actions = [{"action": "view", "label": x["source"][:20], "url": x["link"]} for x, _ in g[:3]]
    label = f"시황 {len(g)}건" if c.get("market") else f"같은 소식 {len(g)}건"
    return f"[{kind}] {track} · {label}", "\n".join(lines), c["link"], actions


def digest(cands, label):
    """속보 여러 건 → 한 통(제목, 본문, 버튼). cands는 시간 순."""
    groups = group_events(cands)
    lines, size = [], 0
    for i, g in enumerate(groups):
        line = "· " + g[0][0]["msg"] + (f" (외 {len(g) - 1}건)" if len(g) > 1 else "")
        if i >= DIGEST_LINES or size + len(line.encode("utf-8")) > 3600:   # ntfy 본문은 4KB까지
            lines.append(f"… 외 {len(groups) - i}건은 뉴스 보드에서")
            break
        lines.append(line)
        size += len(line.encode("utf-8")) + 1
    title = f"속보 {len(cands)}건 · {label}" + (f" (같은 소식 묶어 {len(groups)}줄)" if len(groups) < len(cands) else "")
    actions = [{"action": "view", "label": g[0][0]["msg"][:18], "url": g[0][0]["link"]} for g in groups[:3]]
    return title, "\n".join(lines), actions


def run_digest_test(start, end, mode="each"):
    """지난 기사로 시험 알림을 보낸다(보낸 기록 notified.json은 건드리지 않음).
    each: 같은 소식만 합쳐 소식마다 한 통(11/4 사용자 「비슷해 보이는 것들만 묶어서」), one: 전부 한 통."""
    a, b = ts(start), ts(end)
    cands = []
    for r in sorted(recent_rows(a), key=lambda r: r["date"]):
        d = ts(r["date"])
        m = TAG.match(r["title"])
        if not (m and m.group(1) == "속보" and d and d < b) or r.get("via") in ("gov", "intl"):
            continue
        head = re.sub(r"\s+-\s+[^-]{1,20}$", "", r["title"][m.end():].strip() or r["title"])
        cands.append({"msg": head, "link": r["link"], "source": r["source"], "track": r.get("track")})
    if not cands:
        print("그 시간에 속보가 없음"); return
    if mode == "each":
        groups = group_events(cands)
        for g in groups:
            title, msg, click, actions = event_message("속보·시험", g)
            send(title, msg, click, ["zap"], actions=actions)
        print(f"시험 알림 보냄: 속보 {len(cands)}건 → {len(groups)}통")
        return
    title, msg, actions = digest(cands, f"{b.hour}시 시험")
    send(title, msg, BOARD_URL, ["zap"], actions=actions)
    print("시험 묶음 알림 보냄:", title)


# ---------------- 뉴스 ----------------
def recent_rows(since):
    names = {NOW.strftime("%Y-%m"), (NOW - datetime.timedelta(hours=WINDOW_H)).strftime("%Y-%m")}
    out = []
    for n in sorted(names):
        p = os.path.join(NEWS, n + ".csv")
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                d = ts(r.get("date", ""))
                if d and d >= since:
                    out.append(r)
    return out


def candidates():
    since = NOW - datetime.timedelta(hours=WINDOW_H)
    out = []
    try:
        with open(os.path.join(NEWS, "board.json"), encoding="utf-8") as f:
            board = json.load(f)
    except (OSError, ValueError):
        board = {"tracks": {}, "gov": []}
    rel = board.get("govRel", 3)
    # 1) 기관 보도자료(생활 관련만)
    for r in board.get("gov", []):
        d = ts(r.get("date", ""))
        if d and d >= since and (r.get("rel") is None or r["rel"] >= rel):
            out.append({"kind": "gov", "title": f"보도자료 · {r['source'].replace(' 보도자료', '')}",
                        "msg": r["title"], "link": r["link"], "tags": ["page_facing_up"], "key": r["title"]})
    # 2) 단독·속보(묶기 전 기사 그대로)
    for r in sorted(recent_rows(since), key=lambda r: r["date"]):
        if r.get("via") in ("gov", "intl"):
            continue
        m = TAG.match(r["title"])
        if not m:
            continue
        kind = m.group(1)
        if r.get("track") in ("LIFE", "ETC") and int(r.get("score") or 0) < LIFE_MIN:
            continue   # 생활·사회·일반 단독·속보는 돈과 이어지는 낱말이 있을 때만(정치 발언·사건 사고는 보드에서만, 11/5 사용자 「중간안」)
        head = r["title"][m.end():].strip() or r["title"]
        head = re.sub(r"\s+-\s+[^-]{1,20}$", "", head)   # 끝의 「 - 매체명」
        # 투자·경제 속보도 모두 보낸다(11/4 사용자 요청, 전에는 시장이 크게 흔들린 것만)
        out.append({"kind": kind, "title": f"[{kind}] {TRACK.get(r.get('track'), '')} · {r['source']}",
                    "msg": head, "link": r["link"], "tags": ["pushpin" if kind == "단독" else "zap"],
                    "key": head, "source": r["source"], "track": r.get("track")})
    # 3) 주목: 여러 매체가 다룬 사건(홍보성 제외)
    for t in ("INS", "INV", "LIFE"):
        for g in board.get("tracks", {}).get(t, []):
            if t == "LIFE" and (g.get("score") or 0) < LIFE_MIN:
                continue   # 생활·사회 주목은 돈과 이어지는 낱말이 있는 것만(정치·사건 큰 소식은 보드에서, 11/5)
            d, d0 = ts(g.get("last", "")), ts(g.get("first", ""))
            if not (d and d >= since and d0 and d0 >= NOW - datetime.timedelta(hours=48)):
                continue   # 며칠 지난 사건은 기사가 하나 더 붙어도 다시 알리지 않는다
            if t == "INV" and MARKET.search(g["title"]) and not BIG.search(g["title"]):
                continue   # 매일 나오는 시황(뉴욕증시 마감 등)은 시장이 크게 흔들린 것만
            if g.get("media", 0) >= HOT_MEDIA and not g.get("pr"):
                out.append({"kind": "주목", "title": f"주목 · {TRACK[t]} · {g['media']}개 매체",
                            "msg": TAG.sub("", g["title"]).strip(), "link": g["link"], "tags": ["fire"],
                            "key": g["title"]})
    return out


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_state(items):
    keep = NOW - datetime.timedelta(days=8)
    items = [x for x in items if (ts(x["t"]) or NOW) >= keep]
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"updated": NOW.isoformat(timespec="minutes"), "items": items}, f, ensure_ascii=False, indent=0)


def seen(c, items):
    """이미 보낸 것인가. 링크가 같거나, 비슷한 제목을 최근에 보냈으면 같은 사건으로 본다."""
    tok = tokens(c["key"])
    for x in items:
        if x["link"] == c["link"]:
            return True
        age = (NOW - (ts(x["t"]) or NOW)).total_seconds() / 3600
        # 주목은 주목끼리 3일, 단독·속보·보도자료는 종류 상관없이 하루(단독이 뒤에 주목이 되면 한 번 더 알린다)
        limit = 72 if c["kind"] == "주목" and x["kind"] == "주목" else (24 if c["kind"] != "주목" and x["kind"] != "주목" else -1)
        if age <= limit and same(tok, set(x["tok"])):
            return True
    return False


def run_news():
    state = load_state()
    first = state is None
    items = [] if first else state.get("items", [])
    prev = list(items)   # 지난 실행까지 보낸 것
    df = word_df()
    # 처음 보낸 지 FOLLOW_H시간 안 된 속보 묶음(g = 묶음 대표 링크). 시황은 시간마다 내용이 달라 넣지 않는다.
    # 오래 열어 두면 「특검법 소위 의결 → 법사위 통과 → 본회의 상정」 같은 새 진행이 막혀서 짧게 둔다
    past, start = {}, {}
    for x in prev:
        if x["kind"] == "속보" and not x.get("m") and ts(x["t"]):
            gid, tk = x.get("g") or x["link"], set(x["tok"])
            past.setdefault(gid, []).append((nodigit(tk), tk))
            start[gid] = min(start.get(gid, ts(x["t"])), ts(x["t"]))
    past = {g: ms for g, ms in past.items() if (NOW - start[g]).total_seconds() <= FOLLOW_H * 3600}
    todo, rec = [], {}
    for c in candidates():
        item = {"kind": c["kind"], "link": c["link"], "t": NOW.isoformat(timespec="minutes"), "tok": sorted(tokens(c["key"]))}
        if c["kind"] == "속보":
            # 속보는 지난 실행에 보낸 것과만 견준다. 이번에 같이 들어온 같은 소식은 버리지 않고 아래에서 한 통으로 합친다
            # (11/5: 8시 경상수지 기사 9개 중 4개가 여기서 버려지고 나머지가 3통으로 따로 갔다)
            if any(x["link"] == c["link"] for x in prev):
                continue
            market = is_market(c["msg"])
            if not market:
                w, t = words(c["msg"]), tokens(c["msg"])
                hit = next((gid for gid, ms in past.items() if fits(w, t, ms, df)), None)
                if hit:   # 지난 실행에 보낸 소식의 후속 기사: 알리지 않고 그 묶음에 적어 둔다
                    item["g"] = hit
                    past[hit].append((w, t))
                    items.append(item)
                    continue
            if seen(c, prev):   # 전부터 하던 하루짜리 중복 검사(제목이 아주 비슷하면)
                continue
            if market:
                item["m"] = 1
            rec[c["link"]] = item
        elif seen(c, items):
            continue
        items.append(item)
        todo.append(c)
    if first:   # 처음 한 번은 지금 있는 것을 「보낸 것」으로만 적어 둔다(지난 기사가 한꺼번에 쏟아지지 않게)
        save_state(items)
        print(f"처음 실행: 알림 없이 {len(todo)}건을 보낸 것으로 기록")
        return
    if not TOPIC:
        print(f"NTFY_TOPIC이 없어 알림을 건너뜀(보낼 것 {len(todo)}건)")
        save_state(items)
        return
    order = {"gov": 0, "단독": 1, "속보": 2, "주목": 3}
    todo.sort(key=lambda c: order[c["kind"]])   # 정렬은 안정적이라 같은 종류 안에서는 시간 순이 유지된다
    # 속보는 같은 소식끼리 한 통으로 합치고 본문에 기사 제목을 모두 적는다(11/4 사용자 「당분간은 이렇게 가자」)
    msgs = []   # (제목, 본문, 주소, 태그, 버튼)
    breaking = [c for c in todo if c["kind"] == "속보"]
    for c in todo:
        if c["kind"] == "속보":
            if breaking:
                for g in group_events(breaking, df):
                    for x, _ in g:   # 묶음 대표 링크를 적어 두면 다음 실행에서 후속 기사를 알아본다
                        if x["link"] in rec:
                            rec[x["link"]]["g"] = g[0][0]["link"]
                    t, m, link, acts = event_message("속보", g)
                    msgs.append((t, m, link, g[0][0]["tags"], acts))
                breaking = []
        else:
            msgs.append((c["title"], c["msg"], c["link"], c["tags"], None))
    sent = 0
    for t, m, link, tags, acts in msgs[:MAX_PER_RUN]:
        try:
            send(t, m, link, tags, actions=acts)
            sent += 1
        except Exception as e:  # 한 건이 실패해도 나머지는 보낸다
            print("보내기 실패:", t, e)
    rest = len(msgs) - MAX_PER_RUN
    if rest > 0:
        try:
            send(f"외 {rest}건", "나머지는 뉴스 보드에서 보세요.", BOARD_URL, ["newspaper"])
        except Exception as e:
            print("보내기 실패:", e)
    save_state(items)
    print(f"알림 {sent}건 보냄" + (f", {rest}건은 한 통으로 묶음" if rest > 0 else ""))


# ---------------- 추천 ----------------
def run_picks(path, old_path=None):
    if not TOPIC:
        print("NTFY_TOPIC이 없어 건너뜀")
        return
    with open(path, encoding="utf-8") as f:
        md = f.read()
    secs = re.split(r"(?m)^## ", md)
    if len(secs) < 2:
        return
    old = ""
    if old_path and os.path.exists(old_path):
        with open(old_path, encoding="utf-8") as f:
            old = f.read()
    if secs[-1].splitlines()[0].strip() in re.findall(r"(?m)^## (.*)$", old):
        print("새 추천 묶음이 없어(글 손질) 알림 생략")
        return
    last = secs[-1]
    head = last.splitlines()[0].strip()              # 예: 「1차 추천 (07:12)」
    if "그 밖에" in head:                             # 혹시 맨 끝이 덧붙인 목록이면 그 앞 묶음
        last = secs[-2]
        head = last.splitlines()[0].strip()
    picks = [re.sub(r"\s+—\s+.*$", "", h).strip() for h in re.findall(r"(?m)^### ([①-⑩].*)$", last)]
    if picks:
        msg = "\n".join(picks)
        title = f"영상 소재 {head.split('(')[0].strip()} {len(picks)}개"
    else:
        none = re.search(r"(?m)^- (보충 없음|오늘은 없음).*$", last)
        msg = none.group(0)[2:] if none else "고른 소재가 없어요."
        title = f"영상 소재 {head.split('(')[0].strip()}"
    send(title, msg, BOARD_URL + "#picks", ["clapper"], priority=4)
    print("추천 알림 보냄:", title)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "picks":
        run_picks(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    elif len(sys.argv) >= 4 and sys.argv[1] == "digest-test":
        run_digest_test(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 2 and sys.argv[1] == "test":
        send("알림 연결됨", "뉴스 알림이 이 휴대폰으로 와요. 누르면 뉴스 보드가 열려요.", BOARD_URL, ["white_check_mark"])
        print("시험 알림 보냄")
    elif len(sys.argv) >= 2 and sys.argv[1] == "recent":
        # 서버에 최근 올라간 알림의 시각·제목·버튼 수만 본다(주제 이름은 찍지 않음)
        with urllib.request.urlopen(f"{SERVER}/{TOPIC}/json?poll=1&since={sys.argv[2] if len(sys.argv) > 2 else '2h'}", timeout=20) as r:
            for line in r.read().decode("utf-8").splitlines():
                m = json.loads(line)
                if m.get("event") == "message":
                    t = datetime.datetime.fromtimestamp(m["time"], KST).strftime("%H:%M")
                    print(t, m.get("title", ""), f"| 버튼 {len(m.get('actions', []))}개 | 본문 {len(m.get('message', ''))}자")
    else:
        run_news()
