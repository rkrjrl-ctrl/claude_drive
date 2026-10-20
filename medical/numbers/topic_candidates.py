#!/usr/bin/env python3
"""다음 쇼츠 원고 소재 후보 목록을 만든다(25라운드).

사용법:  python -X utf8 medical/numbers/topic_candidates.py [--today YYYY-MM-DD] [--out 파일.md]
  기본 출력: medical/reviews/원고소재_후보_<today>.md

후보 출처 세 가지(새 문장을 짓지 않고 원문 문장을 그대로 옮긴다):
  ① 숫자 카드 중 활용물에서 한 번도 안 쓰인 카드(card_usage.py와 같은 셈법). 제목 = 카드 한 줄 문장
  ② packages/영상각도_목록.md '전체 표'의 각도 중 아직 쇼츠 원고(packages/drafts)가 없는 것. 제목 = 각도 제목
  ③ CALENDAR.md 2026-11~2027-03 일정 표(영상 소재 칸이 '영상 소재 아님'·'해당 없음'인 줄은 뺀다).
     제목 = 영상 소재 칸에 따옴표 문장이 있으면 그 문장, 없으면 '무엇이 바뀌나' 칸
원고와 겹치는지:
  - 이미 원고: 원고 머리말 '출처: NN_패키지 7절 N번 각도'가 그 각도를 가리키거나, 원고와 MED가 겹치고 제목 낱말이 2개 이상 겹친다
  - 같은 MED 원고: MED만 겹친다(소재는 다를 수 있다) → 후보로 남기고 원고 이름을 적는다
  '이미 원고'인 후보는 목록에서 빼고 개수만 적는다.
정렬: 시의성 날짜가 가까운 순(초=1일, 중·중순=15일, 하순·말=25일로 본다), 날짜가 없는 '상시' 후보는 맨 뒤.
"""
import argparse, collections, csv, datetime, glob, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MED = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(MED, 'tools'))
sys.path.insert(0, os.path.join(MED, 'scripts'))
sys.path.insert(0, HERE)
import factcheck as fc                                                  # noqa: E402
import monthly as mo                                                    # noqa: E402
import card_usage as cu                                                 # noqa: E402

MONTHS = [(2026, 11), (2026, 12), (2027, 1), (2027, 2), (2027, 3)]
FAR = datetime.date(9999, 12, 31)


def cell(s):
    return str(s).replace('|', '/').replace('\n', ' ').strip()


def when_date(s):
    """'2026-11 하순', '2027-01-15 전후', '2026년 하반기' 같은 글에서 가장 이른 날짜."""
    best = FAR
    # 27라운드: 앞뒤가 숫자로 이어지면 날짜로 보지 않고('2026-2027절기'), 없는 달·날은 건너뛴다
    for y, m, d, tail in re.findall(r'(?<![\d-])(\d{4})-(\d{2})(?:-(\d{2}))?(?!\d)\s*(초|중순|중|하순|말)?', s):
        day = int(d) if d else {'초': 1, '중': 15, '중순': 15, '하순': 25, '말': 25}.get(tail, 1)
        try:
            best = min(best, datetime.date(int(y), int(m), day))
        except ValueError:
            continue
    for y, h in re.findall(r'(\d{4})년 (상반기|하반기|중)', s):
        best = min(best, datetime.date(int(y), {'상반기': 1, '하반기': 7, '중': 1}[h], 1))
    return best


def drafts():
    """원고 이름 → (MED 묶음, 제목 낱말, 가리킨 (패키지, 각도 번호) 묶음)."""
    out = {}
    for p in sorted(glob.glob(os.path.join(MED, 'packages', 'drafts', '쇼츠_*.md'))):
        text = open(p, encoding='utf-8').read()
        name = os.path.splitext(os.path.basename(p))[0]
        head = re.search(r'^# (.+)$', text, re.M)
        src = re.search(r'^- 출처: (.+)$', text, re.M)
        angles = set()
        if src:
            for pkg, nums in re.findall(r'(\d\d_[^\s]+) 7절 ([\d·]+)번 각도', src.group(1)):
                angles.update((pkg, int(n)) for n in nums.split('·'))
        title = (head.group(1) if head else '').replace('쇼츠 원고 초안:', '')
        out[name] = (fc.med_refs(text), fc.keywords(title + ' ' + name.replace('_', ' ')), angles)
    return out


