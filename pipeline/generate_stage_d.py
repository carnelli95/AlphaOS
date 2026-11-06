"""
Stage D: 최근 주요 이벤트 Fact 추출 (LLM)

stage_d_raw (공시 목록) → stage_d_sections (구조화된 Fact 3~5개)

설계 원칙:
  - Stage C를 덮어쓰지 않음 — 보강만 (별도 섹션)
  - stage_d_raw 의 공시 제목 + 날짜만으로 fact 구조화 (v1)
  - financial_impact: "none" / "low" / "medium" / "high" (enum)
  - type: "contract" / "operations" / "management" / "finance" / "risk" / "other"

출력 스키마 (ai_reports.stage_d_sections):
{
  "generated_at": "...",
  "period_days": 180,
  "source": "dart_disclosure",
  "fact_count": 3,
  "facts": [
    {
      "date": "20260318",
      "recency_days": 103,
      "type": "finance",
      "fact": "자기주식 취득 결정 공시 (재무 이벤트)",
      "financial_impact": "medium",
      "importance": 2,
      "dart_id": "20260318000123",
      "source_type": "dart"
    }
  ]
}

source_type 우선순위 (SOURCE_PRIORITY):
  dart: 100  — DART 공시 (가장 신뢰도 높음)
  ir:    80  — IR 자료
  news:  60  — 뉴스 기사
같은 이벤트가 여러 소스에서 들어오면 priority가 높은 소스를 유지.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(__file__))
from generate_ai_report import call_llm, get_engine

load_dotenv(override=True)

_VALID_TYPES   = {"contract", "operations", "management", "finance", "risk", "other"}
_VALID_IMPACTS = {"none", "low", "medium", "high"}

# 이벤트 소스 우선순위: 값이 클수록 더 신뢰할 수 있는 소스
SOURCE_PRIORITY: dict[str, int] = {
    "dart": 100,
    "ir":    80,
    "news":  60,
}
_VALID_SOURCE_TYPES = set(SOURCE_PRIORITY.keys())


def _build_prompt(company_name: str, disclosures: list[dict]) -> str:
    if not disclosures:
        return ""

    lines = []
    for d in disclosures:
        lines.append(
            f"  [{d['rcept_dt']}] recency={d['recency_days']}일  {d['report_nm']}"
            f"  (rcept_no={d['rcept_no']})"
        )
    disclosure_block = "\n".join(lines)

    return f"""너는 AlphaOS 투자 이벤트 분석 AI야.
아래는 "{company_name}"이 최근 공시한 DART 수시공시 목록이야.

[절대 규칙]
- 제공된 공시 제목과 날짜만 사용. 없는 사실 절대 생성 금지.
- 각 fact는 공시 제목을 한국어 1문장으로 요약. 수치나 구체적 내용은 제목에서만 인용.
- type 분류 기준:
    contract    = 공급계약·수주·MOU 체결
    operations  = 공장준공·생산능력변경·설비투자
    management  = 대표이사변경·최대주주변경·합병·분할
    finance     = 자기주식·유상증자·전환사채·배당
    risk        = 소송·제재·조사·부정적 공시
    other       = 위 분류에 해당하지 않는 경우
- financial_impact 기준:
    high   = 매출·이익에 직접적·즉각적 영향 (대형 계약·합병·유증)
    medium = 간접적 영향 또는 중기적 영향 (자기주식·소규모 계약)
    low    = 영향이 미미하거나 불분명
    none   = 재무적 영향 없음 (인사 변경·자율공시 등)
- importance: 1(낮음) / 2(보통) / 3(높음) — 투자 판단에의 중요도
- fact 문장에 회사명 포함 금지. "X를 공시했다" 형식 대신 이벤트 내용 중심으로 작성.
  예) ❌ "삼성전자는 자기주식취득결정을 공시했다."
      ✅ "자기주식 취득 및 처분 결정 (총 3회 반복 공시)"
- 동일 유형 이벤트가 같은 날 또는 유사한 시점에 중복 공시된 경우 (취득결정+처분결정 쌍, 동일 계약의 보완 공시 등) 반드시 1개 fact로 병합. 전체 공시 목록을 먼저 그룹핑한 뒤 fact를 생성할 것.
- 반드시 중요도 높은 것부터 정렬하여 최대 5개만 출력

[최근 수시공시 목록]
{disclosure_block}

