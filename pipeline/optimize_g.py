"""
G-신호 비교 프레임워크: G_neutral vs G_quant vs G_AI vs G_combined

AlphaOS 핵심 질문:
  "AI가 사업보고서에서 추출한 성장 정보가 단순 재무 성장률보다 추가 Alpha를 내는가?"

테스트 구성:
  G_neutral  : G=0.5 고정 (G 정보 없는 베이스라인)
  G_quant    : 매출/영업이익 YoY 성장률 업종 percentile (순수 재무)
  G_AI       : ai_reports stage_c 기반 성장 신호 (LLM 분석)
  G_combined : (G_quant + G_AI) / 2

나머지(V/M/S)는 alpha_score_history에서 그대로 사용.
composite = V×0.30 + G×0.25 + M×0.20 + S×0.25 → z-score sigmoid

사용:
  python optimize_g.py --score-year 2023 --return-year 2024
  python optimize_g.py --score-year 2025 --return-year 2026
  python optimize_g.py --multi
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import date

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(__file__))
from batch_collect_runner import get_engine

# ─── 통계 유틸 (optimize_v.py와 동일) ────────────────────────────────────────

def _rank_vec(vals: list[float]) -> list[float]:
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n - 1 and vals[order[j + 1]] == vals[order[j]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(x: list[float], y: list[float]) -> float:
    n = len(x)
    if n < 3:
        return 0.0
    rx, ry = _rank_vec(x), _rank_vec(y)
    mx = sum(rx) / n
    my = sum(ry) / n
    num  = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dsdx = sum((rx[i] - mx) ** 2 for i in range(n))
    dsdy = sum((ry[i] - my) ** 2 for i in range(n))
    denom = (dsdx * dsdy) ** 0.5
    return num / denom if denom else 0.0


def _pvalue(ic: float, n: int) -> float:
    if n < 3 or ic == 0:
        return 1.0
    t = ic * math.sqrt(n - 2) / math.sqrt(max(1 - ic ** 2, 1e-9))
    return math.erfc(abs(t) / math.sqrt(2))


def _zscore_sigmoid(raw: list[float], k: float = 1.0) -> list[float]:
    n = len(raw)
    if n == 0:
        return []
    mu  = sum(raw) / n
    var = sum((r - mu) ** 2 for r in raw) / n
    std = var ** 0.5 or 1e-9
    return [1.0 / (1.0 + math.exp(-k * (r - mu) / std)) * 100 for r in raw]


def _long_short(pairs: list[tuple[float, float]], pct: float = 0.10) -> float:
    srt = sorted(pairs, key=lambda x: x[0])
    n = len(srt)
    k = max(1, int(n * pct))
    return sum(p[1] for p in srt[-k:]) / k - sum(p[1] for p in srt[:k]) / k


def _monotone(pairs: list[tuple[float, float]], n_dec: int = 10) -> int:
    srt  = sorted(pairs, key=lambda x: x[0])
    n    = len(srt)
    size = max(1, n // n_dec)
    avgs = [sum(p[1] for p in srt[i * size:(i + 1) * size]) / size
            for i in range(n_dec) if srt[i * size:(i + 1) * size]]
    return sum(1 for i in range(len(avgs) - 1) if avgs[i] <= avgs[i + 1])


# ─── 데이터 로드 ────────────────────────────────────────────────────────────────

def load_vms(engine, score_year: int) -> dict[str, dict]:
    """alpha_score_history에서 V/M/S/G_AI (0-100) 로드."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT code, valuation, growth, moat, safety
            FROM alpha_score_history
            WHERE year = :y
        """), {"y": score_year}).fetchall()
    return {
        r[0]: {
            "V":    (r[1] or 50) / 100,
            "G_AI": (r[2] or 50) / 100,
            "M":    (r[3] or 50) / 100,
            "S":    (r[4] or 50) / 100,
        }
        for r in rows
    }


def compute_g_quant(engine, score_year: int) -> dict[str, float]:
    """
    매출/영업이익 YoY 성장률 → 업종 percentile → G_quant (0-1).
    데이터 없는 종목은 0.5(중립) 반환.
    """
    prev_year = score_year - 1

    def load_fin(yr: int) -> dict[str, dict]:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT code, account_nm, amount, fs_type
                FROM financial_data
                WHERE year = :y
                  AND account_nm IN ('매출액', '영업이익', '영업이익(손실)')
            """), {"y": str(yr)}).fetchall()
        raw: dict = {}
        for code, acct, amount, fs_type in rows:
            nm = "op" if "영업이익" in acct else "rev"
            d  = raw.setdefault(code, {})
            existing = d.get(nm)
            if existing is None or (fs_type == "CFS" and existing[1] != "CFS"):
                d[nm] = (amount, fs_type)
        return {c: {k: v[0] for k, v in d.items()} for c, d in raw.items()}

    curr = load_fin(score_year)
    prev = load_fin(prev_year)

    # 업종 로드
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT code, sector FROM stocks WHERE code = ANY(:codes)"
        ), {"codes": list(curr.keys())}).fetchall()
    sector_map = {r[0]: (r[1] or "기타") for r in rows}

    # YoY 성장률 계산 (winsorize ±200%)
    def _yoy(curr_v, prev_v):
        if curr_v is None or prev_v is None or prev_v == 0:
            return None
        g = (curr_v - prev_v) / abs(prev_v)
        return max(-2.0, min(2.0, g))

    raw_g: dict[str, dict] = {}
    for code, cd in curr.items():
        pd_ = prev.get(code, {})
        raw_g[code] = {
            "rev_yoy": _yoy(cd.get("rev"), pd_.get("rev")),
            "op_yoy":  _yoy(cd.get("op"),  pd_.get("op")),
        }

    # 업종 내 percentile
    sectors: dict = {}
    for code in raw_g:
        sectors.setdefault(sector_map.get(code, "기타"), []).append(code)

    def _pct(own, vals):
        if own is None or not vals:
            return 50.0
        return sum(1 for v in vals if v < own) / len(vals) * 100

    result: dict[str, float] = {}
    for sector_codes in sectors.values():
        rev_vals = [raw_g[c]["rev_yoy"] for c in sector_codes
                    if raw_g[c]["rev_yoy"] is not None]
        op_vals  = [raw_g[c]["op_yoy"]  for c in sector_codes
                    if raw_g[c]["op_yoy"]  is not None]

        for code in sector_codes:
            g = raw_g[code]
            rev_p = _pct(g["rev_yoy"], rev_vals) / 100
            op_p  = _pct(g["op_yoy"],  op_vals)  / 100

            if g["rev_yoy"] is None and g["op_yoy"] is None:
                result[code] = 0.5   # 데이터 없음 → 중립
            elif g["rev_yoy"] is None:
                result[code] = op_p
            elif g["op_yoy"] is None:
                result[code] = rev_p
            else:
                result[code] = (rev_p + op_p) / 2

    return result


