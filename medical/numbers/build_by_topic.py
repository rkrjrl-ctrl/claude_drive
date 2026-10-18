#!/usr/bin/env python3
"""numbers.csv 카드를 질병·주제별로 묶은 찾아보기 목록(numbers/BY_TOPIC.md)을 만든다.

사용법:  python -X utf8 medical/numbers/build_by_topic.py [--month YYYY-MM] [--check]
  --month : 재확인 기한 '3개월 안' 표시의 기준 달(기본: 이번 달). 그달 1일부터 3개월 안에 기한이 오면 ⏰를 붙인다
  --check : 파일을 쓰지 않고, 다시 만들면 내용이 바뀌는지만 본다(바뀌면 종료코드 1). check_all.py가 쓴다

묶는 기준: 패키지(packages/NN_*.md)와 가이드(guides/*.md)가 인용하는 MED 기록을 출처로 한 카드를 그 주제에 넣는다.
한 카드가 여러 주제에 들어갈 수 있다. 주제 안에서는 주제 이름의 낱말이 카드 문장·출처 기록 질병·제목에 들어 있는 카드, 그 파일이 출처 기록을 많이 인용한 카드 순으로 놓는다.
어느 패키지·가이드도 출처 기록을 인용하지 않는 카드는 맨 끝 '주제 밖 카드'에 모은다.
파이썬 표준 라이브러리만 쓴다.
"""
import argparse, collections, csv, datetime, glob, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MED = os.path.dirname(HERE)
OUT = os.path.join(HERE, 'BY_TOPIC.md')
sys.path.insert(0, os.path.join(MED, 'tools'))
from factcheck import med_refs                                         # 'MED-1543·1544', 'MED-1558~1573'도 푼다


def cell(s):
    return str(s).replace('|', '/').replace('\n', ' ').strip()


def add_months(y, m, k):
    m += k
    return y + (m - 1) // 12, (m - 1) % 12 + 1


def topics():
    """[(구분, 파일 이름, 제목, {MED: 인용 줄 수})]: 패키지 NN_ 번호 순, 그다음 가이드 이름 순."""
    out = []
    for kind, pat in (('패키지', 'packages/[0-9][0-9]_*.md'), ('가이드', 'guides/*.md')):
        for path in sorted(glob.glob(os.path.join(MED, pat))):
            name = os.path.splitext(os.path.basename(path))[0]
            if name == 'README':
                continue
            text = open(path, encoding='utf-8').read()
            m = re.search(r'^# (.+)$', text, re.M)
            title = m.group(1).strip() if m else name
            refs = collections.Counter()
            for line in text.split('\n'):
                for rid in med_refs(line):
                    refs[rid] += 1
            out.append((kind, name, title, refs))
    return out


