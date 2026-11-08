"""전국 축제·행사 수집기(한국관광공사 국문 관광정보 서비스 searchFestival2).

공공데이터포털 키(DATA_GO_KR_API_KEY, 청약 수집과 같은 키)로 오늘부터 앞으로 3주 사이에 열리는 축제를 받아
data/tour/festivals.csv 를 통째로 새로 쓴다(지난 축제는 남기지 않는다).
쓰는 곳: 매일 아침 카드의 월요일 「이번 주 축제 지도」(daily-card 저장소 NIGHT.md).
- 9시 수집(tick.py)에 붙어 매일 돌고, 일요일 밤 지도 만들 때 한 번 더 「지금 실행」으로 깨운다.
- 시작일이 오래전인 긴 행사도 잡히게 90일 전부터 받고, 기간이 [오늘, 오늘+21일]과 겹치는 것만 남긴다.
- 취소·연기(progresstype)도 그대로 적어 두고, 고르는 쪽에서 거른다.
"""
import csv, datetime, json, os, sys, time, urllib.error, urllib.parse, urllib.request

URL = "https://apis.data.go.kr/B551011/KorService2/searchFestival2"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "tour", "festivals.csv")
FIELDS = ["contentid", "title", "eventstartdate", "eventenddate", "progresstype", "festivaltype",
          "addr1", "addr2", "mapx", "mapy", "tel", "lDongRegnCd", "lDongSignguCd",
          "firstimage", "cpyrhtDivCd", "modifiedtime"]
KST = datetime.timezone(datetime.timedelta(hours=9))


def get(params):
    qs = urllib.parse.urlencode(params, safe=":[]")
    req = urllib.request.Request(URL + "?" + qs, headers={"User-Agent": "claude_drive festivals (python urllib)"})
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode("utf-8")
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                raise SystemExit(f"JSON이 아닌 응답(키 미등록·승인 대기일 수 있음): {body[:300]}")
        except urllib.error.URLError as e:
            if i == 2:
                raise SystemExit(f"축제 받기 실패: {e} / 응답 앞부분: {locals().get('body', '')[:200]}")
            time.sleep(5)


def main():
    key = os.environ.get("DATA_GO_KR_API_KEY", "")
    if not key:
        raise SystemExit("DATA_GO_KR_API_KEY 가 없습니다.")
    today = datetime.datetime.now(KST).date()
    lo, hi = today, today + datetime.timedelta(days=21)
    rows, page = {}, 1
    while True:
        res = get({"serviceKey": key, "MobileOS": "ETC", "MobileApp": "claude_drive", "_type": "json",
                   "numOfRows": 100, "pageNo": page, "arrange": "A",
                   "eventStartDate": (today - datetime.timedelta(days=90)).strftime("%Y%m%d")})
        head = res.get("response", {}).get("header", {})
        if head.get("resultCode") not in ("0000", "00"):
            raise SystemExit(f"API 오류: {json.dumps(res, ensure_ascii=False)[:400]}")
        body = res["response"]["body"]
        items = body.get("items") or {}
        items = items.get("item", []) if isinstance(items, dict) else []
        if isinstance(items, dict):
            items = [items]
        for it in items:
            s, e = it.get("eventstartdate", ""), it.get("eventenddate", "")
            try:
                sd = datetime.datetime.strptime(s, "%Y%m%d").date()
                ed = datetime.datetime.strptime(e, "%Y%m%d").date()
            except ValueError:
                continue
            if ed < lo or sd > hi:
                continue
            rows[it.get("contentid")] = {k: it.get(k, "") for k in FIELDS}
        total = int(body.get("totalCount") or 0)
        if page * 100 >= total or not items:
            break
        page += 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    out = sorted(rows.values(), key=lambda r: (r["eventstartdate"], r["title"]))
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS + ["fetched_at"])
        w.writeheader()
        stamp = datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
        for r in out:
            w.writerow({**r, "fetched_at": stamp})
    print(f"축제 {len(out)}건 저장({lo}~{hi} 기간과 겹치는 것, API 전체 {total}건)")


if __name__ == "__main__":
    main()
