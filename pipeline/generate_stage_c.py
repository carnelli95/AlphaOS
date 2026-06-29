"""
Stage C: DART 사업보고서 원문 + 정량 데이터 결합 분석

흐름 (2단계 구조):
    Step 1. 사업보고서 원문 → LLM → 후보 Fact 추출 (중요도 순 정렬, 최대 10개)
            Python에서 Top 5/3 슬라이싱 (Fact Ranking MVP)
    Step 2. Top Facts → LLM → 1문장 요약 생성

공통 함수는 generate_ai_report에서 재사용 (중복 제거).
"""

from __future__ import annotations

import json
import os

from dotenv import load_dotenv
from sqlalchemy import text

from generate_ai_report import (
    call_llm,
    compute_metrics,
    compute_signals,
    get_business_overview,
    get_engine,
    get_financial_history,
    get_stock_market_data,
)

load_dotenv(override=True)


def _sample_document(text: str, target_chars: int = 8000, n_chunks: int = 5) -> str:
    """문서를 균등 구간으로 나눠 각 구간 앞부분을 조합해서 반환.

    앞 N자 + 뒤 N자 방식은 중간 구간(특허/R&D/핵심 사업부 등)을 놓침.
    삼성전자 기준: 특허(48%), DS 반도체(80%)가 중간에 위치 → 균등 샘플링 필요.
    """
    if len(text) <= target_chars:
        return text
    chunk_size = len(text) // n_chunks
    chars_per_chunk = target_chars // n_chunks
    parts = [text[i * chunk_size : i * chunk_size + chars_per_chunk] for i in range(n_chunks)]
    return "\n\n[...]\n\n".join(parts)


