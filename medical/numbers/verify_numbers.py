"""numbers.csv 각 줄의 숫자가 출처 기록(sources/MED-NNNN_*.md) '핵심 근거' 절에 그대로 있는지 대조한다.
실행: python medical/numbers/verify_numbers.py
"""
import csv, glob, os, re, sys
sys.stdout.reconfigure(encoding="utf-8")
MED = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
idx = {x['id']: x for x in csv.DictReader(open(os.path.join(MED, 'index.csv'), encoding='utf-8'))}


def src_file(i):
    f = glob.glob(os.path.join(MED, 'sources', i + '_*.md'))
    return f[0] if f else None


def sec(t, name):
    m = re.search(r'## ' + name + r'\n(.*?)(?=\n## |\Z)', t, re.S)
    return m.group(1) if m else ''


rows = list(csv.DictReader(open(os.path.join(MED, 'numbers', 'numbers.csv'), encoding='utf-8')))
res = dict(total=len(rows), ok=0, notfound=[], badstatus=[], stale_meta=[], calc_unflagged=[],
           caution_num_missing=[], tax_no_gwisok=[], annual_no_year=[], in_ban=[], long=[], dup=[])

# 해마다 다시 정하는 숫자를 가려내는 기준(22라운드). 카드 한 줄 문장이나 출처 기록 제목의 낱말로 본다.
ANNUAL_SENT = re.compile(r'보험료율|점수당|최저보험료|상한액|월 한도액|중위소득|긴급복지|생계급여|월평균.{0,8}보험료|수가')
ANNUAL_TITLE = re.compile(r'보험료율|상한액|월 한도액|중위소득|긴급복지')        # 단위와 관계없이
ANNUAL_TITLE_WON = re.compile(r'요양급여비용』|수가')                            # 단위가 '원'일 때만(수가 책·수가 고시의 금액)
ANNUAL_TOPIC = re.compile(r'^(건강보험료|본인부담상한제)$')                # 카드 주제 자체가 해마다 바뀌는 금액인 것
NOT_ANNUAL = re.compile(r'^(금융분쟁조정위원회|대법원)|판결')                   # 분쟁조정·판례는 그 사건 시점 숫자라 뺀다


def annual(r, x):
    if NOT_ANNUAL.search(x['title']):
        return False
    return bool(ANNUAL_TOPIC.search(r['주제']) or ANNUAL_SENT.search(r['한 줄 문장']) or ANNUAL_TITLE.search(x['title'])
                or (r['단위'] == '원' and ANNUAL_TITLE_WON.search(x['title'])))
seen = {}
for r in rows:
    src, num = r['출처기록'], r['숫자']
    x = idx.get(src)
    # 0) 세법 기록(소득세법·시행령 조문, 국세청 자료)을 출처로 한 카드는 기준시점에 귀속 연도('2026년 귀속')를 적는다(20라운드)
    #    같은 공제율·한도라도 귀속 연도마다 법이 다를 수 있어서다
    if x and ('국세청' in x['org'] or '소득세법' in x['title']) and '귀속' not in r['기준시점']:
        res['tax_no_gwisok'].append((r['카드번호'], src, r['기준시점']))
    # 0-2) 해마다 다시 정하는 숫자(보험료율·점수당 금액·본인부담상한액·장기요양 월 한도액·기준 중위소득·긴급복지·수가 등)는
    #      기준시점에 적용 연도('2026년 적용', '2026년 진료분', '2026년 1월분'처럼 'YYYY년')를 적는다(22라운드)
    if x and annual(r, x) and not re.search(r'(19|20)\d\d년', r['기준시점']):
        res['annual_no_year'].append((r['카드번호'], src, r['기준시점']))
    # 1) 출처 기록이 verified이고 tier C가 아닌지
    if not x or x['status'] != 'verified' or x['tier'] == 'C':
        res['badstatus'].append(r['카드번호'])
    # 1-2) 재확인기한·출처기관이 지금 index.csv 값과 같은지(출처 기록 수정 뒤 낡은 값 확인)
    elif x['recheck_by'] != r['재확인기한'] or x['org'] != r['출처기관']:
        res['stale_meta'].append((r['카드번호'], src, r['재확인기한'], x['recheck_by']))
    t = open(src_file(src), encoding='utf-8').read()
    core, ban = sec(t, '핵심 근거'), sec(t, '미검증·사용 금지')
    # 2) 숫자가 '핵심 근거' 절에 앞뒤 숫자에 붙지 않은 채 그대로 있는지(예: '5'가 '15'에 걸리지 않게)
    pat = r'(?<![\d.,])' + re.escape(num) + r'(?![\d]|[.,]\d)'
    if not re.search(pat, core):
        res['notfound'].append((r['카드번호'], src, num))
        continue
    res['ok'] += 1
    # 3) 그 숫자가 나오는 문장이 모두 '직접 계산/합산'인데 주의 열에 표시가 없으면 경고
    sents = [s for s in re.split(r'(?<=[.)])\s+|\n', core) if re.search(pat, s)]
    if sents and all(re.search(r'직접 (계산|합산)', s) for s in sents) and '직접' not in r['주의']:
        res['calc_unflagged'].append((r['카드번호'], src, num))
    # 4) 주의 열의 숫자도 출처 기록 본문(또는 주의에 적은 다른 MED 기록)에 있는지
    body = t.split('---', 2)[2]
    pool = body + ''.join(open(src_file(o), encoding='utf-8').read()
                          for o in re.findall(r'MED-\d{4}', r['주의']) if src_file(o))
    for tok in re.findall(r'\d[\d,.]*', re.sub(r'MED-\d{4}(~\d{4})?', '', r['주의'])):
        tok = tok.rstrip('.,')
        if tok and tok not in pool:
            res['caution_num_missing'].append((r['카드번호'], src, tok))
    # 5) 같은 숫자가 '미검증·사용 금지' 절에도 나오면 사람이 한 번 더 본다(알림용)
    #    '(2026-10-17 해결)'처럼 해결된 확인 메모 줄은 금지 문구가 아니므로 보지 않는다(14라운드)
    ban_open = '\n'.join(l for l in ban.split('\n') if not re.search(r'\(\d{4}-\d{2}-\d{2} 해결\)', l))
    if len(num) >= 3 and num in ban_open:
        res['in_ban'].append((r['카드번호'], src, num))
    if len(r['한 줄 문장']) > 40:
        res['long'].append((r['카드번호'], len(r['한 줄 문장'])))
    k = (src, num, r['단위'])
    if k in seen:
        res['dup'].append((seen[k], r['카드번호'], src, num))
    seen[k] = r['카드번호']

for k, v in res.items():
    print(k, v if not isinstance(v, list) else (len(v), v))
