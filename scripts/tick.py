"""매시간 한 번 깨어나 그 시각에 할 수집 작업을 실행한다(11/3 사용자: 깃허브 예약이 3~6시간 늦어 바깥 시계로 깨운다).

깨우는 쪽: cron-job.org(매시 5분, 정시) → 이 작업(tick.yml)의 「지금 실행」.
안전망: tick.yml 자체 깃허브 예약(늦게라도 돈다). 오늘 이미 성공한 작업은 다시 돌리지 않으니 두 번 깨어나도 괜찮다.
"""
import datetime
import json
import os
import subprocess

KST = datetime.timezone(datetime.timedelta(hours=9))
NOW = datetime.datetime.now(KST)
TODAY0 = NOW.replace(hour=0, minute=0, second=0, microsecond=0)

# (작업 파일, 이 시각(KST)부터 실행). 그 시각 신호가 빠져도 다음 신호에서 따라잡는다.
DAILY = [
    ("update-indicators.yml", 6),   # 경제 지표 + 사이트 배포. 11/4: 8시 → 6시(미국 장 마감 05~06시 뒤, 아침 카드가 06:40에 읽음)
    ("medical-watch.yml", 8),       # 의료 자료 감시
    ("collect-rates.yml", 9),       # 금감원 공시 금리
    ("collect-housing.yml", 9),     # 청약·LH·지원 제도
    ("collect-trades.yml", 9),      # 아파트 실거래가(오래 걸림)
    ("collect-festivals.yml", 9),   # 전국 축제(daily-card 월요일 지도, 11/8)
]
MAX_TRIES = 3          # 오늘 실패가 이만큼 쌓이면 더 돌리지 않는다(같은 오류를 매시간 되풀이하지 않게)
NEWS = "news-collect.yml"
NEWS_GAP_MIN = 40      # 뉴스는 매시간. 이 안에 이미 시작한 게 있으면 건너뛴다(신호가 몰릴 때)


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def runs(wf):
    out = gh("run", "list", "--workflow", wf, "--limit", "30", "--json", "status,conclusion,createdAt")
    rs = json.loads(out)
    for r in rs:
        r["t"] = datetime.datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")).astimezone(KST)
    return rs


def dispatch(wf, why):
    if os.environ.get("TICK_DRY"):   # 시험: 실행하지 않고 화면에만
        print(f"(시험) 실행할 것: {wf} ({why})")
        return
    gh("workflow", "run", wf, "--ref", "main")
    print(f"실행: {wf} ({why})")


def main():
    # 뉴스: 매시간
    rs = runs(NEWS)
    busy = [r for r in rs if r["status"] in ("queued", "in_progress", "waiting", "pending")]
    recent = [r for r in rs if (NOW - r["t"]).total_seconds() < NEWS_GAP_MIN * 60]
    if busy or recent:
        print(f"건너뜀: {NEWS} (도는 중 {len(busy)}, {NEWS_GAP_MIN}분 안 시작 {len(recent)})")
    else:
        dispatch(NEWS, "매시간")

    # 하루 한 번 작업: 시각이 지났고 오늘 성공·진행 중인 실행이 없으면
    for wf, hour in DAILY:
        if NOW.hour < hour:
            continue
        today = [r for r in runs(wf) if r["t"] >= TODAY0]
        if any(r["conclusion"] == "success" or r["status"] != "completed" for r in today):
            print(f"건너뜀: {wf} (오늘 이미 성공 또는 도는 중)")
            continue
        fails = sum(1 for r in today if r["status"] == "completed")
        if fails >= MAX_TRIES:
            print(f"건너뜀: {wf} (오늘 {fails}번 실패, 더 돌리지 않음)")
            continue
        dispatch(wf, f"{hour}시 작업" + (f", 다시 시도 {fails + 1}번째" if fails else ""))


if __name__ == "__main__":
    main()
