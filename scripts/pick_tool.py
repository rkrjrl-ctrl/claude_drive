"""영상 소재 추천(daily-news-pick 예약 작업)을 가볍게 하는 도구. 11/3: 한 번에 46번 오가던 일을 10번 안팎으로.

  python scripts/pick_tool.py prep            # 몇 차인지 판단 → 수집 실행·기다림 → pull → 후보 30건 안팎 + 이미 고른 것
  python scripts/pick_tool.py fetch G1 I3     # 후보 번호(또는 주소)의 본문 + 첨부 PDF 글자(번호마다 앞부분만)
  python scripts/pick_tool.py save <파일.md> [--titles "제목1|제목2"]   # 오늘 글에 덧붙이고 picks.csv·커밋·푸시

판단(무엇을 고를지, 숫자가 맞는지)은 Claude가 하고, 정해진 일(수집 기다리기·거르기·글자 뽑기·저장)은 여기서 한다.
"""
import csv
import datetime
import html
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from notify import BIG, MARKET, TAG, same, tokens  # noqa: E402  같은 거름 기준을 쓴다

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS = os.path.join(ROOT, "news")
BRIEFS = os.path.join(ROOT, "briefs", "news")
PICKS = os.path.join(BRIEFS, "picks.csv")
# 이미 만든 영상(없으면 건너뜀). 클라우드 예약에서는 받아 둔 claude-skills 위치를 CONTENT_STATUS로 준다(11/4).
STATUS = os.environ.get("CONTENT_STATUS") or os.path.expanduser("~/.claude/skills/_memory/content-status.md")
CACHE = os.path.join(tempfile.gettempdir(), "pick_candidates.json")
KST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime.now(KST)
TODAY = NOW.strftime("%Y-%m-%d")
MD = os.path.join(BRIEFS, TODAY + ".md")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
TRACK = {"INS": "보험·의료", "INV": "투자·경제", "LIFE": "생활·사회"}


def ts(s):
    s = (s or "").strip()
    if len(s) == 10:
        s += "T00:00+09:00"
    try:
        d = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=KST)


def sh(*args, check=False):
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip())
    return r


# ---------------- prep ----------------
def round_now():
    md = open(MD, encoding="utf-8").read() if os.path.exists(MD) else ""
    if "## 2차 보충" in md:
        return None, None
    m = re.search(r"(?m)^## 1차 추천 \((\d\d:\d\d)\)", md)
    if not m:
        return "1차", NOW - datetime.timedelta(hours=36)
    if NOW.hour < 12:
        return None, None   # 오전에 1차가 이미 있으면 할 일 없음
    h, mi = map(int, m.group(1).split(":"))
    return "2차", NOW.replace(hour=h, minute=mi, second=0, microsecond=0)


def collect():
    """수집을 바로 돌리고 끝날 때까지 기다린다(최대 6분). 실패해도 있는 자료로 진행."""
    t0 = datetime.datetime.now(datetime.timezone.utc)
    if sh("gh", "workflow", "run", "news-collect.yml").returncode:
        return "수집 실행 실패(gh) — 있는 자료로 진행"
    rid = None
    for _ in range(12):
        time.sleep(5)
        out = sh("gh", "run", "list", "--workflow", "news-collect.yml", "--limit", "3",
                 "--json", "databaseId,createdAt").stdout
        for r in json.loads(out or "[]"):
            if datetime.datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")) >= t0 - datetime.timedelta(seconds=5):
                rid = r["databaseId"]
                break
        if rid:
            break
    if not rid:
        return "수집 실행을 못 찾음 — 있는 자료로 진행"
    for _ in range(72):
        out = sh("gh", "run", "view", str(rid), "--json", "status,conclusion").stdout
        st = json.loads(out or "{}")
        if st.get("status") == "completed":
            return f"수집 {st.get('conclusion')}"
        time.sleep(5)
    return "수집이 6분 안에 안 끝남 — 있는 자료로 진행"