def build_extraction_prompt(company_name: str, business_overview: str) -> str:
    """Step 1: 사업보고서 → 성장/리스크/중립/해자 관련 facts 추출."""
    text = business_overview or ""
    excerpt = _sample_document(text)
    return f"""너는 사업보고서 원문 파서야. 아래 "{company_name}" 사업보고서에서
투자자에게 중요한 사실을 추출하고 분류해줘. 추론이나 평가는 절대 금지.

[원문]
{excerpt}

[핵심 원칙 — 기업 고유 사실만 추출]
growth_catalysts / risk_factors / moat_hints는 반드시 "이 기업만의 구체적인 사실"이어야 한다.
업종·산업 전체에 해당하는 일반론은 neutral_facts로만 분류. growth/risk/moat에 절대 포함 금지.

🔑 경쟁사 테스트 (가장 중요한 필터):
"이 문장을 경쟁사에 그대로 붙여넣어도 성립하는가?"
→ 성립하면 neutral_facts로만. growth/risk/moat 불허.
→ 성립하지 않아야만 growth/risk/moat 허용.

[빈 리스트 규칙]
기업 고유 사실이 없으면 growth_catalysts/risk_factors/moat_hints를 빈 배열([])로 반환.
일반론으로 채워 넣지 말 것. 없으면 없는 것이 맞다.

❌ 산업 일반론 예시 (growth/risk/moat 금지 → neutral_facts):
  - "조선업은 전 세계가 단일 시장을 형성하고 있다"
  - "반도체 시장이 확대되고 있다"
  - "ESG의 중요성이 높아지고 있다"
  - "친환경 수요가 증가하고 있다"
  - "고령화로 인해 의약품 수요가 증가한다"
  - "전기차 시장이 성장하고 있다"
  - "글로벌 경기가 둔화되고 있다"

✅ 기업 고유 사실 예시 (growth/risk/moat 허용):
  - "[이 기업]이 정신신경용제 시장 점유율 1위를 확보했다" (growth) ← 경쟁사에 붙여넣기 불가
  - "[이 기업]의 특정 고객사가 전체 매출의 X%를 차지한다" (risk) ← 이 기업 고유 비중
  - "[이 기업]이 해당 분야 국내 유일 cGMP 인증 생산설비를 보유한다" (moat) ← 유일
  - "[이 기업]이 원유운반선·컨테이너선 등 제품군을 확대했다" (growth) ← 이 기업 행동

❌ risk 일반론 추가 예시 (특히 많이 틀리는 패턴):
  - "원재료 가격이 상승하고 있다" → neutral_facts (업종 공통)
  - "글로벌 경기침체로 수요가 감소한다" → neutral_facts (업종 공통)
  - "환율 변동이 실적에 영향을 줄 수 있다" → neutral_facts (업종 공통)
  ✅ 올바른 risk: "[이 기업]의 상위 3개 고객이 전체 매출의 68%를 차지한다"

[분류 기준]
- growth_catalysts: 이 기업만의 성장 드라이버 (신사업/신제품 진출, 시장 점유율 확대, 핵심 제품 경쟁력 강화 등)
  ❌ 금지: 단순 사업부 매출 규모 수치 ("DX부문 매출 174조" 같은 것) — 규모가 아니라 왜 성장하는지
  ❌ 금지: 업종 전체에 해당하는 시장 환경 설명
- risk_factors: 이 기업에 명확히 부정적인 사실 (경쟁 심화, 특정 고객 집중, 규제 리스크, 핵심 수요 급감 등)
  ❌ 금지: 가격 변동(상승/하락)은 risk에 넣지 말 것. 가격 하락은 원가 절감일 수 있음
  ❌ 금지: 업종 전체에 해당하는 일반적 리스크 → neutral_facts로
- neutral_facts: 방향이 모호한 사실 또는 산업 전반에 해당하는 내용 (가격 변동, 원재료 가격, 사업부 비중, 시장 구조 변화, 산업 일반론 등)
- moat_hints: 이 기업만의 경쟁우위 근거 (특허, 독자 기술, 브랜드, 전환비용, 네트워크효과, 인증 등)
  ❌ 금지: "글로벌 기업", "종속기업 수" 같은 단순 규모 서술
  ❌ 금지: 업계 공통 기술이나 표준 인증 (모든 경쟁사도 갖춘 것)
  moat_type 분류 (각 fact에 반드시 지정):
    pricing_power   — 가격 결정력, 브랜드 프리미엄, 독점적 공급 계약, 희소성 기반 포지션
    switching_cost  — 고객 전환비용, 시스템 통합, 장기 납품 계약, 고객 커스터마이제이션
    network_barrier — 특허/기술 독점, 인증·규제 진입장벽, 네트워크 효과, 규모의 경제

[중요도 기준 - 각 항목을 아래 우선순위 순서로 정렬해서 반환]
growth: 신기술/신시장 진입 > 시장 점유율 확대 > 제품 경쟁력 강화 > 수요 회복
risk:   구조적 위협(경쟁/대체) > 규제/지정학 > 고객 집중 > 공급망 불안정
moat:   기술 독점성/특허 > 브랜드/전환비용 > 네트워크효과 > 규모의 경제

[규칙]
- 각 항목은 없으면 빈 배열
- growth/risk/neutral 중복 배치 금지
- growth/risk 최대 10개 (중요도 높은 순), neutral 최대 3개, moat 최대 5개
- ❌ 절대 금지 표현: "가능성", "전망", "예상", "~할 수 있음", "~로 보임", "예측"
  Fact는 이미 발생했거나 사업보고서에 명시된 사실만 작성

[중요도 점수 기준 (importance)]
3 = 핵심: 사업 모델 근간, 구조적 우위/위협, 재현 불가능한 경쟁우위
2 = 중요: 유의미한 성장/리스크 요인, 수치로 뒷받침된 사실
1 = 참고: 일반적 배경 정보, 업계 공통 현상, 단순 언급 수준

[출력 형식 - JSON만]
각 항목은 "fact", "source_quote", "importance" 세 필드를 가진 객체로 반환.
source_quote는 위 [원문]에서 그대로 복사한 15~50자 인용구. 재작성/요약 금지.
moat_hints만 추가로 "moat_type" 필드 필수 (pricing_power | switching_cost | network_barrier 중 하나).
{{
  "growth_catalysts": [{{"fact": "요약 사실", "source_quote": "원문 인용구 15~50자", "importance": 3}}],
  "risk_factors":     [{{"fact": "요약 사실", "source_quote": "원문 인용구 15~50자", "importance": 2}}],
  "neutral_facts":    [{{"fact": "요약 사실", "source_quote": "원문 인용구 15~50자", "importance": 1}}],
  "moat_hints":       [{{"fact": "요약 사실", "source_quote": "원문 인용구 15~50자", "importance": 3, "moat_type": "pricing_power"}}]
}}
"""


