"""
QA 자동 채점기.

qa_sample.py 실행 후 사람이 보기 전에 먼저 실행:
  python pipeline/qa_auto.py

자동 검증 가능한 3항목을 DB에서 재확인해 qa_results.csv에 채워 넣는다.
사람은 나머지 3항목만 검토하면 된다.

자동 항목:
  DART_attribution   attribution validator 재실행
  News_attribution   attribution validator 재실행 (동일 로직)
  산업일반론_없음     _GENERIC_KEYWORDS DB 재검사

사람이 봐야 하는 항목:
  사실관계_정확        (blank 유지 — Stage D 포함한 사실관계는 사람 확인 필요)
  투자논리_자연스러움   (blank 유지)
  핵심이벤트_누락없음   (blank 유지)
"""

from __future__ import annotations

import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv(override=True)

from generate_ai_report import get_engine
from sqlalchemy import text

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "qa_results.csv")

# generate_thesis.py _validate_thesis 와 동일 로직
_ATTR_VIOLATIONS = ("DART에서", "사업보고서에는", "공시에서", "공시에 따르면")

# generate_stage_c.py _GENERIC_KEYWORDS 와 동일
_GENERIC_KEYWORDS = (
    "일반적으로", "통상적으로", "업계는", "시장에서는",
    "업종 전체", "업계 전반", "업계 공통",
    "전 세계적으로", "경쟁사도", "모든 기업이",
    "친환경 수요가", "글로벌 경기", "고령화로 인해",
    "업황 회복", "업황 개선", "업황 호조",
    "시장이 확대", "의 수혜가 예상",
)


def _check_attribution(thesis: dict, c_dart: int, d_dart: int) -> tuple[int, str]:
    """(pass_flag, memo) — 1=통과, 0=위반"""
    if c_dart > 0 or d_dart > 0:
        return 1, ""
    ip      = thesis.get("investment_point") or ""
    summary = thesis.get("summary") or ""
    text    = ip + " " + summary
    found   = [w for w in _ATTR_VIOLATIONS if w in text]
    if found:
        return 0, f"attribution 오류: {found}"
    return 1, ""


def _check_generalization(stage_c: dict) -> tuple[int, str]:
    """(pass_flag, memo)"""
    # DB 저장 포맷 기준 (growth_drivers / risks / moat)
    db_cats = {
        "growth": (stage_c.get("growth_drivers") or {}).get("facts", []),
        "risk":   (stage_c.get("risks") or {}).get("facts", []),
        "moat":   (stage_c.get("moat") or {}).get("facts", []),
    }
    found = []
    for cat_name, facts in db_cats.items():
        for f in facts:
            text = f.get("fact", "") if isinstance(f, dict) else str(f)
            for kw in _GENERIC_KEYWORDS:
                if kw in text:
                    found.append(f"{cat_name}:{kw}")
    if found:
        return 0, f"일반론: {found[:2]}"
    return 1, ""


def _check_fact_proxy(stage_c: dict, thesis: dict) -> tuple[int, str]:
    """Stage C facts > 0 이고 thesis.summary 비어있지 않으면 프록시 통과."""
    growth_facts = (stage_c.get("growth_drivers") or {}).get("facts", [])
    risk_facts   = (stage_c.get("risks") or {}).get("facts", [])
    summary      = (thesis.get("summary") or "").strip()
    total_facts  = len(growth_facts) + len(risk_facts)
    if total_facts == 0:
        return 0, "Stage C 사실 없음 (사람이 확인 필요)"
    if not summary:
        return 0, "Thesis 비어있음"
    return 1, ""


def main() -> None:
    path = os.path.abspath(CSV_PATH)
    if not os.path.exists(path):
        print(f"파일 없음: {path}")
        print("먼저 python pipeline/qa_sample.py 를 실행하세요.")
        return

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    engine = get_engine()
    auto_cols  = ["DART_attribution", "News_attribution", "산업일반론_없음"]
    human_cols = ["사실관계_정확", "투자논리_자연스러움", "핵심이벤트_누락없음"]

    updated = 0
    already_manual = 0

    with engine.connect() as conn:
        for r in rows:
            code = r.get("code", "").strip()
            if not code:
                continue

            row = conn.execute(text("""
                SELECT r.thesis_sections, r.stage_c_sections, r.stage_d_sections
                FROM ai_reports r
                WHERE r.stock_code = :code
            """), {"code": code}).fetchone()
            if not row:
                continue

            thesis, stage_c, stage_d = row

            c_dart = int((thesis.get("confidence") or {}).get("dart_count") or 0)
            d_dart = sum(
                1 for f in (stage_d or {}).get("facts", [])
                if f.get("source_type", "dart") == "dart"
            )

            attr_pass, attr_memo = _check_attribution(thesis, c_dart, d_dart)
            gen_pass,  gen_memo  = _check_generalization(stage_c or {})
            fact_pass, fact_memo = _check_fact_proxy(stage_c or {}, thesis)

            auto_results = {
                "DART_attribution": (attr_pass, attr_memo),
                "News_attribution": (attr_pass, attr_memo),
                "산업일반론_없음":  (gen_pass, gen_memo),
            }

            auto_memos = [m for _, m in auto_results.values() if m]

            # 이미 사람이 수동 입력한 경우 덮어쓰지 않음
            has_manual = any(r.get(col, "").strip() in ("0", "1") for col in human_cols)
            if has_manual:
                already_manual += 1

            for col, (score, _) in auto_results.items():
                if r.get(col, "").strip() not in ("0", "1"):  # 이미 채워진 건 유지
                    r[col] = str(score)

            existing_memo = r.get("memo", "").strip()
            if auto_memos and not existing_memo:
                r["memo"] = "; ".join(auto_memos)

            updated += 1

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"자동 채점 완료: {updated}종목")
    print(f"  자동 항목(3개): DART attribution, News attribution, 산업일반론")
    print(f"  사람이 채울 항목(3개): 사실관계_정확, 투자논리_자연스러움, 핵심이벤트_누락없음")
    print()

    # 자동 항목 요약
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows2 = list(csv.DictReader(f))

    for col in auto_cols:
        vals = [r.get(col, "").strip() for r in rows2]
        n = len([v for v in vals if v in ("0", "1")])
        fail = vals.count("0")
        print(f"  {col:<28}  자동 0점: {fail}건" + (" ⚠" if fail else ""))

    print()
    print(f"다음 단계:")
    print(f"  1. qa_results.csv 열어서 '투자논리_자연스러움' / '핵심이벤트_누락없음' 열에 0 또는 1 입력")
    print(f"  2. python pipeline/qa_score.py")


if __name__ == "__main__":
    main()
