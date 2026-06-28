"""
Investment Thesis 합성 레이어

Stage A (V+S score) + Stage B (Peer Benchmark) + Stage C (DART Facts) → 투자 근거 통합

설계 원칙:
  - Reason Graph : 결정론적 계산 (LLM 없음 → 100% 정확)
  - 텍스트 합성  : LLM, 단 제공된 근거만 사용 (새 사실 생성 금지)
  - source_ref   : 모든 주장은 quant:/ dart: 로 추적 가능

출력 스키마 (ai_reports.thesis_sections):
{
  "summary":          "한 줄 요약",
  "strengths":        [{"point": "...", "source_ref": "quant:roe|dart:growth[0]", "evidence": "..."}],
  "risks":            [{"point": "...", "source_ref": "dart:risk[0]", "evidence": "..."}],
  "investment_point": "종합 투자 포인트",
  "reason_graph": {
    "alpha_score": 82,
    "rank_label":  "업종 내 3/45위",
    "v_score":     78,
    "s_score":     65,
    "quant_drivers": [{"factor":"ROE 상위 8%","metric":"roe","percentile":92,"value":18.5}],
    "dart_drivers":  [{"factor":"신규 라인 증설","type":"growth","importance":3}]
  },
  "generated_at": "2026-06-27T12:00:00"
}
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


# ─── DB 준비 ──────────────────────────────────────────────────────────────────

def ensure_thesis_column(engine) -> None:
    """ai_reports에 thesis_sections 컬럼이 없으면 추가."""
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE ai_reports ADD COLUMN IF NOT EXISTS thesis_sections JSONB"
        ))


# ─── Reason Graph (결정론적, LLM 없음) ──────────────────────────────────────────

def build_reason_graph(alpha_score: dict, stage_b: dict, stage_c: dict) -> dict:
    """
    Alpha Score → 왜? → 근거(정량 + DART) 구조 생성.
    순수 계산, LLM 호출 없음.

    stage_b percentile 규칙: 높을수록 좋음 (PER/PBR도 이미 역전 완료).
    """
    score     = alpha_score.get("score", 50)
    breakdown = alpha_score.get("breakdown") or {}
    rank_s    = alpha_score.get("rank_sector")
    total_s   = alpha_score.get("total_sector")

    # 정량 드라이버: percentile 상위 3개 지표
    METRICS = [
        ("roe",       "ROE"),
        ("op_margin", "영업이익률"),
        ("roa",       "ROA"),
        ("per",       "PER"),
        ("pbr",       "PBR"),
    ]
    quant_candidates = []
    for key, label in METRICS:
        m = (stage_b or {}).get(key)
        if not m or m.get("percentile") is None:
            continue
        pct     = m["percentile"]
        top_pct = 100 - pct          # percentile=92 → "상위 8%"
        quant_candidates.append({
            "factor":     f"{label} 상위 {top_pct}%",
            "metric":     key,
            "percentile": pct,
            "value":      m["value"],
            "sector_avg": m.get("sector_avg"),
        })
    quant_candidates.sort(key=lambda x: x["percentile"], reverse=True)
    quant_drivers = quant_candidates[:3]

    # DART 드라이버: growth 상위 2개 + moat 상위 1개 (importance 기준)
    dart_drivers: list[dict] = []

    growth_facts = ((stage_c or {}).get("growth_drivers") or {}).get("facts", [])
    for f in sorted(growth_facts, key=lambda x: x.get("importance", 1), reverse=True)[:2]:
        dart_drivers.append({
            "factor":     f["fact"],
            "type":       "growth",
            "importance": f.get("importance", 2),
        })

    moat_facts = ((stage_c or {}).get("moat") or {}).get("facts", [])
    for f in sorted(moat_facts, key=lambda x: x.get("importance", 1), reverse=True)[:1]:
        entry: dict = {
            "factor":     f["fact"],
            "type":       "moat",
            "importance": f.get("importance", 2),
        }
        if f.get("moat_type"):
            entry["moat_type"] = f["moat_type"]
        dart_drivers.append(entry)

    return {
        "alpha_score":   score,
        "rank_label":    f"업종 내 {rank_s}/{total_s}위" if rank_s and total_s else None,
        "v_score":       breakdown.get("valuation", 50),
        "s_score":       breakdown.get("safety", 50),
        "quant_drivers": quant_drivers,
        "dart_drivers":  dart_drivers,
    }


def compute_confidence(reason_graph: dict, stage_b: dict) -> dict:
    """
    Confidence 점수 결정론적 계산 — LLM 없음.

    기준:
      High   : 정량 >= 2개 AND DART >= 2개
      Medium : 정량 >= 1개 AND DART >= 1개  (또는 DART 전체 importance 평균 >= 2.8이면 High 승격)
      Low    : 한쪽 근거만 있거나 둘 다 없음
    """
    quant_drivers = reason_graph.get("quant_drivers", [])
    dart_drivers  = reason_graph.get("dart_drivers", [])
    quant_count   = len(quant_drivers)
    dart_count    = len(dart_drivers)

    imp_avg = (
        sum(d.get("importance", 2) for d in dart_drivers) / dart_count
        if dart_count else 0.0
    )

    peer_count = sum(
        1 for k in ("roe", "op_margin", "roa", "per", "pbr")
        if (stage_b or {}).get(k) and (stage_b or {}).get(k, {}).get("percentile") is not None
    )

    if quant_count >= 2 and dart_count >= 2:
        level = "High"
    elif quant_count >= 1 and dart_count >= 1:
        level = "High" if imp_avg >= 2.8 else "Medium"
    else:
        level = "Low"

    reasons = []
    if quant_count > 0:
        metrics = [d["metric"] for d in quant_drivers]
        reasons.append(f"정량 {quant_count}개 ({', '.join(metrics)})")
    else:
        reasons.append("정량 근거 없음")
    if dart_count > 0:
        reasons.append(f"DART {dart_count}개 (중요도 평균 {imp_avg:.1f}/3)")
    else:
        reasons.append("DART 근거 없음")
    if peer_count >= 3:
        reasons.append(f"Peer {peer_count}개 지표 비교 완료")

    caution = None
    if quant_count == 0 and dart_count == 0:
        caution = "데이터 부족"
    elif quant_count == 0:
        caution = "정량 데이터 없음 — DART 근거만 확인됨"
    elif dart_count == 0:
        caution = "정량은 우수하나 DART 사업보고서 근거 미확인"

    return {
        "level":          level,
        "quant_count":    quant_count,
        "dart_count":     dart_count,
        "peer_count":     peer_count,
        "importance_avg": round(imp_avg, 1),
        "reasons":        reasons,
        "caution":        caution,
    }


# ─── LLM 텍스트 합성 ─────────────────────────────────────────────────────────────

def _fmt_quant(stage_b: dict, alpha_score: dict) -> str:
    if not stage_b:
        return "[Peer Benchmark]\n  (데이터 없음)"

    peer_group      = stage_b.get("peer_group", "동종 업종")
    peer_count      = stage_b.get("peer_count", "?")
    peer_confidence = stage_b.get("peer_confidence", "high")

    confidence_note = ""
    if peer_confidence == "low":
        confidence_note = (
            " ⚠ 비교 대상 제한적 (3~4개사)"
            " — 업종 대표성이 낮으므로 '동일 업종 내 주요 비교 기업 기준' 표현 사용."
            " '업종 평균', '업계 전반'과 같은 단정적 표현 금지."
        )
    elif peer_confidence == "medium":
        confidence_note = " (5~9개사 기준)"

    METRIC_DEFS = [
        ("roe",       "ROE",       "%",  False),
        ("op_margin", "영업이익률", "%",  False),
        ("roa",       "ROA",       "%",  False),
        ("per",       "PER",       "x",  True),
        ("pbr",       "PBR",       "x",  True),
    ]

    # 코드 레벨 Strengths 후보 — percentile >= 70만 선별 (프롬프트 조립 전에 먼저 계산)
    # 지주회사 왜곡 방지: 영업이익률 > 50%는 별도 재무제표 기준 왜곡 가능성 → Strengths 후보 제외
    op_margin_m = stage_b.get("op_margin")
    op_margin_distorted = (
        op_margin_m is not None
        and op_margin_m.get("value") is not None
        and op_margin_m["value"] > 50
    )

    candidates = []
    excluded_notes = []
    for key, label, unit, lower_better in METRIC_DEFS:
        m = stage_b.get(key)
        if not m or m.get("percentile") is None:
            continue
        pct = m["percentile"]
        val = m["value"]
        if pct >= 70:
            top_pct = 100 - pct
            # 음수 지표는 Strengths 후보 제외 (섹터 전체 적자 시 상위 percentile이어도 misleading)
            if key in ("roe", "roa", "op_margin") and val is not None and val < 0:
                excluded_notes.append(
                    f"  ⚠ quant:{key} 제외 ({label} {val}{unit}) "
                    f"— 음수 지표는 섹터 내 상위 percentile이어도 strengths 사용 금지."
                )
                continue
            if key == "op_margin" and op_margin_distorted:
                excluded_notes.append(
                    f"  ⚠ quant:op_margin 제외 ({label} {val}{unit}) "
                    f"— 영업이익률 50% 초과, 지주회사 별도 재무 왜곡 가능성. strengths에 사용 금지."
                )
            else:
                candidates.append(f"  quant:{key}  ({label} {val}{unit}, 업종 상위 {top_pct}%)")

    # 후보가 없으면 Peer Benchmark 헤더에 "strengths 사용 불가" 명시
    no_quant = len(candidates) == 0
    peer_header = (
        f"[Peer Benchmark — {peer_group}, {peer_count}개사{confidence_note} | 참고 전용 — strengths 사용 불가]"
        if no_quant else
        f"[Peer Benchmark — {peer_group}, {peer_count}개사{confidence_note} | 참고 전용]"
    )
    lines = [peer_header]

    for key, label, unit, lower_better in METRIC_DEFS:
        m = stage_b.get(key)
        if not m or m.get("value") is None:
            continue
        val     = m["value"]
        pct     = m.get("percentile")
        avg     = m.get("sector_avg")
        note    = " (낮을수록 저평가)" if lower_better else ""
        top_pct = (100 - pct) if pct is not None else "?"
        if avg is not None:
            exceed_wrong = (
                val is not None and not lower_better and val < avg
            )
            if exceed_wrong:
                reason = "음수지표" if val < 0 else f"값({val}{unit}) < 중앙값({avg}{unit})"
                avg_str = (f"  업종 중앙값 {avg}{unit}"
                           f"  ※{reason} — summary에서 '초과' 표현 금지."
                           f" '업종 상위 {top_pct}%에 해당한다' 형식만 사용.")
            else:
                avg_str = f"  업종 중앙값 {avg}{unit}"
        else:
            avg_str = ""
        lines.append(f"  {label}: {val}{unit} → 업종 상위 {top_pct}%{avg_str}{note}")

    score   = alpha_score.get("score", "?")
    rank_s  = alpha_score.get("rank_sector")
    total_s = alpha_score.get("total_sector")
    bd      = alpha_score.get("breakdown") or {}
    rank_str = f", 업종 내 {rank_s}/{total_s}위" if rank_s and total_s else ""
    lines.append(f"\n[Alpha Score: {score}/100{rank_str}]")
    lines.append(f"  V(밸류에이션): {bd.get('valuation', '?')}/100"
                 f"   S(안전성): {bd.get('safety', '?')}/100")

    lines.append("\n[Strengths 후보 — 정량 (업종 상위 30% 이내만)]")
    if candidates:
        lines.extend(candidates)
    else:
        lines.append("  (없음 — 정량 후보 없음. strengths는 DART 중요도 3 fact만 사용할 것)")
    if excluded_notes:
        lines.extend(excluded_notes)

    return "\n".join(lines)


def _fmt_dart(stage_c: dict) -> str:
    if not stage_c:
        return "[사업보고서 분석 (Stage C)]\n  (데이터 없음)"

    lines = []

    def _section(header: str, items: list, ref_prefix: str) -> None:
        if not items:
            return
        lines.append(header)
        for i, f in enumerate(items):
            fact = f["fact"] if isinstance(f, dict) else str(f)
            imp  = f.get("importance", 2) if isinstance(f, dict) else 2
            sq   = str(f.get("source_quote", "") if isinstance(f, dict) else "").strip()
            ref  = f"{ref_prefix}[{i}]"
            sq_str = f'\n     원문: "{sq}"' if sq else ""
            lines.append(f"  {ref} (중요도{imp}) {fact}{sq_str}")

    growth_facts  = (stage_c.get("growth_drivers") or {}).get("facts", [])
    risk_facts    = (stage_c.get("risks") or {}).get("facts", [])
    moat_facts    = (stage_c.get("moat") or {}).get("facts", [])

    _section("[성장 근거 (source_ref: dart:growth[i])]", growth_facts, "dart:growth")

    # risk 카운트를 헤더에 명시 — LLM이 최소 1개 출력 규칙을 지키도록
    if risk_facts:
        lines.append(f"[리스크 근거 (source_ref: dart:risk[i]) — {len(risk_facts)}개 제공. 반드시 risks에 최소 1개 포함]")
        for i, f in enumerate(risk_facts):
            fact = f["fact"] if isinstance(f, dict) else str(f)
            imp  = f.get("importance", 2) if isinstance(f, dict) else 2
            sq   = str(f.get("source_quote", "") if isinstance(f, dict) else "").strip()
            sq_str = f'\n     원문: "{sq}"' if sq else ""
            lines.append(f"  dart:risk[{i}] (중요도{imp}) {fact}{sq_str}")
    else:
        lines.append("[리스크 근거 — 제공 없음. risks = [] 허용]")

    _section("[해자 근거 (source_ref: dart:moat[i])]",  moat_facts,   "dart:moat")

    # Stage C 1문장 요약도 context로 추가 (LLM이 요약의 방향성 참고용)
    for key, label in [
        ("growth_drivers", "성장"),
        ("risks",          "리스크"),
        ("moat",           "해자"),
    ]:
        interp = (stage_c.get(key) or {}).get("interpretation", "").strip()
        if interp:
            lines.append(f"[Stage C {label} 해석]: {interp}")

    return "\n".join(lines) if lines else "[사업보고서 분석]\n  (Fact 없음)"


def _fmt_stage_d(stage_d: dict) -> str:
    """Stage D 최근 이벤트 블록 포맷 (Thesis 프롬프트 보강용)."""
    if not stage_d:
        return ""
    facts = stage_d.get("facts", [])
    if not facts:
        return ""

    lines = [f"[최근 주요 이벤트 (Stage D) — Stage C를 보강하는 최신 정보. 기존 투자 논리 변경 금지]"]
    for i, f in enumerate(facts):
        date_str    = f.get("date", "")
        recency     = f.get("recency_days", "?")
        ev_type     = f.get("type", "other")
        fact_text   = f.get("fact", "")
        impact      = f.get("financial_impact", "low")
        imp         = f.get("importance", 2)
        ref         = f"dart:event[{i}]"

        impact_note = ""
        if impact == "high":
            impact_note = " ★재무영향高"
        elif impact == "medium":
            impact_note = " ★재무영향中"

        lines.append(
            f"  {ref} [{date_str}+{recency}일] [{ev_type}]{impact_note} (중요도{imp}) {fact_text}"
        )

    # high-impact 이벤트 강제 반영 지시
    high_impact = [f for f in facts if f.get("financial_impact") == "high"]
    if high_impact:
        titles = " / ".join(f.get("fact", "") for f in high_impact)
        lines.append(
            f"  ⚠ financial_impact=high 이벤트 {len(high_impact)}개 감지: [{titles}]"
            f" — 반드시 strengths 또는 risks 중 적합한 곳에 1개 이상 포함. 무시 금지."
        )

    # risk 타입 이벤트 별도 강조
    risk_events = [f for f in facts if f.get("type") == "risk"]
    if risk_events:
        lines.append(f"  ⚠ 위 이벤트 중 risk 유형 {len(risk_events)}개 — risks 섹션에 후보로 고려할 것 (Stage C risk와 중복 시 병합)")

    lines.append("  ※ Stage D는 최신 이벤트 컨텍스트 제공용. investment_point에 최근 이벤트를 언급할 것.")
    lines.append("  ※ recent_events 작성 규칙: '공시하였다' '결정하였다' '발표하였다' 등 사실형 문장만 사용."
                 " '영향을 미칠 것으로' '것으로 보인다' '분석된다' 등 추정·해석 표현 절대 금지.")
    return "\n".join(lines)


def build_thesis_prompt(
    company_name: str,
    alpha_score: dict,
    stage_b: dict,
    stage_c: dict,
    stage_d: dict | None = None,
) -> str:
    quant_block  = _fmt_quant(stage_b, alpha_score)
    dart_block   = _fmt_dart(stage_c)
    event_block  = _fmt_stage_d(stage_d or {})

    return f"""너는 AlphaOS 투자 리포트 합성 AI야. 아래 제공된 데이터만 사용해서 "{company_name}"의 투자 근거를 작성해줘.

