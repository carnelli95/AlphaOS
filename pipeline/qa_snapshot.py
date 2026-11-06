"""
QA Snapshot — QA 점수 + t0 가격 고정.

IC 계산의 선행 조건: QA 시점의 가격을 반드시 오늘 저장해야 함.
나중에 qa_ic.py --days 20 으로 forward return + IC 계산 가능.

사용:
  python pipeline/qa_snapshot.py               # qa_results.csv 기준 snapshot
  python pipeline/qa_snapshot.py --csv qa_results.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import date, timedelta

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(override=True)

from generate_ai_report import get_engine
from sqlalchemy import text

QA_CSV   = os.path.join(os.path.dirname(__file__), "..", "qa_results.csv")
SNAP_DIR = os.path.join(os.path.dirname(__file__), "..", "qa_snapshots")

COLS = [
    "사실관계_정확",
    "DART_attribution",
    "News_attribution",
    "산업일반론_없음",
    "투자논리_자연스러움",
    "핵심이벤트_누락없음",
]


def _latest_price(conn, code: str, ref_date: date) -> tuple[float | None, str | None]:
    """price_history에서 ref_date 이하 최신 종가 반환."""
    row = conn.execute(text("""
        SELECT date, close
        FROM price_history
        WHERE code = :code AND date <= :d
        ORDER BY date DESC
        LIMIT 1
    """), {"code": code, "d": ref_date}).fetchone()
    if row:
        return float(row[1]), str(row[0])
    return None, None


def _stock_meta(conn, code: str) -> tuple[int | None, str | None]:
    """stocks 테이블에서 marcap, sector 조회."""
    row = conn.execute(text("""
        SELECT marcap, sector FROM stocks WHERE code = :code LIMIT 1
    """), {"code": code}).fetchone()
    if row:
        return row[0], row[1]
    return None, None


def main(qa_csv: str) -> None:
    today = date.today()
    snap_date_str = today.strftime("%Y%m%d")
    os.makedirs(SNAP_DIR, exist_ok=True)
    out_path = os.path.join(SNAP_DIR, f"qa_snapshot_{snap_date_str}.csv")

    with open(qa_csv, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    engine = get_engine()
    result_rows = []

    with engine.connect() as conn:
        for row in rows:
            code = row["code"].strip()
            scores = {}
            total = 0
            for col in COLS:
                val_str = row.get(col, "").strip()
                val = int(val_str) if val_str in ("0", "1") else None
                scores[col] = val
                if val is not None:
                    total += val

            price, price_date = _latest_price(conn, code, today)
            marcap, sector    = _stock_meta(conn, code)

            result_rows.append({
                "code":            code,
                "name":            row.get("name", ""),
                "group":           row.get("group", ""),
                "snapshot_date":   str(today),
                "marcap":          marcap,
                "sector":          sector,
                **scores,
                "total_score":     total,
                "t0_price":        price,
                "t0_price_date":   price_date,
            })

    fieldnames = [
        "code", "name", "group", "snapshot_date",
        "marcap", "sector",
        *COLS,
        "total_score", "t0_price", "t0_price_date",
    ]

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(result_rows)

    print(f"Snapshot 저장: {out_path}")
    print(f"종목 수: {len(result_rows)}")
    print()

    missing_price = [r for r in result_rows if r["t0_price"] is None]
    if missing_price:
        print(f"⚠ 가격 없음 ({len(missing_price)}건) — price_history 업데이트 필요:")
        for r in missing_price:
            print(f"   {r['code']} {r['name']}")
        print()
        print("  → python pipeline/collect_price_history.py --from 2026-06-01")
    else:
        print("✅ 전종목 t0 가격 확인")

    print()
    print("다음 단계:")
    print(f"  5D  후 : python pipeline/qa_ic.py --snapshot {os.path.basename(out_path)} --days 5")
    print(f"  20D 후 : python pipeline/qa_ic.py --snapshot {os.path.basename(out_path)} --days 20")
    print(f"  60D 후 : python pipeline/qa_ic.py --snapshot {os.path.basename(out_path)} --days 60")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=QA_CSV, help="qa_results.csv 경로")
    args = parser.parse_args()
    main(args.csv)
