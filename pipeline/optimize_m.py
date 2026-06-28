"""
M-신호 비교 및 레이어별 Factor Attribution

목적:
  "M(Moat) proxy가 현재 Alpha를 추가하는가, 아니면 G처럼 희석시키는가?"
  레이어별로 V+S -> V+S+M -> V+S+G -> V+S+G+M 순서로 IC 변화 추적.

테스트 구성:
  Baseline    : G=0.5, M=0.5  (G,M 둘 다 중립 = V+S 기준)
  +M          : G=0.5, M=proxy (M만 활성화 = Moat 단독 효과)
  +G_quant    : G=quant, M=0.5 (G만 활성화 = Growth 단독 효과)
  +G+M        : G=quant, M=proxy (둘 다 활성화)
  Curr_proxy  : G=AI_proxy, M=proxy (현재 운영 모델 = 비교 기준)

composite = V*0.30 + G*0.25 + M*0.20 + S*0.25 -> z-score sigmoid

사용:
  python optimize_m.py --score-year 2023 --return-year 2024
  python optimize_m.py --score-year 2025 --return-year 2026
  python optimize_m.py --multi
"""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(__file__))
from batch_collect_runner import get_engine
from optimize_g import (
    _spearman, _pvalue, _zscore_sigmoid,
    _long_short, _monotone,
    load_vms, compute_g_quant, load_returns,
)

# ─── 변형 정의 ────────────────────────────────────────────────────────────────

M_VARIANTS = {
    "Baseline":   "G=0.5, M=0.5  -> V+S 기준",
    "+M":         "G=0.5, M=proxy -> Moat 효과",
    "+G_quant":   "G=quant, M=0.5 -> Growth 효과",
    "+G+M":       "G=quant, M=proxy -> G+M 합산",
    "Curr_proxy": "G=AI, M=proxy  -> 현재 모델",
}


def evaluate(
    variant: str,
    vms: dict[str, dict],
    g_quant: dict[str, float],
    returns: dict[str, float],
) -> dict | None:
    raw_composite, codes_used = [], []

    for code, d in vms.items():
        v = d["V"]
        s = d["S"]

        if variant == "Baseline":
            g, m = 0.5, 0.5
        elif variant == "+M":
            g, m = 0.5, d["M"]
        elif variant == "+G_quant":
            g, m = g_quant.get(code, 0.5), 0.5
        elif variant == "+G+M":
            g, m = g_quant.get(code, 0.5), d["M"]
        elif variant == "Curr_proxy":
            g, m = d["G_AI"], d["M"]
        else:
            continue

        raw_composite.append(v * 0.30 + g * 0.25 + m * 0.20 + s * 0.25)
        codes_used.append(code)

    final = _zscore_sigmoid(raw_composite)

    pairs = [(final[i], returns[code])
             for i, code in enumerate(codes_used) if code in returns]
    if len(pairs) < 10:
        return None

    sc = [p[0] for p in pairs]
    rt = [p[1] for p in pairs]
    ic = _spearman(sc, rt)

    return {
        "variant": variant,
        "ic":    ic,
        "p":     _pvalue(ic, len(pairs)),
        "ls":    _long_short(pairs),
        "mono":  _monotone(pairs),
        "n":     len(pairs),
    }


# ─── 단일 기간 실행 ────────────────────────────────────────────────────────────

def run_one(engine, score_year: int, return_year: int) -> list[dict]:
    print(f"  [데이터 로드] VMS from alpha_score_history {score_year}...", end="", flush=True)
    vms = load_vms(engine, score_year)
    print(f" {len(vms)}종목")

    print(f"  [G_quant] {score_year-1}->{score_year} YoY 성장률...", end="", flush=True)
    g_quant = compute_g_quant(engine, score_year)
    has_data = sum(1 for v in g_quant.values() if v != 0.5)
    print(f" {has_data}/{len(g_quant)}종목 실데이터")

    print(f"  [수익률] price_history {return_year}년 로드...", end="", flush=True)
    returns = load_returns(engine, return_year, list(vms.keys()))
    print(f" {len(returns)}개")

    results = []
    for variant in M_VARIANTS:
        res = evaluate(variant, vms, g_quant, returns)
        if res:
            results.append(res)
            sig = "**" if res["p"] < 0.01 else ("*" if res["p"] < 0.05 else "")
            print(f"    {variant:<14} IC={res['ic']:+.4f}{sig}")

    return results


