import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from generate_ai_report import get_engine
from sqlalchemy import text

engine = get_engine()
with engine.connect() as conn:
    rows = conn.execute(text("""
        SELECT
            r.stock_code,
            jsonb_array_length(r.thesis_sections->'risks')                              AS thesis_risks,
            CASE WHEN jsonb_typeof(r.stage_c_sections->'risks'->'facts') = 'array'
                 THEN jsonb_array_length(r.stage_c_sections->'risks'->'facts')
                 ELSE -1 END                                                            AS dart_risks
        FROM ai_reports r
        WHERE r.thesis_sections IS NOT NULL
          AND jsonb_typeof(r.thesis_sections->'risks') = 'array'
          AND jsonb_array_length(r.thesis_sections->'risks') = 1
        ORDER BY dart_risks DESC
        LIMIT 20
    """)).fetchall()

print(f"{'코드':<10} {'thesis_risks':>12} {'dart_risks':>10}  판정")
print("-" * 50)
for code, tr, dr in rows:
    verdict = "DART도 1개 (추출 부족)" if dr <= 1 else f"DART={dr}개 → 프롬프트가 1개만 선택"
    print(f"{code:<10} {tr:>12} {dr:>10}  {verdict}")

dart_vals = [dr for _, _, dr in rows]
if dart_vals:
    avg = sum(dart_vals) / len(dart_vals)
    print()
    print(f"dart_risks 평균: {avg:.1f}  (음수=-1은 stage_c 없음)")