[절대 규칙]
- 제공된 데이터에 없는 사실 절대 포함 금지. 항상 입력의 수치 또는 DART 원문을 직접 근거로 쓸 것.
- ❌ 미래 예측·추정·일반화 표현 절대 금지 (summary 포함 모든 필드):
  "가능성", "전망", "예상", "기대", "~할 것이다", "~로 보인다", "~으로 보인다",
  "성장할", "개선될", "높습니다"(미래 함의 시), "좋아질", "오를",
  "성장 가능성", "성장 잠재력", "주목받고 있다", "기대된다",
  "전망된다", "것으로 보인다", "판단된다", "유리할 것으로 예상된다",
  "분석된다", "것으로 분석됨", "영향을 미칠 것으로", "작용할 수 있다"
- ✅ 허용 표현: "공시하였다", "결정하였다", "발표하였다", "기재돼 있다", "해당한다", "기록됐다", "확인된다"
- ✅ 허용 표현: "확인됨", "언급됨", "기재됨", "나타남", "보고됨", "기록됨"
- 문장 구조: 사실 → 관찰. 관찰 이후 미래 해석 금지.
- 각 항목에 source_ref 필수 (quant:roe / dart:growth[0] / quant:op_margin|dart:moat[0] 형식)
- summary는 반드시 2~3문장 (80~150자). "한 줄 슬로건" 형식 금지. 정량 Peer 수치 1개 이상 + DART 사실 1개 이상 각각 포함 필수.
- strengths: [Strengths 후보 — 정량] 섹션에 있는 지표 또는 DART 중요도 3 fact만 사용. 후보에 없는 정량 지표는 strengths에 절대 포함 금지.
- strengths 금지 항목: 제도 설명(리츠 제도·법규·부동산투자회사법 등)·산업 일반론·시장 환경 설명·"일반 국민" 표현 포함 항목. → 기업 자체의 수익성·경쟁우위·기술·제품·시장 지위만 허용.
- risks: dart:risk 섹션에 1개 이상 사실이 제공된 경우, risks에 반드시 최소 1개 이상 포함. 입력이 있는데 0개 출력 절대 금지. 입력 자체가 없을 때만 risks = [] 허용.
- investment_point: "정량에서 X가 확인되고, DART에서 Y가 기재돼 있다" 형식으로 작성

