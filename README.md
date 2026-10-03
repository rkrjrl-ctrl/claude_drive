# 경제 지표 자동 수집

한국/미국 경제 지표 30종을 매일 자동으로 수집해 `data/history.csv`에 누적합니다.
GitHub Actions 스케줄러(cron)로 실행되므로 **컴퓨터가 꺼져 있어도** 매일 갱신됩니다.

## 수집 지표 (30종)

| 필드 | 설명 | 출처 |
|---|---|---|
| usdkrw | 원/달러 환율 | Yahoo Finance (KRW=X) — FRED(DEXKOUS)는 공개 지연이 수일씩 발생해 제외 |
| kr_rate | 한국 기준금리 | 한국은행 ECOS (722Y001) |
| us_rate | 미국 기준금리(상단/하단 중간값) | FRED (DFEDTARU/DFEDTARL) |
| kr_2y / kr_10y | 한국 국고채 2년/10년 금리 | 한국은행 ECOS (817Y002) |
| us_2y | 미국 국채 2년 금리 | FRED (DGS2) — Yahoo에 2년물 전용 실시간 티커가 없어 유지 |
| us_10y | 미국 국채 10년 금리 | Yahoo Finance (^TNX) — 실시간 |
| kospi / kosdaq | 코스피/코스닥 지수 | Yahoo Finance |
| sp500 / dow / nasdaq | S&P500/다우/나스닥 | Yahoo Finance (^GSPC/^DJI/^IXIC) — 실시간 |
| kr_cpi / us_cpi | 소비자물가 상승률(전년동월비) | 한국은행 ECOS(901Y009) / FRED(CPIAUCSL)에서 계산 |
| wti | WTI 원유 | Yahoo Finance 선물(CL=F) — 실시간. FRED(DCOILWTICO)는 공식 EIA 벤치마크지만 1~2일 지연 |
| gold | 국제 금값 | Yahoo Finance (GC=F) |
| kr_m2 / us_m2 | 통화량 M2 | 한국은행 ECOS(161Y006) / FRED(M2SL) |
| vix | VIX 변동성지수 | Yahoo Finance (^VIX) — 실시간. 시장 위험회피 심리 지표 |
| dxy | 달러인덱스 | Yahoo Finance (DX-Y.NYB) — 실시간. 달러 전반의 강약 지표 |
| copper | 구리 선물 | Yahoo Finance (HG=F) — 실시간. 글로벌 경기 선행지표("닥터 코퍼") |
| soybean / corn / wheat | 대두·옥수수·밀 선물 (¢/부셸) | Yahoo Finance (ZS=F / ZC=F / ZW=F) — 일별, 근월물 연속 시세(만기 교체 시 가격 단차 있을 수 있음). 장기: 2000~ |
| us_unemployment | 미국 실업률 | FRED (UNRATE) — 월간. Fed 정책 판단 핵심 지표 |
| us_hy_spread | 미국 하이일드 신용스프레드 | FRED (BAMLH0A0HYM2) — 일별. 신용시장 위험선호도 지표 |
| us_yield_spread | 미국 10Y-2Y 금리 스프레드 | 로컬 계산(us_10y - us_2y), 별도 수집 없음. 경기침체 선행지표로 유명 |
| kr_apt_price_national | 전국 아파트 매매가격지수 | 한국부동산원 R-ONE Open API (통계표 T244183132827305, CLS_ID 50001) — 주간. 특정 시점=100 기준 지수 |
| kr_apt_price_seoul | 서울 아파트 매매가격지수 | 한국부동산원 R-ONE Open API (통계표 T244183132827305, CLS_ID 50008) — 주간 |
| kr_apt_avgprice_national / _seoul | 전국/서울 아파트 평균 매매가격 (만원/㎡) | 한국부동산원 R-ONE Open API (통계표 A_2024_00188, CLS_ID 500001/500004) — 월간, 지수가 아닌 실제 가격 |
| kr_apt_medprice_national / _seoul | 전국/서울 아파트 중위 매매가격 (만원/㎡) | 한국부동산원 R-ONE Open API (통계표 A_2024_00189, CLS_ID 500001/500004) — 월간, 지수가 아닌 실제 가격 |

## 설정 (최초 1회)

1. https://ecos.bok.or.kr/api/#/ 에서 무료 회원가입 후 OpenAPI 인증키를 발급받습니다.
2. https://www.reb.or.kr/r-one/portal/openapi/openApiActKeyPage.do 에서 네이버/구글/카카오 로그인 후
   "인증키발급" 메뉴에서 한국부동산원 R-ONE Open API 인증키를 발급받습니다.
3. 이 저장소의 **Settings → Secrets and variables → Actions → New repository secret**에서
   이름 `BOK_ECOS_API_KEY` / `REB_API_KEY`, 값에 각각 발급받은 키를 등록합니다.
4. 키를 등록하기 전에도 워크플로는 정상 실행되며, 해당 키가 필요한 지표만
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

`scripts/dashboard_template.html`은 기존 Claude Artifact/구글드라이브 대시보드와 **완전히 동일한 UI/기능**
(일·월 토글, 1·3·5·10년/전체 기간, 마우스 휠 확대·축소, 드래그 패닝, 카드 클릭 시 크게 보기, M2 듀얼축 등)을
그대로 가진 앱 셸입니다. `scripts/build_dashboard.js`가 여기에 `data/long_term/*.csv` + `data/history.csv`를
구워 넣어 `site/index.html`을 완전한 정적 파일로 만들고, 매 실행마다 GitHub Pages로 자동 배포합니다.
라이브 DB 연동 코드는 빌드 시 제거되고, 그 자리에 생성 시각이 적힌 안내 문구로 대체됩니다.

Pages URL: **Settings → Pages**에서 확인하거나, 저장소의 About 섹션에 표시됩니다.

## 동작 방식

- 매일 08:00 KST(UTC 23:00)에 자동 실행되고, 값이 바뀌면 `github-actions[bot]` 이름으로 자동 커밋·푸시합니다.
- 특정 필드 수집에 실패하면 전체를 실패시키지 않고 직전 값을 유지하면서 `data/STATUS.md`에 경고를 남깁니다
  (한 지표가 조용히 며칠씩 멈추는 문제를 방지하기 위함).
- 5개 미만의 필드만 성공하면(광범위한 네트워크 장애로 추정) 워크플로 자체를 실패 처리해 Actions 탭에서 바로 보이게 합니다.
