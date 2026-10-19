#!/usr/bin/env python3
"""숫자 카드(numbers.csv)가 활용물에서 얼마나 쓰이는지 세어 보고서를 만든다(23라운드).

사용법:  python -X utf8 medical/numbers/card_usage.py [--today YYYY-MM-DD] [--out 파일.md]
  기본 출력: medical/reviews/숫자카드_사용현황_<today>.md

활용물: packages/*.md(영상각도_목록 포함), packages/drafts/*.md, guides/*.md, CALENDAR.md (README.md 제외).
문장 나누기·숫자 뽑기·값 비교는 tools/factcheck.py 함수를 그대로 쓴다. 한 카드가 한 문장에서 쓰였다고 세는 경우는 둘이다.
  ① factcheck 카드 근거: factcheck가 그 문장의 숫자에 대해 고른 첫 근거가 이 카드다(check_number 결과 맨 앞이 '카드').
  ② 같은 MED·같은 값: 그 문장(또는 그 줄·목록 항목)이 인용한 MED 번호가 카드의 출처기록이고,
     문장 숫자가 카드 값(한 줄 문장에 풀어 쓴 값 포함)과 같다.
  ③ 글자 그대로: factcheck가 값을 읽지 못하는 카드('100분의 15', '30세 미만'처럼 숫자 열에 말이 섞인 카드)는
     그 문장이 카드의 출처기록을 인용하고 숫자 열 글자가 문장에 그대로 있으면 쓰인 것으로 본다.
①은 기간·나이·횟수 같은 흔한 숫자면 문장이 그 카드 출처기록을 인용할 때만 센다.
한 문장에서 ①~③이 겹치면 한 번만 센다. 숫자를 우리말로 바꿔 쓴 문장('절반', '열 명 중 한 명')은 셀 수 없다.
check_all.py에는 넣지 않는다(보고서용).
"""
import argparse, collections, csv, datetime, glob, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MED = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(MED, 'tools'))
import factcheck as fc                                                  # noqa: E402

KINDS = [('패키지', 'packages/*.md'), ('쇼츠 원고', 'packages/drafts/*.md'), ('가이드', 'guides/*.md'), ('CALENDAR', 'CALENDAR.md')]


def cell(s):
    return str(s).replace('|', '/').replace('\n', ' ').strip()


def files():
    for kind, pat in KINDS:
        for p in sorted(glob.glob(os.path.join(MED, pat))):
            if os.path.basename(p) != 'README.md':
                yield kind, os.path.splitext(os.path.basename(p))[0], p


def usage(today):
    """카드번호 → {(종류, 파일, 문장 순번): 방법 집합}."""
    _, recs, cards = fc.load(today)
    by_src = collections.defaultdict(list)
    for c in cards:
        by_src[c['row']['출처기록']].append(c)
    parsed = {c['row']['카드번호'] for c in cards}
    raw_only = collections.defaultdict(list)                         # factcheck가 값을 못 읽은 카드(출처기록별)
    for r in csv.DictReader(open(os.path.join(HERE, 'numbers.csv'), encoding='utf-8')):
        if r['카드번호'] not in parsed:
            raw_only[r['출처기록']].append(r)
    used = collections.defaultdict(dict)
    for kind, name, path in files():
        text = open(path, encoding='utf-8').read()
        for k, (s, heading, refs) in enumerate(fc.sentences(text), 1):
            for rid in refs:
                for r in raw_only.get(rid, ()):
                    if r['숫자'] in s:
                        used[r['카드번호']].setdefault((kind, name, k), set()).add('글자 그대로')
            nums, _ = fc.extract(re.sub(r'\(MED-[\d, MED-]+\)', '', s))
            if not nums:
                continue
            kw = fc.keywords(s)
            hkw = fc.keywords(re.sub(r'[\d.]+', ' ', heading))
            for n in nums:
                _, hits = fc.check_number(n, kw, recs, cards, today, fc.entities_in(s), fc.entities_in(heading), hkw,
                                          limits=not refs)
                # 기간·나이·횟수·작은 건수('6개월', '8주', '65세')는 여러 주제에 흔해 factcheck 단어 겹침만으로는 다른 카드에 붙는다.
                # 그런 숫자는 문장이 그 카드의 출처기록을 인용할 때만 센다(factcheck의 substantive 기준)
                if hits and hits[0][0] == '카드' and (fc.substantive(n) or hits[0][3] in refs):
                    used[hits[0][2]].setdefault((kind, name, k), set()).add('factcheck')
                for rid in refs:
                    for c in by_src.get(rid, ()):
                        if any(fc.same_number(n, x) for x in c['alts']):
                            used[c['row']['카드번호']].setdefault((kind, name, k), set()).add('같은 MED·값')
    return used, cards, sum(len(v) for v in raw_only.values())


