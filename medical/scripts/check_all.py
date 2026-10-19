#!/usr/bin/env python3
"""커밋 전 검사를 한 번에 돌린다: build_index --strict, verify_numbers, factcheck 일괄 점검.

사용법:  python -X utf8 medical/scripts/check_all.py [--dry] [--out 결과.md] [--today YYYY-MM-DD]
  --dry   : medical/ 를 임시 폴더에 복사해 그 안에서 돌린다(index.csv·STATUS.md·BY_DISEASE.md 를 고치지 않는다)
  --out   : 요약과 factcheck 지적 전체를 마크다운 파일로도 저장한다
  --today : 기한 판단 기준일(기본: 오늘). build_index·factcheck에도 같은 날을 넘긴다

종료코드: 새 문제가 있으면 1, 없으면 0.
  - build_index: 오류나 경고가 하나라도 있으면 문제('재확인 기한 경과' 경고는 아래 기한 경고로 옮기고 문제로 세지 않는다)
  - verify_numbers: notfound·badstatus·stale_meta·calc_unflagged·caution_num_missing·tax_no_gwisok(세법 카드 귀속 연도 없음)·annual_no_year(해마다 바뀌는 숫자 카드 적용 연도 없음)·long 이 하나라도 있으면 문제.
    in_ban·dup 은 알려진 예외(reviews/factcheck_known.csv)에 없는 것만 문제
  - factcheck: '근거 없음'·'인용 번호 불일치' 숫자 중 알려진 예외에 없는 것만 문제
  - 링크·MED 번호(16라운드): packages·packages/drafts·guides·CALENDAR.md·README류의 마크다운 링크 [..](파일)가 없는 파일을
    가리키거나, 본문의 MED 번호가 index.csv에 없으면 문제. superseded·rejected 기록 인용은 경고만
    (그 줄에 'superseded'·'rejected'라고 스스로 밝혔으면 경고하지 않는다)
  - 쇼츠 원고 길이(17라운드, 경고만): packages/drafts 원고 '낭독판'의 예상 낭독 시간이 45~55초를 벗어나거나,
    '근거 표시판'과 '낭독판' 문장이 (번호와 (MED-…)를 빼고) 서로 다르면 경고
알려진 예외는 메인이 원문과 맞다고 확인한 것만 reviews/factcheck_known.csv 에 한 줄씩 적는다.
기한 경고(종료코드에 영향 없음, 13라운드 추가):
  - 재확인 기한이 지난 verified 기록 중 활용물(packages·packages/drafts·guides·CALENDAR)이나 숫자 카드가 인용하는 것
  - 쇼츠 원고 중 packages/drafts/README.md 표의 '시의성 날짜'·'사용 기한' 칸에 적힌 날짜가 지난 것
    (README '작성 YYYY-MM-DD'보다 앞선 날짜는 설명용 날짜로 보고 뺀다)
  - CALENDAR.md 달별 절에서 날짜가 지났는데 구분이 '시행됨'이 아닌 항목(이미 시행됨 절로 옮기지 않은 것)
  - numbers/BY_TOPIC.md가 낡았으면(다시 만들면 내용이 바뀌면) 경고(15라운드)
파이썬 표준 라이브러리만 쓴다.
"""
import argparse, calendar, collections, csv, datetime, glob, os, re, shutil, subprocess, sys, tempfile, time

MED = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # medical/
KNOWN = os.path.join('reviews', 'factcheck_known.csv')
FC_INPUTS = ['--dir', 'packages', '--dir', os.path.join('packages', 'drafts'), '--dir', 'guides', 'CALENDAR.md']
VERIFY_HARD = ['notfound', 'badstatus', 'stale_meta', 'calc_unflagged', 'caution_num_missing', 'tax_no_gwisok', 'annual_no_year', 'long']
VERIFY_SOFT = ['in_ban', 'dup']
PROBLEM = ('근거 없음', '인용 번호 불일치')


def run(args, cwd):
    """하위 스크립트를 실행한다. 반환: (종료코드, 표준출력, 걸린 초)."""
    t0 = time.time()
    p = subprocess.run([sys.executable, '-X', 'utf8'] + args, cwd=cwd, capture_output=True,
                       text=True, encoding='utf-8', errors='replace')
    return p.returncode, p.stdout + (('\n' + p.stderr) if p.stderr.strip() else ''), time.time() - t0