def build_interpretation_prompt(company_name: str, facts: dict) -> str:
    """Step 2: facts만 보고 투자자 관점 해석 1~2문장 생성.
    신호(signal)는 Stage A에서 이미 처리됨 — Stage C 해석에 섞지 않는다.
    재무 수치(PER/PBR/ROE 등)는 해석에 절대 포함 금지."""
    growth_catalysts = facts.get("growth_catalysts") or []
    risk_factors = facts.get("risk_factors") or []
    neutral_facts = facts.get("neutral_facts") or []
    moat_hints = facts.get("moat_hints") or []

    def _fact_text(x): return x["fact"] if isinstance(x, dict) else x
    def fmt(lst): return "\n".join(f"  - {_fact_text(x)}" for x in lst) if lst else "  - 없음"

    return f"""너는 한국 주식 투자 리서치 어시스턴트야.
아래 "{company_name}" 사업보고서에서 추출한 사실(Fact) 목록을 1문장으로 요약해줘.

[절대 규칙]
- 아래 Fact 목록에 없는 내용 절대 금지
- ❌ 금지 표현: "가능성", "전망", "예상", "부담", "신중", "주의", "~할 수 있음", "작용할", "투자자들은"
- 목적: 분석이나 평가가 아닌, 어떤 Fact들이 언급됐는지를 요약하는 것
  ✅ 올바른 형식: "스마트폰 패널 수요 회복과 전장부품 OLED 수요 증가가 언급됨."
  ❌ 잘못된 형식: "해자로 판단될 수 있음", "경쟁우위로 작용할 것으로 보임"
- 1문장만. 한국어만. 고유명사 제외 영문 금지
- moat: Fact가 없으면 "해자 근거가 사업보고서에서 확인되지 않음."으로 서술

[성장 Fact]
{fmt(growth_catalysts)}

[리스크 Fact]
{fmt(risk_factors)}

[중립 Fact (맥락 참고용, 해석에 포함 여부는 판단)]
{fmt(neutral_facts)}

[해자 후보 Fact]
{fmt(moat_hints)}

[출력 형식 - JSON만]
{{
  "growth_interpretation": "성장 Fact 기반 투자자 관점 해석 1~2문장",
  "risk_interpretation": "리스크 Fact 기반 투자자 관점 해석 1~2문장",
  "moat_interpretation": "위 두 해석에서 도출한 해자 판단 1~2문장"
}}
"""


_VALID_MOAT_TYPES = {"pricing_power", "switching_cost", "network_barrier"}


def _normalize_facts(lst: list, with_moat_type: bool = False) -> list:
    """LLM 응답을 {fact, source_quote, importance} object로 정규화.
    구 형식(string / importance 없는 object)도 수용.
    with_moat_type=True: moat_hints 전용, moat_type 필드 보존.
    """
    out = []
    for item in (lst or []):
        if isinstance(item, dict) and "fact" in item:
            fact = {
                "fact":         item["fact"],
                "source_quote": item.get("source_quote", ""),
                "importance":   int(item.get("importance") or 2),
            }
            if with_moat_type:
                mt = item.get("moat_type", "")
                if mt in _VALID_MOAT_TYPES:
                    fact["moat_type"] = mt
            out.append(fact)
        elif isinstance(item, str):
            out.append({"fact": item, "source_quote": "", "importance": 2})
    return out


