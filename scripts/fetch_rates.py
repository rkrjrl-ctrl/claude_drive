"""금감원 「금융상품 한눈에」 금리 수집기.

예금·적금·주택담보대출·전세자금대출·개인신용대출 공시 금리를 받아
data/rates/<상품>/latest.csv 로 저장하고, 지난번과 달라졌을 때만
data/rates/<상품>/<날짜>.csv 기록을 남긴다.

키는 환경 변수 FSS_API_KEY 로 받는다 (깃허브엔 Secrets 로 넣는다).
"""

import csv
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://finlife.fss.or.kr/finlifeapi/"
KST = timezone(timedelta(hours=9))
DATA = Path(__file__).resolve().parent.parent / "data" / "rates"

PRODUCTS = {
    "deposit": ("depositProductsSearch.json", "정기예금"),
    "saving": ("savingProductsSearch.json", "적금"),
    "mortgage": ("mortgageLoanProductsSearch.json", "주택담보대출"),
    "rent": ("rentHouseLoanProductsSearch.json", "전세자금대출"),
    "credit": ("creditLoanProductsSearch.json", "개인신용대출"),
}

# 권역: 은행, 여신전문, 저축은행, 보험, 금융투자
GROUPS = {
    "020000": "은행",
    "030200": "여신전문",
    "030300": "저축은행",
    "050000": "보험",
    "060000": "금융투자",
}

# 상품을 구분하는 열 (정렬·비교 기준)
KEY_COLS = ["fin_grp", "kor_co_nm", "fin_prdt_nm", "fin_co_no", "fin_prdt_cd"]


def fetch(key, path, grp, page):
    qs = urllib.parse.urlencode({"auth": key, "topFinGrpNo": grp, "pageNo": page})
    req = urllib.request.Request(BASE + path + "?" + qs,
                                 headers={"User-Agent": "rate-tracker (python urllib)"})
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read().decode("utf-8"))
            res = body["result"]
            if res.get("err_cd") != "000":
                raise RuntimeError(f"API 오류 {res.get('err_cd')}: {res.get('err_msg')}")
            return res
        except RuntimeError:
            raise
        except Exception as e:  # 네트워크 오류는 두 번 더 시도
            last_err = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"요청 실패 ({path}, {grp}, {page}쪽): {last_err}")


def collect_product(key, path):
    rows = []
    for grp, grp_nm in GROUPS.items():
        page, max_page = 1, 1
        bases, options = [], []
        while page <= max_page:
            res = fetch(key, path, grp, page)
            max_page = int(res.get("max_page_no") or 1)
            bases += res.get("baseList") or []
            options += res.get("optionList") or []
            page += 1
        # 상품 기본 정보 + 금리 옵션을 한 줄로 합친다
        by_prdt = {(b["fin_co_no"], b["fin_prdt_cd"]): b for b in bases}
        for o in options:
            b = by_prdt.get((o.get("fin_co_no"), o.get("fin_prdt_cd")), {})
            row = {"fin_grp": grp_nm, **b, **o}
            rows.append(row)
    return rows


def to_csv(rows):
    if not rows:
        return ""
    cols = list(KEY_COLS)
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    rows = sorted(rows, key=lambda r: tuple(str(r.get(c, "")) for c in cols))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in cols})
    return buf.getvalue()


def main():
    key = os.environ.get("FSS_API_KEY", "").strip()
    if not key:
        sys.exit("FSS_API_KEY 환경 변수가 없습니다.")
    today = datetime.now(KST).strftime("%Y-%m-%d")
    changed, failed = [], []
    for name, (path, label) in PRODUCTS.items():
        try:
            text = to_csv(collect_product(key, path))
        except Exception as e:
            print(f"[실패] {label}: {e}")
            failed.append(label)
            continue
        if not text:
            print(f"[비었음] {label}: 받은 상품이 없습니다.")
            failed.append(label)
            continue
        folder = DATA / name
        folder.mkdir(parents=True, exist_ok=True)
        latest = folder / "latest.csv"
        old = latest.read_text(encoding="utf-8-sig") if latest.exists() else None
        n = text.count("\n") - 1
        if old == text:
            print(f"[그대로] {label}: {n}줄")
            continue
        # 엑셀에서 한글이 안 깨지도록 BOM 포함 저장
        latest.write_text(text, encoding="utf-8-sig")
        (folder / f"{today}.csv").write_text(text, encoding="utf-8-sig")
        changed.append(label)
        print(f"[바뀜] {label}: {n}줄 저장")
    print(f"\n{today} 바뀐 상품: {', '.join(changed) or '없음'}")
    if failed:
        sys.exit(f"실패한 상품: {', '.join(failed)}")


if __name__ == "__main__":
    main()
