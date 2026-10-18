#!/usr/bin/env python3
"""매달 1일 점검 목록을 마크다운으로 낸다.

사용법:  python -X utf8 medical/scripts/monthly.py 2026-11 [--out 파일.md]

내는 것
  1. 그달 recheck_by가 오는 verified 기록(index.csv 기준). 활용물·카드 인용이 많은 순서, 확인할 url 포함
  2. CALENDAR.md의 그달 항목: 'YYYY년 M월' 절의 표, 'M월에 확인할 것' 표, '이번 달·다음 달 할 일'의 그달 줄
  3. 함께 고칠 곳: 1·2의 기록을 인용하는 패키지·가이드·쇼츠 원고(packages/drafts)·CALENDAR·숫자 카드
인용 횟수는 활용물에서 그 MED 번호가 나온 줄 수(번호 나열·범위 포함)와 그 기록을 출처로 쓴 카드 수다.
파이썬 표준 라이브러리만 쓴다. 파일을 고치지 않는다.
"""
import argparse, collections, csv, glob, os, re, sys

MED = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # medical/
sys.path.insert(0, os.path.join(MED, 'tools'))
from factcheck import med_refs                                         # 'MED-1543·1544', 'MED-1558~1573'도 푼다

KINDS = [('패키지', 'packages/*.md'), ('쇼츠 원고', 'packages/drafts/*.md'), ('가이드', 'guides/*.md'), ('CALENDAR', 'CALENDAR.md')]


def cell(s):
    return str(s).replace('|', '/').replace('\n', ' ')


def citations():
    """MED 번호 → {(종류, 파일 이름): 줄 수}."""
    where = collections.defaultdict(collections.Counter)
    for kind, pat in KINDS:
        for path in sorted(glob.glob(os.path.join(MED, pat))):
            if os.path.basename(path) == 'README.md':
                continue
            name = os.path.splitext(os.path.basename(path))[0]
            with open(path, encoding='utf-8') as f:
                for line in f:
                    for rid in med_refs(line):
                        where[rid][(kind, name)] += 1
    return where


def cards_by_record():
    out = collections.defaultdict(list)
    with open(os.path.join(MED, 'numbers', 'numbers.csv'), encoding='utf-8') as f:
        for r in csv.DictReader(f):
            out[r['출처기록']].append(r['카드번호'])
    return out


def month_in(title, y, m):
    """'2026년 11월', '2027년 7~8월', '2027년 10~12월' 같은 절 제목이 y년 m월을 포함하는가."""
    mt = re.match(r'(\d{4})년 (\d{1,2})(?:~(\d{1,2}))?월', title)
    if not mt or int(mt.group(1)) != y:
        return False
    a = int(mt.group(2))
    b = int(mt.group(3) or a)
    return a <= m <= b


def calendar_items(y, m):
    """CALENDAR.md에서 그달 항목을 뽑는다. 반환: (절 이름, 표 머리 칸 목록 또는 None, 줄) 목록."""
    path = os.path.join(MED, 'CALENDAR.md')
    if not os.path.exists(path):
        return []
    sec, header, out = '', None, []
    todo = re.compile(rf'^- \*\*{m}월[^*]*\*\*')                       # '- **11월 27일**:' 같은 할 일 줄
    for line in open(path, encoding='utf-8').read().split('\n'):
        if line.startswith('## '):
            sec, header = line[3:].strip(), None
            continue
        want = month_in(sec, y, m) or sec.startswith(f'{m}월에 확인할 것')
        if line.startswith('|'):
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if re.fullmatch(r'[\s|:\-]+', line):
                continue
            if header is None:
                header = cells
                continue
            if want:
                out.append((sec, header, cells))
        elif sec.startswith('이번 달·다음 달 할 일') and todo.match(line):
            out.append((sec, None, [line[2:].strip()]))
    return out