def load_returns(engine, return_year: int, codes: list[str]) -> dict[str, float]:
    today    = date.today()
    end_from = date(return_year, 10, 1)
    end_to   = date(return_year, 12, 31)
    if end_from > today:
        end_from = date(return_year, 4, 1)
        end_to   = today

    with engine.connect() as conn:
        starts = conn.execute(text("""
            SELECT DISTINCT ON (code) code, close
            FROM price_history
            WHERE code = ANY(:codes)
              AND date >= :s AND date <= :m
            ORDER BY code, date ASC
        """), {"codes": codes, "s": date(return_year, 1, 1),
               "m": date(return_year, 3, 31)}).fetchall()

        ends = conn.execute(text("""
            SELECT DISTINCT ON (code) code, close
            FROM price_history
            WHERE code = ANY(:codes)
              AND date >= :m AND date <= :e
            ORDER BY code, date DESC
        """), {"codes": codes, "m": end_from, "e": end_to}).fetchall()

    s_map = {r[0]: r[1] for r in starts}
    e_map = {r[0]: r[1] for r in ends}
    out   = {}
    for code in codes:
        s = s_map.get(code)
        e = e_map.get(code)
        if s and e and s > 0:
            ret = (e / s) - 1.0
            if -0.95 <= ret <= 9.5:
                out[code] = ret
    return out


# ─── G 버전별 평가 ──────────────────────────────────────────────────────────────

G_VARIANTS = {
    "G_neutral":  "G=0.5 고정 (베이스라인)",
    "G_quant":    "매출/영업이익 YoY (재무)",
    "G_AI":       "stage_c AI 신호 (LLM proxy)",
    "G_combined": "(G_quant + G_AI) / 2",
}


def evaluate_g(
    variant: str,
    vms: dict[str, dict],
    g_quant: dict[str, float],
    returns: dict[str, float],
) -> dict | None:
    raw_composite, codes_used = [], []

    for code, d in vms.items():
        v = d["V"]
        m = d["M"]
        s = d["S"]

        if variant == "G_neutral":
            g = 0.5
        elif variant == "G_quant":
            g = g_quant.get(code, 0.5)
        elif variant == "G_AI":
            g = d["G_AI"]
        elif variant == "G_combined":
            g = (g_quant.get(code, 0.5) + d["G_AI"]) / 2
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


# ─── 단일 기간 실행 ─────────────────────────────────────────────────────────────

