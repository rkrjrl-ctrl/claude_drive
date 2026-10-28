"""수집 결과를 data/STATUS.md 끝에 덧붙인다(fetch_indicators.py가 매 실행 처음에 STATUS.md를 새로 쓰고, 뒤 단계들이 덧붙임).
섹터·펀드 기준가 단계는 실패해도 워크플로가 계속 가도록 해 둬서, 여기 적어 두지 않으면 실패가 묻힌다."""
import os

STATUS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "STATUS.md")


def append(title, lines):
    with open(STATUS, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"\n## {title}\n\n" + "\n".join(lines) + "\n")
