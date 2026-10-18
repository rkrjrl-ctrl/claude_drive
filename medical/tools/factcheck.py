"""영상 원고 숫자 팩트체크 도구.

원고(.txt/.md)를 문장으로 나누고, 문장 속 숫자를 numbers/numbers.csv 카드와
sources/ 기록의 '핵심 근거' 절에서 찾아 판정 표(마크다운)를 낸다.

실행:  python -X utf8 medical/tools/factcheck.py 원고.txt > 결과.md
옵션:  --today YYYY-MM-DD  재확인 기한 비교 기준일(기본: 오늘)
       --detail            근거 후보를 숫자마다 최대 3개까지 모두 보인다(기본 1개)

파이썬 표준 라이브러리만 쓴다. 숫자 일치와 단어 겹침만 보며, 문장의 뜻이 근거와 같은지는
사람이 확인해야 한다(tools/README.md '한계').
"""
import argparse, csv, datetime, glob, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MED = os.path.dirname(HERE)                       # medical/
INDEX = os.path.join(MED, 'index.csv')
CARDS = os.path.join(MED, 'numbers', 'numbers.csv')
SOURCES = os.path.join(MED, 'sources')

# ---------------------------------------------------------------- 숫자 뽑기
MAG = {'조': 1e12, '천억': 1e11, '억': 1e8, '천만': 1e7, '백만': 1e6, '만': 1e4, '천': 1e3, '백': 1e2}
GROUP = re.compile(r'(?<![A-Za-z\d.,])((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(조|천억|억|천만|백만|만|천|백)?')
UNIT = re.compile(r'\s?(%p|%포인트|퍼센트|%|원(?!인|칙|문|외|급|래|내|장)|명|건(?!강|물|축|의|설|전)|개월|개사|개(?!정|선|별|월)|회(?!사|원|의|계)|일(?!당|반|부|시|상)|년(?!생|도)|세(?!대|부)|곳|배(?!우|아)|위(?!암|원|험)|점(?!수)|cm|kg|타|갑년|시간|mmHg)')
FRACTION = re.compile(r'(\d+)분의\s?(\d+)')
# 숫자로 보지 않을 것: 날짜, 고시·공고 번호, 판례 번호, 기록·카드 번호, 상병코드
SKIP = [re.compile(p) for p in [
    r'\d{4}-\d{2}-\d{2}', r'\d{4}\.\s?\d{1,2}\.\s?\d{1,2}\.?', r'\d{1,2}월\s?\d{1,2}일',
    r'제?\d{4}-\d+\s?호', r'제\d+(?:-\d+)?호', r'\d{2,4}[가-힣]{1,2}\d{3,}', r'MED-\d{4}', r'N-\d{4}',
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


def extract(text):
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
        val = _group_value(g.group(1), g.group(2))
        has_mag = bool(g.group(2))
        j = i + 1
        # '1조 1,571억', '7만 8,979' 처럼 큰 단위 뒤에 이어지는 묶음을 합친다
        while has_mag and j < len(groups) and masked[end:groups[j].start()] in ('', ' '):
            gj = groups[j]
            prev_mag = MAG.get(groups[j - 1].group(2) or '', 1)
            cur = _group_value(gj.group(1), gj.group(2))
            if cur >= prev_mag:
                break
            val += cur
            end = gj.end()
            has_mag = bool(gj.group(2))
            j += 1
        um = UNIT.match(masked, end)
        unit = um.group(1) if um else None
        if um:
            end = um.end()
        elif re.match(r'[가-힣]', masked[end:end + 1]) and not has_mag and not g.group(2):
            i = j                                    # '1인실', '2형', '3세대'처럼 이름에 붙은 숫자는 건너뛴다
            continue
        out.append(dict(raw=text[start:end].strip(), values=(round(val, 6),), unit=unit, start=start, end=end,
                        before=clause_before(text, start)))   # 숫자 앞 같은 마디의 글(주어 확인용)
        i = j
    # 범위('30~40%', '90만~843만 원')를 하나로 묶는다
    merged = []
    for it in sorted(out, key=lambda x: x['start']):
        if merged and 'end' in merged[-1] and re.fullmatch(r'\s?~\s?', masked[merged[-1]['end']:it['start']] or 'x'):
            a = merged.pop()
            unit = it['unit'] or a['unit']
            merged.append(dict(raw=text[a['start']:it.get('end', it['start'])].strip(),
                               values=a['values'] + it['values'], unit=unit, start=a['start'], end=it.get('end')))
        else:
            merged.append(it)
    nums, years = [], []
    for it in merged:
        if it['unit'] is None and all(x < 100 for x in it['values']) and not re.search(r'[.,조억만천백]', it['raw']):
            years.append(it['raw'])                       # 단위 없는 작은 수(예: '2.0:1'의 1)는 맥락으로만
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


def keywords(text):
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


def load(today):
    idx = {x['id']: x for x in csv.DictReader(open(INDEX, encoding='utf-8'))}
    recs = {}
    for path in glob.glob(os.path.join(SOURCES, 'MED-*.md')):
        rid = os.path.basename(path)[:8]
        if rid not in idx:
            continue
        t = open(path, encoding='utf-8').read()
        core, ban = section(t, '핵심 근거'), section(t, '미검증·사용 금지')
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
        recs[rid] = dict(meta=x, lines=lines, ban_lines=ban_lines, blanket=bool(BLANKET.search(ban)),
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


def check_number(num, kw, recs, cards, today, ents=(), ctx=(), hkw=frozenset()):
    """숫자 하나 판정. 반환: (판정, 근거 목록)
    kw: 원고 문장 단어, hkw: 제목 단어, ents: 원고 문장의 질병·제도 이름 묶음, ctx: 제목의 이름 묶음.
    카드는 값·단위가 같은 카드(한 줄 문장에 풀어 쓴 값 포함)를, 기록은 '핵심 근거'에서 값·단위가 같은 숫자가 있는
    목록 한 줄을 찾고, accept()로 받을지 정한다."""
    hits = []
    for c in cards:
        if any(same_number(num, x) for x in c['alts']):
            r = c['row']
            text = r['주제'] + ' ' + r['한 줄 문장'] + ' ' + r['기준시점'] + ' ' + r['주의']
            score = accept(kw, hkw, ents, ctx, c['kw'], text)
            if score and not conflict(ents, r['주제'] + ' ' + r['한 줄 문장']):
                rid = r['출처기록']
                fl = flags_for(rid, num, recs, today, direct=bool(re.search(r'직접 (계산|합산)', r['주의'])), kw=kw)
                hits.append(('카드', score, r['카드번호'], rid, fl, r['한 줄 문장']))
    if not hits:
        for rid, rec in recs.items():
            m = rec['meta']
            if m['status'] in ('superseded', 'rejected'):
                continue
            for sent, nums, direct, whole in rec['lines']:
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
        return '근거 없음', []
    # 카드를 먼저, 그다음 단어가 많이 겹치는 근거를 앞에 둔다(표시 유무는 마지막 기준)
    hits.sort(key=lambda h: (h[0] != '카드', -h[1], bool(h[4])))
    best = hits[0]
    if best[4]:
        verdict = '주의'
    else:
        verdict = '근거 있음(카드)' if best[0] == '카드' else '근거 있음(기록)'
    return verdict, hits

# ---------------------------------------------------------------- 원고 → 문장


def sentences(text):
    """원고를 문장으로 나눈다. 반환: (문장, 그 문장이 속한 제목) 목록.
    제목(#으로 시작하는 줄)은 판정하지 않고, 아래 문장의 질병·제도 이름 맥락으로만 쓴다."""
    out = []
    heading = ''
    for line in text.split('\n'):
        line = line.strip()
        if line.startswith('#'):
            heading = line.lstrip('#').strip()
            continue
        if not line:
            continue
        line = re.sub(r'^[-*>\d]+[.)]?\s+', '', line) if re.match(r'^([-*>]|\d+[.)])\s', line) else line
        line = re.sub(r'\*\*|`', '', line)
        for s in re.split(r'(?<=[.!?])\s+(?=\S)|(?<=다\.)(?=\S)', line):
            s = s.strip()
            if s:
                out.append((s, heading))
    return out


RANK = {'근거 없음': 4, '주의': 3, '근거 있음(기록)': 2, '근거 있음(카드)': 1}


def cell(s):
    return s.replace('|', '\\|').replace('\n', ' ')


def main():
    ap = argparse.ArgumentParser(description='영상 원고 숫자 팩트체크')
    ap.add_argument('script')
    ap.add_argument('--today', default=datetime.date.today().isoformat())
    ap.add_argument('--detail', action='store_true')
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    idx, recs, cards = load(a.today)
    text = open(a.script, encoding='utf-8').read()
    rows, counts = [], {}
    for k, (s, heading) in enumerate(sentences(text), 1):
        nums, years = extract(re.sub(r'\(MED-[\d, MED-]+\)', '', s))
        if not nums:
            v = '숫자 없음' if not years else '숫자 없음(연도·날짜만)'
            counts[v] = counts.get(v, 0) + 1
            rows.append((k, s, ', '.join(years) or '-', v, '사람이 확인'))
            continue
        # 문장 단어에 제목 단어를 더한다(예: 제목 '뇌졸중 3. 생존율·환자 수·진료비' 아래 주어 없는 문장)
        kw = keywords(s)
        hkw = keywords(re.sub(r'[\d.]+', ' ', heading))   # 제목 단어(주어 없는 문장용)
        parts, worst = [], '근거 있음(카드)'
        for n in nums:
            v, hits = check_number(n, kw, recs, cards, a.today, entities_in(s), entities_in(heading), hkw)
            if RANK[v] > RANK[worst]:
                worst = v
            if not hits:
                parts.append(f"**{n['raw']}**: 근거 없음")
                continue
            shown = hits[:3] if a.detail else hits[:1]
            desc = []
            for kind, ov, cid, rid, fl, txt in shown:
                m = recs[rid]['meta']
                head = f"{cid} {rid}" if cid else rid
                desc.append(f"{head}({m['status']}·tier {m['tier']}·재확인 {m['recheck_by']})"
                            + (f" ⚠ {', '.join(fl)}" if fl else '') + f" — {txt}")
            more = f" 외 {len(hits) - len(shown)}건" if len(hits) > len(shown) else ''
            parts.append(f"**{n['raw']}**: {v} · " + ' / '.join(desc) + more)
        counts[worst] = counts.get(worst, 0) + 1
        rows.append((k, s, ', '.join([n['raw'] for n in nums] + years), worst, '<br>'.join(parts)))
    print(f"# 팩트체크 결과: {os.path.basename(a.script)}\n")
    print(f"- 기준일 {a.today}, 카드 {len(cards)}줄, 기록 {len(recs)}건을 대조했다.")
    print('- 판정별 문장 수: ' + ', '.join(f'{k} {v}' for k, v in sorted(counts.items(), key=lambda x: -RANK.get(x[0], 0))))
    print("- 숫자와 단어 겹침만 본 결과다. 문장 뜻이 근거와 같은지는 사람이 확인한다.\n")
    print('| # | 문장 | 뽑은 숫자 | 판정 | 근거(숫자별) |')
    print('|---|---|---|---|---|')
    for k, s, n, v, d in rows:
        print(f"| {k} | {cell(s)} | {cell(n)} | {v} | {cell(d)} |")


if __name__ == '__main__':
    main()
