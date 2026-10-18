"""영상 원고 숫자 팩트체크 도구.

원고(.txt/.md)를 문장으로 나누고, 문장 속 숫자를 numbers/numbers.csv 카드와
sources/ 기록의 '핵심 근거' 절에서 찾아 판정 표(마크다운)를 낸다.

원고 문장에 MED 번호가 함께 적혀 있으면, 그 숫자가 인용한 기록(또는 그 기록에서 나온 카드)에
실제로 있는지도 대조해 '인용 번호 불일치'를 따로 표시한다.

실행:  python -X utf8 medical/tools/factcheck.py 원고.txt > 결과.md
일괄:  python -X utf8 medical/tools/factcheck.py --dir medical/packages --dir medical/guides --only-problems > 점검.md
옵션:  --today YYYY-MM-DD  재확인 기한 비교 기준일(기본: 오늘)
       --detail            근거 후보를 숫자마다 최대 3개까지 모두 보인다(기본 1개)
       --only-problems     '근거 없음'·'인용 번호 불일치' 문장만 보인다

파이썬 표준 라이브러리만 쓴다. 숫자 일치와 단어 겹침만 보며, 문장의 뜻이 근거와 같은지는
사람이 확인해야 한다(tools/README.md '한계').
"""
import argparse, bisect, csv, datetime, functools, glob, hashlib, os, pickle, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MED = os.path.dirname(HERE)                       # medical/
INDEX = os.path.join(MED, 'index.csv')
CARDS = os.path.join(MED, 'numbers', 'numbers.csv')
SOURCES = os.path.join(MED, 'sources')

# ---------------------------------------------------------------- 숫자 뽑기
MAG = {'조': 1e12, '천억': 1e11, '억': 1e8, '천만': 1e7, '백만': 1e6, '만': 1e4, '천': 1e3, '백': 1e2}
# 큰 단위는 숫자에 붙여 쓰거나('4만'), 띄어 썼더라도 뒤에 숫자·금액 단위가 이어질 때만('4 만 3,850원') 같은 묶음으로 읽는다
GROUP = re.compile(r'(?<![A-Za-z\d.,])((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)'
                   r'(?:(조|천억|억|천만|백만|만|천|백)|\s(조|천억|억|천만|백만|만|천)(?=\s?(?:\d|원|명|건|회)))?')


def _mag(g):
    """GROUP 일치의 큰 단위(붙여 쓴 것 또는 띄어 쓴 것)."""
    return g.group(2) or g.group(3)


UNIT = re.compile(r'\s?(%p|%포인트|퍼센트|%|원(?!인|칙|문|외|급|래|내|장)|명|건(?!강|물|축|의|설|전)|개월|개사|개(?!정|선|별|월)|회(?!사|원|의|계)|일(?!당|반|부|시|상)|년(?!생|도)|세(?!대(?!상)|부(?!터))|곳|배(?!우|아)|위(?!암|원|험)|점(?!수)|cm|kg|타|갑년|시간|mmHg)')
FRACTION = re.compile(r'(\d+)분의\s?(\d+)')
# 숫자로 보지 않을 것: 날짜, 고시·공고 번호, 판례 번호, 기록·카드 번호, 상병코드
SKIP = [re.compile(p) for p in [
    r'\d{4}-\d{2}-\d{2}', r'\d{4}\.\s?\d{1,2}\.\s?\d{1,2}\.?', r'\d{1,2}월\s?\d{1,2}일',
    r'제?\d{4}-\d+\s?호', r'제\d+(?:-\d+)?호', r'\d{2,4}[가-힣]{1,2}\d{3,}',
    r'MED-\d{4}(?:\s?[~·,]\s?(?:MED-)?\d{4}(?!\d))*', r'N-\d{4}',
    r'(?<![\d,.])\d{6,}(?![\d,])',                          # 공공데이터 번호·법령 일련번호·20261224 같은 날짜
    r'\d{4}-\d{2}(?:\s?~\s?\d{4}-\d{2})?', r'(?<![\d.])(?:19|20)\d{2}\.\d{1,2}(?![\d.])',   # 2024-04~2026-12, 2025.12
    r'[A-Z]{1,3}\d{2,}[\d.]*(?:-[A-Z]{1,3}\d{2,}[\d.]*)?', r'제\d+조(?:의\d+)?(?:제\d+항)?(?:제\d+호)?', r'\d+차', r'\d+(?:\.\d+)?:\d+(?:\.\d+)?']]
DATE = [SKIP[0], SKIP[1], SKIP[2]]


def clause_before(text, start):
    """숫자 앞 글 중 같은 마디(쉼표·쌍점·마침표 뒤부터, 최대 40자)를 돌려준다.
    예: '폐암 16.6%(2001~2005)→42.5%'의 42.5% → '폐암 16.6%(2001~2005)→'"""
    seg = text[max(0, start - 40):start]
    cut = max(seg.rfind(', '), seg.rfind('; '), seg.rfind(': '), seg.rfind('. '))
    return seg[cut + 2:] if cut >= 0 else seg


def _group_value(digits, mag):
    v = float(digits.replace(',', ''))
    return v * MAG.get(mag or '', 1)


