"""
Stage E — A/B 비교 실험
Stage C only vs Stage C + Stage D (Thesis 품질 비교)

사용법:
    python ab_comparison.py              # 자동 선택 20개 (이벤트 유형 다양성 우선)
    python ab_comparison.py --codes 001470 020560 036420   # 수동 지정
    python ab_comparison.py --n 10       # 자동 선택 N개

출력:
    - 터미널: 항목별 요약 비교
    - pipeline/ab_comparison_result.json : 전체 데이터 (정량 평가 포함)

설계 원칙:
    - Stage C only는 DB에 저장하지 않음 (read-only 실험)
    - 각 항목에 대해 4가지 차원 평가
      1. 최신성    : recent_events 존재 여부
      2. 근거 구체성: dart 소스 리스크 개수
      3. 리스크 서술: 이벤트 유형이 risks에 반영됐는가
      4. 중복 여부 : recent_events 내용이 risks와 중복인가 (LLM 판단)
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from sqlalchemy import text

from generate_ai_report import call_llm, get_engine
from generate_thesis import (
    _safe_parse,
    build_reason_graph,
    build_thesis_prompt,
    compute_confidence,
    load_thesis_inputs,
)


# ─── 샘플 선택 ────────────────────────────────────────────────────────────────

def _select_samples(engine, n: int) -> list[tuple[str, str, str]]:
    """
    이벤트 유형 다양성을 최대화하는 종목 선택.
    유형별 최대 n//5개, 나머지는 high-impact 우선.
    반환: [(stock_code, name, top_event_type)]
    """
    per_type = max(2, n // 5)
    with engine.connect() as conn:
        rows = conn.execute(text("""
            WITH ranked AS (
                SELECT DISTINCT ON (a.stock_code, f->>'type')
                    a.stock_code,
                    s.name,
                    f->>'type'              AS ev_type,
                    f->>'fact'              AS fact,
                    (f->>'recency_days')::int AS recency,
                    ROW_NUMBER() OVER (
                        PARTITION BY f->>'type'
                        ORDER BY (f->>'recency_days')::int ASC
                    ) AS rn
                FROM ai_reports a
                JOIN stocks s ON s.code = a.stock_code,
                     jsonb_array_elements(a.stage_d_sections->'facts') AS f
                WHERE a.stage_d_sections IS NOT NULL
                  AND a.thesis_sections   IS NOT NULL
                  AND f->>'financial_impact' = 'high'
                  AND (f->>'recency_days')::int <= 30
            )
            SELECT stock_code, name, ev_type, fact, recency
            FROM ranked
            WHERE rn <= :per_type
            ORDER BY ev_type, recency
            LIMIT :n
        """), {"per_type": per_type, "n": n}).fetchall()

    seen = {}
    result = []
    for code, name, ev_type, fact, recency in rows:
        if code not in seen:
            seen[code] = True
            result.append((code, name, ev_type, fact, recency))
    return result


# ─── Stage C only 생성 (DB 저장 없음) ───────────────────────────────────────

def _generate_stage_c_only(engine, stock_code: str) -> dict | None:
    """Stage D를 제외하고 Thesis 생성 (in-memory, no save)."""
    name, alpha_score, stage_b, stage_c, _stage_d = load_thesis_inputs(engine, stock_code)
    if not name or (not alpha_score and not stage_b):
        return None

    reason_graph = build_reason_graph(alpha_score, stage_b, stage_c)
    confidence   = compute_confidence(reason_graph, stage_b)

    # stage_d=None → Stage D 블록 없음
    prompt = build_thesis_prompt(name, alpha_score, stage_b, stage_c, stage_d=None)
    raw    = call_llm(prompt)
    if not raw:
        return None
    thesis = _safe_parse(raw)

    result = {
        "summary":          thesis.get("summary"),
        "strengths":        thesis.get("strengths", []),
        "risks":            thesis.get("risks", []),
        "investment_point": thesis.get("investment_point"),
        "recent_events":    thesis.get("recent_events"),   # Stage C only → 항상 null 기대
        "reason_graph":     reason_graph,
        "confidence":       confidence,
        "generated_at":     datetime.utcnow().isoformat(),
        "_mode":            "stage_c_only",
    }
    return result


# ─── 정량 평가 ────────────────────────────────────────────────────────────────

def _score(thesis: dict | None) -> dict:
    if not thesis:
        return {"recent_events": False, "dart_risks": 0, "dart_strengths": 0}
    risks     = thesis.get("risks", []) or []
    strengths = thesis.get("strengths", []) or []
    re        = thesis.get("recent_events")
    return {
        "recent_events":   bool(re and str(re) not in ("null", "None", "")),
        "dart_risks":      sum(1 for r in risks     if "dart" in str(r.get("source_ref", ""))),
        "dart_strengths":  sum(1 for s in strengths if "dart" in str(s.get("source_ref", ""))),
    }


def _delta_label(a: bool | int, b: bool | int) -> str:
    if isinstance(a, bool):
        return "C+D ✅" if b and not a else ("동일" if a == b else "C ✅")
    diff = int(b) - int(a)
    if diff > 0:  return f"C+D +{diff}"
    if diff < 0:  return f"C +{-diff}"
    return "동일"


# ─── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", nargs="*", default=None)
    parser.add_argument("--n",     type=int, default=20)
    args = parser.parse_args()

    engine = get_engine()

    # 대상 종목 결정
    if args.codes:
        with engine.connect() as conn:
            samples = []
            for code in args.codes:
                row = conn.execute(text(
                    "SELECT s.name FROM stocks s WHERE s.code = :c"
                ), {"c": code}).fetchone()
                samples.append((code, row[0] if row else code, "manual", "", 0))
    else:
        raw_samples = _select_samples(engine, args.n)
        samples = [(c, n, t, f, r) for c, n, t, f, r in raw_samples]

    total = len(samples)
    print(f"\n[Stage E A/B 비교] 대상 {total}개  —  {datetime.now().strftime('%H:%M:%S')}\n")

    results = []
    ok = fail = 0

    for i, (code, name, ev_type, *_) in enumerate(samples, 1):
        print(f"[{i}/{total}] {code} {name}  [{ev_type}]")

        # Stage C only 생성 (API 호출)
        c_only = _generate_stage_c_only(engine, code)
        if not c_only:
            print(f"  ❌ Stage C only 생성 실패\n")
            fail += 1
            continue

        # Stage C+D: 기존 DB Thesis 읽기
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT thesis_sections FROM ai_reports WHERE stock_code = :c"
            ), {"c": code}).fetchone()
        cd = row[0] if row else None

        sc = _score(c_only)
        sd = _score(cd)

        re_delta    = _delta_label(sc["recent_events"],  sd["recent_events"])
        risk_delta  = _delta_label(sc["dart_risks"],     sd["dart_risks"])

        print(f"  recent_events : C={sc['recent_events']}  C+D={sd['recent_events']}  → {re_delta}")
        print(f"  dart risks    : C={sc['dart_risks']}      C+D={sd['dart_risks']}      → {risk_delta}")
        print(f"  [C   ip] {str(c_only.get('investment_point',''))[:90]}")
        print(f"  [C+D ip] {str((cd or {}).get('investment_point',''))[:90]}")
        if sd["recent_events"]:
            print(f"  [recent] {str((cd or {}).get('recent_events',''))[:100]}")
        print()

        results.append({
            "code":        code,
            "name":        name,
            "event_type":  ev_type,
            "stage_c_only": {
                "summary":          c_only.get("summary"),
                "investment_point": c_only.get("investment_point"),
                "risks":            c_only.get("risks"),
                "recent_events":    c_only.get("recent_events"),
                "score":            sc,
            },
            "stage_cd": {
                "summary":          (cd or {}).get("summary"),
                "investment_point": (cd or {}).get("investment_point"),
                "risks":            (cd or {}).get("risks"),
                "recent_events":    (cd or {}).get("recent_events"),
                "score":            sd,
            },
        })
        ok += 1
        time.sleep(0.5)

    # ─── 집계 ────────────────────────────────────────────────────────────────
    print("=" * 60)
    print(f"완료: 성공 {ok} / 실패 {fail}")

    if results:
        re_gain     = sum(1 for r in results
                          if r["stage_cd"]["score"]["recent_events"]
                          and not r["stage_c_only"]["score"]["recent_events"])
        risk_gain   = sum(1 for r in results
                          if r["stage_cd"]["score"]["dart_risks"] >
                             r["stage_c_only"]["score"]["dart_risks"])
        no_change   = sum(1 for r in results
                          if r["stage_cd"]["score"] == r["stage_c_only"]["score"])

        print(f"\n[정량 요약]")
        print(f"  recent_events 추가 효과 : {re_gain}/{ok}개  ({re_gain/ok*100:.0f}%)")
        print(f"  dart risks 증가         : {risk_gain}/{ok}개  ({risk_gain/ok*100:.0f}%)")
        print(f"  변화 없음               : {no_change}/{ok}개")

        # JSON 저장
        out_path = os.path.join(os.path.dirname(__file__), "ab_comparison_result.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({
                "generated_at": datetime.utcnow().isoformat(),
                "summary": {
                    "total": ok,
                    "recent_events_gain": re_gain,
                    "dart_risk_gain": risk_gain,
                    "no_change": no_change,
                },
                "results": results,
            }, f, ensure_ascii=False, indent=2)
        print(f"\n  상세 결과 → {out_path}")


if __name__ == "__main__":
    main()
