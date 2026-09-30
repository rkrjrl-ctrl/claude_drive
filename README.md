# 경제 지표 자동 수집

한국/미국 경제 지표 18종을 매일 자동으로 수집해 `data/history.csv`에 누적합니다.
GitHub Actions 스케줄러(cron)로 실행되므로 **컴퓨터가 꺼져 있어도** 매일 갱신됩니다.

## 수집 지표 (18종)

| 필드 | 설명 | 출처 |
|---|---|---|
| usdkrw | 원/달러 환율 | FRED (DEXKOUS) |
| kr_rate | 한국 기준금리 | 한국은행 ECOS (722Y001) |
| us_rate | 미국 기준금리(상단/하단 중간값) | FRED (DFEDTARU/DFEDTARL) |
| kr_2y / kr_10y | 한국 국고채 2년/10년 금리 | 한국은행 ECOS (817Y002) |
| us_2y / us_10y | 미국 국채 2년/10년 금리 | FRED (DGS2/DGS10) |
| kospi / kosdaq | 코스피/코스닥 지수 | Yahoo Finance |
| sp500 / dow / nasdaq | S&P500/다우/나스닥 | FRED (SP500/DJIA/NASDAQCOM) |
| kr_cpi / us_cpi | 소비자물가 상승률(전년동월비) | 한국은행 ECOS(901Y009) / FRED(CPIAUCSL)에서 계산 |
| wti | WTI 원유 | FRED (DCOILWTICO) |
| gold | 국제 금값 | Yahoo Finance (GC=F) |
| kr_m2 / us_m2 | 통화량 M2 | 한국은행 ECOS(161Y006) / FRED(M2SL) |

## 설정 (최초 1회)

1. https://ecos.bok.or.kr/api/#/ 에서 무료 회원가입 후 OpenAPI 인증키를 발급받습니다.
2. 이 저장소의 **Settings → Secrets and variables → Actions → New repository secret**에서
   이름 `BOK_ECOS_API_KEY`, 값에 발급받은 키를 등록합니다.
3. 키를 등록하기 전에도 워크플로는 정상 실행되며, 한국 지표(kr_rate/kr_2y/kr_10y/kr_cpi/kr_m2) 5개만
   비어있거나 이전 값이 유지됩니다(`data/STATUS.md`에서 상태 확인 가능).

## 수동 실행

저장소의 **Actions → Update economic indicators → Run workflow**에서 즉시 실행할 수 있습니다.

## 로컬 실행

```bash
BOK_ECOS_API_KEY=발급받은키 python scripts/fetch_indicators.py
```

## 결과 파일

- `data/history.csv` — 2026-08-21부터의 날짜별 일간 이력 (누적, 같은 날짜 재실행 시 덮어씀)
- `data/long_term/<지표>.csv` — 지표별 장기 이력(최대 1948년~2026-09-22, 기존 대시보드에서 이전).
  `date,값` 2열 구조이며 이후 매일 `data/history.csv`가 이어서 갱신됩니다.
- `data/latest.json` — 가장 최근 값 하나
- `data/STATUS.md` — 최근 실행에서 어떤 필드가 정상 갱신됐는지/실패해서 이전 값을 유지했는지 기록

## 대시보드 (GitHub Pages)

`scripts/build_dashboard.py`가 `data/long_term/*.csv` + `data/history.csv`를 합쳐 `site/index.html`을
정적으로 생성하고, 매 실행마다 GitHub Pages로 자동 배포합니다. 별도 서버나 클라이언트 JS 데이터 로딩 없이
빌드 시점에 SVG로 굽는 방식이라 그대로 열립니다.

Pages URL: **Settings → Pages**에서 확인하거나, 저장소의 About 섹션에 표시됩니다.

## 동작 방식

- 매일 08:00 KST(UTC 23:00)에 자동 실행되고, 값이 바뀌면 `github-actions[bot]` 이름으로 자동 커밋·푸시합니다.
- 특정 필드 수집에 실패하면 전체를 실패시키지 않고 직전 값을 유지하면서 `data/STATUS.md`에 경고를 남깁니다
  (한 지표가 조용히 며칠씩 멈추는 문제를 방지하기 위함).
- 5개 미만의 필드만 성공하면(광범위한 네트워크 장애로 추정) 워크플로 자체를 실패 처리해 Actions 탭에서 바로 보이게 합니다.