def load_known(med):
    path = os.path.join(med, KNOWN)
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        return [r for r in csv.DictReader(f) if (r.get('종류') or '').strip()]


# ---------------------------------------------------------------- build_index
def check_build(med, today):
    code, out, sec = run([os.path.join('scripts', 'build_index.py'), '--strict', '--today', today], med)
    m = re.search(r'자료 (\d+)건 / 오류 (\d+) / 경고 (\d+)', out)
    n, err, warn = (int(x) for x in m.groups()) if m else (0, -1, -1)
    lines = [l for l in out.splitlines() if l.startswith(('오류:', '경고:'))]
    due = [l for l in lines if l.startswith('경고:') and '재확인 기한 경과' in l]
    other_warn = warn - len(due) if m else warn
    bad = (m is None) or err > 0 or other_warn > 0 or code != 0
    head = (f"자료 {n}건 / 오류 {err} / 경고 {warn}" + (f"(그중 재확인 기한 경과 {len(due)}건은 기한 경고로 셈)" if due else '')
            if m else '결과를 읽지 못함(출력 확인)')
    detail = [l for l in lines if l not in due] if m else out.splitlines()[-20:]
    return dict(name='build_index --strict', bad=bad, head=head, detail=detail, sec=sec)


# ---------------------------------------------------------------- verify_numbers
def check_verify(med, known):
    code, out, sec = run([os.path.join('numbers', 'verify_numbers.py')], med)
    res = {}
    for line in out.splitlines():
        m = re.match(r'(\w+) \((\d+), (.*)\)$', line)
        if m:
            res[m.group(1)] = (int(m.group(2)), m.group(3))
        m = re.match(r'(total|ok) (\d+)$', line)
        if m:
            res[m.group(1)] = int(m.group(2))
    detail, bad = [], code != 0 or 'ok' not in res
    for k in VERIFY_HARD:
        n, items = res.get(k, (0, '[]'))
        if n:
            bad = True
            detail.append(f'{k} {n}건: {items[:300]}')
    kn = {(r['대상'].strip(), r['카드'].strip()) for r in known if r['종류'].strip() == 'verify'}
    soft_new, soft_known = [], 0
    for k in VERIFY_SOFT:
        n, items = res.get(k, (0, '[]'))
        for tup in re.findall(r"\(([^()]*)\)", items):
            cards = re.findall(r"'(N-\d{4})'", tup)
            key = (k, '·'.join(cards))
            if key in kn:
                soft_known += 1
            else:
                soft_new.append(f'{k}: {tup}')
    if soft_new:
        bad = True
        detail += ['새 알림 ' + s for s in soft_new]
    head = f"카드 {res.get('ok', '?')}/{res.get('total', '?')} 일치, " + ', '.join(
        f"{k} {res.get(k, (0,))[0]}" for k in VERIFY_HARD) + f" / in_ban·dup 알려진 예외 {soft_known}건, 새 것 {len(soft_new)}건"
    return dict(name='verify_numbers', bad=bad, head=head, detail=detail, sec=sec)


# ---------------------------------------------------------------- factcheck
ROW = re.compile(r'^\| (\d+) \| (.*) \| (.*) \| ([^|]+) \| (.*) \|$')


def parse_factcheck(out):
    """--only-problems 결과에서 (파일, 문장 번호, 문장, 숫자, 판정, 설명) 목록을 뽑는다."""
    items, f = [], ''
    for line in out.splitlines():
        if line.startswith('## '):
            f = line[3:].strip().replace('\\', '/')
            continue
        m = ROW.match(line)
        if not m or not f:
            continue
        k, sent, _, verdict, ev = m.groups()
        if verdict.strip() not in PROBLEM:
            continue
        sent = sent.replace('\\|', '|')
        for part in ev.split('<br>'):
            pm = re.match(r'\*\*(.+?)\*\*: (근거 없음|인용 번호 불일치)(.*)', part)
            if pm:
                items.append(dict(file=f, k=int(k), sent=sent, num=pm.group(1), verdict=pm.group(2),
                                  note=pm.group(3).strip(' ·')[:160]))
    return items


def is_known(it, known):
    for r in known:
        if r['종류'].strip() != 'factcheck':
            continue
        if r['대상'].strip().replace('\\', '/') != it['file']:
            continue
        if r['숫자'].strip() != it['num']:
            continue
        if r['문장'].strip() and r['문장'].strip() not in it['sent']:
            continue
        return r
    return None


