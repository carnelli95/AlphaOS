"""pykrx 업종 데이터 검증 — Stage B v1 진행 전 확인."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"C:\Users\carne\Desktop\AIProjects\AlphaOS\pipeline")

from pykrx import stock
from generate_ai_report import get_engine
from sqlalchemy import text
import datetime

DATE = datetime.date.today().strftime("%Y%m%d")

# 1. pykrx에서 KOSPI 업종 분류 가져오기
print(f"pykrx KOSPI 업종 데이터 로딩 ({DATE})...")
df = stock.get_market_sector_classifications(DATE, "KOSPI")
print(f"컬럼: {df.columns.tolist()}")
print(f"총 {len(df)}개\n")
print(df.head(3).to_string())
print()

# 2. 5개 대표 종목 업종 확인
TARGET = {
    "005930": "삼성전자   → 기대: 반도체",
    "000660": "SK하이닉스 → 기대: 반도체",
    "035420": "NAVER     → 기대: IT서비스",
    "105560": "KB금융     → 기대: 은행",
    "068270": "셀트리온   → 기대: 바이오",
}
print("=== 대표 종목 업종 확인 ===")
ticker_col = df.index.name or "티커"
for code, label in TARGET.items():
    if code in df.index:
        row = df.loc[code]
        print(f"  {label:<30}  실제: {row.to_dict()}")
    else:
        print(f"  {label:<30}  ⚠ pykrx 미수록")

# 3. 우리 DB 종목과 커버리지 계산
print("\n=== DB 커버리지 ===")
engine = get_engine()
with engine.connect() as conn:
    db_codes = {r[0] for r in conn.execute(text("SELECT code FROM stocks")).fetchall()}

pykrx_codes = set(df.index.tolist())
matched = db_codes & pykrx_codes
print(f"DB: {len(db_codes)}개 / pykrx: {len(pykrx_codes)}개 / 매칭: {len(matched)}개 ({len(matched)/len(db_codes)*100:.1f}%)")

# 4. 업종 분포 (상위 20개)
sector_col = [c for c in df.columns if '업종' in c or 'sector' in c.lower() or 'Sector' in c]
if not sector_col:
    print(f"\n업종 컬럼을 찾지 못함. 전체 컬럼: {df.columns.tolist()}")
else:
    col = sector_col[0]
    matched_df = df[df.index.isin(db_codes)]
    dist = matched_df[col].value_counts()
    print(f"\n=== 업종 분포 (컬럼: '{col}', 상위 20개) ===")
    for sector, cnt in dist.head(20).items():
        bar = "■" * min(cnt // 2, 20)
        print(f"  {str(sector):<25} {cnt:>3}개  {bar}")
    no_sector = matched_df[col].isna().sum()
    print(f"\n업종 없음: {no_sector}개")
    valid = (dist >= 10).sum()
    print(f"peer_count >= 10 유효 업종: {valid}개")
