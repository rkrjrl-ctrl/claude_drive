#!/usr/bin/env python3
"""커밋 전 검사를 한 번에 돌린다: build_index --strict, verify_numbers, factcheck 일괄 점검.

사용법:  python -X utf8 medical/scripts/check_all.py [--dry] [--out 결과.md]
  --dry  : medical/ 를 임시 폴더에 복사해 그 안에서 돌린다(index.csv·STATUS.md·BY_DISEASE.md 를 고치지 않는다)
  --out  : 요약과 factcheck 지적 전체를 마크다운 파일로도 저장한다

종료코드: 새 문제가 있으면 1, 없으면 0.
  - build_index: 오류나 경고가 하나라도 있으면 문제
  - verify_numbers: notfound·badstatus·stale_meta·calc_unflagged·caution_num_missing·long 이 하나라도 있으면 문제.
    in_ban·dup 은 알려진 예외(reviews/factcheck_known.csv)에 없는 것만 문제
  - factcheck: '근거 없음'·'인용 번호 불일치' 숫자 중 알려진 예외에 없는 것만 문제
알려진 예외는 메인이 원문과 맞다고 확인한 것만 reviews/factcheck_known.csv 에 한 줄씩 적는다.
파이썬 표준 라이브러리만 쓴다.
"""
import argparse, csv, os, re, shutil, subprocess, sys, tempfile, time

MED = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # medical/
KNOWN = os.path.join('reviews', 'factcheck_known.csv')
FC_INPUTS = ['--dir', 'packages', '--dir', os.path.join('packages', 'drafts'), '--dir', 'guides', 'CALENDAR.md']
VERIFY_HARD = ['notfound', 'badstatus', 'stale_meta', 'calc_unflagged', 'caution_num_missing', 'long']
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
def check_build(med):
    code, out, sec = run([os.path.join('scripts', 'build_index.py'), '--strict'], med)
    m = re.search(r'자료 (\d+)건 / 오류 (\d+) / 경고 (\d+)', out)
    n, err, warn = (int(x) for x in m.groups()) if m else (0, -1, -1)
    lines = [l for l in out.splitlines() if l.startswith(('오류:', '경고:'))]
    bad = (m is None) or err > 0 or warn > 0 or code != 0
    head = f"자료 {n}건 / 오류 {err} / 경고 {warn}" if m else '결과를 읽지 못함(출력 확인)'
    return dict(name='build_index --strict', bad=bad, head=head, detail=lines if m else out.splitlines()[-20:], sec=sec)


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


def check_factcheck(med, known):
    code, out, sec = run([os.path.join('tools', 'factcheck.py')] + FC_INPUTS + ['--only-problems'], med)
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


def main():
    ap = argparse.ArgumentParser(description='커밋 전 검사 한 번에 돌리기')
    ap.add_argument('--dry', action='store_true', help='임시 사본에서 돌린다(index.csv 등을 고치지 않는다)')
    ap.add_argument('--out', help='요약과 factcheck 지적 전체를 저장할 마크다운 파일')
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
        results = [check_build(med), check_verify(med, known), check_factcheck(med, known)]
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    total = time.time() - t0
    L = [f"# 커밋 전 검사 결과{' (임시 사본, --dry)' if a.dry else ''}", '']
    for r in results:
        L.append(f"- {'❌' if r['bad'] else '✅'} **{r['name']}** ({r['sec']:.0f}초): {r['head']}")
    fc = results[2]
    L.append(f"- 전체 {total:.0f}초. factcheck 판정별 문장 수: {fc['total']}")
    L.append('')
    for r in results:
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