def check_factcheck(med, known, today):
    code, out, sec = run([os.path.join('tools', 'factcheck.py')] + FC_INPUTS + ['--only-problems', '--today', today], med)
    m = re.search(r'파일 (\d+)개, 판정별 문장 수 합계: (.*)', out)
    items = parse_factcheck(out)
    new, old = [], []
    for it in items:
        r = is_known(it, known)
        (old if r else new).append((it, r))
    sents_new = sorted({(it['file'], it['k']) for it, _ in new})
    sents_old = sorted({(it['file'], it['k']) for it, _ in old} - set(sents_new))
    detail = []
    for it, _ in new:
        detail.append(f"새 지적 · {it['file']} {it['k']}번 · **{it['num']}** {it['verdict']} · {it['sent'][:90]}"
                      + (f" ({it['note']})" if it['note'] else ''))
    for it, r in old:
        detail.append(f"알려진 예외 · {it['file']} {it['k']}번 · {it['num']} · {r['메모'].strip()[:60]}")
    total = m.group(2) if m else '결과를 읽지 못함'
    head = (f"파일 {m.group(1) if m else '?'}개. 지적 문장 {len(sents_new) + len(sents_old)}개 = "
            f"새 지적 {len(sents_new)} + 알려진 예외 {len(sents_old)}")
    bad = code != 0 or m is None or bool(new)
    unused = [r for r in known if r['종류'].strip() == 'factcheck'
              and not any(is_known(it, [r]) for it in items)]
    for r in unused:
        detail.append(f"참고 · 예외 목록에 있지만 이번에 지적되지 않음(고쳐졌거나 문장이 바뀜): {r['대상']} {r['숫자']} — 목록에서 지워도 된다")
    return dict(name='factcheck 일괄', bad=bad, head=head, detail=detail, sec=sec, total=total, raw=out)


# ---------------------------------------------------------------- 기한 경고(종료코드에 영향 없음)
ACT = [('패키지', 'packages/*.md'), ('쇼츠 원고', 'packages/drafts/*.md'), ('가이드', 'guides/*.md'), ('CALENDAR', 'CALENDAR.md')]


def month_end(y, m):
    return datetime.date(y, m, calendar.monthrange(y, m)[1])