def recent_rows(since):
    rows = []
    for d in {since.strftime("%Y-%m"), NOW.strftime("%Y-%m")}:
        p = os.path.join(NEWS, d + ".csv")
        if os.path.exists(p):
            with open(p, encoding="utf-8-sig", newline="") as f:
                rows += [r for r in csv.DictReader(f) if (ts(r["date"]) or NOW) >= since - datetime.timedelta(hours=12)]
    return rows


def alt_links(title, rows, n=2):
    """같은 사건을 다룬 기사 중 구글 뉴스가 아닌 직접 링크(본문을 바로 열 수 있게)."""
    tok, out = tokens(title), []
    for r in rows:
        if "news.google.com" in r["link"] or r["via"] in ("gov", "intl"):
            continue
        if same(tok, tokens(r["title"])) and r["link"] not in out:
            out.append(r["link"])
            if len(out) >= n:
                break
    return out


def picked_titles(days=14):
    if not os.path.exists(PICKS):
        return []
    lim = (NOW - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    with open(PICKS, encoding="utf-8-sig", newline="") as f:
        return [(r["date"], r["title"], r.get("decision", "")) for r in csv.DictReader(f) if r["date"] >= lim]


def made_videos():
    if not os.path.exists(STATUS):
        return []
    names = re.findall(r"(?m)^\s*-\s+\*\*([^*]{2,80})\*\*", open(STATUS, encoding="utf-8").read())
    return [re.sub(r"\s+", " ", n) for n in names]


def prep():
    rnd, since = round_now()
    if not rnd:
        print("끝: 오늘 할 추천이 없음(이미 2차까지 있거나, 오전인데 1차가 이미 있음)")
        return 3
    note = collect()
    sh("git", "pull", "--rebase", "-q")
    board = json.load(open(os.path.join(NEWS, "board.json"), encoding="utf-8"))
    rows = recent_rows(since)
    done = picked_titles()
    done_tok = [tokens(t) for _, t, _ in done]
    cands = []

    def add(kind, item, line, link):
        if any(same(tokens(item), d) for d in done_tok):
            return
        flat = re.sub(r"\s+", "", item)
        if any(sum(1 for w in d if w in flat) >= 3 for d in done_tok):   # 표현만 다른 같은 소식(빼지 않고 표시)
            line = "≈이미 추천 · " + line
        cid = f"{kind}{sum(1 for c in cands if c['id'][0] == kind) + 1}"
        alts = alt_links(item, rows) if "news.google.com" in link else []
        cands.append({"id": cid, "title": item, "link": link, "alts": alts})
        print(f"[{cid}] {line}")

    print(f"# {TODAY} {rnd} 후보 (기준 {since.strftime('%m-%d %H:%M')} 이후, {note})")
    print("\n## 기관 보도자료(1차 출처, 생활 관련만)")
    for r in board.get("gov", []):
        d = ts(r["date"])
        if d and d >= since and (r.get("rel") is None or r["rel"] >= board.get("govRel", 3)):
            add("G", r["title"], f"{d.strftime('%m-%d %H:%M')} {r['source'].replace(' 보도자료', '')} | {r['title']}", r["link"])
    for t, n in (("INS", 15), ("INV", 10), ("LIFE", 12)):
        print(f"\n## {TRACK[t]} 기사(여러 매체가 다룬 순, 홍보성·일상 시황 뺌)")
        k = 0
        for g in board["tracks"].get(t, []):
            d, d0 = ts(g["last"]), ts(g["first"])
            if not d or d < since or g.get("pr") or not d0 or d0 < NOW - datetime.timedelta(hours=48):
                continue   # 며칠 지난 사건은 기사가 하나 더 붙어도 다시 올리지 않는다
            if t == "INV" and MARKET.search(g["title"]) and not BIG.search(g["title"]):
                continue
            tag = f"[{g['tag']}] " if g.get("tag") else ""
            add({"INS": "I", "INV": "V", "LIFE": "L"}[t], g["title"],
                f"{g['media']}곳 {d.strftime('%m-%d')} {g['source']} | {tag}{TAG.sub('', g['title']).strip()}", g["link"])
            k += 1
            if k >= n:
                break
    print("\n## 이미 추천한 것(최근 14일, 겹치면 빼기)")
    for d, t, dec in done:
        print(f"- {d[5:]} {t} ({dec})")
    mv = made_videos()
    if mv:
        print("\n## 이미 만든 영상·접은 기획")
        print("- " + " / ".join(mv))
    json.dump({"round": rnd, "cands": cands}, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"\n(후보 {len(cands)}건. 본문은 `python scripts/pick_tool.py fetch <번호…>`)")
    return 0


# ---------------- fetch ----------------
def get(url, data=None, headers=None, timeout=25):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.geturl(), r.headers.get_content_charset() or "utf-8"


def google_decode(url):
    """구글 뉴스 RSS 주소 → 원래 기사 주소(구글이 자바스크립트로 넘기는 것을 같은 방식으로 풀어 낸다)."""
    m = re.search(r"/articles/([^?/]+)", url)
    if not m:
        return None
    aid = m.group(1)
    page = get(f"https://news.google.com/rss/articles/{aid}")[0].decode("utf-8", "ignore")
    sg = re.search(r'data-n-a-sg="([^"]+)"', page)
    tm = re.search(r'data-n-a-ts="([^"]+)"', page)
    if not (sg and tm):
        return None
    inner = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
             f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{aid}",{tm.group(1)},"{sg.group(1)}"]')
    body = "f.req=" + urllib.parse.quote(json.dumps([[["Fbv4je", inner]]]))
    raw = get("https://news.google.com/_/DotsSplashUi/data/batchexecute", body.encode(),
              {"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})[0].decode("utf-8", "ignore")
    try:
        part = json.loads(raw.split("\n\n")[1])[:-2]
        return json.loads(part[0][2])[1]
    except Exception:
        return None


def text_of(page):
    page = re.sub(r"(?is)<(script|style|noscript|header|footer|nav|form)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", page)
    t = html.unescape(re.sub(r"<[^>]+>", " ", page))
    lines = [re.sub(r"[ \t\u00a0]+", " ", l).strip() for l in t.splitlines()]
    return "\n".join(l for l in lines if len(l) >= 15)   # 메뉴·버튼 같은 짧은 줄은 뺀다


def pdf_links(page, base):
    out = []
    # 금감원: 문서보기 링크의 fileName이 .pdf면 같은 번호의 내려받기 주소
    for m in re.finditer(r'docView/view\.do\?[^"]*?atchFileId=(\w+)&(?:amp;)?fileSn=(\d+)&(?:amp;)?fileName=[^"]*?\.pdf', page):
        out.append(urllib.parse.urljoin(base, f"/fss/cmmn/file/fileDown.do?atchFileId={m.group(1)}&fileSn={m.group(2)}&bbsId="))
    # 금융위·복지부·한국은행 등: 태그(주소·제목)나 글자에 .pdf가 보이는 내려받기 링크
    for m in re.finditer(r'<a\b([^>]*)>([^<]{0,120})', page):
        tag, txt = m.group(1), m.group(2)
        h = re.search(r'href="([^"]+)"', tag)
        if not h:
            continue
        href = html.unescape(h.group(1))
        if "viewer" in href or "customLayout" in href or href.startswith("#") or "javascript" in href or "docView" in href:
            continue
        if ".pdf" in (tag + txt).lower():
            u = urllib.parse.urljoin(base, href)
            if u not in out:
                out.append(u)
    return out[:2]


def pdf_text(url):
    import pypdfium2 as pdfium   # 이 PC·액션에 있는 PDF 글자 뽑기
    data = get(url, timeout=40)[0]
    if not data.startswith(b"%PDF"):
        return ""
    doc = pdfium.PdfDocument(io.BytesIO(data))
    return "\n".join(doc[i].get_textpage().get_text_range() for i in range(min(len(doc), 12)))


def fetch(ids, limit=3500, pdf_limit=4500):
    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {"cands": []}
    by = {c["id"]: c for c in cache["cands"]}
    for x in ids:
        c = by.get(x, {"id": x, "title": x, "link": x, "alts": []})
        print(f"\n===== [{c['id']}] {c['title'][:80]}")
        urls = list(c.get("alts") or [])
        if "news.google.com" in c["link"]:   # 구글 주소는 풀어서 원래 기사로
            try:
                real = google_decode(c["link"])
            except Exception:
                real = None
            if real:
                urls.insert(0, real)
        else:
            urls.insert(0, c["link"])
        if not urls:
            print("(본문 주소를 못 찾음 — 다른 후보를 보거나 WebFetch로 검색)")
            continue
        for u in urls[:2]:
            try:
                raw, final, cs = get(u)
                page = raw.decode(cs, "ignore")
            except Exception as e:
                print(f"- 열기 실패 {u[:90]} ({e.__class__.__name__})")
                continue
            body = text_of(page)
            print(f"- 주소: {final}")
            print(body[:limit] + ("…(뒤 생략)" if len(body) > limit else ""))
            for p in pdf_links(page, final):
                try:
                    t = re.sub(r"[ \t]+", " ", pdf_text(p))
                except Exception as e:
                    print(f"- 첨부 PDF 실패 ({e.__class__.__name__})")
                    continue
                if t.strip():
                    print(f"\n- 첨부 PDF 글자 ({p[:80]}):")
                    print(t[:pdf_limit] + ("…(뒤 생략)" if len(t) > pdf_limit else ""))
                    break
            break


# ---------------- save ----------------
def save(path, titles=None):
    sec = open(path, encoding="utf-8").read().strip() + "\n"
    m = re.match(r"## (1차 추천|2차 보충) \(\d\d:\d\d\)", sec)
    if not m:
        print("오류: 파일 첫 줄이 「## 1차 추천 (HH:MM)」 또는 「## 2차 보충 (HH:MM)」이 아님")
        return 2
    rnd = m.group(1)[:2]
    sh("git", "pull", "--rebase", "-q")
    md = open(MD, encoding="utf-8").read().rstrip() + "\n\n" if os.path.exists(MD) else f"# {TODAY} 영상 소재\n\n"
    if f"## {m.group(1)}" in md:
        print(f"오류: 오늘 글에 이미 「{m.group(1)}」이 있음")
        return 2
    open(MD, "w", encoding="utf-8", newline="\n").write(md + sec)
    heads = [re.sub(r"\s+—\s+.*$", "", h).strip() for h in re.findall(r"(?m)^### [①-⑩]\s*(.*)$", sec)]
    names = [t.strip() for t in titles.split("|")] if titles else heads
    if names:
        with open(PICKS, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            for n in names:
                w.writerow([TODAY, n, "후보", rnd])
    msg = f"briefs: {TODAY} {m.group(1)}" + ("" if names else "(보충 없음)")
    sh("git", "add", MD, PICKS, check=True)
    sh("git", "commit", "-q", "-m", msg + "\n\nCo-Authored-By: Claude <noreply@anthropic.com>", check=True)
    p = sh("git", "push", "-q")
    if p.returncode:
        sh("git", "pull", "--rebase", "-q")
        p = sh("git", "push", "-q")
    print(f"저장: {os.path.relpath(MD, ROOT)} + picks.csv {len(names)}줄, 커밋「{msg}」, 푸시 {'성공' if not p.returncode else '실패: ' + p.stderr.strip()}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    if a[:1] == ["prep"]:
        sys.exit(prep())
    elif a[:1] == ["fetch"] and len(a) > 1:
        fetch(a[1:])
    elif a[:1] == ["save"] and len(a) > 1:
        t = a[a.index("--titles") + 1] if "--titles" in a else None
        sys.exit(save(a[1], t))
    else:
        print(__doc__)