def extract(text, keep_small=False):
    """문장에서 숫자 표현을 뽑는다. 반환: 숫자 목록, 연도·날짜 목록.
    숫자 항목 = dict(raw, values(튜플: 범위면 두 값), unit, start)"""
    masked = text
    dates = []
    for p in SKIP:
        for m in p.finditer(masked):
            if p in DATE:
                dates.append(m.group(0))
        masked = p.sub(lambda m: '#' * len(m.group(0)), masked)
    out = []
    for m in FRACTION.finditer(masked):                 # '100분의 40' → 40%
        a, b = float(m.group(1)), float(m.group(2))
        out.append(dict(raw=m.group(0), values=(round(b / a * 100, 6),), unit='%', start=m.start()))
        masked = masked[:m.start()] + '#' * len(m.group(0)) + masked[m.end():]
    groups = list(GROUP.finditer(masked))
    i = 0
    while i < len(groups):
        g = groups[i]
        start, end = g.start(), g.end()
        val = _group_value(g.group(1), _mag(g))
        has_mag = bool(_mag(g))
        j = i + 1
        # '1조 1,571억', '7만 8,979' 처럼 큰 단위 뒤에 이어지는 묶음을 합친다
        while has_mag and j < len(groups) and masked[end:groups[j].start()] in ('', ' '):
            gj = groups[j]
            prev_mag = MAG.get(_mag(groups[j - 1]) or '', 1)
            cur = _group_value(gj.group(1), _mag(gj))
            if cur >= prev_mag:
                break
            val += cur
            end = gj.end()
            has_mag = bool(_mag(gj))
            j += 1
        um = UNIT.match(masked, end)
        unit = um.group(1) if um else None
        if um:
            end = um.end()
        elif re.match(r'[가-힣]', masked[end:end + 1]) and not has_mag and not _mag(g):
            i = j                                    # '1인실', '2형', '3세대'처럼 이름에 붙은 숫자는 건너뛴다
            continue
        out.append(dict(raw=text[start:end].strip(), values=(round(val, 6),), unit=unit, start=start, end=end,
                        before=clause_before(text, start),    # 숫자 앞 같은 마디의 글(주어 확인용)
                        after=text[end:end + 8]))              # 숫자 뒤 글('넘는'·'이상' 같은 어림 표현 확인용)
        i = j
    # 범위('30~40%', '90만~843만 원')를 하나로 묶는다
    merged = []
    for it in sorted(out, key=lambda x: x['start']):
        # 범위는 단위가 같거나 앞쪽 단위가 없을 때만 묶는다('1년 6.5%~10년 45.9%'의 6.5%와 10년은 따로)
        if merged and 'end' in merged[-1] and re.fullmatch(r'\s?~\s?', masked[merged[-1]['end']:it['start']] or 'x')                 and merged[-1]['unit'] in (None, it['unit']):
            a = merged.pop()
            unit = it['unit'] or a['unit']
            merged.append(dict(raw=text[a['start']:it.get('end', it['start'])].strip(),
                               values=a['values'] + it['values'], unit=unit, start=a['start'], end=it.get('end')))
        else:
            merged.append(it)
    nums, years = [], []
    for it in merged:
        if it['unit'] is None and all(x < 100 for x in it['values']) and not re.search(r'[.,조억만천백]', it['raw']):
            if keep_small:
                nums.append(dict(it, small=True))         # 기록의 표 값(예: '호흡기결핵 27 / 1,108')
            else:
                years.append(it['raw'])                   # 단위 없는 작은 수(예: '2.0:1'의 1)는 맥락으로만
            continue
        v = it['values']
        if all(float(x).is_integer() and 1990 <= x <= 2099 for x in v) and ',' not in it['raw'] \
                and (it['unit'] in ('년', None)) and not re.search(r'[조억만천백]', it['raw']):
            years.append(it['raw'])                       # 연도(맥락)로만 쓴다
        else:
            nums.append(it)
    return nums, years + dates


UNIT_NORM = {'%포인트': '%p', '퍼센트': '%', '개사': '개'}


def unit_of(u):
    return UNIT_NORM.get(u, u)


def same_number(a, b):
    """a: 원고 숫자, b: 근거 숫자. 값이 같고 단위가 맞으면 True."""
    if len(a['values']) != len(b['values']):
        return False
    if any(abs(x - y) > 1e-6 * max(1.0, abs(y)) for x, y in zip(a['values'], b['values'])):
        return False
    ua, ub = unit_of(a['unit']), unit_of(b['unit'])
    return ua is None or ub is None or ua == ub

