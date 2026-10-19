#!/usr/bin/env python3
"""check_all.py의 '소리 글자' 변환 시험(18라운드). 쇼츠 원고 예상 길이 계산이 숫자·기호를 소리 나는 대로 세는지 확인한다.

사용법:  python -X utf8 medical/scripts/test_spoken.py   (틀린 것이 있으면 종료코드 1)
"""
import os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_all import spoken_text

CASES = [
    ('1,500만 원', '천오백만원'),
    ('2005다63221', '이천오다육만삼천이백이십일'),          # 판례 번호는 숫자로 읽는다고 본다
    ('72.8%', '칠십이점팔퍼센트'),
    ('10~30%', '십에서삼십퍼센트'),
    ('3,606,521명', '삼백육십만육천오백이십일명'),
    ('0.8%', '영점팔퍼센트'),
    ('1조 1,685억 원', '일조천육백팔십오억원'),
    ('10000명', '만명'),
    ('100만', '백만'),
    ('KCD 9차', '케이씨디구차'),
    ('MRI', '엠알아이'),
    ('2026-07-01부터', '이천이십육년칠월일일부터'),
    ('2026-11', '이천이십육년십일월'),
    ('1/2', '이분의일'),
    ('2분의 1', '이분의일'),
    ('1·2세대', '일이세대'),
    ('4만 3,850원', '사만삼천팔백오십원'),
    ('110세', '백십세'),
    ('11일', '십일일'),
    ('1%p', '일퍼센트포인트'),
]


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    bad = 0
    for src, want in CASES:
        got = ''.join(re.findall(r'[가-힣]', spoken_text(src)))
        ok = got == want
        bad += not ok
        print(('OK ' if ok else 'NG ') + f'{src} → {got}' + ('' if ok else f' (기대 {want})'))
    print(f'{len(CASES)}개 중 틀림 {bad}')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
