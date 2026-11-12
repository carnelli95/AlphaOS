"""
financial_data 무결성 점검 (읽기 전용, SELECT만).

확인 항목:
  1. 유니크 제약 — (code, year, fs_type, account_nm) 하나만 있어야 한다.
     예전 (code, year, account_nm) 제약이 남아 있으면 CFS가 OFS로 덮어써진다.
  2. 연도별 CFS/OFS 행 수·종목 수
  3. 연도별 '자본총계' 기준 CFS 보유 종목 비율
  4. 샘플 종목(삼성전자 005930)의 연도별 자본총계 (CFS/OFS)

사용:
  cd pipeline && python check_financial_data.py
종료 코드: 예전 제약이 남아 있으면 1, 아니면 0.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv

load_dotenv(override=True)
from batch_collect_runner import get_engine

LEGACY_COLS = ["code", "year", "account_nm"]
EXPECTED_COLS = ["code", "year", "fs_type", "account_nm"]
SAMPLE_CODE = "005930"


def unique_constraints(conn) -> dict[str, list[str]]:
    rows = conn.execute(text("""
        SELECT c.conname, array_agg(a.attname ORDER BY k.ord)
        FROM pg_constraint c
        JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
        WHERE c.conrelid = 'financial_data'::regclass AND c.contype = 'u'
        GROUP BY c.conname
    """)).fetchall()
    return {name: list(cols) for name, cols in rows}


def main() -> int:
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))

        print("[1] 유니크 제약")
        cons = unique_constraints(conn)
        legacy = [n for n, cols in cons.items() if cols == LEGACY_COLS]
        for name, cols in cons.items():
            mark = "  ← 예전 제약 (CFS 덮어쓰기 원인)" if name in legacy else ""
            print(f"  {name}: ({', '.join(cols)}){mark}")
        if not any(cols == EXPECTED_COLS for cols in cons.values()):
            print("  경고: (code, year, fs_type, account_nm) 제약이 없음")

        print("\n[2] 연도별 fs_type 분포")
        total = conn.execute(text("SELECT count(*) FROM financial_data")).scalar()
        print(f"  전체 {total:,}행")
        for year, fs, n, codes in conn.execute(text("""
            SELECT year, fs_type, count(*), count(DISTINCT code)
            FROM financial_data GROUP BY 1, 2 ORDER BY 1, 2
        """)):
            print(f"  {year} {fs}: {n:>6,}행  {codes:>4}종목")

        print("\n[3] 연도별 '자본총계' CFS 보유 종목")
        for year, any_n, cfs_n in conn.execute(text("""
            SELECT year, count(DISTINCT code),
                   count(DISTINCT code) FILTER (WHERE fs_type = 'CFS')
            FROM financial_data WHERE account_nm = '자본총계'
            GROUP BY 1 ORDER BY 1
        """)):
            print(f"  {year}: CFS {cfs_n}/{any_n} ({cfs_n / any_n:.0%})")

        print(f"\n[4] {SAMPLE_CODE} 자본총계")
        for year, fs, amount in conn.execute(text("""
            SELECT year, fs_type, amount FROM financial_data
            WHERE code = :c AND account_nm = '자본총계' ORDER BY 1, 2
        """), {"c": SAMPLE_CODE}):
            print(f"  {year} {fs}: {amount / 1e12:,.1f}조")
        conn.rollback()

    return 1 if legacy else 0


if __name__ == "__main__":
    sys.exit(main())