def loose_number(a, b):
    """'7,435천 명'처럼 원고는 단위를 붙였고 기록은 표 머리에만 단위를 적은 경우: 앞자리 숫자 글자가 같으면 후보로 본다."""
    m = re.match(r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?', a['raw'])
    return bool(m) and len(a['values']) == 1 and b['raw'] == m.group(0) and m.group(0) != a['raw'].strip()         and bool(re.search(r'[조억만천]', a['raw'][len(m.group(0)):len(m.group(0)) + 2] or ''))         and not re.search(r'[가-힣A-Za-z]$', b.get('before', ''))     # '누306' 같은 코드 숫자는 제외

# ---------------------------------------------------------------- 단어 겹침
JOSA = ('에서는', '으로는', '에서', '으로', '까지', '부터', '보다', '이다', '였다', '이었다', '했다', '된다', '한다',
        '은', '는', '이', '가', '을', '를', '의', '에', '로', '와', '과', '도', '만', '요', '고', '며')
STOP = set('약 이상 이하 미만 초과 기준 전체 대비 중 등 및 또는 그 이 수 것 때 년 원 명 건 만 억 조 천 연간 '
           '연도 한 두 세 같은 다른 있다 없다 있었다 됐다 보다 더 가장 따라 위해 대한 관련 경우 정도 '
           '사람 우리 내 나 이번 영상 숫자 통계'.split())


@functools.lru_cache(maxsize=None)
def keywords(text):
    """글의 단어 묶음. 같은 글은 한 번만 계산한다(돌려준 묶음을 고치지 않는다)."""
    words = set(re.findall(r'\d+대(?=[^가-힣]|$)', text))   # '60대'처럼 나이대는 단어로 친다
    for w in re.findall(r'[가-힣A-Za-z][가-힣A-Za-z0-9·]*', text):
        for j in JOSA:
            if w.endswith(j) and len(w) - len(j) >= 2:
                w = w[:-len(j)]
                break
        w = SYNONYM.get(w, w)
        if len(w) >= 2 and w not in STOP:
            words.add(w)
    return words


# 같은 뜻으로 쓰는 말(원고 쪽 표현으로 맞춘다)
SYNONYM = {'진료실인원': '진료인원', '실인원': '진료인원', '환자수': '환자', '사망률': '사망', '연령대': '나이',
           '유병률': '유병', '발생률': '발생', '생존율': '생존', '치명률': '치명'}


def overlap(a, b):
    """두 단어 묶음에서 겹치는 단어. 같은 단어이거나, 짧은 쪽이 3글자 이상이고 긴 쪽에 들어 있으면 겹친 것으로 본다."""
    hit = set()
    for x in a:
        for y in b:
            if x == y or (min(len(x), len(y)) >= 3 and (x in y or y in x)):
                hit.add(x)
    return hit


# 질병·제도 이름 묶음. 원고 문장에 이 이름이 있으면 근거 쪽에도 같은 묶음의 이름이 있어야 일치로 본다.
ENTITIES = [g.split('|') for g in '''위암|위의 악성|위의악성
대장암|결장|직장
폐암|기관지및폐|기관지 및 폐
유방암|유방
갑상선암|갑상선
간암|간및간내|간 및 간내
췌장암|췌장
전립선암|전립선
신장암|신장의|신장 등
방광암|방광
자궁경부암|자궁경부
난소암|난소|여성생식기
담도|담낭
백혈병|혈액암
림프종
골수종
식도암|식도
뇌졸중|뇌경색|뇌출혈|뇌내출혈|뇌혈관
심근경색|협심증|허혈성
심부전
심방세동|부정맥
고혈압
당뇨
치매|알츠하이머
파킨슨
콩팥|신장병|신부전|투석|신대체
우울
골다공증|골절
무릎|관절
디스크|추간판
척추|협착
어깨|회전근개
백내장|인공수정체|다초점
COPD|폐쇄성
천식
실손
도수
체외충격파
증식치료
산정특례
상한제|상한액
재난적의료비
부지급
의료자문
유지율
민원
분쟁조정|분조위
보험사기
임플란트
MRI|자기공명
초음파
1인실|상급병실
관리급여
경험생명표|평균수명
약관대출|보험계약대출
달러보험
설계사
지급여력|K-ICS
영양주사|피로 또는 권태
하이푸|고강도초음파
로봇
암검진|국가검진|검진
생명보험|생보
손해보험|손보'''.split('\n')]


def entities_in(text):
    return [g for g in ENTITIES if any(a in text for a in g)]


def entity_ok(ents, evidence):
    return not ents or any(a in evidence for g in ents for a in g)

# ---------------------------------------------------------------- 근거 읽기


def section(t, name):
    m = re.search(r'## ' + name + r'\n(.*?)(?=\n## |\Z)', t, re.S)
    return m.group(1) if m else ''


BLANKET = re.compile(r'(원 발표[^.\n]{0,12}대조 전|화면 수치 사용 금지|수치를? (화면에 |영상에 )?쓰지 않는다|수치 사용 금지)')


CACHE_DIR = os.path.join(HERE, '.cache')                # git에 넣지 않는다(tools/.gitignore)
CACHE = os.path.join(CACHE_DIR, 'factcheck_load.pkl')


def _signature():
    """캐시가 아직 맞는지 판단할 지문: 기록 파일·index.csv·numbers.csv·이 스크립트의 이름·크기·수정 시각."""
    h = hashlib.sha1()
    files = [INDEX, CARDS, os.path.abspath(__file__)]
    with os.scandir(SOURCES) as it:
        files += sorted(e.path for e in it if e.name.startswith('MED-') and e.name.endswith('.md'))
    for f in files:
        st = os.stat(f)
        h.update(f'{os.path.basename(f)}|{st.st_size}|{st.st_mtime_ns}\n'.encode('utf-8'))
    return h.hexdigest()


class ValueIndex:
    """숫자 값(첫 값) → 항목 목록. 값 차이가 허용 오차 안인 후보만 빠르게 고른다.
    후보를 고른 뒤에는 원래 조건(same_number 등)으로 다시 확인하므로 판정은 전체를 훑을 때와 같다."""

    def __init__(self, entries):
        entries.sort(key=lambda e: e[0])
        self.keys = [e[0] for e in entries]
        self.items = [e[1] for e in entries]

    def window(self, lo, hi):
        i = bisect.bisect_left(self.keys, lo)
        j = bisect.bisect_right(self.keys, hi)
        return self.items[i:j]

    def near(self, x, rel=2e-6):
        tol = rel * max(1.0, abs(x)) + 1e-6
        return self.window(x - tol, x + tol)


INDEXES = {}


def build_indexes(recs, cards):
    """기록 '핵심 근거' 줄의 숫자와 카드 값의 색인. 항목은 (기록 순번, 줄 순번) / 카드 순번."""
    order = list(recs)
    lines = []
    for oi, rid in enumerate(order):
        for li, (sent, nums, direct, whole) in enumerate(recs[rid]['lines']):
            for b in nums:
                lines.append((b['values'][0], (oi, li)))
    card_entries = [(x['values'][0], ci) for ci, c in enumerate(cards) for x in c['alts']]
    INDEXES.clear()
    INDEXES.update(order=order, lines=ValueIndex(lines), cards=ValueIndex(card_entries), recs_id=id(recs))


def line_candidates(recs, xs):
    """값 목록 xs 각각의 허용 오차 안에 숫자가 있는 (기록, 줄) 후보를 원래 순서(기록 순서·줄 순서)로 돌려준다.
    반환: [(rid, [줄 순번…])…]. 색인이 없으면 None(전체를 훑는다)."""
    if INDEXES.get('recs_id') != id(recs):
        return None
    cand = set()
    for x in xs:
        if isinstance(x, tuple):
            cand.update(INDEXES['lines'].window(*x))
        else:
            cand.update(INDEXES['lines'].near(x))
    out = {}
    for oi, li in sorted(cand):
        out.setdefault(oi, []).append(li)
    order = INDEXES['order']
    return [(order[oi], lis) for oi, lis in sorted(out.items())]


def load(today):
    """기록·카드를 읽는다. 기록이나 카드, 이 스크립트가 바뀌지 않았으면 캐시(.cache/)를 쓰고, 바뀌었으면 다시 만든다."""
    sig = _signature()
    try:
        with open(CACHE, 'rb') as f:
            saved = pickle.load(f)
        if saved.get('sig') == sig:
            idx, recs, cards = saved['data']
            build_indexes(recs, cards)
            return idx, recs, cards
    except (OSError, EOFError, pickle.UnpicklingError, AttributeError, KeyError, TypeError, ValueError):
        pass
    idx, recs, cards = _load(today)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = CACHE + '.tmp'
        with open(tmp, 'wb') as f:
            pickle.dump(dict(sig=sig, data=(idx, recs, cards)), f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, CACHE)
    except OSError:
        pass                                            # 캐시를 못 써도 판정에는 영향 없음
    build_indexes(recs, cards)
    return idx, recs, cards


def _load(today):
    idx = {x['id']: x for x in csv.DictReader(open(INDEX, encoding='utf-8'))}
    recs = {}
    for path in glob.glob(os.path.join(SOURCES, 'MED-*.md')):
        rid = os.path.basename(path)[:8]
        if rid not in idx:
            continue
        t = open(path, encoding='utf-8').read()
        core, ban = section(t, '핵심 근거'), section(t, '미검증·사용 금지')
        lim_lines = []                                   # '적용 조건·한계' 절의 숫자 줄(마지막 대체 탐색용)
        for line in section(t, '적용 조건·한계').split('\n'):
            if line.strip():
                nums, _ = extract(line)
                if nums:
                    lim_lines.append((line, nums))
        ban_lines = [(s, extract(s)[0]) for s in re.split(r'(?<=\.)\s+|\n', ban) if s.strip()]
        lines = []
        for line in core.split('\n'):
            if not line.strip():
                continue
            for sent in re.split(r'(?<=\.)\s+(?=[가-힣A-Z(*\-\d])', line):
                nums, _ = extract(sent)
                if nums:
                    lines.append((sent, nums, bool(re.search(r'직접 (계산|합산)', sent)), line))
        x = idx[rid]
        body = t.split('---', 2)[2] if t.startswith('---') else t
        all_nums = extract(body, keep_small=True)[0]      # 인용 대조용: 핵심 근거 밖(미검증 절 등)의 숫자까지
        title_nums = extract(x['title'], keep_small=True)[0]   # 기록 제목(index.csv title)의 숫자
        recs[rid] = dict(meta=x, core=core, lines=lines, lim_lines=lim_lines, ban_lines=ban_lines, blanket=bool(BLANKET.search(ban)), all_nums=all_nums,
                         title_nums=title_nums,
                         title=x['title'])
    cards = []
    for r in csv.DictReader(open(CARDS, encoding='utf-8')):
        unit = r['단위'].strip()
        # 숫자 열 + 단위 열을 이어 붙여 원고와 같은 방법으로 읽는다(예: '5,508' + '천 원' → 5,508,000원)
        text = r['숫자'] + ('' if unit in ('-', '') else unit.replace(' ', '') if re.match(r'[천만억조]', unit) else unit)
        text = re.sub(r'([천만억조])(원|명|건)$', lambda m: m.group(1) + ' ' + m.group(2), text)
        nums, _ = extract(text)
        if not nums:
            continue
        n = nums[0]
        alts = [n] + extract(r['한 줄 문장'])[0]          # 한 줄 문장에 풀어 쓴 값(예: 범위)도 같은 카드로 본다
        cards.append(dict(row=r, num=n, alts=alts, kw=keywords(r['주제'] + ' ' + r['한 줄 문장'] + ' ' + r['기준시점'])))
    return idx, recs, cards


def flags_for(rid, num, recs, today, direct=False, kw=()):
    """근거 하나에 붙일 주의 표시 목록."""
    rec = recs.get(rid)
    out = []
    if not rec:
        return ['기록 파일 없음']
    m = rec['meta']
    if m['status'] != 'verified':
        out.append(f"status {m['status']}")
    if m['tier'] == 'C':
        out.append('tier C')
    if m['recheck_by'] and m['recheck_by'] < today:
        out.append(f"재확인 기한 지남({m['recheck_by']})")
    if direct:
        out.append('직접 계산')
    # 같은 숫자가 '미검증·사용 금지' 절의 문장에 있고, 그 문장과 원고 문장이 단어를 공유할 때만 표시한다
    for s, ns in rec['ban_lines']:
        if any(same_number(num, b) for b in ns) and overlap(kw, keywords(s)):
            out.append('미검증 절에 같은 숫자: ' + s.strip()[:60])
            break
    if rec['blanket']:
        out.append('기록이 수치 사용을 제한')
    return out


def conflict(ents, near):
    """근거에서 숫자 앞 같은 마디에 다른 질병·제도 이름만 있으면 True(예: 원고 '간암 … 72.8%', 근거 '위암 … 72.8%')."""
    if not ents:
        return False
    found = entities_in(near) + [g for g in ENTITIES if any(re.search(s, near) for s in SHORT.get(g[0], []))]
    return bool(found) and not any(g in ents for g in found)


# 표에서 '폐 42.5%', '위 78.6%'처럼 '암'을 빼고 쓴 짧은 이름(숫자 앞 주어 확인에만 쓴다)
SHORT = {'위암': [r'(^|[\s,(])위 '], '폐암': [r'(^|[\s,(])폐 '], '간암': [r'(^|[\s,(])간 '], '신장암': [r'(^|[\s,(])신장 '],
         '대장암': [r'(^|[\s,(])대장 '], '췌장암': [r'(^|[\s,(])췌장 '], '유방암': [r'(^|[\s,(])유방 '],
         '전립선암': [r'(^|[\s,(])전립선 '], '갑상선암': [r'(^|[\s,(])갑상선 ']}


def accept(kw, hkw, ents, ctx, ev_kw, ev_text):
    """근거 후보를 받아들일지 정한다. 반환: 겹친 단어 수(0이면 받지 않음).
    1) 원고 문장 단어가 근거와 1개 이상 겹치고, 원고 문장의 질병·제도 이름이 근거에 있으면 받는다.
       이름이 근거에 없어도 문장 단어가 2개 이상 겹치면 받는다.
    2) 원고 문장에 질병·제도 이름이 없을 때만: 제목의 이름이 근거에 있고 제목 단어가 겹치면 받는다
       (예: 제목 '뇌졸중 3. 생존율·환자 수' 아래의 주어 없는 문장)."""
    ov = overlap(kw, ev_kw)
    if ov and (len(ov) >= 2 or entity_ok(ents, ev_text)):
        return len(ov) + (1 if ctx and entity_ok(ctx, ev_text) else 0)
    if not ents and ctx and entity_ok(ctx, ev_text):
        ovh = overlap(hkw, ev_kw)
        if ovh:
            return len(ovh)
    return 0


def check_number(num, kw, recs, cards, today, ents=(), ctx=(), hkw=frozenset(), limits=True):
    """숫자 하나 판정. 반환: (판정, 근거 목록)
    kw: 원고 문장 단어, hkw: 제목 단어, ents: 원고 문장의 질병·제도 이름 묶음, ctx: 제목의 이름 묶음.
    카드는 값·단위가 같은 카드(한 줄 문장에 풀어 쓴 값 포함)를, 기록은 '핵심 근거'에서 값·단위가 같은 숫자가 있는
    목록 한 줄을 찾고, accept()로 받을지 정한다."""
    hits = []
    if INDEXES.get('recs_id') == id(recs):
        card_pool = [cards[ci] for ci in sorted(set(INDEXES['cards'].near(num['values'][0])))]
    else:
        card_pool = cards
    for c in card_pool:
        if any(same_number(num, x) for x in c['alts']):
            r = c['row']
            text = r['주제'] + ' ' + r['한 줄 문장'] + ' ' + r['기준시점'] + ' ' + r['주의']
            score = accept(kw, hkw, ents, ctx, c['kw'], text)
            if score and not conflict(ents, r['주제'] + ' ' + r['한 줄 문장']):
                rid = r['출처기록']
                fl = flags_for(rid, num, recs, today, direct=bool(re.search(r'직접 (계산|합산)', r['주의'])), kw=kw)
                hits.append(('카드', score, r['카드번호'], rid, fl, r['한 줄 문장']))
    if not hits:
        xs = [num['values'][0]]
        lm = re.match(r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?', num['raw'])
        if lm:
            xs.append(round(float(lm.group(0).replace(',', '')), 6))    # loose_number 후보('27천 명' ↔ '27')
        cands = line_candidates(recs, xs)
        if cands is None:
            cands = [(rid, range(len(rec['lines']))) for rid, rec in recs.items()]
        for rid, lis in cands:
            rec = recs[rid]
            m = rec['meta']
            if m['status'] in ('superseded', 'rejected'):
                continue
            for li in lis:
                sent, nums, direct, whole = rec['lines'][li]
                same = [b for b in nums if same_number(num, b)]
                exact = bool(same)
                if not exact:
                    same = [b for b in nums if b['unit'] is None and loose_number(num, b)]
                loose = not exact and bool(same)
                if not same or all(conflict(ents, b.get('before', '')) for b in same):
                    continue
                # 단어 겹침은 숫자가 있는 문장이 아니라 그 문장이 속한 목록 한 줄 전체로 본다
                # (예: '유병 환자 나이: 평균 … 65세 이상 27.3% → 45.1%'에서 45.1%의 주어는 같은 줄 앞부분에 있다)
                score = accept(kw, hkw, ents, ctx, keywords(whole), whole + ' ' + rec['title'])
                if score:
                    fl = flags_for(rid, num, recs, today, direct, kw)
                    if loose:
                        fl = fl + ['단위 표기 없는 값과 일치(단위 확인)']
                    hits.append(('기록', score, '', rid, fl, sent[:80]))
                    break
    if not hits:
        hits = fallback_hits(num, kw, hkw, ents, ctx, recs, today, limits)
    if not hits:
        return '근거 없음', []
    # 카드를 먼저, 그다음 단어가 많이 겹치는 근거를 앞에 둔다(표시 유무는 마지막 기준)
    hits.sort(key=lambda h: (h[0] != '카드', -h[1], bool(h[4])))
    best = hits[0]
    if best[4]:
        verdict = '주의'
    else:
        verdict = '근거 있음(카드)' if best[0] == '카드' else '근거 있음(기록)'
    return verdict, hits

# ---------------------------------------------------------------- 표기 차이 허용(7라운드)
AMOUNT_UNITS = ('명', '건', '원', '회', '곳', '개', '개사')


def _sig(v):
    """정수 v의 유효 숫자 개수(끝 0 제외)."""
    s = str(int(round(v))).rstrip('0')
    return max(len(s), 1)


def approx_match(num, b):
    """원고가 원문 값을 어림해 쓴 경우인지. 예: 360만 명 ↔ 3,606,521명, 18만 명 ↔ 182,312명.
    조건: 같은 금액·인원 단위, 원고 값의 유효 숫자가 원문보다 적다(자릿수가 줄었다). 원고 유효 숫자가 4개 이상이면
    원문이 세 자리 이상 더 자세할 때만 본다(예: '1,042만 회' ↔ 10,424,188회, '약 1조 1,685억 원' ↔ 1,168,485,720천 원).
    '넘는·이상·넘게'면 원문 ≥ 원고 값이고 차이가 원고의 마지막 자리 단위보다 작아야 하고,
    '가까이·미만'이면 반대, 그 밖('약' 포함)은 차이가 마지막 자리 단위보다 작아야 한다."""
    if len(num['values']) != 1 or len(b['values']) != 1 or b.get('small'):
        return False
    if num['unit'] not in AMOUNT_UNITS or unit_of(num['unit']) != unit_of(b['unit']):
        return False
    v, e = num['values'][0], b['values'][0]
    if v < 1000 or not float(v).is_integer() or not float(e).is_integer():
        return False
    sv, se = _sig(v), _sig(e)
    if se <= sv or (sv > 3 and se - sv < 3):
        return False
    step = 10 ** (len(str(int(v))) - sv)
    after = re.sub(r'^[이가을를은는의도]?\s?', '', num.get('after', ''))
    if re.match(r'(이상|넘는|넘게|넘어|넘|초과|남짓|여)', after):
        return v <= e < v + step
    if re.match(r'(가까이|미만|못)', after):
        return v - step < e <= v
    if re.search(r'(약|대략|어림잡아|거의)\s*$', num.get('before', '')) or re.match(r'(가량|쯤|정도|안팎|내외)', after):
        return abs(e - v) < step
    # 어림 표현 없이 자릿수만 줄인 경우: 원문이 두 자리 이상 더 자세하고, 반올림 또는 버림('12만 명 시대' ↔ 126,944명)
    return se - sv >= 2 and (abs(e - v) <= step / 2 or v <= e < v + step)


SCALE_WORD = {1e3: '천', 1e4: '만', 1e8: '억', 1e12: '조'}


def scaled_match(num, b, text):
    """원고 '7.79만 원' ↔ 기록 표 값 '77.9'(표 머리 천 원)처럼 단위를 바꿔 쓴 경우.
    text(기록의 해당 줄 또는 핵심 근거)에 '천 원'·'(천 명)' 같은 단위 머리가 있어야 한다."""
    if len(num['values']) != 1 or b['unit'] is not None or not re.search(r'[.,]', b['raw']):
        return False
    if num['unit'] not in ('원', '명', '건') or b['values'][0] < 1:
        return False
    r = num['values'][0] / b['values'][0]
    for k, w in SCALE_WORD.items():
        if abs(r - k) < 1e-6 * k and re.search(w + r'\s?' + num['unit'], text):
            return True
    return False


def cited_variant(num, rec):
    """인용한 기록 안에서 표기 차이로 같은 숫자를 찾는다(인용이 맥락을 주므로 단어 대조는 하지 않는다).
    반환: 어떤 표기 차이인지 설명 글, 없으면 ''."""
    pool = [b for b in rec['all_nums'] if not b.get('small')]
    near = [b for b in pool if approx_match(num, b)]
    if near:
        b = min(near, key=lambda b: abs(b['values'][0] - num['values'][0]))
        return f"어림 일치(원문 {b['raw']})"
    for b in rec['all_nums']:
        if scaled_match(num, b, rec['core']):
            return f"단위 환산 일치(원문 {b['raw']})"
    if len(num['values']) > 1 and all(any(same_number(dict(num, values=(v,)), b) for b in pool) for v in num['values']):
        return '범위 끝값을 따로 찾음'
    if num['unit'] == '%' and len(num['values']) == 1:
        words = re.findall(r'[가-힣]{2,}', num.get('before', ''))
        digits = re.match(r'[\d,.]+', num['raw']).group(0)
        if words and re.search(re.escape(words[-1]) + r'\s?' + re.escape(digits) + r'(?![\d.%])', rec['core']):
            return f"줄여 쓴 표기 일치('{words[-1]} {digits}')"
    return ''


RANKLIST = re.compile(r'(\d+)~(\d+)위\s*:\s*([^\n]+)')


def fallback_hits(num, kw, hkw, ents, ctx, recs, today, limits=True):
    """기록 대조로 못 찾았을 때 표기 차이를 허용해 다시 찾는다.
    (1) 어림 값·단위 환산 (2) 목록 순서로 정해지는 순위 (3) 범위의 두 끝값 (4) '심한 40%' ↔ '심한 40'처럼 줄여 쓴 표기
    (5) '적용 조건·한계' 절에만 있는 숫자(주의). 인용 번호가 없는 문장에만 쓴다(limits) — 인용 번호가 있으면
    인용 기록 대조가 이미 기록 본문 전체를 본다.
    찾으면 근거 글 앞에 어떤 표기 차이인지 적는다(주의 표시는 붙이지 않는다)."""
    hits = []
    live = [(rid, rec) for rid, rec in recs.items() if rec['meta']['status'] not in ('superseded', 'rejected')]
    # (1) 어림 값·단위 환산: 받아들일 수 있는 후보 중 원고 값에 가장 가까운 원문 값을 고른다
    best = None
    v0 = num['values'][0]
    wins = []
    if len(num['values']) == 1 and v0 >= 1000 and float(v0).is_integer():
        sv = _sig(v0)
        step = 10 ** max(len(str(int(v0))) - sv, 0)
        wins.append((v0 - step - 1, v0 + step + 1))                      # approx_match가 참일 수 있는 범위
    if len(num['values']) == 1:
        for k in SCALE_WORD:
            c = v0 / k
            wins.append((c - 2e-6 * abs(c) - 1e-9, c + 2e-6 * abs(c) + 1e-9))   # scaled_match가 참일 수 있는 값
    cands = line_candidates(recs, wins) if wins else []
    if cands is None:
        cands = [(rid, range(len(rec['lines']))) for rid, rec in live]
    status_ok = {rid for rid, _ in live}
    for rid, lis in cands:
        if rid not in status_ok:
            continue
        rec = recs[rid]
        for li in lis:
            sent, nums, direct, whole = rec['lines'][li]
            for b in nums:
                kind = '어림 일치' if approx_match(num, b) else '단위 환산 일치' if scaled_match(num, b, rec['core']) else ''
                if not kind or conflict(ents, b.get('before', '')):
                    continue
                score = accept(kw, hkw, ents, ctx, keywords(whole), whole + ' ' + rec['title'])
                if not score:
                    continue
                gap = 0 if kind == '단위 환산 일치' else abs(b['values'][0] - num['values'][0])
                if best is None or (-score, gap) < (-best[0], best[1]):
                    best = (score, gap, rid, direct, kind, b, sent)
    if best:
        score, gap, rid, direct, kind, b, sent = best
        fl = flags_for(rid, num, recs, today, direct, kw)
        return [('기록', score, '', rid, fl, f"{kind}(원문 {b['raw']}) — {sent[:60]}")]
    # (2) 목록 순서로 정해지는 순위: '11~20위: A 70.3, B 75.3, …'
    if num['unit'] == '위' and len(num['values']) == 1:
        n = int(num['values'][0])
        for rid, rec in live:
            for m in RANKLIST.finditer(rec['core']):
                a, z = int(m.group(1)), int(m.group(2))
                items = [x.strip() for x in re.split(r',\s*', m.group(3)) if x.strip()]
                if not (a <= n <= z) or n - a >= len(items):
                    continue
                item = items[n - a]
                if overlap(kw, keywords(item)) or (ents and entity_ok(ents, item)):
                    fl = flags_for(rid, num, recs, today, kw=kw)
                    hits.append(('기록', 1, '', rid, fl, f"목록 순서로 센 순위({a}~{z}위 중 {n - a + 1}번째: {item[:40]})"))
                    return hits
    # (3) 범위: 두 끝값이 같은 기록에 따로 적힌 경우
    if len(num['values']) > 1:
        parts = [dict(num, values=(v,)) for v in num['values']]
        per_part = []
        for x in parts:
            c = line_candidates(recs, [x['values'][0]])
            per_part.append(None if c is None else dict(c))
        for rid, rec in live:
            if any(pp is not None and rid not in pp for pp in per_part):
                continue                                  # 끝값 하나라도 이 기록 줄에 없다
            found = []
            for x, pp in zip(parts, per_part):
                lis = pp[rid] if pp is not None else range(len(rec['lines']))
                for li in lis:
                    sent, nums, direct, whole = rec['lines'][li]
                    if any(same_number(x, b) for b in nums):
                        found.append(whole)
                        break
            if len(found) == len(parts):
                score = accept(kw, hkw, ents, ctx, keywords(' '.join(found)), ' '.join(found) + ' ' + rec['title'])
                if score:
                    fl = flags_for(rid, num, recs, today, kw=kw)
                    hits.append(('기록', score, '', rid, fl, f"범위 끝값을 따로 찾음 — {found[0][:60]}"))
                    return hits
    # (4) 줄여 쓴 표기: 원고 '심한 40%' ↔ 기록 '심한 40'
    if num['unit'] == '%' and len(num['values']) == 1:
        words = re.findall(r'[가-힣]{2,}', num.get('before', ''))
        if words:
            label = words[-1]
            digits = re.match(r'[\d,.]+', num['raw']).group(0)
            pat = re.compile(re.escape(label) + r'\s?' + re.escape(digits) + r'(?![\d.%])')
            for rid, rec in live:
                m = pat.search(rec['core'])
                if not m:
                    continue
                line = rec['core'][rec['core'].rfind('\n', 0, m.start()) + 1:]
                line = line.split('\n', 1)[0]
                score = accept(kw, hkw, ents, ctx, keywords(line), line + ' ' + rec['title'])
                if score:
                    fl = flags_for(rid, num, recs, today, kw=kw)
                    hits.append(('기록', score, '', rid, fl, f"줄여 쓴 표기 일치('{m.group(0)}')"))
                    return hits
    # (5) '적용 조건·한계' 절에만 있는 숫자: 기록의 해석·한계 서술이라 '주의'로 낸다(예: '1~2%p 변화는 오차 범위')
    #     같은 문구가 여러 기록에 있으면 원고의 질병·제도 이름이 들어 있는 기록, 그다음 단어가 많이 겹치는 기록을 고른다
    best = None
    for rid, rec in (live if limits else []):
        for line, nums in rec['lim_lines']:
            same = [b for b in nums if same_number(num, b)]
            if not same or all(conflict(ents, b.get('before', '')) for b in same):
                continue
            score = accept(kw, hkw, ents, ctx, keywords(line), line + ' ' + rec['title'])
            if score:
                key = (bool(ents) and entity_ok(ents, rec['title']), score)
                if best is None or key > best[0]:
                    best = (key, score, rid, line)
    if best:
        _, score, rid, line = best
        fl = flags_for(rid, num, recs, today, kw=kw) + ['핵심 근거 밖(적용 조건·한계 절)의 숫자']
        return [('기록', score, '', rid, fl, line.strip()[:80])]
    return hits

# ---------------------------------------------------------------- 원고 → 문장




MEDREF = re.compile(r'MED-\d{4}(?:\s?[~·,]\s?(?:MED-)?\d{4}(?!\d))*')


def med_refs(text):
    """글에서 인용한 MED 번호를 모두 읽는다. 'MED-1543·1544', 'MED-1558~1573'(범위, 최대 60개)도 푼다."""
    refs = set()
    for m in MEDREF.finditer(text):
        toks = re.findall(r'[~·,]|\d{4}', m.group(0))
        prev, rng = None, False
        for tk in toks:
            if tk == '~':
                rng = True
                continue
            if tk in '·,':
                continue
            n = int(tk)
            if rng and prev is not None and 0 < n - prev <= 60:
                refs.update(f'MED-{k:04d}' for k in range(prev + 1, n + 1))
            refs.add(f'MED-{n:04d}')
            prev, rng = n, False
    return refs


def sentences(text):
    """원고를 문장으로 나눈다. 반환: (문장, 제목, 인용 번호 묶음) 목록.
    제목(#으로 시작하는 줄)은 판정하지 않고, 아래 문장의 질병·제도 이름 맥락으로만 쓴다.
    인용 번호는 문장 안에 MED 번호가 있으면 그것을, 없으면 그 문장이 속한 줄(목록 한 항목)의 MED 번호를 쓴다
    (패키지처럼 줄 끝에 한 번만 '(MED-…)'를 붙인 경우). 번호가 없는 하위 목록 항목은 바로 위 단계 항목의 번호를 이어받는다
    ('재가 수가 예(MED-1402)' 아래 '- 주야간보호: 1일 …원')."""
    out = []
    heading = ''
    stack = []                                            # (들여쓰기, 인용 번호) — 목록 단계별
    for raw in text.split('\n'):
        line = raw.strip()
        if line.startswith('#'):
            heading = line.lstrip('#').strip()
            stack = []
            continue
        if not line:
            continue
        is_item = bool(re.match(r'^([-*]|\d+[.)])\s', line))
        line = re.sub(r'^[-*>\d]+[.)]?\s+', '', line) if re.match(r'^([-*>]|\d+[.)])\s', line) else line
        line = re.sub(r'\*\*|`', '', line)
        line_refs = med_refs(line)
        if is_item:
            indent = len(raw) - len(raw.lstrip())
            while stack and stack[-1][0] >= indent:
                stack.pop()
            if not line_refs and stack:
                line_refs = stack[-1][1]
            stack.append((indent, line_refs))
        else:
            stack = []
        # 표 한 줄은 칸마다 따로 읽는다(칸마다 다른 사건·다른 MED 번호가 붙는 경우). 번호가 없는 칸은 그 줄의 번호를 쓴다
        cells = [line]
        if line.startswith('|'):
            if re.fullmatch(r'[|:\-\s]+', line):
                continue
            cells = [c.strip() for c in re.split(r'(?<!\\)\|', line) if c.strip()]
        for cell_text in cells:
            cell_refs = med_refs(cell_text) or line_refs
            for s in re.split(r'(?<=[.!?])\s+(?=\S)|(?<=다\.)(?=\S)', cell_text):
                s = s.strip()
                if s:
                    out.append((s, heading, med_refs(s) or cell_refs))
    return out


def _has(x, pool):
    """x가 pool(기록에서 뽑은 숫자들)에 있는가. 기록의 단위 없는 작은 수(표 값)는 값만 같으면 같은 것으로 본다."""
    for b in pool:
        if b.get('small'):
            if len(x['values']) == len(b['values']) and all(abs(u - v) < 1e-9 for u, v in zip(x['values'], b['values'])):
                return True
            if loose_number(x, b):                       # '27천 명' ↔ 표 값 '27'(표 머리에 천 명)
                return True
        elif same_number(x, b) or (b['unit'] is None and loose_number(x, b)):
            return True
    return False


def cited_hits(num, refs, recs, cards, title_only=None):
    """인용한 MED 기록(본문 전체, 또는 그 기록에서 나온 카드)에 같은 숫자가 있는지.
    반환: 숫자가 있는 인용 기록 번호 목록. 본문·카드에는 없고 기록 제목에만 있으면 title_only 목록에 넣는다."""
    found = []
    parts = [num] if len(num['values']) == 1 else [dict(num, values=(v,)) for v in num['values']]
    for rid in sorted(refs):
        rec = recs.get(rid)
        if not rec:
            continue
        pool = rec['all_nums']
        if _has(num, pool) or (len(parts) > 1 and all(_has(x, pool) for x in parts)):
            found.append(rid)
            continue
        if any(c['row']['출처기록'] == rid and any(same_number(num, x) for x in c['alts']) for c in cards):
            found.append(rid)
            continue
        if title_only is not None and (_has(num, rec['title_nums'])
                                       or (len(parts) > 1 and all(_has(x, rec['title_nums']) for x in parts))):
            title_only.append(rid)
    return found


def substantive(n):
    """인용 번호 불일치를 따질 '내용 숫자'인가. 기간·나이·횟수·단위 없는 수는 인용 기록이 다르게 적는 일이 많아 뺀다."""
    if n['unit'] not in ('%', '%p', '%포인트', '퍼센트', '원', '명', '건', '곳', '배', 'mmHg', '개사'):
        return False
    return not (n['unit'] in ('명', '건', '곳', '개사') and max(n['values']) < 100)


RANK = {'근거 없음': 5, '인용 번호 불일치': 4, '주의': 3, '저장소 자체 집계': 2, '근거 있음(기록)': 2, '근거 있음(인용 기록)': 2,
        '근거 있음(카드)': 1}
# 문장이 스스로 '이 저장소에 모은 기록 …건', '직접 집계하면 …건'이라고 밝힌 건수는 근거 기록이 있을 수 없다(14라운드)
SELF_COUNT = re.compile(r'이 저장소에 모은|저장소에서 센|저장소 안에서 센|직접 집계')


def cell(s):
    return s.replace('|', '\\|').replace('\n', ' ')


def judge(n, s, heading, refs, recs, cards, today, detail):
    """숫자 하나를 판정한다. 반환: (판정, 설명 글).
    인용 번호(refs)가 있으면, 그 숫자가 인용한 기록이나 그 기록에서 나온 카드에 있는지도 본다."""
    kw = keywords(s)
    hkw = keywords(re.sub(r'[\d.]+', ' ', heading))   # 제목 단어(주어 없는 문장용)
    v, hits = check_number(n, kw, recs, cards, today, entities_in(s), entities_in(heading), hkw, limits=not refs)
    cite = ''
    if refs:
        title_only = []
        found = cited_hits(n, refs, recs, cards, title_only)
        if not found and hits and hits[0][3] in refs:
            found = [hits[0][3]]                          # 표기 차이(어림·순위·범위 등)로 찾은 근거가 인용한 기록
        if not found and not hits:
            for rid in sorted(refs):
                if rid in recs:
                    note = cited_variant(n, recs[rid])
                    if note:
                        fl = flags_for(rid, n, recs, today, kw=kw)
                        v = '주의' if fl else '근거 있음(인용 기록)'
                        hits = [('기록', 0, '', rid, fl, note + ' — 인용한 기록에서 찾음')]
                        found = [rid]
                        break
        if not found and title_only:
            # 인용한 기록의 제목에는 있지만 본문(핵심 근거 등)에는 없다: 기록 쪽도 함께 확인할 일
            rid = title_only[0]
            fl = flags_for(rid, n, recs, today, kw=kw) + ['인용 기록 제목에만 있는 숫자(본문에서 확인 필요)']
            cite = '인용 일치(제목에만, ' + ', '.join(title_only) + ')'
            v = '주의'
            hits = [('기록', 0, '', rid, fl, '기록 제목: ' + recs[rid]['title'][:60])]
        elif found:
            cite = '인용 일치(' + ', '.join(found) + ')'
            if not hits:
                # 일반 검색은 단어가 안 맞아 못 찾았지만, 인용한 기록에는 그 숫자가 있다
                rid = found[0]
                fl = flags_for(rid, n, recs, today, kw=kw)
                v = '주의' if fl else '근거 있음(인용 기록)'
                hits = [('기록', 0, '', rid, fl, '인용한 기록에 같은 숫자가 있음(단어 대조는 안 맞음)')]
        else:
            missing = ', '.join(sorted(r for r in refs if r not in recs))
            if hits and not substantive(n):
                cite = f"참고: 인용한 {', '.join(sorted(refs))}에는 이 숫자가 없음(기간·나이·횟수 등이라 판정에 반영하지 않음)"
            elif hits:
                v = '인용 번호 불일치'
                cite = (f"인용 번호 불일치: 인용한 {', '.join(sorted(refs))}에는 이 숫자가 없고, "
                        f"근거는 {hits[0][3]}에 있음")
            else:
                cite = f"인용한 {', '.join(sorted(refs))}에도 없음"
            if missing:
                cite += f" (저장소에 없는 번호: {missing})"
    if not hits:
        return v, f"**{n['raw']}**: 근거 없음" + (f" · {cite}" if cite else '')
    shown = hits[:3] if detail else hits[:1]
    desc = []
    for kind, ov, cid, rid, fl, txt in shown:
        m = recs[rid]['meta']
        head = f"{cid} {rid}" if cid else rid
        desc.append(f"{head}({m['status']}·tier {m['tier']}·재확인 {m['recheck_by']})"
                    + (f" ⚠ {', '.join(fl)}" if fl else '') + f" — {txt}")
    more = f" 외 {len(hits) - len(shown)}건" if len(hits) > len(shown) else ''
    return v, f"**{n['raw']}**: {v} · " + ' / '.join(desc) + more + (f" · {cite}" if cite else '')


def check_file(path, recs, cards, today, detail):
    """파일 하나를 판정한다. 반환: (행 목록, 판정별 문장 수)."""
    text = open(path, encoding='utf-8').read()
    rows, counts = [], {}
    for k, (s, heading, refs) in enumerate(sentences(text), 1):
        nums, years = extract(re.sub(r'\(MED-[\d, MED-]+\)', '', s))
        if not nums:
            v = '숫자 없음' if not years else '숫자 없음(연도·날짜만)'
            counts[v] = counts.get(v, 0) + 1
            rows.append((k, s, ', '.join(years) or '-', v, '사람이 확인'))
            continue
        parts, worst = [], '근거 있음(카드)'
        for n in nums:
            v, d = judge(n, s, heading, refs, recs, cards, today, detail)
            if v == '근거 없음' and n['unit'] in ('건', '개') and SELF_COUNT.search(s):
                v = '저장소 자체 집계'
                d = f"**{n['raw']}**: 저장소 자체 집계 · 문장이 이 저장소 기록을 센 숫자라고 밝혔다(근거 기록 없음, 사람이 개수 확인)"
            if RANK[v] > RANK[worst]:
                worst = v
            parts.append(d)
        counts[worst] = counts.get(worst, 0) + 1
        rows.append((k, s, ', '.join([n['raw'] for n in nums] + years), worst, '<br>'.join(parts)))
    return rows, counts


def fmt_counts(counts):
    return ', '.join(f'{k} {v}' for k, v in sorted(counts.items(), key=lambda x: -RANK.get(x[0], 0)))


def main():
    ap = argparse.ArgumentParser(description='영상 원고 숫자 팩트체크')
    ap.add_argument('scripts', nargs='*', help='원고 파일(.txt/.md). 여러 개를 줄 수 있다')
    ap.add_argument('--dir', action='append', default=[],
                    help='이 폴더의 .md·.txt를 모두 점검한다(여러 번 줄 수 있다, README.md 제외)')
    ap.add_argument('--today', default=datetime.date.today().isoformat())
    ap.add_argument('--detail', action='store_true', help='근거 후보를 숫자마다 3개까지 보인다')
    ap.add_argument('--only-problems', action='store_true',
                    help="'근거 없음'·'인용 번호 불일치' 문장만 보인다(일괄 점검용)")
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    paths = list(a.scripts)
    for d in a.dir:
        paths += sorted(p for p in glob.glob(os.path.join(d, '*'))
                        if p.endswith(('.md', '.txt')) and os.path.basename(p) != 'README.md')
    if not paths:
        ap.error('원고 파일이나 --dir를 주세요')
    idx, recs, cards = load(a.today)
    results = [(p,) + check_file(p, recs, cards, a.today, a.detail) for p in paths]
    batch = len(paths) > 1
    title = '일괄 팩트체크 결과' if batch else f'팩트체크 결과: {os.path.basename(paths[0])}'
    print(f"# {title}\n")
    print(f"- 기준일 {a.today}, 카드 {len(cards)}줄, 기록 {len(recs)}건을 대조했다.")
    print("- 숫자와 단어 겹침, 인용한 MED 번호에 그 숫자가 있는지만 본 결과다. "
          "문장 뜻이 근거와 같은지는 사람이 확인한다.")
    if batch:
        total = {}
        for _, _, c in results:
            for k, v in c.items():
                total[k] = total.get(k, 0) + v
        print(f"- 파일 {len(paths)}개, 판정별 문장 수 합계: {fmt_counts(total)}\n")
        print('| 파일 | 근거 없음 | 인용 번호 불일치 | 주의 | 판정별 문장 수 |')
        print('|---|---|---|---|---|')
        for p, _, c in results:
            print(f"| {os.path.relpath(p)} | {c.get('근거 없음', 0)} | {c.get('인용 번호 불일치', 0)} | "
                  f"{c.get('주의', 0)} | {fmt_counts(c)} |")
        print()
    else:
        print('- 판정별 문장 수: ' + fmt_counts(results[0][2]) + '\n')
    for p, rows, c in results:
        shown = [r for r in rows if r[3] in ('근거 없음', '인용 번호 불일치')] if a.only_problems else rows
        if batch:
            if a.only_problems and not shown:
                continue
            print(f"## {os.path.relpath(p)}\n")
        print('| # | 문장 | 뽑은 숫자 | 판정 | 근거(숫자별) |')
        print('|---|---|---|---|---|')
        for k, s, n, v, d in shown:
            print(f"| {k} | {cell(s)} | {cell(n)} | {v} | {cell(d)} |")
        print()


if __name__ == '__main__':
    main()
