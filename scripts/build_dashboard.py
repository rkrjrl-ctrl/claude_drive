#!/usr/bin/env python3
"""Build a static site/index.html dashboard from data/history.csv.

No JS charting library, no client-side fetch: each metric's line chart is a
plain SVG polyline computed at build time and embedded directly, so the page
works standalone on GitHub Pages with zero client-side data loading.
"""
import csv
import html
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HISTORY_CSV = os.path.join(ROOT, "data", "history.csv")
LONG_TERM_DIR = os.path.join(ROOT, "data", "long_term")
OUT_DIR = os.path.join(ROOT, "site")
OUT_FILE = os.path.join(OUT_DIR, "index.html")
MAX_CHART_POINTS = 400

METRICS = [
    ("usdkrw", "원/달러 환율", ""),
    ("kospi", "코스피", "pt"),
    ("kosdaq", "코스닥", "pt"),
    ("sp500", "S&P 500", "pt"),
    ("dow", "다우존스", "pt"),
    ("nasdaq", "나스닥", "pt"),
    ("kr_rate", "한국 기준금리", "%"),
    ("us_rate", "미국 기준금리", "%"),
    ("kr_2y", "한국 국고채 2년", "%"),
    ("kr_10y", "한국 국고채 10년", "%"),
    ("us_2y", "미국 국채 2년", "%"),
    ("us_10y", "미국 국채 10년", "%"),
    ("kr_cpi", "한국 CPI (전년비)", "%"),
    ("us_cpi", "미국 CPI (전년비)", "%"),
    ("wti", "WTI 원유", "$"),
    ("gold", "국제 금", "$/oz"),
    ("kr_m2", "한국 M2", "조원"),
    ("us_m2", "미국 M2", "십억$"),
]

CHART_W, CHART_H, PAD = 300, 90, 8


def load_rows():
    if not os.path.exists(HISTORY_CSV):
        return []
    with open(HISTORY_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_full_series(field, daily_rows):
    """Long-term history (data/long_term/<field>.csv) plus daily rows, daily wins on overlap."""
    series = {}
    lt_path = os.path.join(LONG_TERM_DIR, f"{field}.csv")
    if os.path.exists(lt_path):
        with open(lt_path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r[field] not in (None, ""):
                    series[r["date"]] = r[field]
    for r in daily_rows:
        if r.get(field) not in (None, ""):
            series[r["date"]] = r[field]
    return sorted(series.items())


def downsample(pts, max_points=MAX_CHART_POINTS):
    if len(pts) <= max_points:
        return pts
    step = (len(pts) - 1) / (max_points - 1)
    idx = sorted({round(i * step) for i in range(max_points)})
    return [pts[i] for i in idx]


def svg_sparkline(pts):
    pts = downsample([(d, v) for d, v in pts if v not in (None, "")])
    if len(pts) < 2:
        return '<svg viewBox="0 0 300 90" class="chart"><text x="10" y="45" class="nodata">데이터 부족</text></svg>'
    nums = [float(v) for _, v in pts]
    lo, hi = min(nums), max(nums)
    span = (hi - lo) or 1
    n = len(nums)
    coords = []
    for i, v in enumerate(nums):
        x = PAD + (CHART_W - 2 * PAD) * (i / (n - 1) if n > 1 else 0)
        y = PAD + (CHART_H - 2 * PAD) * (1 - (v - lo) / span)
        coords.append((x, y))
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    last_x, last_y = coords[-1]
    return (
        f'<svg viewBox="0 0 {CHART_W} {CHART_H}" class="chart">'
        f'<polyline points="{path}" fill="none" stroke="currentColor" stroke-width="1.5"/>'
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2.5" fill="currentColor"/>'
        f"</svg>"
    )


def fmt_value(v, unit):
    if v in (None, ""):
        return "-"
    try:
        num = float(v)
    except ValueError:
        return html.escape(str(v))
    text = f"{num:,.2f}" if abs(num) < 1000 else f"{num:,.1f}"
    return f"{text}{unit}"


def build():
    rows = load_rows()
    last_updated = rows[-1]["updated_at"] if rows else "-"

    cards = []
    for field, label, unit in METRICS:
        series = load_full_series(field, rows)
        latest = series[-1][1] if series else ""
        span = f"{series[0][0]} ~ {series[-1][0]}" if series else "-"
        cards.append(
            f'<div class="card"><div class="card-head">'
            f'<span class="label">{html.escape(label)}</span>'
            f'<span class="value">{fmt_value(latest, unit)}</span></div>'
            f"{svg_sparkline(series)}"
            f'<div class="span">{html.escape(span)}</div></div>'
        )

    page = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>경제 지표 자동 수집</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, "Malgun Gothic", sans-serif; margin: 0; padding: 24px 16px;
         background: Canvas; color: CanvasText; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .updated {{ color: GrayText; font-size: 13px; margin-bottom: 20px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; max-width: 1200px; }}
  .card {{ border: 1px solid color-mix(in srgb, CanvasText 20%, transparent); border-radius: 10px; padding: 12px; }}
  .card-head {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px; }}
  .label {{ font-size: 13px; color: GrayText; }}
  .value {{ font-size: 16px; font-weight: 600; }}
  .chart {{ width: 100%; height: 70px; color: #2563eb; }}
  .span {{ font-size: 11px; color: GrayText; margin-top: 4px; }}
  .nodata {{ font-size: 12px; fill: GrayText; }}
  footer {{ margin-top: 24px; font-size: 12px; color: GrayText; }}
  a {{ color: inherit; }}
</style>
</head>
<body>
  <h1>경제 지표 자동 수집</h1>
  <div class="updated">최근 갱신: {html.escape(last_updated)} (KST) · 총 {len(rows)}일치</div>
  <div class="grid">
    {''.join(cards)}
  </div>
  <footer>
    GitHub Actions로 매일 08:00 KST 자동 수집·배포됩니다.
    원본 데이터: <a href="https://github.com/rkrjrl-ctrl/claude_drive/blob/main/data/history.csv">data/history.csv</a>
  </footer>
</body>
</html>
"""
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"built {OUT_FILE} ({len(rows)} rows)")


if __name__ == "__main__":
    build()
