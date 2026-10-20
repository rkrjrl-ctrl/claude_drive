#!/usr/bin/env python3
"""check_all.py의 날짜 읽기 시험(27라운드). 원고 README·CALENDAR의 날짜 칸에 '2026-2027절기'처럼
날짜가 아닌 연도 범위나 없는 달·날이 있어도 기한 검사가 멈추지 않고, 읽지 못한 날짜를 경고로 내는지 확인한다.

사용법:  python -X utf8 medical/scripts/test_dates.py   (틀린 것이 있으면 종료코드 1)
"""
import datetime, os, shutil, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check_all import loose_date, find_dates, check_deadlines

D = datetime.date
# (글, loose_date 기대값, find_dates가 찾을 날짜 원문 목록, 읽지 못한 원문 목록)
CASES = [
    ('2026-11-27', D(2026, 11, 27), ['2026-11-27'], []),
    ('2026-11', D(2026, 11, 30), ['2026-11'], []),
    ('2026-11 초', D(2026, 11, 10), ['2026-11 초'], []),
    ('2026-11 중순', D(2026, 11, 20), ['2026-11 중순'], []),
    ('2026-11 하순', D(2026, 11, 30), ['2026-11 하순'], []),
    ('2027-01 연말정산 이후', D(2027, 1, 31), ['2027-01'], []),
    ('2026년 4분기', D(2026, 12, 31), [], []),
    ('2026년 상반기', D(2026, 6, 30), [], []),
    ('2026-2027절기', None, [], []),                         # 연도 범위: 날짜가 아니다(26라운드에 멈췄던 표기)
    ('2026-2027절기(2026-09-21~2027-04-30)', None, ['2026-09-21', '2027-04-30'], []),
    ('2026-13', None, [], ['2026-13']),                      # 없는 달
    ('2026-02-30', None, [], ['2026-02-30']),                 # 없는 날
    ('2026-12-24 시행 · 2027-01-01 300회', D(2026, 12, 24), ['2026-12-24', '2027-01-01'], []),
    ('MED-2026-1234', None, [], []),                          # 앞이 '-'로 이어진 번호 조각은 날짜로 보지 않는다
    ('일정 없음(상시 소재)', None, [], []),
]

README = '''# 쇼츠 원고 초안

작성 2026-10-19.

| 제목(파일) | 시의성 날짜 | 예상 길이 | 사용 기한(고칠 문장) | 근거 MED | 팩트체크 |
|---|---|---|---|---|---|
| 독감 (`쇼츠_가.md`) | 2026-2027절기(국가예방접종 기간) | 50초 | 2027-04-30 뒤 절기가 바뀌면 고친다 | MED-0001 | - |
| 없는 달 (`쇼츠_나.md`) | 2026-13 | 50초 | - | MED-0001 | - |
| 지난 날짜 (`쇼츠_다.md`) | 2026-10-25 | 50초 | - | MED-0001 | - |
'''
CAL = '''# 달력

## 2026년 12월

| 날짜 | 구분 | 무엇이 바뀌나·나오나 | 시청자 돈과의 관계 | 근거 | 확인할 곳 | 영상 소재 |
|---|---|---|---|---|---|---|
| 2026-2027절기 | 예상 | 절기 항목 | - | MED-0001 | - | - |
| 2026-02-30 | 확정 | 없는 날 항목 | - | MED-0001 | - | - |
| 2026-11-01 | 확정 | 지난 항목 | - | MED-0001 | - | - |
'''


def integration():
    """작은 임시 저장소에서 check_deadlines를 끝까지 돌린다."""
    med = tempfile.mkdtemp(prefix='test_dates_')
    try:
        os.makedirs(os.path.join(med, 'tools'))
        os.makedirs(os.path.join(med, 'numbers'))
        os.makedirs(os.path.join(med, 'packages', 'drafts'))
        shutil.copy(os.path.join(os.path.dirname(HERE), 'tools', 'factcheck.py'), os.path.join(med, 'tools'))
        open(os.path.join(med, 'index.csv'), 'w', encoding='utf-8').write('id,status,recheck_by\n')
        open(os.path.join(med, 'numbers', 'numbers.csv'), 'w', encoding='utf-8').write('카드번호,출처기록\n')
        open(os.path.join(med, 'packages', 'drafts', 'README.md'), 'w', encoding='utf-8').write(README)
        open(os.path.join(med, 'CALENDAR.md'), 'w', encoding='utf-8').write(CAL)
        res = check_deadlines(med, '2026-11-15')
        det = res['detail']
        checks = [
            ('멈추지 않고 결과를 냄', isinstance(res, dict)),
            ('절기 원고는 2027-04-30 기한만 읽어 지남 아님', not any('쇼츠_가' in x for x in det)),
            ('없는 달 2026-13은 읽기 실패 경고', any('날짜 읽기 실패' in x and '쇼츠_나' in x and '2026-13' in x for x in det)),
            ('지난 날짜 원고는 지남 경고', any('날짜 지남' in x and '쇼츠_다' in x for x in det)),
            ('CALENDAR 절기 줄은 경고 없음', not any('2026-2027절기' in x for x in det)),
            ('CALENDAR 없는 날은 읽기 실패 경고', any('CALENDAR 날짜 읽기 실패' in x and '2026-02-30' in x for x in det)),
            ('CALENDAR 지난 확정 줄은 지남 경고', any('CALENDAR 날짜 지남' in x and '2026-11-01' in x for x in det)),
            ('머리말에 읽지 못한 날짜 수', '읽지 못한 날짜 2곳' in res['head']),
        ]
        return checks
    finally:
        shutil.rmtree(med, ignore_errors=True)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    bad = 0
    for s, want, want_found, want_bad in CASES:
        got = loose_date(s)
        found, unread = find_dates(s)
        ok = got == want and [r for _, r in found] == want_found and unread == want_bad
        bad += not ok
        print(('OK ' if ok else 'NG ') + f"{s!r} → {got}, 찾음 {[r for _, r in found]}, 못 읽음 {unread}"
              + ('' if ok else f' (기대 {want}, {want_found}, {want_bad})'))
    checks = integration()
    for name, ok in checks:
        bad += not ok
        print(('OK ' if ok else 'NG ') + '기한 검사: ' + name)
    print(f'{len(CASES) + len(checks)}개 중 틀림 {bad}')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