def loose_date(s, default_year=None):
    """'2026-11-27' → 그날, '2026-11'·'2026-11 하순'·'2026-11 말' → 그달 말일, '2026-11 초' → 10일, '2026-11 중순' → 20일,
    '2026년 4분기' → 분기 말, '2026년 하반기'·'상반기' → 반기 말. 읽지 못하면 None."""
    s = s.strip()
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return datetime.date(*map(int, m.groups()))
    m = re.match(r'(\d{4})-(\d{2})(?!\d)\s*(초|중순|중|하순|말)?', s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        part = m.group(3)
        if part == '초':
            return datetime.date(y, mo, 10)
        if part in ('중순', '중'):
            return datetime.date(y, mo, 20)
        return month_end(y, mo)
    m = re.match(r'(\d{4})년\s*([1-4])분기', s)
    if m:
        return month_end(int(m.group(1)), int(m.group(2)) * 3)
    m = re.match(r'(\d{4})년\s*(상|하)반기', s)
    if m:
        return month_end(int(m.group(1)), 6 if m.group(2) == '상' else 12)
    return None


def check_deadlines(med, today):
    t0 = time.time()
    sys.path.insert(0, os.path.join(med, 'tools'))
    from factcheck import med_refs
    day = datetime.date.fromisoformat(today)
    detail = []
    # (1) 재확인 기한이 지난 verified 기록 중 활용물·카드가 인용하는 것
    with open(os.path.join(med, 'index.csv'), encoding='utf-8') as f:
        idx = {r['id']: r for r in csv.DictReader(f)}
    where = collections.defaultdict(collections.Counter)
    for kind, pat in ACT:
        for path in sorted(glob.glob(os.path.join(med, pat))):
            if os.path.basename(path) == 'README.md':
                continue
            name = os.path.splitext(os.path.basename(path))[0]
            with open(path, encoding='utf-8') as f:
                for line in f:
                    for rid in med_refs(line):
                        where[rid][f'{kind}:{name}'] += 1
    with open(os.path.join(med, 'numbers', 'numbers.csv'), encoding='utf-8') as f:
        for r in csv.DictReader(f):
            where[r['출처기록']]['카드:' + r['카드번호']] += 1
    overdue = []
    for rid, r in idx.items():
        rb = r.get('recheck_by', '')
        if r.get('status') == 'verified' and re.fullmatch(r'\d{4}-\d{2}-\d{2}', rb) and rb < today and where[rid]:
            overdue.append((rid, rb, where[rid]))
    overdue.sort(key=lambda x: (-sum(x[2].values()), x[1], x[0]))
    for rid, rb, w in overdue:
        top = ', '.join(f'{k}({c})' for k, c in sorted(w.items(), key=lambda kv: -kv[1])[:4])
        detail.append(f"재확인 기한 지남 · {rid}(기한 {rb}) · 인용 {sum(w.values())}곳: {top}")
    # (2) 쇼츠 원고의 시의성 날짜·사용 기한
    drafts = []
    rd = os.path.join(med, 'packages', 'drafts', 'README.md')
    if os.path.exists(rd):
        header = None
        rd_text = open(rd, encoding='utf-8').read()
        wm = re.search(r'작성 (\d{4}-\d{2}-\d{2})', rd_text)
        written = datetime.date.fromisoformat(wm.group(1)) if wm else datetime.date(1900, 1, 1)
        for line in rd_text.split('\n'):
            if not line.startswith('|') or re.fullmatch(r'[\s|:\-]+', line):
                continue
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if header is None or '시의성 날짜' in cells:
                header = cells
                continue
            if '시의성 날짜' not in header:
                continue
            row = dict(zip(header, cells))
            fn = re.search(r'`([^`]+\.md)`', row.get(header[0], ''))
            name = fn.group(1) if fn else row.get(header[0], '')[:30]
            texts = [row.get('시의성 날짜', ''), row.get('사용 기한(고칠 문장)', row.get('사용 기한', ''))]
            dates = []
            for txt in texts:
                for mm in re.finditer(r'\d{4}-\d{2}(?:-\d{2})?(?:\s*(?:초|중순|하순|말))?', txt):
                    d = loose_date(mm.group(0))
                    if d and d >= written:                     # 작성일 전 날짜('2017-03-01 이후 계약')는 기한이 아니다
                        dates.append((d, mm.group(0)))
            dates = list(dict.fromkeys(dates))                # 두 칸에 같은 날짜가 있으면 한 번만
            passed = [s for d, s in dates if d < day]
            if passed:
                allp = len(passed) == len(dates)
                drafts.append(name)
                detail.append(f"쇼츠 원고 날짜 지남 · {name} · 지난 날짜 {', '.join(passed)}"
                              + (' (적힌 날짜가 모두 지남)' if allp else f' (남은 날짜 {len(dates) - len(passed)}개)')
                              + f" · 고칠 곳: {row.get('사용 기한(고칠 문장)', '')[:80]}")
    # (3) CALENDAR.md 달별 절에서 날짜가 지났는데 '시행됨'이 아닌 항목
    cal_n = 0
    cp = os.path.join(med, 'CALENDAR.md')
    if os.path.exists(cp):
        sec, header = '', None
        for line in open(cp, encoding='utf-8').read().split('\n'):
            if line.startswith('## '):
                sec, header = line[3:].strip(), None
                continue
            if not re.match(r'\d{4}년 \d{1,2}(~\d{1,2})?월', sec) or not line.startswith('|'):
                continue
            if re.fullmatch(r'[\s|:\-]+', line):
                continue
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            if header is None:
                header = cells
                continue
            d = loose_date(cells[0])
            kind = cells[1] if len(cells) > 1 else ''
            if d and d < day and '시행됨' not in kind:
                cal_n += 1
                what = re.sub(r'\*\*', '', cells[2] if len(cells) > 2 else '')[:70]
                detail.append(f"CALENDAR 날짜 지남 · {sec} · {cells[0]}({kind}) · {what}")
    head = (f"기준일 {today}. 재확인 기한 지난 인용 기록 {len(overdue)}건, 날짜 지난 쇼츠 원고 {len(drafts)}편, "
            f"'시행됨'으로 안 옮긴 지난 CALENDAR 항목 {cal_n}개(경고만, 종료코드에 영향 없음)")
    return dict(name='기한 경고', bad=False, warn=bool(detail), head=head, detail=detail, sec=time.time() - t0)


# ---------------------------------------------------------------- 링크·MED 번호
LINK_FILES = ['packages/*.md', 'packages/drafts/*.md', 'guides/*.md', 'CALENDAR.md', 'README.md', 'CLAUDE.md',
              'numbers/README.md', 'numbers/BY_TOPIC.md', 'tools/README.md', 'reviews/*.md']
MED_FILES = ['packages/*.md', 'packages/drafts/*.md', 'guides/*.md', 'CALENDAR.md']
LINK = re.compile(r'(?<!!)\[[^\]\n]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')


def check_links(med):
    t0 = time.time()
    sys.path.insert(0, os.path.join(med, 'tools'))
    from factcheck import med_refs
    with open(os.path.join(med, 'index.csv'), encoding='utf-8') as f:
        status = {r['id']: r['status'] for r in csv.DictReader(f)}
    broken, missing, old = [], [], []
    files = sorted({p for pat in LINK_FILES for p in glob.glob(os.path.join(med, pat))})
    for path in files:
        rel = os.path.relpath(path, med).replace('\\', '/')
        text = open(path, encoding='utf-8').read()
        text_nocode = re.sub(r'`[^`\n]*`', '', text)                  # 코드 표기 안의 [..](..)는 링크가 아니다
        for k, line in enumerate(text_nocode.split('\n'), 1):
            for m in LINK.finditer(line):
                target = m.group(1)
                if re.match(r'(https?|mailto|ftp):', target) or target.startswith('#'):
                    continue
                file_part = target.split('#', 1)[0]
                if not file_part:
                    continue
                full = os.path.normpath(os.path.join(os.path.dirname(path), file_part.replace('%20', ' ')))
                if not os.path.exists(full):
                    broken.append(f"깨진 링크 · {rel}:{k} → {target}")
    for path in sorted({p for pat in MED_FILES for p in glob.glob(os.path.join(med, pat))}):
        rel = os.path.relpath(path, med).replace('\\', '/')
        for k, line in enumerate(open(path, encoding='utf-8').read().split('\n'), 1):
            for rid in sorted(med_refs(line)):
                st = status.get(rid)
                if st is None:
                    missing.append(f"없는 MED 번호 · {rel}:{k} → {rid}")
                elif st in ('superseded', 'rejected') and st not in line:   # 'superseded인 MED-… 대신'처럼 밝힌 줄은 뺀다
                    old.append(f"{st} 기록 인용(경고) · {rel}:{k} → {rid}")
    head = (f"링크 검사 파일 {len(files)}개: 깨진 링크 {len(broken)}, 없는 MED 번호 {len(missing)} / "
            f"superseded·rejected 인용 {len(old)}(경고)")
    return dict(name='링크·MED 번호', bad=bool(broken or missing), warn=bool(old), head=head,
                detail=broken + missing + old, sec=time.time() - t0)


# ---------------------------------------------------------------- 쇼츠 원고 길이·두 판 일치(경고만)
# 낭독 속도(18라운드 재계산): 기존 쇼츠 6편의 낭독 원고(narr.txt)를 소리 글자로 세고, 최종 렌더 길이(ffprobe)로 나눈 값.
#   렌더 길이는 문장별 음성 조각(assets/n*.wav) 길이 합과 거의 같아(앞뒤 여백 1초 안팎) 렌더 전체를 내레이션 구간으로 썼다.
#   kcs2 309자/50.97초=6.06, kcs3 278/45.80=6.07, kc1s 320/49.13=6.51, kc2s 275/42.60=6.46, car8 375/58.23=6.44, cxmt 356/55.33=6.43
#   합계 1,913자/302.07초 = 6.33자/초(편별 6.06~6.51, 표준편차 0.21, 합계 대비 -4.3%~+2.8%). 17라운드 값 6.06은 kcs2·kcs3 두 편만 쓴 값.
#   뺀 것: byeonaek 3편·shorts-test(18~27초·10초 시험 영상), mid(최종 렌더가 다른 영상).
# 원고의 숫자·영문·기호는 소리 나는 대로 바꿔 센다(spoken_text, 시험: scripts/test_spoken.py). 문장부호·공백은 세지 않는다.
CHARS_PER_SEC = 1913 / 302.07
TARGET_SEC = (45, 55)
_DIG = '영일이삼사오육칠팔구'
_LETTER = {'A': '에이', 'B': '비', 'C': '씨', 'D': '디', 'E': '이', 'F': '에프', 'G': '지', 'H': '에이치', 'I': '아이', 'J': '제이',
           'K': '케이', 'L': '엘', 'M': '엠', 'N': '엔', 'O': '오', 'P': '피', 'Q': '큐', 'R': '알', 'S': '에스', 'T': '티',
           'U': '유', 'V': '브이', 'W': '더블유', 'X': '엑스', 'Y': '와이', 'Z': '지'}


def _read_int(n):
    """정수를 한자어 수 읽기로(15000000 → 천오백만, 10000 → 만)."""
    if n == 0:
        return '영'
    out, big, k = '', ['', '만', '억', '조', '경'], 0
    while n:
        n, part = divmod(n, 10000)
        if part:
            s = ''
            for i, u in enumerate(['천', '백', '십', '']):
                d = part // 10 ** (3 - i) % 10
                if d:
                    s += ('' if d == 1 and u else _DIG[d]) + u
            if k == 1 and s == '일':
                s = ''
            out = s + big[k] + out
        k += 1
    return out


def spoken_text(text):
    """낭독할 글을 소리 나는 한글로 바꾼다(18라운드: 날짜·분수 추가).
    2026-07-01 → 이천이십육년칠월일일, 1/2 → 이분의일, 1,500만 → 천오백만, 72.8% → 칠십이점팔퍼센트,
    10~30% → 십에서삼십퍼센트, KCD → 케이씨디, 2005다63221 → 이천오다육만삼천이백이십일(숫자로 읽음)."""
    t = re.sub(r'(\d{4})-(\d{1,2})-(\d{1,2})', lambda m: f"{m.group(1)}년 {int(m.group(2))}월 {int(m.group(3))}일", text)
    t = re.sub(r'(\d{4})-(\d{1,2})(?!\d)', lambda m: f"{m.group(1)}년 {int(m.group(2))}월", t)
    t = re.sub(r'(\d+)/(\d+)', lambda m: f"{m.group(2)}분의 {m.group(1)}", t)
    t = t.replace('%p', '퍼센트포인트').replace('%', '퍼센트').replace('~', '에서')

    def num(m):
        ip, _, dp = m.group(0).replace(',', '').partition('.')
        return _read_int(int(ip)) + ('점' + ''.join(_DIG[int(c)] for c in dp) if dp else '')
    t = re.sub(r'\d[\d,]*(?:\.\d+)?', num, t)
    t = re.sub(r'[A-Za-z]', lambda m: _LETTER[m.group(0).upper()], t)
    return t


def spoken_chars(text):
    """낭독할 글의 소리 글자 수(소리 나는 한글로 바꾼 뒤 한글만 센다. 문장부호·공백은 세지 않는다)."""
    return len(re.findall(r'[가-힣]', spoken_text(text)))


def _section(text, name):
    m = re.search(r'^## ' + name + r'\s*\n(.*?)(?=^## |\Z)', text, re.S | re.M)
    return m.group(1) if m else ''


def _norm(s):
    s = re.sub(r'^\s*\d+\.\s*', '', s)                                 # 번호
    s = re.sub(r'\s*\((?:MED-[^()]*)\)', '', s)                         # (MED-…), (MED-…, MED-…)
    return re.sub(r'\s+', ' ', s).strip()


def check_drafts(med):
    t0 = time.time()
    rows, detail = [], []
    for path in sorted(glob.glob(os.path.join(med, 'packages', 'drafts', '*.md'))):
        name = os.path.basename(path)
        if name == 'README.md':
            continue
        text = open(path, encoding='utf-8').read()
        read = [l.strip() for l in _section(text, '낭독판').split('\n') if l.strip()]
        cite = [l.strip() for l in _section(text, '근거 표시판').split('\n') if re.match(r'\s*\d+\.', l)]
        if not read:
            detail.append(f"낭독판 없음 · {name}")
            continue
        n = sum(spoken_chars(l) for l in read)
        sec = n / CHARS_PER_SEC
        ok = TARGET_SEC[0] <= sec <= TARGET_SEC[1]
        rows.append((name, n, sec, ok))
        if not ok:
            detail.append(f"길이 벗어남 · {name} · 소리 글자 {n}자 · 예상 {sec:.1f}초(기준 {TARGET_SEC[0]}~{TARGET_SEC[1]}초)")
        a, b = [_norm(x) for x in cite], [_norm(x) for x in read]
        if a != b:
            diff = [f'{i + 1}번' for i in range(max(len(a), len(b)))
                    if (a[i] if i < len(a) else None) != (b[i] if i < len(b) else None)]
            detail.append(f"두 판 문장 다름 · {name} · 근거 표시판 {len(a)}문장·낭독판 {len(b)}문장, 다른 문장: {', '.join(diff[:8])}")
    table = ['| 원고 | 소리 글자 수 | 예상 길이 | 45~55초 |', '|---|---|---|---|']
    table += [f"| {n} | {c} | {s:.1f}초 | {'예' if ok else '벗어남'} |" for n, c, s, ok in rows]
    out = sum(1 for r in rows if not r[3])
    mism = sum(1 for d in detail if d.startswith('두 판'))
    head = (f"원고 {len(rows)}편: 45~55초 벗어남 {out}편, 두 판 문장 다름 {mism}편"
            f"(속도 {CHARS_PER_SEC:.2f}자/초, 경고만)")
    return dict(name='쇼츠 원고 길이', bad=False, warn=bool(detail), head=head, detail=detail, table=table,
                sec=time.time() - t0)


# ---------------------------------------------------------------- 숫자 카드 찾아보기(BY_TOPIC.md) 최신 여부(경고만)
def check_by_topic(med, today):
    code, out, sec = run([os.path.join('numbers', 'build_by_topic.py'), '--check', '--month', today[:7]], med)
    head = out.strip().splitlines()[0] if out.strip() else '결과를 읽지 못함'
    return dict(name='숫자 카드 찾아보기', bad=False, warn=code != 0, head=head + ('' if code == 0 else '(경고만, 종료코드에 영향 없음)'),
                detail=[] if code == 0 else [head], sec=sec)


def main():
    ap = argparse.ArgumentParser(description='커밋 전 검사 한 번에 돌리기')
    ap.add_argument('--dry', action='store_true', help='임시 사본에서 돌린다(index.csv 등을 고치지 않는다)')
    ap.add_argument('--out', help='요약과 factcheck 지적 전체를 저장할 마크다운 파일')
    ap.add_argument('--today', default=datetime.date.today().isoformat(), help='기한 판단 기준일 YYYY-MM-DD(기본: 오늘)')
    a = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    t0 = time.time()
    med, tmp = MED, None
    if a.dry:
        tmp = tempfile.mkdtemp(prefix='check_all_')
        med = os.path.join(tmp, 'medical')
        shutil.copytree(MED, med, ignore=shutil.ignore_patterns('__pycache__'))
    try:
        known = load_known(med)
        results = [check_build(med, a.today), check_verify(med, known), check_factcheck(med, known, a.today),
                   check_links(med), check_drafts(med), check_deadlines(med, a.today), check_by_topic(med, a.today)]
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    total = time.time() - t0
    L = [f"# 커밋 전 검사 결과{' (임시 사본, --dry)' if a.dry else ''}", '']
    for r in results:
        mark = '❌' if r['bad'] else '⚠️' if r.get('warn') else '✅'
        L.append(f"- {mark} **{r['name']}** ({r['sec']:.0f}초): {r['head']}")
    fc = results[2]
    L.append(f"- 전체 {total:.0f}초. factcheck 판정별 문장 수: {fc['total']}")
    L.append('')
    for r in results:
        if r.get('table'):
            L.append(f"## {r['name']} 표")
            L += r['table']
            L.append('')
        if r['detail']:
            L.append(f"## {r['name']}")
            L += [f"- {d}" for d in r['detail'][:60]]
            if len(r['detail']) > 60:
                L.append(f"- … 외 {len(r['detail']) - 60}줄")
            L.append('')
    bad = any(r['bad'] for r in results)
    L.append('결론: ' + ('새 문제가 있다(종료코드 1). 위 ❌ 항목을 고치거나, 원문과 맞다고 확인했으면 '
                         f'`medical/{KNOWN}`에 예외로 적는다.' if bad else '새 문제 없음(종료코드 0).'))
    text = '\n'.join(L)
    print(text)
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            f.write(text + '\n\n## factcheck 원래 출력(--only-problems)\n\n' + fc['raw'])
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