def build(month):
    y, m = int(month[:4]), int(month[5:])
    start = datetime.date(y, m, 1)
    ey, em = add_months(y, m, 3)
    soon = datetime.date(ey, em, 1).isoformat()                       # 이 날 전에 기한이 오면 ⏰
    with open(os.path.join(HERE, 'numbers.csv'), encoding='utf-8') as f:
        cards = list(csv.DictReader(f))
    by_src = collections.defaultdict(list)
    for c in cards:
        by_src[c['출처기록']].append(c)
    tps = topics()
    with open(os.path.join(MED, 'index.csv'), encoding='utf-8') as f:
        idx = {r['id']: r for r in csv.DictReader(f)}
    used = set()
    L = ['# 숫자 카드 주제별 찾아보기 (자동 생성)', '',
         '`python -X utf8 medical/numbers/build_by_topic.py`로 만든다. 직접 고치지 않는다. '
         '`numbers.csv`나 패키지·가이드의 MED 인용이 바뀌면 다시 만든다(`scripts/check_all.py`가 낡았는지 알려 준다).', '',
         '- 묶는 기준: 그 패키지·가이드가 인용하는 MED 기록을 출처로 한 카드. 한 카드가 여러 주제에 나올 수 있다.',
         '- 주제 안 순서: 주제 이름의 낱말(예: 위암, 실손)이 카드 문장이나 출처 기록의 질병·제목에 들어 있는 카드부터, 그다음 그 파일이 출처 기록을 많이 인용한 카드, 같으면 카드 번호 순.',
         f"- ⏰: 재확인 기한이 {start.isoformat()}부터 3개월 안({soon} 전)이다. 영상에 쓰기 전에 출처 기록을 다시 연다"
         f"(기준 달 {month}, 달이 바뀌면 다시 만든다).",
         '- 카드를 쓰기 전에 주의 열을 꼭 읽는다(직접 계산·tier B·해석 제한). 자세한 규칙은 `numbers/README.md`.', '']
    rows_by_topic = []
    for kind, name, title, refs in tps:
        words = topic_words(name)
        rows = []
        for rid, n in refs.items():
            meta = idx.get(rid, {})
            hay = ' '.join([meta.get('disease', ''), meta.get('title', '')])
            for c in by_src.get(rid, []):
                hit = sum(1 for w in words if w in c['한 줄 문장'] or w in c['주제'] or w in hay)
                rows.append((-hit, -n, c['카드번호'], c))
        rows.sort(key=lambda r: r[:3])
        rows = [(r[1], r[2], r[3]) for r in rows]
        rows_by_topic.append((kind, name, title, rows))
        used.update(c['카드번호'] for _, _, c in rows)
    with_cards = [t for t in rows_by_topic if t[3]]
    L.append(f"주제 {len(with_cards)}개(패키지 {sum(1 for t in with_cards if t[0] == '패키지')}, "
             f"가이드 {sum(1 for t in with_cards if t[0] == '가이드')}), 카드 {len(cards)}줄 중 주제에 묶인 카드 {len(used)}줄.")
    L.append('')
    L.append('## 목차')
    L.append('')
    for kind, name, title, rows in with_cards:
        L.append(f"- [{kind} {name}](#{anchor(kind, name)}) — 카드 {len(rows)}줄")
    L.append('')
    for kind, name, title, rows in with_cards:
        L.append(f"## {kind} {name}")
        L.append('')
        path = f"../packages/{name}.md" if kind == '패키지' else f"../guides/{name}.md"
        L.append(f"[{title}]({path}) · 카드 {len(rows)}줄")
        L.append('')
        L.append('| 카드 | 한 줄 문장 | 값 | 기준 시점 | 출처 | 재확인 기한 | 주의 |')
        L.append('|---|---|---|---|---|---|---|')
        for _, cid, c in rows:
            due = c['재확인기한']
            mark = ' ⏰' if due and due < soon else ''
            val = c['숫자'] + ('' if c['단위'] in ('', '-') else ' ' + c['단위'])
            L.append(f"| {cid} | {cell(c['한 줄 문장'])} | {cell(val)} | {cell(c['기준시점'])} | {c['출처기록']} | "
                     f"{due}{mark} | {cell(c['주의'])} |")
        L.append('')
    rest = [c for c in cards if c['카드번호'] not in used]
    L.append('## 주제 밖 카드')
    L.append('')
    L.append(f"어느 패키지·가이드도 출처 기록을 인용하지 않는 카드 **{len(rest)}줄**. 새 패키지·가이드를 만들 때 소재 후보로 본다.")
    L.append('')
    if rest:
        L.append('| 카드 | 주제(numbers.csv) | 한 줄 문장 | 값 | 출처 | 재확인 기한 |')
        L.append('|---|---|---|---|---|---|')
        for c in rest:
            due = c['재확인기한']
            mark = ' ⏰' if due and due < soon else ''
            val = c['숫자'] + ('' if c['단위'] in ('', '-') else ' ' + c['단위'])
            L.append(f"| {c['카드번호']} | {cell(c['주제'])} | {cell(c['한 줄 문장'])} | {cell(val)} | {c['출처기록']} | {due}{mark} |")
        L.append('')
    return '\n'.join(L) + '\n', len(with_cards), len(rest)


GENERIC = {'가이드', '패키지', '체크', '판단', '총정리', '비교와', '갈아타기', '가입과', '청구', '받기', '전', '받을때', '대응',
           '인정', '범위와', '평가', '읽기와', '등록과', '재등록', '타임라인', '할일', '진단후', '보험', '영상'}


def topic_words(name):
    """'01_위암' → ['위암'], '24_근골격_도수체외충격파디스크' → ['근골격', '도수체외충격파디스크'].
    가이드 이름의 '…과/와'·'…의' 같은 꼬리는 떼고, 일반 낱말은 뺀다."""
    out = []
    for w in re.sub(r'^\d+_', '', name).split('_'):
        w = re.sub(r'(과|와|의|을|를)$', '', w)
        if len(w) >= 2 and w not in GENERIC:
            out.append(w)
    return out


def anchor(kind, name):
    """GitHub 마크다운 제목 앵커(소문자, 공백→-, 영숫자·한글·-·_ 외 제거)."""
    s = f'{kind} {name}'.lower().replace(' ', '-')
    return re.sub(r'[^0-9a-z가-힣_\-]', '', s)


def main():
    ap = argparse.ArgumentParser(description='숫자 카드 주제별 찾아보기 만들기')
    ap.add_argument('--month', default=datetime.date.today().strftime('%Y-%m'), help='⏰ 표시 기준 달 YYYY-MM')
    ap.add_argument('--check', action='store_true', help='쓰지 않고 최신인지만 본다(낡았으면 종료코드 1)')
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    text, n_topics, n_rest = build(a.month)
    old = open(OUT, encoding='utf-8').read() if os.path.exists(OUT) else ''
    if a.check:
        if old == text:
            print(f'BY_TOPIC.md 최신(주제 {n_topics}개, 주제 밖 카드 {n_rest}줄)')
            sys.exit(0)
        print('BY_TOPIC.md 낡음: `python -X utf8 medical/numbers/build_by_topic.py`로 다시 만든다'
              + ('' if old else '(파일 없음)'))
        sys.exit(1)
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print(f'BY_TOPIC.md 작성: 주제 {n_topics}개, 주제 밖 카드 {n_rest}줄, {len(text.splitlines())}줄')


if __name__ == '__main__':
    main()
