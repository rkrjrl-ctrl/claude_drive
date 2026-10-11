# medical/ 작업 지침 (Claude Code)

이 폴더는 영상 제작용 의료 근거자료 저장소다. 작업 전에 반드시 [README.md](README.md)(규칙·형식)와 [BACKLOG.md](BACKLOG.md)(다음 작업)를 읽는다.

- 자료 1건 = `sources/MED-NNNN_*.md` 1파일. 다음 번호는 `index.csv`의 최대 번호 + 1.
- `index.csv`, `STATUS.md`는 자동 생성물이다. 직접 고치지 말고 `python3 medical/scripts/build_index.py --strict` 로 만든다. 오류 0이어야 커밋한다.
- 원문을 실제로 열어 읽은 것만 기록한다. 확인하지 못한 것은 `미검증` 섹션에 적고 추정으로 채우지 않는다. 첨부 다운로드가 1회 실패하면 멈추고 `partial` + BACKLOG로 넘긴다.
- 발행일·수정일·시행일·확인일을 섞지 않는다. 모르면 `unknown`.
- 개인정보(고객 증권·진단서·영수증)는 넣지 않는다.
