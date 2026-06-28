import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\carne\Desktop\AIProjects\AlphaOS\pipeline")
from generate_ai_report import get_engine
from sqlalchemy import text

engine = get_engine()
with engine.connect() as conn:
    rows = conn.execute(text("""
        SELECT code, name, induty_code, sector FROM stocks
        WHERE code IN ('005930','000660','035420','105560','068270','005380','032830','003490')
        ORDER BY code
    """)).fetchall()

for r in rows:
    print(f"{r[0]}  {r[1]:<20}  induty={r[2]:<6}  sector={r[3]}")

# 항공 47개 실체 확인
print("\n=== 항공 분류 종목 샘플 ===")
with engine.connect() as conn:
    rows2 = conn.execute(text(
        "SELECT code, name, induty_code FROM stocks WHERE sector = '항공' ORDER BY name LIMIT 20"
    )).fetchall()
for r in rows2:
    print(f"  {r[0]}  {r[1]:<20}  induty={r[2]}")