# ─── 출력 ─────────────────────────────────────────────────────────────────────

def _get(results: list[dict], variant: str) -> float | None:
    r = next((r for r in results if r["variant"] == variant), None)
    return r["ic"] if r else None


def print_table(results: list[dict], score_year: int, return_year: int) -> None:
    base_ic = _get(results, "Baseline")

    print(f"\n{'='*80}")
    print(f"  레이어별 Factor Attribution  |  {score_year} Score -> {return_year} Return")
    print(f"{'-'*80}")
    print(f"  {'모델':<14}  {'IC':>7}  {'p':>6}  {'L-S':>7}  {'Mono':>5}  {'dIC':>7}  설명")
    print(f"  {'-'*14}  {'-'*7}  {'-'*6}  {'-'*7}  {'-'*5}  {'-'*7}  {'-'*28}")

    for r in results:
        sig = "**" if r["p"] < 0.01 else ("*" if r["p"] < 0.05 else "  ")
        dlt = f"{r['ic'] - base_ic:+.4f}" if base_ic and r["variant"] != "Baseline" else "(기준)"
        desc = M_VARIANTS.get(r["variant"], "")
        print(f"  {r['variant']:<14}  {r['ic']:+.4f}{sig}  {r['p']:>6.3f}  {r['ls']:>+7.1%}  {r['mono']:>3}/9  {dlt:>7}  {desc}")

    if base_ic:
        print(f"\n  [Attribution 분석]")
        m_c  = (_get(results, "+M") or base_ic) - base_ic
        g_c  = (_get(results, "+G_quant") or base_ic) - base_ic
        gm_c = (_get(results, "+G+M") or base_ic) - base_ic

        def _flag(delta):
            if delta > 0.005:  return "✓ 채택 후보"
            if delta < -0.005: return "✗ 기각"
            return "- 중립 (효과 미미)"

        print(f"    M proxy   기여: {m_c:+.4f}  {_flag(m_c)}")
        print(f"    G_quant   기여: {g_c:+.4f}  {_flag(g_c)}")
        print(f"    G+M 합산  기여: {gm_c:+.4f}")

    print(f"{'='*80}")