def overlap_with(meds, title, pkg_angle, ds):
    """('이미 원고', [원고]) / ('같은 MED 원고', [원고]) / ('없음', [])."""
    kw = fc.keywords(title)
    same, med_only = [], []
    for name, (dm, dk, da) in ds.items():
        if pkg_angle and pkg_angle in da:
            same.append(name)
        elif meds & dm and len(fc.overlap(kw, dk)) >= 2:
            same.append(name)
        elif meds & dm:
            med_only.append(name)
    if same:
        return '이미 원고', same
    if med_only:
        return '같은 MED 원고', med_only
    return '없음', []


def angle_rows():
    """영상각도_목록.md 전체 표: (패키지, 패키지 안 순번, 각도 제목, 핵심 숫자와 MED, 시의성)."""
    text = open(os.path.join(MED, 'packages', '영상각도_목록.md'), encoding='utf-8').read()
    part = text.split('## 전체 표', 1)[1]
    seen = collections.Counter()
    out = []
    for line in part.split('\n'):
        if not line.startswith('| ') or line.startswith('| 패키지') or line.startswith('|---'):
            continue
        c = [x.strip() for x in line.strip().strip('|').split('|')]
        if len(c) < 5:
            continue
        seen[c[0]] += 1
        out.append((c[0], seen[c[0]], c[1], c[2], c[3]))
    return out


def calendar_rows():
    """CALENDAR 2026-11~2027-03 표 줄: (날짜, 제목, 근거 칸, 구분)."""
    out, seen = [], set()
    for y, m in MONTHS:
        for sec, header, cells in mo.calendar_items(y, m):
            if not header or '영상 소재' not in header:
                continue
            row = dict(zip(header, cells))
            key = (row.get('날짜'), row.get('무엇이 바뀌나·나오나'))
            if key in seen:
                continue
            seen.add(key)
            use = row.get('영상 소재', '')
            if re.search(r'영상 소재 아님|해당 없음', use):
                continue
            q = re.search(r'"([^"]+)"', use)
            title = q.group(1) if q else row.get('무엇이 바뀌나·나오나', '')
            out.append((row.get('날짜', ''), title, row.get('근거', ''), row.get('구분', '')))
    return out