_GENERIC_KEYWORDS = (
    "일반적으로", "통상적으로", "업계는", "시장에서는",
    "업종 전체", "업계 전반", "업계 공통",
    "전 세계적으로", "경쟁사도", "모든 기업이",
    "친환경 수요가", "글로벌 경기", "고령화로 인해",
    # 시장·업황 일반론 패턴 (복합어로 지정 — 단독 "업황"은 기업 맥락에서도 사용 가능)
    "업황 회복",   # "업황 회복으로 실적 개선" — 항상 업종 전체 현상
    "업황 개선",   # "업황 개선이 기대됨" — 항상 업종 전체 현상
    "업황 호조",   # "업황 호조에 따른 수혜" — 항상 업종 전체 현상
    "시장이 확대",  # "시장이 확대되고 있다" — 항상 시장 수준 일반론
    "의 수혜가 예상",  # "시장 성장의 수혜가 예상된다" — 항상 미래 예측 일반론
)


def _validate_stage_c_facts(facts: dict) -> list[str]:
    """
    growth/risk/moat에 산업 일반론 키워드 탐지.
    반환: 위반 설명 목록 (빈 리스트 = 통과).
    """
    violations = []
    for cat in ("growth_catalysts", "risk_factors", "moat_hints"):
        for item in (facts.get(cat) or []):
            text = item.get("fact", "") if isinstance(item, dict) else str(item)
            for kw in _GENERIC_KEYWORDS:
                if kw in text:
                    violations.append(f"{cat}: '{kw}'")
    return violations


def extract_stage_c_facts(company_name: str, business_overview: str) -> dict:
    """Step 1: 사업보고서 원문 → 구조화된 facts. 산업 일반론 감지 시 1회 재시도."""
    _EMPTY: dict = {"growth_catalysts": [], "risk_factors": [], "neutral_facts": [], "moat_hints": []}
    _RETRY_HINT = (
        "\n\n[재생성 경고] 이전 응답의 growth/risk/moat에 산업 일반론이 감지됐습니다."
        " 경쟁사에도 그대로 적용되는 문장은 neutral_facts로만 이동하고,"
        " growth/risk/moat에는 이 기업만의 구체적 사실만 사용하십시오."
        " 기업 고유 사실이 없으면 해당 항목을 빈 배열([])로 반환하십시오."
    )
    prompt = build_extraction_prompt(company_name, business_overview)
    facts: dict = _EMPTY.copy()
    violations: list[str] = []

    for attempt in range(2):  # 최초 1회 + 재시도 1회
        call_prompt = prompt if attempt == 0 else prompt + _RETRY_HINT
        try:
            result = call_llm(call_prompt)
            facts = {
                "growth_catalysts": _normalize_facts(result.get("growth_catalysts")),
                "risk_factors":     _normalize_facts(result.get("risk_factors")),
                "neutral_facts":    _normalize_facts(result.get("neutral_facts")),
                "moat_hints":       _normalize_facts(result.get("moat_hints"), with_moat_type=True),
            }
            violations = _validate_stage_c_facts(facts)
            if not violations:
                break
        except Exception:
            return _EMPTY

    if violations:
        print(f"  ⚠ Stage C 일반론 감지 (2회 후 유지): {violations[:3]}")
    return facts


