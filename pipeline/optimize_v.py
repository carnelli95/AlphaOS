"""
V-팩터 Feature Selection 프레임워크.

기존 V 신호(ROE·ROA·OpMargin)에 후보 팩터를 조합별로 테스트하여
각 신호의 IC 기여도를 체계적으로 측정합니다.

각 조합: 선택된 신호의 업종내 percentile 단순 평균 = V_score (0-100)
         V×0.30 + G×0.25 + M×0.20 + S×0.25 → z-score sigmoid → Alpha Score

사용:
  python optimize_v.py --score-year 2023 --return-year 2024
  python optimize_v.py --score-year 2025 --return-year 2026
  python optimize_v.py --multi
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import date
from itertools import combinations

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(__file__))
from batch_collect_runner import get_engine

# ─── 팩터 정의 ─────────────────────────────────────────────────────────────────

BASE_FEATURES      = ["ROE", "ROA", "OP_M"]
CANDIDATE_FEATURES = ["AT", "CR", "RE_A"]

FEATURE_LABELS = {
    "ROE":  "ROE",
    "ROA":  "ROA",
    "OP_M": "OpMargin",
    "AT":   "AssetTurnover",
    "CR":   "CurrentRatio",
    "RE_A": "RE/Assets",
}

NEEDED_ACCOUNTS = [
    "자본총계", "자산총계", "영업이익", "매출액",
    "당기순이익", "연결당기순이익",
    "유동자산", "유동부채", "이익잉여금",
]


# ─── 통계 유틸 ─────────────────────────────────────────────────────────────────

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
    long_r  = sum(p[1] for p in srt[-k:]) / k
    short_r = sum(p[1] for p in srt[:k])  / k
    return long_r - short_r


def _monotone(pairs: list[tuple[float, float]], n_dec: int = 10) -> int:
    srt  = sorted(pairs, key=lambda x: x[0])
    n    = len(srt)
    size = n // n_dec
    avgs = [sum(p[1] for p in srt[i * size:(i + 1) * size]) / size
            for i in range(n_dec)]
    return sum(1 for i in range(n_dec - 1) if avgs[i] <= avgs[i + 1])


# ─── 데이터 로드 ────────────────────────────────────────────────────────────────

def load_extended_financials(engine, year: int) -> dict[str, dict]:
    """재무 데이터 + 신규 계정(유동자산·유동부채·이익잉여금) 포함. CFS 우선."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT code, account_nm, amount, fs_type
            FROM financial_data
            WHERE year = :year
              AND account_nm = ANY(:accounts)
        """), {"year": str(year), "accounts": NEEDED_ACCOUNTS}).fetchall()

    raw: dict = {}
    for code, account_nm, amount, fs_type in rows:
        acct = raw.setdefault(code, {})
        key  = "net_income" if "당기순이익" in account_nm else account_nm
        existing = acct.get(key)
        if existing is None or (fs_type == "CFS" and existing[1] != "CFS"):
            acct[key] = (amount, fs_type)

    return {code: {k: v[0] for k, v in acct.items()} for code, acct in raw.items()}


def load_sector_map(engine, codes: list[str]) -> dict[str, str]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT code, sector FROM stocks WHERE code = ANY(:codes)"
        ), {"codes": codes}).fetchall()
    return {r[0]: (r[1] or "기타") for r in rows}


def load_gms(engine, score_year: int) -> dict[str, dict]:
    """alpha_score_history에서 G/M/S (0-100 정수) 로드."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT code, growth, moat, safety
            FROM alpha_score_history
            WHERE year = :y
        """), {"y": score_year}).fetchall()
    return {r[0]: {"G": r[1] or 0, "M": r[2] or 0, "S": r[3] or 0} for r in rows}


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


# ─── 신호 계산 ─────────────────────────────────────────────────────────────────

def compute_raw(financials: dict) -> dict[str, dict]:
    out = {}
    for code, fin in financials.items():
        def sf(k):
            try:    return float(fin[k])
            except: return None

        eq  = sf("자본총계")
        ta  = sf("자산총계")
        ni  = sf("net_income")
        oi  = sf("영업이익")
        rev = sf("매출액")
        ca  = sf("유동자산")
        cl  = sf("유동부채")
        re  = sf("이익잉여금")

        out[code] = {
            "ROE":  (ni / eq  * 100) if (eq and eq != 0 and ni is not None) else None,
            "ROA":  (ni / ta  * 100) if (ta and ta  > 0 and ni is not None) else None,
            "OP_M": (oi / rev * 100) if (rev and rev > 0 and oi is not None) else None,
            "AT":   (rev / ta)       if (ta and ta  > 0 and rev is not None) else None,
            "CR":   (ca / cl)        if (cl and cl  > 0 and ca is not None) else None,
            "RE_A": (re / ta  * 100) if (ta and ta  > 0 and re is not None) else None,
        }
    return out


def sector_pct(
    raw: dict[str, dict],
    sector_map: dict[str, str],
    features: list[str],
) -> dict[str, dict[str, float]]:
    """업종내 percentile — None은 50(중립) 처리."""
    sectors: dict[str, list[str]] = {}
    for code in raw:
        sectors.setdefault(sector_map.get(code, "기타"), []).append(code)

    result: dict[str, dict[str, float]] = {code: {} for code in raw}

    for sector_codes in sectors.values():
        for feat in features:
            vals = [raw[c][feat] for c in sector_codes if raw[c].get(feat) is not None]
            for code in sector_codes:
                own = raw[code].get(feat)
                if own is None or not vals:
                    result[code][feat] = 50.0
                else:
                    result[code][feat] = sum(1 for v in vals if v < own) / len(vals) * 100

    return result


# ─── 단일 조합 평가 ─────────────────────────────────────────────────────────────

def evaluate(
    features: list[str],
    raw: dict[str, dict],
    sector_map: dict[str, str],
    gms: dict[str, dict],
    returns: dict[str, float],
) -> dict | None:
    pcts     = sector_pct(raw, sector_map, features)
    v_scores = {code: sum(pcts[code].get(f, 50.0) for f in features) / len(features)
                for code in pcts}

    raw_composite, codes_used = [], []
    for code, v in v_scores.items():
        gms_i = gms.get(code)
        if gms_i is None:
            continue
        g, m, s = gms_i["G"], gms_i["M"], gms_i["S"]
        raw_composite.append(v / 100 * 0.30 + g / 100 * 0.25 + m / 100 * 0.20 + s / 100 * 0.25)
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
        "features": features,
        "ic":   ic,
        "p":    _pvalue(ic, len(pairs)),
        "ls":   _long_short(pairs),
        "mono": _monotone(pairs),
        "n":    len(pairs),
    }


# ─── 전체 실행 ──────────────────────────────────────────────────────────────────

def run_all(engine, score_year: int, return_year: int) -> list[dict]:
    print(f"  재무 데이터 로드 ({score_year})...", end="", flush=True)
    financials  = load_extended_financials(engine, score_year)
    sector_map  = load_sector_map(engine, list(financials.keys()))
    gms         = load_gms(engine, score_year)
    returns     = load_returns(engine, return_year, list(financials.keys()))
    raw         = compute_raw(financials)
    print(f" {len(financials)}종목  수익률 {len(returns)}개")

    # 테스트할 feature set: Base, Base+각 1개, Base+각 2개, Base+All
    subsets = [BASE_FEATURES] + [
        BASE_FEATURES + list(combo)
        for r in range(1, len(CANDIDATE_FEATURES) + 1)
        for combo in combinations(CANDIDATE_FEATURES, r)
    ]

    results = []
    for fs in subsets:
        label = _label(fs)
        print(f"    {label:<28} ...", end="", flush=True)
        res = evaluate(fs, raw, sector_map, gms, returns)
        if res:
            results.append(res)
            print(f" IC={res['ic']:+.4f}")
        else:
            print(" 데이터 부족")

    return results


def _label(features: list[str]) -> str:
    extras = [FEATURE_LABELS[f] for f in features if f not in BASE_FEATURES]
    return "Base" + (("+" + "+".join(extras)) if extras else "")


# ─── 출력 ───────────────────────────────────────────────────────────────────────

def print_table(results: list[dict], score_year: int, return_year: int) -> None:
    base_ic = next((r["ic"] for r in results if r["features"] == BASE_FEATURES), None)
    sorted_res = sorted(results, key=lambda x: -x["ic"])

    print(f"\n{'='*76}")
    print(f"  V Feature Selection  |  {score_year} Score -> {return_year} Return")
    print(f"{'-'*76}")
    hdr = f"  {'Feature Set':<30}  {'IC':>7}  {'p':>6}  {'L-S':>7}  {'Mono':>5}  {'dIC':>7}"
    print(hdr)
    print(f"  {'-'*30}  {'-'*7}  {'-'*6}  {'-'*7}  {'-'*5}  {'-'*7}")

    for r in sorted_res:
        lab  = _label(r["features"])
        sig  = "**" if r["p"] < 0.01 else ("*" if r["p"] < 0.05 else "  ")
        dlt  = f"{r['ic'] - base_ic:+.4f}" if base_ic and r["features"] != BASE_FEATURES else "(기준)"
        print(f"  {lab:<30}  {r['ic']:+.4f}{sig}  {r['p']:>6.3f}  {r['ls']:>+7.1%}  {r['mono']:>3}/{9}  {dlt:>7}")

    print(f"{'='*76}")


def print_multi_table(all_results: dict, pairs: list[tuple]) -> None:
    fs_list = [r["features"] for r in all_results[pairs[0]]]
    base_ic = None

    rows = []
    for fs in fs_list:
        ics = [r["ic"] for per in pairs
               for r in all_results[per] if r["features"] == fs]
        if not ics:
            continue
        avg_ic = sum(ics) / len(ics)
        last   = next((r for r in all_results[pairs[-1]] if r["features"] == fs), {})
        rows.append({"features": fs, "avg_ic": avg_ic,
                     "ls": last.get("ls", 0), "mono": last.get("mono", 0),
                     "ics": ics})
        if fs == BASE_FEATURES:
            base_ic = avg_ic

    period_str = " │ ".join(f"{s}→{r}" for s, r in pairs)
    print(f"\n{'='*72}")
    print(f"  V Feature Selection  |  Multi-Period  ({period_str})")
    print(f"{'-'*72}")
    print(f"  {'Feature Set':<30}  {'avgIC':>7}  {'Last L-S':>8}  {'LastMono':>8}  {'dIC':>7}")
    print(f"  {'-'*30}  {'-'*7}  {'-'*8}  {'-'*8}  {'-'*7}")

    for r in sorted(rows, key=lambda x: -x["avg_ic"]):
        lab = _label(r["features"])
        dlt = f"{r['avg_ic'] - base_ic:+.4f}" if base_ic and r["features"] != BASE_FEATURES else "(기준)"
        print(f"  {lab:<30}  {r['avg_ic']:+.4f}  {r['ls']:>+8.1%}  {r['mono']:>5}/{9}  {dlt:>7}")

    print(f"{'='*72}\n")

    # 기간별 IC 상세
    print("  기간별 IC 상세:")
    header = f"  {'Feature Set':<30}" + "".join(f"  {s}→{r}" for s, r in pairs)
    print(header)
    for r in rows:
        row_s = f"  {_label(r['features']):<30}"
        for ic in r["ics"]:
            row_s += f"  {ic:+.4f}  "
        print(row_s)
    print()


# ─── main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="V Feature Selection 프레임워크")
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
            print(f"\n[{sy}→{ry}]")
            all_results[(sy, ry)] = run_all(engine, sy, ry)

        print_multi_table(all_results, pairs)

    elif args.score_year and args.return_year:
        print(f"[{args.score_year}→{args.return_year}] Feature 조합 테스트")
        results = run_all(engine, args.score_year, args.return_year)
        print_table(results, args.score_year, args.return_year)

    else:
        parser.print_help()