def main():
    ap = argparse.ArgumentParser(description='숫자 카드 사용 현황 보고서')
    ap.add_argument('--today', default=datetime.date.today().isoformat())
    ap.add_argument('--out', help='저장할 마크다운 파일(기본: reviews/숫자카드_사용현황_<today>.md)')
    ap.add_argument('--top', type=int, default=30)
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    out = a.out or os.path.join(MED, 'reviews', f'숫자카드_사용현황_{a.today}.md')
    used, cards, n_raw = usage(a.today)
    rows = list(csv.DictReader(open(os.path.join(HERE, 'numbers.csv'), encoding='utf-8')))
    nfiles = len(list(files()))
    unused = [r for r in rows if not used.get(r['카드번호'])]
    by_method = collections.Counter()
    for cid, uses in used.items():
        ms = set().union(*uses.values())
        by_method['factcheck만' if ms == {'factcheck'} else '같은 MED·값만' if ms == {'같은 MED·값'}
                  else '글자 그대로' if ms == {'글자 그대로'} else '둘 다'] += 1

    L = [f'# 숫자 카드 사용 현황 ({a.today})', '',
         f'`python -X utf8 medical/numbers/card_usage.py --today {a.today}`로 만든 보고서다. 카드 {len(rows)}줄이 활용물 {nfiles}개'
         '(패키지·쇼츠 원고·가이드·CALENDAR, README 제외)에서 쓰였는지 문장 단위로 셌다.', '',
         '## 세는 방법', '',
         '- ① **factcheck 카드 근거**: `tools/factcheck.py`가 그 문장 숫자의 첫 근거로 고른 카드. 단, 기간·나이·횟수·100 미만 건수(`substantive()`가 아닌 숫자)는 여러 주제에 흔해 단어 겹침만으로 다른 카드에 붙으므로, 문장이 그 카드의 출처기록을 인용할 때만 센다.',
         '- ② **같은 MED·같은 값**: 문장(또는 그 줄·목록 항목)이 인용한 MED 번호가 카드의 출처기록이고, 숫자가 카드 값과 같다(한 줄 문장에 풀어 쓴 값 포함).',
         f"- ③ **글자 그대로**: 숫자 열에 말이 섞여 factcheck가 값을 읽지 못하는 카드 {n_raw}줄('100분의 15', '30세 미만' 등)은 "
         '문장이 그 출처기록을 인용하고 숫자 열 글자가 문장에 그대로 있으면 쓰인 것으로 본다.',
         '- 한 문장에서 ①~③이 겹치면 한 번으로 센다. \'사용 문장 수\'는 그 카드가 쓰인 (파일, 문장) 개수다.',
         "- 한계: 숫자를 말로 바꿔 쓴 문장('절반', '열 명 중 한 명'), 다른 기록을 인용하며 같은 값을 쓴 문장은 ②로 잡히지 않는다. "
         "안 쓰이는 카드라도 활용물이 그 기록 자체(다른 숫자)를 인용할 수는 있다. "
         "반대로 ①은 값과 주제가 비슷한 다른 카드에 붙을 수 있어(예: 치매 패키지의 '산정특례 10%'가 투석 산정특례 10% 카드로), 사용 문장 수는 넉넉하게 센 값이다.", '',
         '## 요약', '',
         f'- 카드 {len(rows)}줄 중 한 번 이상 쓰인 카드 {len(rows) - len(unused)}줄, **한 번도 안 쓰인 카드 {len(unused)}줄**.',
         f"- 쓰인 카드의 잡힌 방법: ①·② 둘 다 {by_method['둘 다']}, ① factcheck만 {by_method['factcheck만']}, ② 같은 MED·값만 {by_method['같은 MED·값만']}, ③ 글자 그대로 {by_method['글자 그대로']}.", '']

    # 많이 쓰이는 카드
    top = sorted(((cid, uses) for cid, uses in used.items()), key=lambda x: (-len(x[1]), x[0]))[:a.top]
    rowmap = {r['카드번호']: r for r in rows}
    L += [f'## 많이 쓰이는 카드 상위 {a.top}개', '',
          '이 카드의 출처 기록이 바뀌면 고칠 활용물이 많다. 재확인 기한 전에 기록을 먼저 본다.', '',
          '| 순위 | 카드 | 출처 | 숫자 | 한 줄 문장 | 사용 문장 수 | 파일 수 | 재확인 기한 | 많이 쓴 파일 |', '|---|---|---|---|---|---|---|---|---|']
    for i, (cid, uses) in enumerate(top, 1):
        r = rowmap[cid]
        fc_ = collections.Counter((k, n) for k, n, _ in uses)
        names = ', '.join(f'{n}({c})' for (k, n), c in fc_.most_common(4))
        L.append(f"| {i} | {cid} | {r['출처기록']} | {cell(r['숫자'])} {cell(r['단위']).replace('-', '')} | {cell(r['한 줄 문장'])} | "
                 f"{len(uses)} | {len(fc_)} | {r['재확인기한']} | {names} |")
    L.append('')

    # 안 쓰이는 카드: 주제별
    g = collections.defaultdict(list)
    for r in unused:
        g[r['주제']].append(r)
    total_by_topic = collections.Counter(r['주제'] for r in rows)
    L += ['## 한 번도 안 쓰인 카드 — 주제별 (다음 원고·패키지 소재 후보)', '',
          '주제마다 안 쓰인 카드 수 / 그 주제 전체 카드 수. 안 쓰인 비율이 높은 주제가 아직 활용물로 덜 옮겨진 쪽이다.', '',
          '| 주제 | 안 쓰인 카드 | 전체 | 비율 |', '|---|---|---|---|']
    order = sorted(g, key=lambda t: (-len(g[t]), t))
    for t in order:
        L.append(f'| {t} | {len(g[t])} | {total_by_topic[t]} | {len(g[t]) * 100 // total_by_topic[t]}% |')
    L.append('')
    for t in order:
        L += [f'### {t} ({len(g[t])}줄)', '', '| 카드 | 출처 | 숫자 | 한 줄 문장 | 기준시점 |', '|---|---|---|---|---|']
        for r in sorted(g[t], key=lambda r: (r['출처기록'], r['카드번호'])):
            L.append(f"| {r['카드번호']} | {r['출처기록']} | {cell(r['숫자'])} {cell(r['단위']).replace('-', '')} | "
                     f"{cell(r['한 줄 문장'])} | {cell(r['기준시점'])} |")
        L.append('')
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(L))
    print(f'{os.path.relpath(out, MED)} 작성: 카드 {len(rows)}줄, 쓰인 카드 {len(rows) - len(unused)}, '
          f'안 쓰인 카드 {len(unused)}(주제 {len(g)}개)')


if __name__ == '__main__':
    main()