[출력 형식 — JSON만, 다른 텍스트 절대 금지]
{{
  "facts": [
    {{
      "date":            "YYYYMMDD",
      "recency_days":    숫자,
      "type":            "contract | operations | management | finance | risk | other",
      "fact":            "공시 내용 1문장 요약",
      "financial_impact": "none | low | medium | high",
      "importance":      1 또는 2 또는 3,
      "dart_id":         "rcept_no 문자열"
    }}
  ]
}}"""


def _validate_facts(raw_facts: list) -> list[dict]:
    """LLM 응답 정규화 및 필드 유효성 검증."""
    validated = []
    for f in raw_facts:
        if not isinstance(f, dict):
            continue
        fact_text = str(f.get("fact", "")).strip()
        if not fact_text:
            continue
        src = f.get("source_type", "dart")
        validated.append({
            "date":            str(f.get("date", "")),
            "recency_days":    int(f.get("recency_days", 0)),
            "type":            f.get("type", "other") if f.get("type") in _VALID_TYPES else "other",
            "fact":            fact_text,
            "financial_impact": f.get("financial_impact", "low") if f.get("financial_impact") in _VALID_IMPACTS else "low",
            "importance":      min(3, max(1, int(f.get("importance", 2)))),
            "dart_id":         str(f.get("dart_id", "")),
            "source_type":     src if src in _VALID_SOURCE_TYPES else "dart",
        })
    return validated[:5]


def generate_stage_d_for_stock(
    engine,
    stock_code: str,
    verbose: bool = False,
) -> dict | None:
    """
    stage_d_raw 에서 공시 목록을 읽어 LLM으로 Fact 추출 후
    stage_d_sections 에 저장한다.
    """
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT s.name, r.stage_d_raw
            FROM stocks s
            LEFT JOIN ai_reports r ON r.stock_code = s.code
            WHERE s.code = :c
        """), {"c": stock_code}).fetchone()

    if not row or not row[1]:
        print(f"  {stock_code}: stage_d_raw 없음 → 수집 먼저 실행")
        return None

    name, raw = row
    disclosures = raw.get("disclosures", [])

    if not disclosures:
        # 공시 없음도 정상 케이스 — 빈 sections로 저장
        result = {
            "generated_at": datetime.now().isoformat(),
            "period_days":  raw.get("period_days", 180),
            "source":       raw.get("source", "dart_disclosure"),
            "fact_count":   0,
            "facts":        [],
        }
        _save_stage_d(engine, stock_code, result)
        return result

    prompt = _build_prompt(name, disclosures)
    if verbose:
        print(f"\n--- 프롬프트 ({len(prompt)}자) ---\n{prompt[:2000]}")

    raw_response = call_llm(prompt)

    if verbose:
        print(f"\n--- LLM 응답 ---\n{json.dumps(raw_response, ensure_ascii=False, indent=2)}")

    facts = _validate_facts(raw_response.get("facts", []))

    result = {
        "generated_at": datetime.now().isoformat(),
        "period_days":  raw.get("period_days", 180),
        "source":       raw.get("source", "dart_disclosure"),
        "fact_count":   len(facts),
        "facts":        facts,
    }
    _save_stage_d(engine, stock_code, result)
    return result


def _save_stage_d(engine, stock_code: str, data: dict) -> None:
    # 기존 비-DART facts(news, ir 등) 보존 — DART 재생성 시 뉴스 데이터 유실 방지
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT stage_d_sections FROM ai_reports WHERE stock_code = :c"),
            {"c": stock_code},
        ).fetchone()

    preserved: list[dict] = []
    if row and row[0]:
        preserved = [
            f for f in (row[0].get("facts") or [])
            if f.get("source_type", "dart") != "dart"
        ]

    dart_facts: list[dict] = data.get("facts", [])
    slots_left = max(0, 5 - len(dart_facts))
    all_facts  = dart_facts + preserved[:slots_left]

    data = dict(data, facts=all_facts, fact_count=len(all_facts))

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO ai_reports (stock_code, stage_d_sections)
            VALUES (:code, CAST(:data AS JSONB))
            ON CONFLICT (stock_code) DO UPDATE
              SET stage_d_sections = CAST(:data AS JSONB)
        """), {"code": stock_code, "data": json.dumps(data, ensure_ascii=False)})


if __name__ == "__main__":
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    target = sys.argv[1] if len(sys.argv) > 1 else "005930"
    engine = get_engine()

    result = generate_stage_d_for_stock(engine, target, verbose=True)
    if result:
        print(f"\n=== Stage D Facts ({target}) ===")
        print(f"fact_count: {result['fact_count']}")
        for f in result["facts"]:
            print(f"  [{f['date']}] +{f['recency_days']}일  [{f['type']}] "
                  f"impact={f['financial_impact']} imp={f['importance']}  {f['fact']}")