[summary 작성 규칙 및 예시]
- 반드시 2~3문장으로 작성. 1문장 슬로건 형식 금지.
- 문장 1: 정량 Peer 수치 인용 (예: "영업이익률 X%로 업종 중앙값 Y%의 N배가 기록됐다.")
- 문장 2: DART 사실 인용 (예: "사업보고서에는 Z가 기재돼 있다.")
- 문장 3 (선택): 두 근거를 연결하는 관찰 (미래 해석 금지)

❌ 나쁜 예: "A사는 성장 잠재력이 크고 시장에서 주목받고 있다."
  → 입력 데이터 없는 LLM 일반화. 금지.
❌ 나쁜 예: "A사는 우수한 영업이익률과 안정적인 ROE를 기록하며 경쟁력을 입증하고 있다."
  → 수치 없는 1문장 슬로건. 금지.
❌ 나쁜 예: "영업이익률 -10.4%로 업종 중앙값 3.0%를 초과한다."
  → 음수 지표가 양수 중앙값을 '초과'하는 것은 수학적으로 불가능. 절대 금지.
❌ 나쁜 예: "ROE -15.2%로 업종 중앙값 -14.4%보다 낮고 상위 67%에 해당한다."
  → 음수끼리 비교 + 순위 병기는 혼란 초래. 둘 중 하나만 쓸 것.
