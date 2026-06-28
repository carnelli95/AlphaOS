"""
Fact Ranking importance 필드 검증 스크립트.
5~10개 다양한 업종 종목에 generate_stage_c 실행 후 importance 품질 체크.

확인 항목:
1. importance 누락 없음
2. 숫자 타입 (string "High" 등 이상값 아님)
3. 1~3 범위만 존재
4. _rank() 정렬 후 실제로 importance 내림차순 정렬 확인
"""

import sys
sys.path.insert(0, r"C:\Users\carne\Desktop\AIProjects\AlphaOS\pipeline")

from generate_stage_c import generate_stage_c_for_stock, get_engine

TARGETS = [
    ("005930", "삼성전자",       "반도체/전자"),
    ("000660", "SK하이닉스",     "반도체"),
    ("035420", "NAVER",         "IT 플랫폼"),
    ("068270", "셀트리온",       "바이오"),
    ("003550", "LG",            "지주사"),
    ("005380", "현대차",         "자동차"),
    ("105560", "KB금융",         "금융"),
]

engine = get_engine()

total_facts = 0
anomalies = []

print("=== importance 필드 검증 ===\n")

for code, name, sector in TARGETS:
    print(f"[{code}] {name} ({sector})")
    result = generate_stage_c_for_stock(engine, code)
    if result is None:
        print("  SKIP (데이터 없음)\n")
        continue

    for section_key in ["growth_drivers", "risks", "moat"]:
        sec = result.get(section_key, {})
        facts = sec.get("facts", [])
        if section_key == "risks":
            facts = facts + sec.get("neutral_facts", [])

        importances = []
        for f in facts:
            total_facts += 1
            imp = f.get("importance") if isinstance(f, dict) else None

            # 이상값 체크
            if imp is None:
                anomalies.append(f"[{code}] {section_key}: importance 누락 — {str(f)[:50]}")
            elif not isinstance(imp, int):
                anomalies.append(f"[{code}] {section_key}: 타입 이상 ({type(imp).__name__}='{imp}') — {str(f.get('fact',''))[:40]}")
            elif imp not in (1, 2, 3):
                anomalies.append(f"[{code}] {section_key}: 범위 이상 (importance={imp}) — {str(f.get('fact',''))[:40]}")
            else:
                importances.append(imp)

        # 정렬 확인 (내림차순이어야 함)
        if importances and importances != sorted(importances, reverse=True):
            anomalies.append(f"[{code}] {section_key}: 정렬 오류 {importances}")

        label = {"growth_drivers": "growth", "risks": "risk", "moat": "moat"}[section_key]
        print(f"  {label}: {importances}")

    print()

print("=" * 50)
print(f"총 검증 Fact: {total_facts}개")
if anomalies:
    print(f"\n⚠ 이상값 {len(anomalies)}건:")
    for a in anomalies:
        print(f"  {a}")
else:
    print("\n✅ 이상값 없음 — 재배치 진행 가능")