def main():
    ap = argparse.ArgumentParser(description='쇼츠 원고 소재 후보 목록')
    ap.add_argument('--today', default=datetime.date.today().isoformat())
    ap.add_argument('--out')
    ap.add_argument('--top', type=int, default=10)
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    out = a.out or os.path.join(MED, 'reviews', f'원고소재_후보_{a.today}.md')
    ds = drafts()
    cal = calendar_rows()
    cal_by_med = collections.defaultdict(list)                          # MED → [(날짜, 일정 제목)]
    for d, t, ev, _ in cal:
        for rid in fc.med_refs(ev):
            cal_by_med[rid].append((d, t))

    cands, dropped = [], collections.Counter()

    # ① 안 쓰인 카드
    used, _, _ = cu.usage(a.today)
    rows = list(csv.DictReader(open(os.path.join(HERE, 'numbers.csv'), encoding='utf-8')))
    for r in rows:
        if used.get(r['카드번호']):
            continue
        meds = {r['출처기록']}
        hits = sorted(cal_by_med.get(r['출처기록'], []), key=lambda x: when_date(x[0]))
        when = f"{hits[0][0]}({cell(hits[0][1])[:30]}…)" if hits else '상시'
        kind, names = overlap_with(meds, r['한 줄 문장'], None, ds)
        if kind == '이미 원고':
            dropped['① 카드'] += 1
            continue
        val = r['숫자'] + ('' if r['단위'] in ('', '-') else ' ' + r['단위'])
        cands.append(dict(src='① 카드', title=r['한 줄 문장'], nums=f"{val} ({r['카드번호']}, {r['출처기록']})",
                          when=when, date=when_date(when) if hits else FAR, kind=kind, names=names,
                          pkg=r['주제']))

    # ② 영상 각도
    for pkg, k, title, nums, when in angle_rows():
        meds = fc.med_refs(nums)
        kind, names = overlap_with(meds, title, (pkg, k), ds)
        if kind == '이미 원고':
            dropped['② 각도'] += 1
            continue
        w = '상시' if when in ('-', '') else when
        cands.append(dict(src='② 각도', title=title, nums=nums, when=w, date=when_date(w), kind=kind, names=names,
                          pkg=f'{pkg} 7절 {k}번'))

    # ③ CALENDAR
    for d, title, ev, gubun in cal:
        meds = fc.med_refs(ev)
        kind, names = overlap_with(meds, title, None, ds)
        if kind == '이미 원고':
            dropped['③ 달력'] += 1
            continue
        cands.append(dict(src='③ 달력', title=title, nums=ev or '-', when=f'{d}({gubun})', date=when_date(d),
                          kind=kind, names=names, pkg='CALENDAR'))

    cands.sort(key=lambda c: (c['date'], c['src'], c['title']))
    today = datetime.date.fromisoformat(a.today)

    # 11월에 쓰기 좋은 소재: 시의성이 11월 1일~30일인 후보 중 원고와 MED가 겹치지 않는 것부터,
    # 모자라면 같은 MED 원고가 있는 11월 후보, 그래도 모자라면 12월 이후 일정 순으로 채운다.
    # 같은 제목·같은 근거는 한 번만, 같은 일정에 걸린 카드(①)는 하나만, 이미 고른 후보와 MED가 겹치면 건너뛴다(한 일정에 몰리지 않게).
    ny = today.year if today.month <= 11 else today.year + 1
    nov_start, nov_end = datetime.date(ny, 11, 1), datetime.date(ny, 11, 30)
    pool = [c for c in cands if c['date'] != FAR and c['date'] >= nov_start]
    picks, keys, events, picked_meds = [], set(), set(), set()
    for stage in (lambda c: c['date'] <= nov_end and c['kind'] == '없음',
                  lambda c: c['date'] <= nov_end,
                  lambda c: c['kind'] == '없음',
                  lambda c: True):
        for c in pool:
            if len(picks) >= a.top:
                break
            k = (c['title'], c['nums'])
            ev = c['when'] if c['src'] == '① 카드' else None
            meds = fc.med_refs(c['nums'])
            if k in keys or not stage(c) or (ev and ev in events) or (meds & picked_meds):
                continue
            picks.append(c)
            keys.add(k)
            picked_meds |= meds
            if ev:
                events.add(ev)

    by_src = collections.Counter(c['src'] for c in cands)
    L = [f'# 원고 소재 후보 ({a.today})', '',
         f'`python -X utf8 medical/numbers/topic_candidates.py --today {a.today}`로 만든 목록이다. 아직 쇼츠 원고(`packages/drafts`)로 쓰지 않은 소재를 모았다. '
         '제목은 원문 문장을 그대로 옮겼고 새로 짓지 않았다(카드는 한 줄 문장, 각도는 각도 제목, 달력은 영상 소재 칸 따옴표 문장이나 \'무엇이 바뀌나\' 칸).', '',
         '## 만드는 방법', '',
         '- ① **안 쓰인 카드**: `numbers/card_usage.py`와 같은 셈법으로 활용물 어디에도 안 쓰인 카드. 시의성은 그 카드 출처 MED가 CALENDAR 2026-11~2027-03 일정의 근거에 있으면 그 날짜, 없으면 \'상시\'.',
         '- ② **영상 각도**: `packages/영상각도_목록.md` 전체 표. 시의성은 그 표의 값을 그대로 옮겼다.',
         "- ③ **CALENDAR 일정**: 2026-11~2027-03 일정 표 줄. 영상 소재 칸이 '영상 소재 아님'·'해당 없음'인 줄은 뺐다.",
         "- **원고와 겹침**: 원고 머리말 '출처: NN_패키지 7절 N번 각도'가 그 각도를 가리키거나, 원고와 인용 MED가 겹치고 제목 낱말이 2개 이상 겹치면 '이미 원고'로 보고 목록에서 뺐다. "
         "MED만 겹치면 '같은 MED 원고'로 적고 후보에 남겼다(같은 근거로 다른 소재를 쓸 수 있는지 사람이 본다).",
         '- 시의성 날짜는 초=1일, 중·중순=15일, 하순·말=25일로 보고 가까운 순서로 놓았다. 날짜가 없는 상시 후보는 맨 뒤다.', '',
         '## 요약', '',
         f"- 후보 **{len(cands)}개**: ① 카드 {by_src['① 카드']}, ② 각도 {by_src['② 각도']}, ③ 달력 {by_src['③ 달력']}. "
         f"시의성 날짜가 있는 후보 {sum(1 for c in cands if c['date'] != FAR)}개, 상시 {sum(1 for c in cands if c['date'] == FAR)}개.",
         f"- 이미 원고가 있어 뺀 것: ① 카드 {dropped['① 카드']}, ② 각도 {dropped['② 각도']}, ③ 달력 {dropped['③ 달력']}.",
         f"- 원고와 인용 MED가 전혀 안 겹치는 후보 {sum(1 for c in cands if c['kind'] == '없음')}개, 같은 MED 원고가 있는 후보 {sum(1 for c in cands if c['kind'] != '없음')}개.", '',
         f'## 11월에 쓰기 좋은 소재 {len(picks)}개', '',
         '시의성이 11월 1일~30일인 후보 중 원고와 MED가 안 겹치는 것부터 골랐다. 모자라면 같은 MED 원고가 있는 11월 후보, 그다음 가까운 12월 이후 후보 순으로 채웠다. '
         '같은 제목·같은 근거는 한 번만, 같은 일정에 걸린 카드는 하나만, 이미 고른 후보와 근거 MED가 겹치는 후보는 넣지 않았다. 시의성 날짜는 기계적으로 붙인 값이라, 고를 때 괄호 속 일정이 소재와 맞는지 본다.', '',
         '| 순서 | 출처 | 각도 제목(원문 그대로) | 핵심 숫자와 MED | 시의성 | 원고와 겹침 |', '|---|---|---|---|---|---|']

    def ov(c):
        return '없음' if c['kind'] == '없음' else f"{c['kind']}: {', '.join(c['names'][:3])}{' 외' if len(c['names']) > 3 else ''}"

    for i, c in enumerate(picks, 1):
        L.append(f"| {i} | {c['src']} {cell(c['pkg'])} | {cell(c['title'])} | {cell(c['nums'])} | {cell(c['when'])} | {ov(c)} |")
    L += ['', f'## 전체 후보 {len(cands)}개 (시의성 가까운 순)', '',
          '| 시의성 | 출처 | 각도 제목(원문 그대로) | 핵심 숫자와 MED | 원고와 겹침 |', '|---|---|---|---|---|']
    for c in cands:
        L.append(f"| {cell(c['when'])} | {c['src']} {cell(c['pkg'])} | {cell(c['title'])} | {cell(c['nums'])} | {ov(c)} |")
    L.append('')
    with open(out, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(L))
    print(f"{os.path.relpath(out, MED)} 작성: 후보 {len(cands)}개(① {by_src['① 카드']}, ② {by_src['② 각도']}, ③ {by_src['③ 달력']}), "
          f"이미 원고로 뺀 것 {sum(dropped.values())}개, 11월 소재 {len(picks)}개")


if __name__ == '__main__':
    main()