✅ 좋은 예 (양수): "ROE 22.8%로 업종 중앙값 15.5%를 초과한다. 사업보고서에는 신규 라인 증설 완료가 기재돼 있다."
  → 수치 + DART 사실 2문장.
✅ 좋은 예 (음수 지표): "영업이익률 -10.4%로 업종 상위 25%에 해당한다. 사업보고서에는 수배전반 부문 매출이 증가했다고 기재돼 있다."
  → 음수일 때는 '업종 상위 N%에 해당한다'만 사용. 중앙값과 '초과/하회' 비교 금지.
✅ 좋은 예 (관찰 포함): "영업이익률 18.4%가 업종 상위 12%에 해당한다. 사업보고서에는 특수강 제품 확대와 기술연구소 투자가 기재돼 있다. 재무와 사업 근거가 모두 확인된다."
  → 수치 비교 + DART 사실 + 관찰 연결.

{quant_block}

{dart_block}

{event_block}

[출력 형식 - JSON만, 다른 텍스트 절대 금지]
{{
  "summary": "2~3문장 요약 (80~150자, 한국어). 정량 Peer 수치 1개 + DART 사실 1개 각각 반드시 포함. 1문장 슬로건 형식 금지.",
  "strengths": [
    {{
      "point":      "핵심 강점 한 문장",
      "source_ref": "quant:roe 또는 dart:growth[0] 또는 quant:op_margin|dart:moat[0]",
      "evidence":   "근거 수치 또는 DART 원문 인용구 (15~50자)"
    }}
  ],
  "risks": [
    {{
      "point":      "핵심 리스크 한 문장",
      "source_ref": "dart:risk[0] 또는 dart:event[0]",
      "evidence":   "DART 원문 인용구 또는 수치"
    }}
  ],
  "recent_events": "Stage D에 데이터가 있는 경우: 최근 주요 이벤트를 1~2문장 사실형으로 요약 ('공시하였다' '결정하였다' 형식). 추정 해석 금지. 없으면 null.",
  "investment_point": "정량에서 확인된 X와 DART에서 기재된 Y가 일치한다는 형식으로 1~2문장 (한국어, 미래 예측 금지)"
}}

