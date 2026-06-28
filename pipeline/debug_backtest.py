"""
Alpha90+ 포트폴리오 진단 스크립트

IC는 높은데 Alpha90+ 수익률이 낮은 원인 추적:
  1. Alpha90+ 종목 목록 + 개별 수익률 + V/S 성분 분해
  2. 점수 분포 (score 구간별 평균 수익률 → 비단조성 탐지)
  3. 업종 집중도 확인
  4. IC 독립 재계산 (방향 검증)

사용:
  python debug_backtest.py --score-year 2023 --return-year 2024
  python debug_backtest.py --score-year 2025 --return-year 2026
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


# ─── 유틸 ─────────────────────────────────────────────────────────────────────

def _rank_vec(vals):
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j < n and vals[order[j]] == vals[order[i]]:
            j += 1
        avg = (i + j - 1) / 2 + 1
        for k in range(i, j):
            ranks[order[k]] = avg
        i = j
    return ranks


def _spearman(x, y):
    n = len(x)
    rx, ry = _rank_vec(x), _rank_vec(y)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    dy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


def _pvalue(ic, n):
    if n < 3 or ic == 0:
        return 1.0
    t = ic * math.sqrt(n - 2) / math.sqrt(max(1 - ic ** 2, 1e-9))
    return math.erfc(abs(t) / math.sqrt(2))


# ─── 데이터 로드 ────────────────────────────────────────────────────────────────

def load_scores_with_breakdown(engine, score_year: int) -> dict[str, dict]:
    """alpha_score_history에서 최종 score + 성분(V/G/M/S) 로드."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT code, score, valuation, growth, moat, safety
            FROM alpha_score_history
            WHERE year = :y
        """), {"y": score_year}).fetchall()
    return {
        r[0]: {
            "score": r[1],
            "V": r[2],
            "G": r[3],
            "M": r[4],
            "S": r[5],
        }
        for r in rows
    }


def load_sector_map(engine, codes: list[str]) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT code, sector FROM stocks WHERE code = ANY(:codes)"
        ), {"codes": codes}).fetchall()
    return {r[0]: (r[1] or "기타") for r in rows}


def load_returns(engine, return_year: int, codes: list[str]) -> dict[str, float]:
    today    = date.today()
    end_from = date(return_year, 10, 1)
    end_to   = date(return_year, 12, 31)
    partial  = False
    if end_from > today:
        end_from = date(return_year, 4, 1)
        end_to   = today
        partial  = True
    if partial:
        print(f"  ※ {return_year}년 부분기간: {end_from} ~ {end_to}")

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


# ─── 진단 ─────────────────────────────────────────────────────────────────────

def run_debug(engine, score_year: int, return_year: int) -> None:
    print(f"\n{'='*72}")
    print(f"  DEBUG: {score_year} Score -> {return_year} Return")
    print(f"{'='*72}")

    print(f"\n[1] 데이터 로드...")
    sdata   = load_scores_with_breakdown(engine, score_year)
    codes   = list(sdata.keys())
    sector_map = load_sector_map(engine, codes)
    returns = load_returns(engine, return_year, codes)
    print(f"    score 데이터: {len(sdata)}종목")
    print(f"    수익률 데이터: {len(returns)}종목 (교집합 사용)")

    common = {c: (sdata[c], returns[c]) for c in sdata if c in returns}
    n = len(common)
    print(f"    교집합: {n}종목")

    # ─ 1. Score 분포 ─────────────────────────────────────────────────────────
    scores_flat = [v["score"] for v in sdata.values() if v["score"] is not None]
    print(f"\n[2] Score 분포 (alpha_score_history.score)")
    print(f"    min={min(scores_flat)}  max={max(scores_flat)}"
          f"  mean={sum(scores_flat)/len(scores_flat):.1f}")
    for thr in [90, 80, 70, 60, 50]:
        cnt = sum(1 for s in scores_flat if s >= thr)
        print(f"    score >= {thr}: {cnt}종목")

    # ─ 2. IC 독립 재계산 ───────────────────────────────────────────────────────
    sc_list = [common[c][0]["score"] for c in common]
    rt_list = [common[c][1]          for c in common]
    ic = _spearman(sc_list, rt_list)
    p  = _pvalue(ic, n)
    sig = "**" if p < 0.01 else ("*" if p < 0.05 else "")
    print(f"\n[3] IC 독립 재계산 (Spearman, n={n})")
    print(f"    IC = {ic:+.4f}{sig}  p={p:.4f}")
    print(f"    방향 확인: IC {'양수(+) = 고점수→고수익' if ic > 0 else '음수(-) = 고점수→저수익!!!'}")

    # ─ 3. 점수 구간별 평균 수익률 (비단조성 탐지) ─────────────────────────────
    print(f"\n[4] 점수 구간별 평균 수익률")
    print(f"    {'구간':<8} {'종목':>5} {'평균수익':>9}  종목 목록(상위5)")
    bands = [(90, 101), (80, 90), (70, 80), (60, 70), (50, 60),
             (40, 50), (30, 40), (20, 30), (0, 20)]
    for lo, hi in bands:
        items = [(c, d[0]["score"], d[1]) for c, d in common.items()
                 if lo <= d[0]["score"] < hi]
        if not items:
            continue
        rets = [r for _, _, r in items]
        avg  = sum(rets) / len(rets)
        items_sorted = sorted(items, key=lambda x: x[2], reverse=True)
        sample = ", ".join(f"{c}({r*100:+.0f}%)" for c, _, r in items_sorted[:5])
        print(f"    {lo}-{hi-1:<3}  {len(rets):>5}  {avg*100:>+8.1f}%  {sample}")

    # ─ 4. Alpha90+ 종목 상세 ─────────────────────────────────────────────────
    top90 = sorted(
        [(c, d[0], d[1]) for c, d in common.items() if d[0]["score"] >= 90],
        key=lambda x: x[2], reverse=True
    )
    print(f"\n[5] Alpha90+ 종목 상세 ({len(top90)}개)")
    if top90:
        print(f"    {'코드':<8} {'Score':>5}  {'V':>3} {'S':>3}  {'수익률':>8}  업종")
        print(f"    {'-'*8} {'-'*5}  {'-'*3} {'-'*3}  {'-'*8}  {'-'*16}")
        for code, bd, ret in top90:
            sector = sector_map.get(code, "?")
            print(f"    {code:<8} {bd['score']:>5}  "
                  f"{bd.get('V') or '?':>3} {bd.get('S') or '?':>3}  "
                  f"{ret*100:>+8.1f}%  {sector}")

        avg_ret = sum(r for _, _, r in top90) / len(top90)
        avg_v   = sum(d.get("V") or 50 for _, d, _ in top90) / len(top90)
        avg_s   = sum(d.get("S") or 50 for _, d, _ in top90) / len(top90)
        print(f"\n    Alpha90+ 평균: 수익률={avg_ret*100:+.1f}%  V={avg_v:.0f}  S={avg_s:.0f}")

        # 업종 집중도
        sector_ret: dict[str, list] = {}
        for code, _, ret in top90:
            sector_ret.setdefault(sector_map.get(code, "기타"), []).append(ret)
        print(f"\n    업종 분포:")
        for sec, rets in sorted(sector_ret.items(), key=lambda x: -len(x[1])):
            avg = sum(rets) / len(rets)
            print(f"      {sec}: {len(rets)}종목  avg={avg*100:+.1f}%")
    else:
        print("    Alpha90+ 종목 없음!")

    # ─ 5. Top10 vs Bottom10 구체 비교 ────────────────────────────────────────
    print(f"\n[6] 점수 상위 10 vs 하위 10 비교")
    ranked = sorted(common.items(), key=lambda x: x[1][0]["score"], reverse=True)
    top10  = ranked[:10]
    bot10  = ranked[-10:]
    top_avg = sum(d[1] for _, d in top10) / 10
    bot_avg = sum(d[1] for _, d in bot10) / 10
    print(f"    Top10  수익률 평균: {top_avg*100:+.1f}%  "
          f"(점수 {top10[0][1][0]['score']} ~ {top10[-1][1][0]['score']})")
    print(f"    Bot10  수익률 평균: {bot_avg*100:+.1f}%  "
          f"(점수 {bot10[0][1][0]['score']} ~ {bot10[-1][1][0]['score']})")
    print(f"    Top-Bot 스프레드: {(top_avg - bot_avg)*100:+.1f}%pp")
    print(f"    (IC가 양수라면 Top > Bot 이어야 함)")

    # ─ 6. S 성분 vs 수익률 상관 ────────────────────────────────────────────────
    s_list  = [d[0].get("S") or 50 for d in common.values()]
    ic_s = _spearman(s_list, rt_list)
    v_list  = [d[0].get("V") or 50 for d in common.values()]
    ic_v = _spearman(v_list, rt_list)
    print(f"\n[7] 성분별 단독 IC")
    print(f"    V 단독 IC: {ic_v:+.4f}")
    print(f"    S 단독 IC: {ic_s:+.4f}")
    print(f"    (S가 음수이면 Safety proxy가 방향 역전 → look-ahead bias 확인 필요)")

    print(f"\n{'='*72}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Alpha90+ 포트폴리오 진단")
    parser.add_argument("--score-year",  type=int, required=True)
    parser.add_argument("--return-year", type=int, required=True)
    args = parser.parse_args()

    engine = get_engine()
    run_debug(engine, args.score_year, args.return_year)
