"""FinanceDataReader Sector 커버리지 확인 — Stage B v1 설계용."""
import sys, io
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\carne\Desktop\AIProjects\AlphaOS\pipeline")

import FinanceDataReader as fdr
from generate_ai_report import get_engine
from sqlalchemy import text

# FDR에서 KRX 전체 종목 + Sector 가져오기
print("FDR StockListing 로딩 중...")
krx = fdr.StockListing('KRX')[['Code', 'Market', 'Name', 'Sector', 'Industry']]
print(f"FDR 전체: {len(krx)}개\n")

# 우리 DB의 종목 코드 가져오기
engine = get_engine()
with engine.connect() as conn:
    db_codes = {r[0] for r in conn.execute(text("SELECT code FROM stocks")).fetchall()}

print(f"DB 종목 수: {len(db_codes)}개")

# 매칭 + sector 커버리지 분석
fdr_dict = {row['Code']: row['Sector'] for _, row in krx.iterrows()}

matched        = [c for c in db_codes if c in fdr_dict]
sector_filled  = [c for c in matched if fdr_dict[c] and str(fdr_dict[c]).strip()]
sector_empty   = [c for c in matched if not (fdr_dict[c] and str(fdr_dict[c]).strip())]
not_in_fdr     = [c for c in db_codes if c not in fdr_dict]

print(f"FDR 매칭:      {len(matched)}/{len(db_codes)}")
print(f"Sector 있음:   {len(sector_filled)}/{len(db_codes)}  ({len(sector_filled)/len(db_codes)*100:.1f}%)")
print(f"Sector 없음:   {len(sector_empty)}")
print(f"FDR 미수록:    {len(not_in_fdr)}")

# 상위 Sector 분포
from collections import Counter
sector_counts = Counter(fdr_dict[c] for c in sector_filled)
print(f"\n=== 상위 20개 Sector ===")
for sector, cnt in sector_counts.most_common(20):
    print(f"  {str(sector):<30} {cnt}개")

# peer_count >= 10 기준으로 몇 개 sector가 유효한가
valid_sectors = {s for s, cnt in sector_counts.items() if cnt >= 10}
valid_stocks  = sum(1 for c in sector_filled if fdr_dict[c] in valid_sectors)
print(f"\npeer_count >= 10 유효 sector: {len(valid_sectors)}개")
print(f"유효 sector 소속 종목:        {valid_stocks}/{len(db_codes)} ({valid_stocks/len(db_codes)*100:.1f}%)")