strengths 2~4개.
risks: 위 [리스크 근거] 섹션의 입력 수에 따라 출력.
  - 입력 0개("[리스크 근거 — 제공 없음]" 표시) → risks = []
  - 입력 1개 이상("[리스크 근거 — N개 제공]" 표시) → risks에 반드시 최소 1개 이상. 0개 출력 절대 금지.
  - 입력 2개 이상 → 반드시 2개 이상 포함. 1개로 줄이지 말 것. 최대 3개."""


def _safe_parse(raw: dict) -> dict:
    """LLM 응답 정규화. 예상 밖 형식에 대한 방어 처리."""
    def _items(lst: list | None) -> list:
        out = []
        for item in (lst or []):
            if isinstance(item, dict) and "point" in item:
                out.append({
                    "point":      item["point"],
                    "source_ref": item.get("source_ref", "unknown"),
                    "evidence":   item.get("evidence", ""),
                })
            elif isinstance(item, str):
                out.append({"point": item, "source_ref": "unknown", "evidence": ""})
        return out

    return {
        "summary":          raw.get("summary", ""),
        "strengths":        _items(raw.get("strengths")),
        "risks":            _items(raw.get("risks")),
        "recent_events":    raw.get("recent_events") or None,
        "investment_point": raw.get("investment_point", ""),
    }


# ─── 데이터 로드 / 저장 ───────────────────────────────────────────────────────────

def load_thesis_inputs(engine, stock_code: str) -> tuple[str | None, dict, dict, dict, dict]:
    """DB에서 회사명 + alpha_score + stage_b + stage_c + stage_d 로드."""
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT s.name, r.alpha_score, r.stage_b_sections,
                   r.stage_c_sections, r.stage_d_sections
            FROM stocks s
            LEFT JOIN ai_reports r ON r.stock_code = s.code
            WHERE s.code = :code
        """), {"code": stock_code}).fetchone()

    if not row:
        return None, {}, {}, {}, {}

    return (
        row[0],
        row[1] or {},
        row[2] or {},
        row[3] or {},
        row[4] or {},
    )


