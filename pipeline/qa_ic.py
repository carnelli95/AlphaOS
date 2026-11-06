"""
QA IC 계산 — QA snapshot → forward return → Spearman IC.

사전 조건:
  python pipeline/qa_snapshot.py  실행 완료 (qa_snapshots/qa_snapshot_YYYYMMDD.csv)

사용:
  python pipeline/qa_ic.py --snapshot qa_snapshot_20260630.csv --days 20
  python pipeline/qa_ic.py --snapshot qa_snapshot_20260630.csv --days 5 20 60

출력:
  - 종목별 QA score + forward return
  - Spearman IC (전체 / 항목별)
  - Top QA vs Bottom QA 수익률 비교
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import date, timedelta
from scipy import stats

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(override=True)

from generate_ai_report import get_engine
from sqlalchemy import text

SNAP_DIR = os.path.join(os.path.dirname(__file__), "..", "qa_snapshots")

QA_COLS = [
    "사실관계_정확",
    "DART_attribution",
    "News_attribution",
    "산업일반론_없음",
    "투자논리_자연스러움",
    "핵심이벤트_누락없음",
]


def _price_after(conn, code: str, ref_date: date, days: int) -> tuple[float | None, str | None]:
    """ref_date 기준 최소 days 영업일 이후 첫 번째 종가."""
    target = ref_date + timedelta(days=days)
    row = conn.execute(text("""
        SELECT date, close
        FROM price_history
        WHERE code = :code AND date >= :d
        ORDER BY date ASC
        LIMIT 1
    """), {"code": code, "d": target}).fetchone()
    if row:
        return float(row[1]), str(row[0])
    return None, None


def compute_ic(window_days: int, snap_rows: list[dict], engine) -> None:
    print(f"\n{'='*60}")
    print(f"Forward Return Window: {window_days}D")
    print(f"{'='*60}")

    records = []
    with engine.connect() as conn:
        for row in snap_rows:
            code = row["code"]
            t0_price = float(row["t0_price"]) if row.get("t0_price") else None
            t0_date  = date.fromisoformat(row["t0_price_date"]) if row.get("t0_price_date") else None

            if t0_price is None or t0_date is None:
                print(f"  ⚠ {code} {row.get('name')} — t0 가격 없음, 스킵")
                continue

            t1_price, t1_date = _price_after(conn, code, t0_date, window_days)
            if t1_price is None:
                print(f"  ⚠ {code} {row.get('name')} — {window_days}D 이후 가격 없음 (아직 미래)")
                continue

            fwd_return = (t1_price - t0_price) / t0_price * 100
            total = int(row.get("total_score") or 0)

            item_scores = {}
            for col in QA_COLS:
                v = row.get(col, "")
                item_scores[col] = int(v) if v in ("0", "1") else None

            records.append({
                "code":       code,
                "name":       row.get("name"),
                "group":      row.get("group"),
                "total":      total,
                **item_scores,
                "t0_price":   t0_price,
                "t0_date":    str(t0_date),
                "t1_price":   t1_price,
                "t1_date":    t1_date,
                "fwd_return": round(fwd_return, 2),
            })

    if len(records) < 3:
        print(f"  유효 종목 {len(records)}건 — IC 계산 불가 (최소 3건 필요)")
        return

    print(f"\n종목별 결과 ({len(records)}건):")
    print(f"  {'코드':<8} {'종목명':<18} {'QA':>3} {'t0가격':>8} {'t1가격':>8} {'수익률':>7}")
    print(f"  {'-'*56}")
    for r in sorted(records, key=lambda x: -x["total"]):
        print(f"  {r['code']:<8} {r['name']:<18} {r['total']:>3}  "
              f"{r['t0_price']:>8,.0f}  {r['t1_price']:>8,.0f}  {r['fwd_return']:>+6.1f}%")

    qa_scores  = [r["total"] for r in records]
    fwd_returns = [r["fwd_return"] for r in records]
    ic, pval   = stats.spearmanr(qa_scores, fwd_returns)

    print(f"\n[IC 결과]")
    print(f"  전체 Spearman IC : {ic:+.3f}  (p={pval:.3f}, n={len(records)})")

    if len(records) >= 4:
        sorted_by_qa = sorted(records, key=lambda x: -x["total"])
        half = max(2, len(records) // 2)
        top_ret  = sum(r["fwd_return"] for r in sorted_by_qa[:half]) / half
        bot_ret  = sum(r["fwd_return"] for r in sorted_by_qa[half:]) / max(1, len(sorted_by_qa) - half)
        print(f"  Top {half} 평균 수익률 : {top_ret:+.1f}%")
        print(f"  Bot {len(sorted_by_qa)-half} 평균 수익률 : {bot_ret:+.1f}%")
        print(f"  L-S Spread       : {top_ret - bot_ret:+.1f}%")

    print(f"\n[항목별 IC]")
    for col in QA_COLS:
        vals = [(r[col], r["fwd_return"]) for r in records if r.get(col) is not None]
        if len(vals) < 3 or len(set(v for v, _ in vals)) < 2:
            print(f"  {col:<28}  n/a (분산 없음)")
            continue
        col_ic, col_p = stats.spearmanr([v for v, _ in vals], [r for _, r in vals])
        print(f"  {col:<28}  IC={col_ic:+.3f}  p={col_p:.3f}")

    # 결과 저장
    snap_base = os.path.splitext(os.path.basename(snap_path))[0] if "snap_path" in dir() else "snapshot"
    out_name  = f"qa_ic_{snap_base}_{window_days}d.csv"
    out_path  = os.path.join(SNAP_DIR, out_name)
    if records:
        fieldnames = list(records[0].keys())
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)
        print(f"\n결과 저장: {out_path}")


def main(snapshot_name: str, days_list: list[int]) -> None:
    global snap_path
    snap_path = os.path.join(SNAP_DIR, snapshot_name)
    if not os.path.exists(snap_path):
        print(f"오류: {snap_path} 없음")
        sys.exit(1)

    with open(snap_path, encoding="utf-8-sig") as f:
        snap_rows = list(csv.DictReader(f))

    print(f"Snapshot: {snapshot_name}  ({len(snap_rows)}종목)")
    engine = get_engine()
    for d in days_list:
        compute_ic(d, snap_rows, engine)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, help="qa_snapshots/ 내 파일명")
    parser.add_argument("--days", type=int, nargs="+", default=[5, 20, 60],
                        help="Forward return 윈도우 (기본 5 20 60)")
    args = parser.parse_args()
    main(args.snapshot, args.days)
