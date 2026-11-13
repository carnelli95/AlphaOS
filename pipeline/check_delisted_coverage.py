"""
상장폐지 종목 데이터 커버리지 점검 (읽기 전용, SELECT만).

delisted_stocks 각 종목에 대해:
  1. 시세: price_history 행 수, 마지막 실거래일(volume > 0), 정지일 행 수(volume IS NULL)
     → 마지막 실거래일이 폐지일보다 40일 넘게 앞서면 경고(합병은 ~1개월 전 거래 종료가 정상).
  2. 재무: 사업연도 N 보고서가 "나왔어야 하는" 연도 중 financial_data가 빠진 연도.
     기대 기준: 폐지일 > (N+1)-03-31 이면 N년 사업보고서가 제출됐어야 한다.

사용:
  cd pipeline && python check_delisted_coverage.py
종료 코드: 시세가 없는 종목이 있으면 1.
"""

from __future__ import annotations

import os
import sys
from datetime import date

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)
from batch_collect_runner import get_engine

YEARS = (2022, 2023, 2024, 2025)


def expected_years(delisting_date: date) -> list[int]:
    """폐지일 기준으로 사업보고서가 제출됐어야 하는 사업연도."""
    return [y for y in YEARS if delisting_date > date(y + 1, 3, 31)]


def main() -> int:
    with get_engine().connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        rows = conn.execute(text("""
            SELECT d.code, d.name, d.delisting_date, d.reason_category,
                   count(p.date)                                   AS n_px,
                   max(p.date) FILTER (WHERE p.volume > 0)         AS last_trade,
                   count(*) FILTER (WHERE p.date IS NOT NULL AND p.volume IS NULL) AS n_halt,
                   (SELECT array_agg(DISTINCT f.year) FROM financial_data f WHERE f.code = d.code) AS fy
            FROM delisted_stocks d
            LEFT JOIN price_history p ON p.code = d.code
            GROUP BY 1, 2, 3, 4 ORDER BY 3
        """)).fetchall()
        conn.rollback()

    no_px = gap = 0
    missing_fin = []
    for code, name, dd, cat, n_px, last_trade, n_halt, fy in rows:
        have = {int(y) for y in (fy or [])}
        miss = [y for y in expected_years(dd) if y not in have]
        flag = []
        if n_px == 0:
            no_px += 1
            flag.append("시세없음")
        elif last_trade and (dd - last_trade).days > 40:
            gap += 1
            flag.append(f"마지막거래 {(dd - last_trade).days}일전")
        if miss:
            missing_fin.append((code, name, miss))
            flag.append(f"재무누락 {miss}")
        print(f"  {dd} {code} {name:<12} [{cat}] 시세 {n_px:>4}행 정지 {n_halt:>3}행 "
              f"마지막거래 {last_trade}  재무 {sorted(have)}  {' '.join(flag)}")

    print(f"\n종목 {len(rows)}개 | 시세 없음 {no_px} | 마지막 거래가 폐지 40일 이전 {gap} | "
          f"재무 누락 종목 {len(missing_fin)}")
    return 1 if no_px else 0


if __name__ == "__main__":
    sys.exit(main())