def save_thesis(engine, stock_code: str, thesis: dict) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO ai_reports (stock_code, thesis_sections)
            VALUES (:code, :thesis)
            ON CONFLICT (stock_code) DO UPDATE SET
                thesis_sections = EXCLUDED.thesis_sections
        """), {
            "code":   stock_code,
            "thesis": json.dumps(thesis, ensure_ascii=False),
        })


# ─── 메인 파이프라인 ──────────────────────────────────────────────────────────────

def generate_thesis_for_stock(
    engine,
    stock_code: str,
    verbose: bool = False,
) -> dict | None:
    name, alpha_score, stage_b, stage_c, stage_d = load_thesis_inputs(engine, stock_code)

    if not name:
        print(f"  {stock_code}: stocks 테이블에 없음 → 스킵")
        return None

    if not alpha_score and not stage_b:
        print(f"  {stock_code}: alpha_score + stage_b 모두 없음 → 스킵")
        return None

    # 1. Reason Graph + Confidence (결정론적, LLM 없음)
    reason_graph = build_reason_graph(alpha_score, stage_b, stage_c)
    confidence   = compute_confidence(reason_graph, stage_b)

    # 2. LLM 텍스트 합성
    prompt = build_thesis_prompt(name, alpha_score, stage_b, stage_c, stage_d)
    if verbose:
        print(f"\n--- 프롬프트 ({len(prompt)}자) ---")
        print(prompt[:3000])
        if len(prompt) > 3000:
            print(f"  ... (생략, 총 {len(prompt)}자)")

    raw    = call_llm(prompt)
    thesis = _safe_parse(raw)

    if verbose:
        print("\n--- LLM 응답 ---")
        print(json.dumps(raw, ensure_ascii=False, indent=2))

    thesis["confidence"]   = confidence
    thesis["reason_graph"] = reason_graph
    thesis["generated_at"] = datetime.now().isoformat()

    save_thesis(engine, stock_code, thesis)
    return thesis


# ─── CLI (단일 종목 테스트) ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    target = sys.argv[1] if len(sys.argv) > 1 else "005930"

    engine = get_engine()
    ensure_thesis_column(engine)

    name, alpha, stage_b, stage_c, stage_d = load_thesis_inputs(engine, target)
    if not name:
        print(f"종목 없음: {target}")
        sys.exit(1)

    score   = alpha.get("score", "?")
    rank_s  = alpha.get("rank_sector")
    total_s = alpha.get("total_sector")
    rank_str = f"  업종 내 {rank_s}/{total_s}위" if rank_s and total_s else ""
    print(f"=== {name} ({target}) ===")
    print(f"Alpha Score: {score}{rank_str}")
    has_d = bool(stage_d and stage_d.get("fact_count", 0) > 0)
    print(f"Stage B: {'있음' if stage_b else '없음'}  Stage C: {'있음' if stage_c else '없음'}  Stage D: {'있음' if has_d else '없음'}")

    result = generate_thesis_for_stock(engine, target, verbose=True)
    if result:
        print("\n=== 최종 Investment Thesis ===")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("생성 실패 (데이터 부족)")
