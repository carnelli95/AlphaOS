"""
Investment Thesis QA 자동 진단기

각 기업의 thesis_sections를 검사해 규칙 위반을 찾습니다.
위반 종목 코드를 violations.txt에 저장하면 --codes-file로 재생성 가능합니다.

사용:
  python diagnose_quality.py                    # 전체 검사
  python diagnose_quality.py --sector 화학      # 특정 업종
  python diagnose_quality.py --save             # 위반 코드를 violations.txt에 저장
"""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")
from generate_ai_report import get_engine

# 금지 표현 (summary에서 검출)
FORBIDDEN = [
    "성장 가능성", "성장 잠재력", "주목받고 있다", "기대된다",
    "전망된다", "것으로 보인다", "판단된다", "유리할 것으로 예상된다",
    "전망이다", "것으로 예상", "기대할 수",
]

# source_ref → stage_b 키 매핑
QUANT_REF_MAP = {
    "quant:roe":       "roe",
    "quant:op_margin": "op_margin",
    "quant:roa":       "roa",
    "quant:per":       "per",
    "quant:pbr":       "pbr",
}


def check_thesis(code: str, name: str, thesis: dict, stage_b: dict) -> list[str]:
    """위반 사항 리스트 반환. 없으면 빈 리스트."""
    violations = []

    # 1. Summary 금지 표현 검사
    summary = thesis.get("summary", "")
    for word in FORBIDDEN:
        if word in summary:
            violations.append(f"summary 금지표현: '{word}'")

    # 2. Strengths — 비후보 정량 지표 사용 검사
    strengths = thesis.get("strengths") or []
    for s in strengths:
        if not isinstance(s, dict):
            continue
        ref = s.get("source_ref", "")
        # source_ref에 | 가 있으면 복합 ref (quant:x|dart:y) → quant 부분만 추출
        for part in ref.split("|"):
            part = part.strip()
            if part in QUANT_REF_MAP:
                metric_key = QUANT_REF_MAP[part]
                m = (stage_b or {}).get(metric_key)
                if m and m.get("percentile") is not None:
                    pct = m["percentile"]
                    if pct < 70:
                        top_pct = 100 - pct
                        violations.append(
                            f"strengths 비후보 지표: {part} (업종 상위 {top_pct}%, percentile={pct})"
                        )

    return violations


def run(sector_filter: str | None = None, save: bool = False) -> None:
    engine = get_engine()

    sector_cond = "AND s.sector = :sector" if sector_filter else ""
    params: dict = {"sector": sector_filter} if sector_filter else {}

    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT r.stock_code, s.name, s.sector,
                   r.thesis_sections, r.stage_b_sections
            FROM ai_reports r
            JOIN stocks s ON s.code = r.stock_code
            WHERE r.thesis_sections IS NOT NULL
              {sector_cond}
            ORDER BY r.stock_code
        """), params).fetchall()

    total = len(rows)
    violation_codes: list[str] = []
    violation_details: list[tuple[str, str, str, list[str]]] = []

    for code, name, sector, thesis, stage_b in rows:
        issues = check_thesis(code, name, thesis or {}, stage_b or {})
        if issues:
            violation_codes.append(code)
            violation_details.append((code, name, sector or "-", issues))

    # ── 출력 ──────────────────────────────────────
    print()
    print("=" * 60)
    print(" Investment Thesis QA 진단 결과")
    if sector_filter:
        print(f" 업종 필터: {sector_filter}")
    print("=" * 60)
    print(f"  총 Thesis : {total:>5}개")
    print(f"  규칙 위반 : {len(violation_codes):>5}개 ({len(violation_codes)/total*100:.1f}%)")
    print()

    if violation_details:
        print("  [ 위반 종목 상세 ]")
        for code, name, sector, issues in violation_details:
            print(f"  {code}  {name:<16}  {sector}")
            for issue in issues:
                print(f"    - {issue}")
        print()

    if save and violation_codes:
        out_path = os.path.join(os.path.dirname(__file__), "violations.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(violation_codes))
        print(f"  위반 코드 저장: {out_path} ({len(violation_codes)}개)")
        print(f"  재생성 명령: python batch_generate_thesis.py --codes-file violations.txt")
        print()

    print("=" * 60)
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Investment Thesis QA 진단기")
    parser.add_argument("--sector", type=str, default=None)
    parser.add_argument("--save", action="store_true", help="위반 코드를 violations.txt에 저장")
    args = parser.parse_args()
    run(args.sector, args.save)