def run_one(engine, score_year: int, return_year: int) -> list[dict]:
    print(f"  [데이터 로드] VMS from alpha_score_history {score_year}...", end="", flush=True)
    vms = load_vms(engine, score_year)
    print(f" {len(vms)}종목")

    print(f"  [G_quant] {score_year-1}→{score_year} YoY 성장률 계산...", end="", flush=True)
    g_quant = compute_g_quant(engine, score_year)
    has_data = sum(1 for v in g_quant.values() if v != 0.5)
    print(f" {has_data}/{len(g_quant)}종목 실데이터")

    print(f"  [수익률] price_history {return_year}년 로드...", end="", flush=True)
    returns = load_returns(engine, return_year, list(vms.keys()))
    print(f" {len(returns)}개")

    results = []
    for variant in G_VARIANTS:
        res = evaluate_g(variant, vms, g_quant, returns)
        if res:
            results.append(res)
            sig = "**" if res["p"] < 0.01 else ("*" if res["p"] < 0.05 else "")
            print(f"    {variant:<14} IC={res['ic']:+.4f}{sig}")

    return results


# ─── 출력 ───────────────────────────────────────────────────────────────────────

def print_table(results: list[dict], score_year: int, return_year: int,
                base_ic: float | None = None) -> None:
    base = next((r["ic"] for r in results if r["variant"] == "G_neutral"), None)
    if base_ic is not None:
        base = base_ic

    print(f"\n{'='*72}")
    print(f"  G-신호 비교  |  {score_year} Score -> {return_year} Return")
    print(f"{'-'*72}")
    print(f"  {'G 버전':<16}  {'IC':>7}  {'p':>6}  {'L-S':>7}  {'Mono':>5}  {'dIC(vs neutral)':>16}")
    print(f"  {'-'*16}  {'-'*7}  {'-'*6}  {'-'*7}  {'-'*5}  {'-'*16}")

    for r in results:
        sig  = "**" if r["p"] < 0.01 else ("*" if r["p"] < 0.05 else "  ")
        dlt  = f"{r['ic'] - base:+.4f}" if base and r["variant"] != "G_neutral" else "(기준)"
        desc = G_VARIANTS.get(r["variant"], r["variant"])
        print(f"  {r['variant']:<16}  {r['ic']:+.4f}{sig}  {r['p']:>6.3f}  {r['ls']:>+7.1%}  {r['mono']:>3}/{9}  {dlt:>8}  {desc}")

    print(f"{'='*72}")


def print_multi_table(all_results: dict, pairs: list[tuple]) -> None:
    variants = list(G_VARIANTS.keys())
    rows = []
    base_avg = None

    for v in variants:
        ics = [r["ic"] for per in pairs
               for r in all_results[per] if r["variant"] == v]
        if not ics:
            continue
        avg_ic = sum(ics) / len(ics)
        last   = next((r for r in all_results[pairs[-1]] if r["variant"] == v), {})
        rows.append({"variant": v, "avg_ic": avg_ic, "ics": ics,
                     "ls": last.get("ls", 0), "mono": last.get("mono", 0)})
        if v == "G_neutral":
            base_avg = avg_ic

    period_str = " | ".join(f"{s}->{r}" for s, r in pairs)
    print(f"\n{'='*76}")
    print(f"  G-신호 비교  |  다중 기간 평균  ({period_str})")
    print(f"{'-'*76}")
    print(f"  {'G 버전':<16}  {'avgIC':>7}", end="")
    for s, r in pairs:
        print(f"  {s}->{r}", end="")
    print(f"  {'dIC':>7}  설명")
    print(f"  {'-'*16}  {'-'*7}" + "  " + "  ".join(["-"*7]*len(pairs)) + f"  {'-'*7}  {'-'*20}")

    for row in rows:
        dlt = f"{row['avg_ic'] - base_avg:+.4f}" if base_avg and row["variant"] != "G_neutral" else "(기준)"
        ic_str = "  ".join(f"{ic:+.4f}" for ic in row["ics"])
        print(f"  {row['variant']:<16}  {row['avg_ic']:+.4f}  {ic_str}  {dlt:>7}  {G_VARIANTS[row['variant']]}")

    print(f"\n  핵심 질문: G_AI IC > G_quant IC 이면 'AI 신호가 재무 성장률 대비 추가 Alpha 제공' 확인")
    print(f"{'='*76}\n")


# ─── main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="G-신호 비교 (G_quant vs G_AI)")
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
        print(f"[{args.score_year}->{args.return_year}] G-신호 비교")
        results = run_one(engine, args.score_year, args.return_year)
        print_table(results, args.score_year, args.return_year)

    else:
        parser.print_help()