def print_multi_table(all_results: dict, pairs: list[tuple]) -> None:
    rows = []
    base_avg = None

    for v in M_VARIANTS:
        ics = [r["ic"] for per in pairs
               for r in all_results[per] if r["variant"] == v]
        if not ics:
            continue
        avg_ic = sum(ics) / len(ics)
        rows.append({"variant": v, "avg_ic": avg_ic, "ics": ics})
        if v == "Baseline":
            base_avg = avg_ic

    period_str = " | ".join(f"{s}->{r}" for s, r in pairs)
    print(f"\n{'='*82}")
    print(f"  레이어별 Factor Attribution  |  다중 기간 평균  ({period_str})")
    print(f"{'-'*82}")
    hdr = "  " + "  ".join([f"{s}->{r}" for s, r in pairs])
    print(f"  {'모델':<14}  {'avgIC':>7}{hdr}  {'dIC':>7}  설명")
    sep = "  " + "  ".join(["-"*7]*len(pairs))
    print(f"  {'-'*14}  {'-'*7}{sep}  {'-'*7}  {'-'*28}")

    for row in rows:
        dlt = f"{row['avg_ic'] - base_avg:+.4f}" if base_avg and row["variant"] != "Baseline" else "(기준)"
        ic_str = "  ".join(f"{ic:+.4f}" for ic in row["ics"])
        desc = M_VARIANTS[row["variant"]]
        print(f"  {row['variant']:<14}  {row['avg_ic']:+.4f}  {ic_str}  {dlt:>7}  {desc}")

    if base_avg:
        baseline_avg  = next((r["avg_ic"] for r in rows if r["variant"] == "Baseline"), None)
        plus_m_avg    = next((r["avg_ic"] for r in rows if r["variant"] == "+M"), None)
        plus_g_avg    = next((r["avg_ic"] for r in rows if r["variant"] == "+G_quant"), None)
        plus_gm_avg   = next((r["avg_ic"] for r in rows if r["variant"] == "+G+M"), None)
        curr_avg      = next((r["avg_ic"] for r in rows if r["variant"] == "Curr_proxy"), None)

        def _flag(delta):
            if delta > 0.005:  return "채택 후보"
            if delta < -0.005: return "기각"
            return "중립"

        print(f"\n  [다중기간 Attribution 요약]")
        if baseline_avg and plus_m_avg:
            d = plus_m_avg - baseline_avg
            print(f"    M proxy  기여: {d:+.4f}  -> {_flag(d)}")
        if baseline_avg and plus_g_avg:
            d = plus_g_avg - baseline_avg
            print(f"    G_quant  기여: {d:+.4f}  -> {_flag(d)}")
        if baseline_avg and plus_gm_avg:
            d = plus_gm_avg - baseline_avg
            print(f"    G+M 합산 기여: {d:+.4f}")
        if baseline_avg and curr_avg:
            d = curr_avg - baseline_avg
            print(f"    현재 모델 vs 기준: {d:+.4f}  ({'G+M proxy가 도움됨' if d > 0 else 'G+M proxy가 IC 희석'})")

        print(f"\n  핵심 결론:")
        print(f"    Baseline (V+S) IC = {base_avg:+.4f}")
        if plus_m_avg:
            print(f"    M 추가시       IC = {plus_m_avg:+.4f}  (dIC={plus_m_avg-base_avg:+.4f})")
        if plus_g_avg:
            print(f"    G 추가시       IC = {plus_g_avg:+.4f}  (dIC={plus_g_avg-base_avg:+.4f})")
        print(f"    -> {'G와 M 모두 기각 -> v2.6 = V+S 기준 모델 확정' if (plus_m_avg and plus_m_avg < base_avg and plus_g_avg and plus_g_avg < base_avg) else 'G 또는 M 중 일부 채택 가능 -> 추가 검토 필요'}")

    print(f"{'='*82}\n")


# ─── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="M-신호 비교 및 레이어별 Factor Attribution")
    parser.add_argument("--score-year",  type=int)
    parser.add_argument("--return-year", type=int)
    parser.add_argument("--multi", action="store_true")
    args = parser.parse_args()

    engine = get_engine()

    if args.multi:
        with engine.connect() as conn:
            years = [r[0] for r in conn.execute(text(
                "SELECT DISTINCT year FROM alpha_score_history ORDER BY year"
            )).fetchall()]
        if not years:
            print("alpha_score_history 데이터 없음")
            sys.exit(1)

        pairs = [(sy, sy + 1) for sy in years]
        all_results: dict = {}
        for sy, ry in pairs:
            print(f"\n[{sy}->{ry}]")
            all_results[(sy, ry)] = run_one(engine, sy, ry)
            print_table(all_results[(sy, ry)], sy, ry)

        print_multi_table(all_results, pairs)

    elif args.score_year and args.return_year:
        print(f"[{args.score_year}->{args.return_year}] 레이어별 Factor Attribution")
        results = run_one(engine, args.score_year, args.return_year)
        print_table(results, args.score_year, args.return_year)

    else:
        parser.print_help()
