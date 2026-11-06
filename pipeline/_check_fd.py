import sys, io
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, '.')
from batch_collect_runner import get_engine
from sqlalchemy import text

engine = get_engine()
with engine.connect() as conn:
    # 실제 year 값과 타입 확인
    rows = conn.execute(text(
        "SELECT DISTINCT year, pg_typeof(year) FROM financial_data ORDER BY year"
    )).fetchall()
    print("=== year 컬럼 값 ===")
    for r in rows:
        print(f"  year={r[0]!r}  type={r[1]}")

    # unique constraint 확인
    rows2 = conn.execute(text("""
        SELECT conname, pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'financial_data'::regclass
    """)).fetchall()
    print("\n=== financial_data 제약 ===")
    for r in rows2:
        print(f"  {r[0]}: {r[1]}")
