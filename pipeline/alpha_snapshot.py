"""
Alpha Score IC 실험 — 전체 유니버스 Alpha Score 스냅샷.

QA 품질 점수 IC(qa_ic.py)와는 별도 트랙. "Alpha Score(정량 V/S 신호)가
forward return을 예측하는가"를 검증하기 위해 ai_reports.alpha_score 전체
종목 + 기준일(ref-date) 가격을 고정한다.

사전 조건:
  python compute_alpha_score.py   실행 완료 (ai_reports.alpha_score 최신화)

주의 (look-ahead):
  ref-date 이후에 새 연도 재무가 들어온 상태에서 재계산한 alpha_score를
  과거 ref-date에 붙이면 미래 정보가 섞인다. ref-date 시점에 공개된
  재무 연도와 현재 DB 최신 연도가 같은지 확인하고 사용할 것.

사용:
  python pipeline/alpha_snapshot.py --ref-date 2026-07-15
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import date

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(override=True)

from generate_ai_report import get_engine
from sqlalchemy import text

SNAP_DIR = os.path.join(os.path.dirname(__file__), "..", "qa_snapshots")

FIELDNAMES = ["code", "name", "sector", "marcap", "score",
              "valuation", "growth", "moat", "safety",
              "t0_price", "t0_price_date"]


def main(ref_date: date) -> None:
    engine = get_engine()
    os.makedirs(SNAP_DIR, exist_ok=True)
    out_path = os.path.join(SNAP_DIR, f"alpha_snapshot_{ref_date.strftime('%Y%m%d')}.csv")

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT r.stock_code, s.name, s.sector, s.marcap, r.alpha_score
            FROM ai_reports r
            JOIN stocks s ON s.code = r.stock_code
            WHERE r.alpha_score IS NOT NULL
        """)).fetchall()

        # ref_date 이하 마지막 종가 (종목별 1건)
        prices = {
            code: (float(close), str(d))
            for code, d, close in conn.execute(text("""
                SELECT DISTINCT ON (code) code, date, close
                FROM price_history
                WHERE date <= :d
                ORDER BY code, date DESC
            """), {"d": ref_date})
        }

    result_rows = []
    missing_price = []
    for code, name, sector, marcap, alpha_score in rows:
        bd = alpha_score.get("breakdown", {})
        price, price_date = prices.get(code, (None, None))
        if price is None:
            missing_price.append(code)
        result_rows.append({
            "code": code, "name": name, "sector": sector, "marcap": marcap,
            "score": alpha_score.get("score"),
            "valuation": bd.get("valuation"), "growth": bd.get("growth"),
            "moat": bd.get("moat"), "safety": bd.get("safety"),
            "t0_price": price, "t0_price_date": price_date,
        })

    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(result_rows)

    print(f"Snapshot 저장: {out_path}")
    print(f"종목 수: {len(result_rows)}  (가격 없음: {len(missing_price)}건)")
    if missing_price:
        print(f"  → {', '.join(missing_price[:10])}{' ...' if len(missing_price) > 10 else ''}")

    print()
    print("다음 단계:")
    print(f"  python pipeline/alpha_ic.py --snapshot {os.path.basename(out_path)} --days 5 20 60")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ref-date", required=True, help="기준일 YYYY-MM-DD (alpha_score가 안정화된 시점)")
    args = parser.parse_args()
    main(date.fromisoformat(args.ref_date))