def main():
    ap = argparse.ArgumentParser(description='매달 1일 점검 목록')
    ap.add_argument('month', help='YYYY-MM (예: 2026-11)')
    ap.add_argument('--out', help='마크다운으로 저장할 파일')
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if not re.fullmatch(r'\d{4}-\d{2}', a.month):
        ap.error('달은 YYYY-MM 형식으로 준다(예: 2026-11)')
    y, m = int(a.month[:4]), int(a.month[5:])
    with open(os.path.join(MED, 'index.csv'), encoding='utf-8') as f:
        idx = {r['id']: r for r in csv.DictReader(f)}
    where = citations()
    cards = cards_by_record()

    def mentions(rid):
        return sum(where[rid].values())

    due = [r for r in idx.values() if r['status'] == 'verified' and r['recheck_by'].startswith(a.month)]
    due.sort(key=lambda r: (-(mentions(r['id']) + len(cards[r['id']])), r['recheck_by'], r['id']))
    cal = calendar_items(y, m)
    cal_refs = []
    for _, _, cells in cal:
        for rid in sorted(med_refs(' '.join(cells))):
            if rid not in cal_refs:
                cal_refs.append(rid)

    L = [f'# {y}년 {m}월 점검 목록', '',
         f'`python -X utf8 medical/scripts/monthly.py {a.month}`로 만든 목록이다. 기록을 고치면 3절의 활용물·카드를 함께 고치고, '
         '끝나면 `python -X utf8 medical/scripts/check_all.py`를 돌린다.', '']
    cited = [r for r in due if mentions(r['id']) or cards[r['id']]]
    L.append(f"- 재확인 기한 기록 {len(due)}건(활용물·카드 인용 있음 {len(cited)}, 없음 {len(due) - len(cited)})")
    L.append(f"- CALENDAR 항목 {len(cal)}개(관련 MED 기록 {len(cal_refs)}건)")
    L.append('')

    L += [f'## 1. 재확인 기한이 {y}년 {m}월인 verified 기록 ({len(due)}건)', '']
    if due:
        L += ['| 순서 | 기록 | 기한 | tier | 질병 | 활용물 인용 줄 | 카드 | 확인할 url | 제목 |', '|---|---|---|---|---|---|---|---|---|']
        for i, r in enumerate(due, 1):
            L.append(f"| {i} | {r['id']} | {r['recheck_by']} | {r['tier']} | {cell(r['disease'])[:20]} | {mentions(r['id'])} | "
                     f"{len(cards[r['id']])} | {r['url']} | {cell(r['title'])[:60]} |")
    else:
        L.append('해당 기록 없음.')
    L.append('')

    L += [f'## 2. CALENDAR.md의 {m}월 항목 ({len(cal)}개)', '']
    if not cal:
        L.append('해당 항목 없음.')
    last = None
    for sec, header, cells in cal:
        if sec != last:
            L += ([] if L[-1] == '' else ['']) + [f'### {sec}', '']
            if header:
                L += ['| ' + ' | '.join(header) + ' |', '|' + '---|' * len(header)]
            last = sec
        L.append('| ' + ' | '.join(cells) + ' |' if header else '- ' + cells[0])
    L.append('')

    L += ['## 3. 함께 고칠 곳(위 기록을 인용하는 활용물·카드)', '',
          '| 기록 | 왜 | 패키지 | 쇼츠 원고 | 가이드 | CALENDAR | 카드 |', '|---|---|---|---|---|---|---|']
    targets = [(r['id'], '재확인 기한') for r in due] + [(rid, 'CALENDAR 항목') for rid in cal_refs if rid not in {r['id'] for r in due}]
    n_rows = 0
    for rid, why in targets:
        w = where[rid]
        cols = []
        for kind, _ in KINDS:
            names = [f'{n}({c})' for (k, n), c in sorted(w.items(), key=lambda x: (-x[1], x[0][1])) if k == kind]
            cols.append(', '.join(names) or '-')
        cs = cards[rid]
        if all(c == '-' for c in cols) and not cs:
            continue
        n_rows += 1
        L.append(f"| {rid} | {why} | " + ' | '.join(cols) + f" | {', '.join(cs) or '-'} |")
    if not n_rows:
        L.append('| (없음) | | | | | | |')
    L.append('')
    text = '\n'.join(L)
    print(text)
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            f.write(text + '\n')


if __name__ == '__main__':
    main()
