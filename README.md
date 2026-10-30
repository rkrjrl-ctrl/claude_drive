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
| silver | 은 선물 ($/oz) | Yahoo Finance (SI=F) — 일별, 근월물 연속 시세. 장기: 2000~ |
| shanghai / hsi / nikkei / dax / ftse / cac / stoxx50 | 상해종합·홍콩 항셍·닛케이 225·DAX·FTSE 100·CAC 40·유로스톡스 50 | Yahoo Finance (000001.SS / ^HSI / ^N225 / ^GDAXI / ^FTSE / ^FCHI / ^STOXX50E) — 일별. 장기: 닛케이 1965~, FTSE 1984~, 항셍 1986~, DAX 1987~ 등 |
| sensex / nifty | 인도 센섹스·니프티 50 | Yahoo Finance (^BSESN / ^NSEI) — 일별. 장기: 센섹스 1997~, 니프티 2007~ |
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

## 날짜 기준 (2026-10 수정)

- 일별 시장 지표(환율·지수·금리·원자재)는 '최신 가격'이 아니라 **실제 거래일이 붙은 일별 종가**로 받습니다(Yahoo 일봉, ECOS 일별, FRED 일별).
- `data/history.csv`의 각 행은 그 날짜의 종가(그 시장이 쉰 날은 직전 종가)를 담습니다. 예전에는 수집한 날짜로 찍혀서, 수집 시각에 따라 전날 값이 들어갔습니다. 오늘 행은 다음 실행 때 확정됩니다.
- `data/long_term/*.csv`도 매일 실제 거래일 기준으로 이어 붙입니다(최근 약 3개월 구간은 매번 다시 씀).

## 은행별 예적금·대출 금리 (2026-10 추가)

금감원 「금융상품 한눈에」 공시 금리를 매일 09:00 KST에 받는다 (`scripts/fetch_rates.py`, 워크플로 `Collect FSS rates`).

| 폴더 | 내용 |
|---|---|
| `data/rates/deposit` | 정기예금 |
| `data/rates/saving` | 적금 |
| `data/rates/mortgage` | 주택담보대출 |
| `data/rates/rent` | 전세자금대출 |
| `data/rates/credit` | 개인신용대출 (신용점수 구간별) |

- `latest.csv`는 가장 최근 값, `YYYY-MM-DD.csv`는 그날 값이 바뀌었을 때만 남는 기록이다. 권역(은행·저축은행·보험 등)은 `fin_grp` 열.
- API가 지금 값만 주므로 지난 금리는 다시 받을 수 없다. 이 기록이 유일한 이력이다.
- 공시 기준 금리라 개인이 실제 받는 금리와 다르고, 금감원은 대략 월 단위로 공시를 바꾼다.
- 키: https://finlife.fss.or.kr 오픈 API에서 발급받아 저장소 Secret `FSS_API_KEY`로 등록한다. 키가 없으면 워크플로는 건너뛰고 성공으로 끝난다.
- 로컬 실행: `FSS_API_KEY=발급받은키 python scripts/fetch_rates.py`

## 청약·LH 공고·지원 제도 (2026-10 추가)

공공데이터포털 키 하나(Secret `DATA_GO_KR_API_KEY`)로 매일 09:20 KST에 받는다 (`scripts/fetch_housing.py`, 워크플로 `Collect housing notices`).

| 파일 (`data/housing/`) | 내용 | 출처 |
|---|---|---|
| `applyhome_apt.csv` | APT 분양 공고 (접수일·발표일·주소) | 한국부동산원 청약홈 분양정보 |
| `applyhome_remndr.csv` | APT 무순위·잔여세대 | 〃 |
| `applyhome_urbty.csv` | 오피스텔·도시형·민간임대 | 〃 |
| `applyhome_pblpvt.csv` | 공공지원 민간임대 | 〃 |
| `applyhome_opt.csv` | 임의공급 | 〃 |
| `applyhome_apt_models.csv` | APT 주택형별 공급 세대·분양가 | 〃 |
| `applyhome_apt_competition.csv` | APT 순위·지역별 경쟁률 | 청약홈 경쟁률 |
| `applyhome_apt_score.csv` | APT 당첨 가점 (최저·최고·평균) | 〃 |
| `lh_notices.csv` | LH 분양·임대 공고 (행복·국민·매입·전세임대 등) | 한국토지주택공사 분양임대공고문 |
| `gov24_services.csv` | 정부·지자체 지원 제도 전체 (청년·주거 포함) | 행정안전부 공공서비스(혜택) 정보 |
| `myhome_rent.csv` / `myhome_sale.csv` | 공공임대·공공분양 모집공고 (보증금·월세·모집 기간) | 국토부 마이홈포털 (LH·지방공사, **SH는 없음**) |
| `seoul_youth_housing.csv` | 서울 청년안심주택 모집공고 (공공·민간, 최초·추가) | soco.seoul.go.kr 목록 데이터 (키 없음) |

- 공고는 공고 번호별로 한 줄. `first_seen`은 처음 받은 날, `last_changed`는 내용이 마지막으로 바뀐 날이다. 정정 공고나 경쟁률·가점 발표를 따라잡으려고 최근 60일치는 매일 다시 받는다.
- 처음 만들 때 2026-01-01부터 거슬러 받았다 (`HOUSING_BACKFILL_FROM`으로 바꿀 수 있음).
- Actions에서 손으로 돌릴 때 `only`에 `applyhome,lh,gov24` 중 일부만 적으면 그것만 받는다.
- SH 임대 공고(행복주택·장기전세·국민임대)는 어느 API에도 없고, SH 사이트(i-sh.co.kr)는 프로그램 접속을 막아 두어 자동 수집하지 않는다. SH 분양 공고는 청약홈 수집에 들어온다.

## 아파트 실거래가 (2026-10 추가)

국토부 실거래가 API로 전국 시군구 아파트 매매·전월세를 매일 09:40 KST에 받는다 (`scripts/fetch_trades.py`, 워크플로 `Collect apartment trades`, 키는 `DATA_GO_KR_API_KEY`).

- `data/trades/apt_trade/YYYY-MM.csv`, `data/trades/apt_rent/YYYY-MM.csv`: 거래(계약) 월별 전국 파일. 금액 단위는 만원. `지역` 열은 시군구 이름.
- 신고 기한이 계약 후 30일이라 최근 3개월은 매일 통째로 다시 받는다. 처음엔 2026-01부터 거슬러 받았다 (`TRADES_BACKFILL_FROM`).
- 시군구 코드는 행정안전부 법정동코드 API에서 받아 `data/ref/lawd_sgg.csv`에 두고 30일마다 새로 받는다. 구가 있는 시는 구 단위로 받는다.
- 한 달치에서 실패한 시군구가 있으면 그 달 파일은 덮어쓰지 않고 그대로 둔다.

## 의료 근거자료

영상 제작용 의료·치료비 근거자료는 [`medical/`](medical/README.md)에서 관리한다. (경제 지표 수집과는 별개)
