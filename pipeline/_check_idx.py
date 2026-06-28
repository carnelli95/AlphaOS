import sys, os
sys.path.insert(0, os.path.dirname(__file__) or ".")
from batch_collect_runner import get_engine
from sqlalchemy import text

engine = get_engine()
with engine.connect() as conn:
    for code in ("IDX_KOSPI", "IDX_KOSDAQ"):
        rows = conn.execute(text("""
            SELECT date, close FROM price_history
            WHERE code = :c ORDER BY date
        """), {"c": code}).fetchall()
        if not rows:
            print(f"{code}: 데이터 없음")
            continue
        print(f"\n{code}: {len(rows)}일")
        print(f"  최초: {rows[0][0]}  close={rows[0][1]}")
        print(f"  최근: {rows[-1][0]}  close={rows[-1][1]}")
        # 2026년 데이터 샘플
        y26 = [(r[0], r[1]) for r in rows if str(r[0]).startswith("2026")]
        if y26:
            print(f"  2026년 첫: {y26[0][0]}  close={y26[0][1]}")
            print(f"  2026년 말: {y26[-1][0]}  close={y26[-1][1]}")
            ret = (y26[-1][1] / y26[0][1]) - 1
            print(f"  2026 YTD 수익률: {ret*100:+.1f}%")
        else:
            print("  2026년 데이터 없음")