def save_stage_c(engine, stock_code: str, llm_result: dict) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO ai_reports (stock_code, stage_c_sections)
                VALUES (:code, :sections)
                ON CONFLICT (stock_code) DO UPDATE SET
                    stage_c_sections = EXCLUDED.stage_c_sections
                """
            ),
            {
                "code": stock_code,
                "sections": json.dumps(llm_result, ensure_ascii=False),
            },
        )


def generate_stage_c_for_stock(engine, stock_code: str, verbose: bool = False) -> dict | None:
    market = get_stock_market_data(engine, stock_code)
    if market is None:
        print(f"  {stock_code}: stocks 테이블에 없음 -> 스킵")
        return None

    history = get_financial_history(engine, stock_code)
    if not history:
        print(f"  {stock_code}: 재무데이터 없음 -> 스킵")
        return None

    overview_text = get_business_overview(engine, stock_code)
    if not overview_text:
        print(f"  {stock_code}: 사업개요 원문 없음 -> 스킵")
        return None

    # Step 1: 사업보고서 원문 → facts 추출 (한 번만 호출)
    facts = extract_stage_c_facts(market["name"], overview_text)
    if verbose:
        print("\n--- Step 1: facts 후보 ---")
        print(json.dumps(facts, ensure_ascii=False, indent=2))

    # Fact Ranking: importance 점수 내림차순 정렬 후 Top N 슬라이싱
    # LLM 응답 순서가 아니라 점수 기반 → 재실행 시에도 일관된 결과
    def _rank(lst, n):
        return sorted(lst, key=lambda x: x.get("importance", 2), reverse=True)[:n]

    ranked = {
        "growth_catalysts": _rank(facts.get("growth_catalysts") or [], 5),
        "risk_factors":     _rank(facts.get("risk_factors") or [], 5),
        "neutral_facts":    _rank(facts.get("neutral_facts") or [], 3),
        "moat_hints":       _rank(facts.get("moat_hints") or [], 3),
    }
    if verbose:
        print("\n--- Step 1 → Ranking (Top 5/3 슬라이싱) ---")
        print(json.dumps(ranked, ensure_ascii=False, indent=2))

    # Step 2: Top Facts만 보고 1문장 요약 생성 (신호 제외 — Stage A에서 처리됨)
    prompt = build_interpretation_prompt(market["name"], ranked)
    interpretations = call_llm(prompt)
    if verbose:
        print("\n--- Step 2: AI 해석 ---")
        print(json.dumps(interpretations, ensure_ascii=False, indent=2))

    # ranked facts + 해석을 명시적으로 분리해서 저장
    result = {
        "growth_drivers": {
            "facts": ranked["growth_catalysts"],
            "interpretation": interpretations.get("growth_interpretation", ""),
        },
        "risks": {
            "facts": ranked["risk_factors"],
            "neutral_facts": ranked["neutral_facts"],
            "interpretation": interpretations.get("risk_interpretation", ""),
        },
        "moat": {
            "facts": ranked["moat_hints"],
            "interpretation": interpretations.get("moat_interpretation", ""),
        },
    }

    save_stage_c(engine, stock_code, result)
    return result


if __name__ == "__main__":
    import sys

    target_code = sys.argv[1] if len(sys.argv) > 1 else "005930"

    engine = get_engine()
    market = get_stock_market_data(engine, target_code)
    if not market:
        print(f"종목 없음: {target_code}")
        sys.exit(1)

    print(f"=== {market['name']} ({target_code}) ===")

    # verbose=True: 내부에서 Step 1 / Step 2 디버그 출력 (LLM 호출은 각각 1번만)
    result = generate_stage_c_for_stock(engine, target_code, verbose=True)
    if result:
        print("\n--- 최종 결과 ---")
        for key, label in [
            ("growth_drivers", "성장 동력"),
            ("risks", "리스크"),
            ("moat", "경제적 해자"),
        ]:
            section = result.get(key, {})
            print(f"\n[{label}] Fact")
            for f in section.get("facts", []):
                text = f["fact"] if isinstance(f, dict) else f
                imp  = f" [{f['importance']}]" if isinstance(f, dict) else ""
                print(f"  · {text}{imp}")
            if key == "risks":
                for f in section.get("neutral_facts", []):
                    text = f["fact"] if isinstance(f, dict) else f
                    print(f"  - {text}")
            print(f"[{label}] AI 해석")
            print(f"  {section.get('interpretation', '(없음)')}")
