"""
백테스트 — Alpha Score 예측력 검증 (팩터 리포트 수준).

검증 방법:
  기준연도 N의 Alpha Score → N+1년도 실제 주가 수익률 예측
  Look-ahead bias 없음: N 데이터 → N+1 결과 (시간 순서 엄수)

출력 지표:
  [구간별]  평균·중앙값·KOSPI초과·승률(vs0/vs시장)·종목수
  [10분위]  스코어 분위별 평균 수익률 (단조성 확인)
  [IC]      Spearman Rank IC, p-value, 95% CI (Fisher's z)
  [L/S]     Long-Short Spread (Top10% - Bottom10%)
  [제외]    Universe / 사용 / 제외 종목 수 명시

모드:
  단일 기간:  python backtest.py --score-year 2022 --return-year 2023
  다중 기간:  python backtest.py --multi
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import io
from datetime import date

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(__file__))

from batch_collect_runner import get_engine


# ─── 모델 명세 ────────────────────────────────────────────────────────────────

MODEL_SPEC = {
    "name":    "Alpha Score (Historical Mode)",
    "version": "v2.6",
    "signals": "주 검증: V(ROE/ROA/OpMargin/RE_Assets 업종 percentile) 단독 · 참고: V+S",
    "weights": "V+S 참고치 = V×0.55 + S×0.45, z-score sigmoid 정규화",
    "note":    "S는 현재 LLM 분석을 모든 과거 연도에 써서 미래 정보가 섞임 → 주 검증은 V만 (DECISIONS.md 2026-11-15).",
}

# 검증할 점수 열 (alpha_score_history)
SIGNALS = {
    "v":  ("valuation", "V 단독 (주 검증 모델)"),
    "vs": ("score",     "V+S (참고치 — S는 현재 LLM 분석 사용)"),
}

INDEX_CODES = {"KOSPI": "IDX_KOSPI", "KOSDAQ": "IDX_KOSDAQ"}

TIERS = [
    ("Top10%", lambda s: s >= 80),   # ~10% of universe (~80-90 stocks)
    ("70-79",  lambda s: 70 <= s < 80),
    ("60-69",  lambda s: 60 <= s < 70),
    ("50-59",  lambda s: 50 <= s < 60),
    ("<50",    lambda s: s < 50),
]


# ─── 데이터 로드 ─────────────────────────────────────────────────────────────

def load_scores(engine, score_year: int, signal: str = "v") -> dict[str, int]:
    col = SIGNALS[signal][0]   # 화이트리스트 열 이름만 사용
    with engine.connect() as conn:
        rows = conn.execute(text(
            f"SELECT code, {col} FROM alpha_score_history WHERE year = :y"
        ), {"y": score_year}).fetchall()
    if not rows:
        raise SystemExit(
            f"alpha_score_history에 {score_year}년 데이터 없음.\n"
            f"먼저 실행: python compute_alpha_score.py --year {score_year}"
        )
    return {r[0]: r[1] for r in rows if r[1] is not None}


def period_window(return_year: int, today: date | None = None) -> dict:
    """
    보유 구간 (DECISIONS.md 2026-11-15). 점수 연도 N → return_year = N+1.
      시작: N+1년 4월 1일 이후 첫 시장 거래일 (사업보고서 제출 기한 3/31 다음)
            신규 상장은 4~6월 첫 거래일
      종료: N+2년 1~3월 마지막 거래일 (12개월 보유)
      아직 N+2년 3월 말 전이면 부분 구간: 종료 = 최신 데이터
    """
    today = today or date.today()
    w = {
        "start_from": date(return_year, 4, 1),
        "start_to":   date(return_year, 6, 30),
        "end_from":   date(return_year + 1, 1, 1),
        "end_to":     date(return_year + 1, 3, 31),
        "partial":    False,
    }
    if w["end_to"] > today:
        w["partial"] = True
        w["end_to"] = today
        if w["end_from"] > today:
            w["end_from"] = w["start_from"]
    return w


def period_label(return_year: int) -> str:
    w = period_window(return_year)
    end = w["end_to"].strftime("%Y.%m.%d") if w["partial"] else f"{return_year + 1}.03"
    return f"{return_year}.04~{end}" + (" (부분)" if w["partial"] else "")


def load_annual_returns(
    engine, return_year: int, codes: list[str]
) -> dict[str, float]:
    """
    구간 수익률 — **지수 전용**(지수 행은 volume이 없다). 구간은 period_window와 같다.
    종목 수익률은 load_period_returns(상장폐지·거래정지 규칙)를 쓴다.
    start: 4~6월 첫 거래일 종가 / end: 종료 창 마지막 거래일 종가. ±95%·+950% 초과는 제외.
    """
    w = period_window(return_year)
    with engine.connect() as conn:
        starts = conn.execute(text("""
            SELECT DISTINCT ON (code) code, close
            FROM price_history
            WHERE code = ANY(:codes)
              AND date >= :s AND date <= :m
            ORDER BY code, date ASC
        """), {"codes": codes, "s": w["start_from"], "m": w["start_to"]}).fetchall()

        ends = conn.execute(text("""
            SELECT DISTINCT ON (code) code, close
            FROM price_history
            WHERE code = ANY(:codes)
              AND date >= :m AND date <= :e
            ORDER BY code, date DESC
        """), {"codes": codes, "m": w["end_from"], "e": w["end_to"]}).fetchall()

    s_map = {r[0]: r[1] for r in starts}
    e_map = {r[0]: r[1] for r in ends}

    returns = {}
    for code in codes:
        s = s_map.get(code)
        e = e_map.get(code)
        if s and e and s > 0:
            ret = (e / s) - 1.0
            if -0.95 <= ret <= 9.5:  # ±95% / +950% 초과는 데이터 이상
                returns[code] = ret
    return returns


# ─── 시점 기준 수익률 (상장폐지·거래정지 처리) ─────────────────────────────────
#
# DECISIONS.md 2026-11-13. price_history에서 거래정지일은 volume IS NULL(close=정지 직전가)이다.
#   구간: period_window (N+1년 4월 첫 거래일 → N+2년 3월 말, DECISIONS.md 2026-11-15).
#   시작: 구간 첫 시장 거래일(d0)에 거래정지 중이면 제외(살 수 없는 종목).
#         d0 행이 없으면(구간 중 신규 상장) 4~6월 첫 행을 쓴다.
#         d0 이전에 폐지된 종목은 제외.
#   종료: 구간 안에 폐지 → 폐지일까지의 마지막 실거래 종가(부실은 정리매매 마지막 날).
#         구간 끝에 정지 중 → 이후 폐지면 마지막 실거래 종가(정리매매), 거래 재개면 재개 후 첫 종가,
#                              아직 정지 중이면 제외(결과 미확정).
#   필터: +950% 초과는 모두 제외. −95% 미만은 폐지가 관련된 경우엔 실제 손실이므로 유지.

OUTLIER_HI = 9.5
OUTLIER_LO = -0.95

STATUS_LABELS = {
    "ok":                 "정상",
    "delisted":           "구간 중 폐지 (마지막 실거래 종가)",
    "halt_end_delisted":  "구간 끝 정지 → 이후 폐지 (정리매매 종가)",
    "halt_end_resumed":   "구간 끝 정지 → 거래 재개 후 첫 종가",
    "x_not_listed":       "제외: 구간 시작 전 폐지",
    "x_halt_start":       "제외: 구간 시작 시 거래정지",
    "x_no_start":         "제외: 시작가 없음",
    "x_no_end":           "제외: 종료가 없음",
    "x_halt_unresolved":  "제외: 구간 끝 정지, 아직 미해소",
    "x_outlier":          "제외: 이상값",
}
_DELISTING_STATUSES = {"delisted", "halt_end_delisted"}


def _traded(volume) -> bool:
    return volume is not None and volume > 0


def period_return(
    rows: list[tuple],           # [(date, close, volume)] 날짜 오름차순, 구간 시작~데이터 끝
    d0: date,                    # 구간 첫 시장 거래일
    start_to: date,              # 신규 상장 시작가 탐색 마지막 날 (6/30)
    end_from: date,              # 종료가 창 시작
    end_to: date,                # 종료가 창 끝 (구간 끝)
    delisting_date: date | None,
) -> tuple[float | None, str]:
    """한 종목의 구간 수익률과 처리 상태. 순수 함수(테스트 대상)."""
    if delisting_date is not None and delisting_date < d0:
        return None, "x_not_listed"

    # 시작가
    start = next((r for r in rows if r[0] == d0), None)
    if start is None:
        start = next((r for r in rows if d0 <= r[0] <= start_to), None)
    if start is None:
        return None, "x_no_start"
    if not _traded(start[2]):
        return None, "x_halt_start"
    s_date, s_close = start[0], start[1]

    after = [r for r in rows if r[0] > s_date]

    # 종료가
    if delisting_date is not None and delisting_date <= end_to:
        last = [r for r in after if r[0] <= delisting_date and _traded(r[2])]
        if not last:
            return None, "x_no_end"
        e_close, status = last[-1][1], "delisted"
    else:
        window = [r for r in after if end_from <= r[0] <= end_to]
        if not window:
            return None, "x_no_end"
        last = window[-1]
        if _traded(last[2]):
            e_close, status = last[1], "ok"
        elif delisting_date is not None:
            traded = [r for r in after if r[0] <= delisting_date and _traded(r[2])]
            if not traded:
                return None, "x_no_end"
            e_close, status = traded[-1][1], "halt_end_delisted"
        else:
            resumed = next((r for r in after if r[0] > last[0] and _traded(r[2])), None)
            if resumed is None:
                return None, "x_halt_unresolved"
            e_close, status = resumed[1], "halt_end_resumed"

    if not s_close or s_close <= 0:
        return None, "x_no_start"
    ret = e_close / s_close - 1.0
    if ret > OUTLIER_HI or (ret < OUTLIER_LO and status not in _DELISTING_STATUSES):
        return None, "x_outlier"
    return ret, status


def load_delistings(engine) -> dict[str, date]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT code, max(delisting_date) FROM delisted_stocks GROUP BY code"
        )).fetchall()
    return {r[0]: r[1] for r in rows}


def load_period_returns(
    engine, return_year: int, codes: list[str],
    delistings: dict[str, date] | None = None,
) -> tuple[dict[str, float], dict[str, str]]:
    """
    종목별 return_year 수익률 (시점 기준 규칙). 반환: (returns, status_by_code).
    returns에는 제외되지 않은 종목만 들어간다.
    """
    w = period_window(return_year)
    if delistings is None:
        delistings = load_delistings(engine)

    with engine.connect() as conn:
        d0 = conn.execute(text("""
            SELECT min(date) FROM price_history
            WHERE code = 'IDX_KOSPI' AND date >= :s
        """), {"s": w["start_from"]}).scalar()
        if d0 is None:
            raise SystemExit(f"IDX_KOSPI에 {w['start_from']} 이후 데이터 없음 — 구간 시작일을 정할 수 없음")
        px = conn.execute(text("""
            SELECT code, date, close, volume FROM price_history
            WHERE code = ANY(:codes) AND date >= :s
            ORDER BY code, date
        """), {"codes": codes, "s": w["start_from"]}).fetchall()

    by_code: dict[str, list[tuple]] = {}
    for code, d, close, vol in px:
        by_code.setdefault(code, []).append((d, close, vol))

    returns: dict[str, float] = {}
    status: dict[str, str] = {}
    for code in codes:
        ret, st = period_return(by_code.get(code, []), d0, w["start_to"],
                                w["end_from"], w["end_to"], delistings.get(code))
        status[code] = st
        if ret is not None:
            returns[code] = ret
    return returns, status


def status_counts(status: dict[str, str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for st in status.values():
        out[st] = out.get(st, 0) + 1
    return out


def load_index_return(engine, index_code: str, return_year: int) -> float | None:
    r = load_annual_returns(engine, return_year, [index_code])
    return r.get(index_code)


def available_score_years(engine) -> list[int]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT year FROM alpha_score_history ORDER BY year"
        )).fetchall()
    return [r[0] for r in rows]


# ─── 통계 함수 ────────────────────────────────────────────────────────────────

def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    return (s[n // 2 - 1] + s[n // 2]) / 2 if n % 2 == 0 else s[n // 2]


def _normal_sf(z: float) -> float:
    """P(Z > z) for Z ~ N(0,1).  math.erfc 기반."""
    return 0.5 * math.erfc(z / math.sqrt(2))


def multi_period_t(ics: list[float]) -> tuple[float, float]:
    """
    기간별 IC 평균이 0인지 t검정. t = mean / (sd / √k), p는 t분포(자유도 k−1) 양측.
    기간 수 k가 작아(4개) 정규 근사를 쓰면 p가 크게 과소 표시된다(2026-11-15 수정).
    """
    from scipy.stats import t as t_dist

    k = len(ics)
    m = sum(ics) / k
    sd = (sum((x - m) ** 2 for x in ics) / (k - 1)) ** 0.5
    t_stat = m / (sd / math.sqrt(k))
    return t_stat, float(2 * t_dist.sf(abs(t_stat), df=k - 1))


def _rank_vec(vals: list) -> list[float]:
    """동점 평균 처리 포함 Spearman 순위 벡터 (모듈 레벨 공유용)."""
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


def _spearman(x: list[float], y: list[float]) -> float:
    """Spearman ρ — rank 벡터의 Pearson 상관."""
    n = len(x)
    if n < 3:
        return 0.0
    rx, ry = _rank_vec(x), _rank_vec(y)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx  = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    dy  = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    return num / (dx * dy) if dx > 0 and dy > 0 else 0.0


def compute_ic_stats(
    scores: dict[str, int],
    returns: dict[str, float],
) -> dict | None:
    """
    IC (Spearman Rank Correlation) + 통계적 유의성.

    반환:
      ic        — Spearman ρ
      p_value   — 양측 t-검정 p값 (대표본 근사: t ~ N(0,1))
      ci_lo/hi  — 95% CI (Fisher's z-변환)
      n         — 유효 종목 수
    """
    common = [(scores[c], returns[c]) for c in scores if c in returns]
    n = len(common)
    if n < 5:
        return None

    sv, rv = [c[0] for c in common], [c[1] for c in common]
    # 동점 평균 순위의 Pearson 상관. 순위 차 공식(1-6Σd²/…)은 동점이 많으면
    # (V 점수는 0~100 정수) 정확하지 않아 2026-11-15에 바꿨다.
    ic = _spearman(sv, rv)

    # p-value (t-검정, large n → 정규 근사)
    if abs(ic) >= 1.0:
        p_value, ci_lo, ci_hi = 0.0, ic, ic
    else:
        t_stat = ic * math.sqrt(n - 2) / math.sqrt(1.0 - ic ** 2)
        p_value = 2 * _normal_sf(abs(t_stat))

        # 95% CI — Fisher's z-변환
        z_ic = math.atanh(min(max(ic, -0.9999), 0.9999))
        se   = 1 / math.sqrt(max(n - 3, 1))
        ci_lo = math.tanh(z_ic - 1.96 * se)
        ci_hi = math.tanh(z_ic + 1.96 * se)

    return {"ic": ic, "p_value": p_value, "ci_lo": ci_lo, "ci_hi": ci_hi, "n": n}


# ─── 분석 함수 ───────────────────────────────────────────────────────────────

def analyze_tiers(
    scores: dict[str, int],
    returns: dict[str, float],
    benchmark: float | None,
) -> list[dict]:
    common = {c: (scores[c], returns[c]) for c in scores if c in returns}
    results = []
    for label, pred in TIERS:
        rets = [r for s, r in common.values() if pred(s)]
        n = len(rets)
        if n == 0:
            results.append({"tier": label, "n": 0}); continue
        avg    = sum(rets) / n
        med    = _median(rets)
        excess = (avg - benchmark) if benchmark is not None else None
        win_abs = sum(1 for r in rets if r > 0) / n
        win_mkt = (sum(1 for r in rets if r > benchmark) / n) \
                  if benchmark is not None else None
        results.append({
            "tier": label, "n": n,
            "avg_ret": avg, "median_ret": med, "excess": excess,
            "win_abs": win_abs, "win_mkt": win_mkt,
        })
    return results


def analyze_deciles(
    scores: dict[str, int],
    returns: dict[str, float],
    benchmark: float | None,
    n_dec: int = 10,
) -> list[dict]:
    common = sorted(
        [(c, scores[c], returns[c]) for c in scores if c in returns],
        key=lambda x: x[1], reverse=True,
    )
    if len(common) < n_dec:
        return []
    size = len(common) // n_dec
    deciles = []
    for i in range(n_dec):
        s = i * size
        e = (i + 1) * size if i < n_dec - 1 else len(common)
        chunk = common[s:e]
        rets  = [r for _, _, r in chunk]
        scs   = [sc for _, sc, _ in chunk]
        avg   = sum(rets) / len(rets)
        deciles.append({
            "rank": i + 1, "n": len(chunk),
            "score_hi": max(scs), "score_lo": min(scs),
            "avg_ret":  avg,
            "excess":   (avg - benchmark) if benchmark is not None else None,
        })
    return deciles


def compute_long_short(
    scores: dict[str, int],
    returns: dict[str, float],
    pct: float = 0.10,
) -> dict | None:
    common = sorted(
        [(scores[c], returns[c]) for c in scores if c in returns],
        key=lambda x: x[0], reverse=True,
    )
    n = len(common)
    if n < 20:
        return None
    k = max(1, int(n * pct))
    top = [r for _, r in common[:k]]
    bot = [r for _, r in common[-k:]]
    return {
        "long":   sum(top) / len(top),
        "short":  sum(bot) / len(bot),
        "spread": sum(top) / len(top) - sum(bot) / len(bot),
        "k": k, "pct": pct,
    }


# ─── Safety 필터 분석 ────────────────────────────────────────────────────────

def analyze_safety_filter(
    engine, score_year: int, returns: dict[str, float]
) -> dict | None:
    """
    Safety 팩터가 IC(예측력)보다 하방 리스크 필터로 기여하는지 분석.

    Safety가 낮은 IC를 보여도 다음 역할을 할 수 있음:
      - 큰 손실 방어 (최대 손실 비교)
      - 변동성 억제 (표준편차 비교)
      - 하위 꼬리 방어 (하위 10% 수익률 비교)

    High S(≥70) vs Low S(≤30) 두 그룹 비교로 측정.
    """
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT code, safety FROM alpha_score_history WHERE year = :y
        """), {"y": score_year}).fetchall()

    safety = {r[0]: r[1] for r in rows if r[1] is not None}

    def _group_stats(group_returns: list[float]) -> dict | None:
        if not group_returns:
            return None
        n    = len(group_returns)
        avg  = sum(group_returns) / n
        var  = sum((v - avg) ** 2 for v in group_returns) / n
        std  = var ** 0.5
        s    = sorted(group_returns)
        p10  = s[max(0, int(n * 0.10) - 1)]    # 하위 10% (10th percentile)
        worst = s[0]
        pct_loss = sum(1 for v in group_returns if v < 0) / n
        return {"n": n, "avg": avg, "std": std,
                "p10": p10, "worst": worst, "pct_loss": pct_loss}

    high_rets = [returns[c] for c in returns if (safety.get(c) or 0) >= 70]
    low_rets  = [returns[c] for c in returns if (safety.get(c) or 0) <= 30]

    if not high_rets or not low_rets:
        return None

    return {
        "high_s": _group_stats(high_rets),
        "low_s":  _group_stats(low_rets),
    }


# ─── Factor Drift (다중 기간용) ───────────────────────────────────────────────

def load_factor_means(engine, year: int) -> dict | None:
    """alpha_score_history에서 특정 연도의 V/G/M/S 평균 분포 로드."""
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT
                AVG(valuation)::NUMERIC(5,1),
                AVG(growth)::NUMERIC(5,1),
                AVG(moat)::NUMERIC(5,1),
                AVG(safety)::NUMERIC(5,1),
                STDDEV(valuation)::NUMERIC(5,1),
                STDDEV(growth)::NUMERIC(5,1),
                STDDEV(moat)::NUMERIC(5,1),
                STDDEV(safety)::NUMERIC(5,1),
                COUNT(*)
            FROM alpha_score_history WHERE year = :y
        """), {"y": year}).fetchone()
    if row is None or row[8] == 0:
        return None
    return {
        "year": year,
        "mean": {"V": float(row[0] or 0), "G": float(row[1] or 0),
                 "M": float(row[2] or 0), "S": float(row[3] or 0)},
        "std":  {"V": float(row[4] or 0), "G": float(row[5] or 0),
                 "M": float(row[6] or 0), "S": float(row[7] or 0)},
        "n":    int(row[8]),
    }


# ─── Factor Attribution ──────────────────────────────────────────────────────

# 복합 점수 가중치 — v2.6: V×0.55 + S×0.45. G/M은 Research Layer (합성 점수 미포함).
# Ablation에서 G/M을 제거해도 IC 변화=0 이어야 정상 (모델에 없으므로).
_WEIGHTS = {"V": 0.55, "G": 0.00, "M": 0.00, "S": 0.45}
_DIM_LABELS = {
    "V": "V (Valuation  0.55)",
    "G": "G (Growth     0.00, Research)",
    "M": "M (Moat       0.00, Research)",
    "S": "S (Safety     0.45)",
}


def compute_factor_attribution(
    engine, score_year: int, returns: dict[str, float]
) -> dict | None:
    """
    Factor Attribution:

    1. 단독 IC — 신호 하나만 사용했을 때의 IC
       IC(V→수익률), IC(G→수익률), IC(M→수익률), IC(S→수익률)

    2. Ablation IC — 신호 하나를 제거한 복합 점수의 IC
       신호가 없을 때 IC가 크게 떨어지면 → 해당 신호가 주요 기여

    Spearman IC는 순위만 보므로, ablation 복합 점수는 절대값보다
    상대 순위가 중요 — 원래 가중치 유지하고 해당 신호 항 제거.
    """
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT code, score, valuation, growth, moat, safety
            FROM alpha_score_history WHERE year = :y
        """), {"y": score_year}).fetchall()
    if not rows:
        return None

    full_scores: dict[str, int] = {}
    dim_raw: dict[str, dict[str, float]] = {"V": {}, "G": {}, "M": {}, "S": {}}

    for code, score, v, g, m, s in rows:
        if score is not None:
            full_scores[code] = score
        vals = zip(["V", "G", "M", "S"], [v, g, m, s])
        for key, val in vals:
            if val is not None:
                dim_raw[key][code] = float(val)

    # 1. 전체 복합 IC
    full_ic = compute_ic_stats(full_scores, returns)

    # 2. 단독 IC
    single: dict[str, dict | None] = {}
    for dim, scores in dim_raw.items():
        single[dim] = compute_ic_stats(scores, returns)

    # 3. Ablation: 신호 하나씩 제거한 proxy 복합 점수
    ablation: dict[str, dict | None] = {}
    for drop in _WEIGHTS:
        proxy: dict[str, float] = {}
        for code in full_scores:
            s_val = 0.0
            for dim, w in _WEIGHTS.items():
                if dim == drop:
                    continue
                d = dim_raw[dim].get(code)
                if d is not None:
                    s_val += d * w
            proxy[code] = s_val
        ablation[drop] = compute_ic_stats(proxy, returns)

    # 4. Factor Correlation Matrix (Spearman)
    dim_keys = ["V", "G", "M", "S"]
    # 공통 종목만 사용 (4개 dim 모두 값 있는 종목)
    common_codes = [
        c for c in full_scores
        if all(c in dim_raw[d] for d in dim_keys)
    ]
    corr: dict[tuple, float] = {}
    if len(common_codes) >= 5:
        for i, d1 in enumerate(dim_keys):
            for d2 in dim_keys[i:]:
                x = [dim_raw[d1][c] for c in common_codes]
                y = [dim_raw[d2][c] for c in common_codes]
                rho = 1.0 if d1 == d2 else _spearman(x, y)
                corr[(d1, d2)] = rho
                corr[(d2, d1)] = rho

    return {
        "full": full_ic, "single": single, "ablation": ablation,
        "corr": corr, "corr_n": len(common_codes),
    }


# ─── 단일 기간 요약 (다중 기간 모드용) ───────────────────────────────────────

def collect_year_summary(
    engine, score_year: int, return_year: int,
    exclude_delisted: bool = False,
    delistings: dict[str, date] | None = None,
    signal: str = "v",
) -> dict | None:
    """
    단일 기간 분석 결과를 compact dict로 반환 (인쇄 없음).
    exclude_delisted=True: delisted_stocks에 있는 종목(현재까지 폐지된 종목)을 모두 빼고
    계산한다 — 예전 "현재 상장 종목만" 방식(생존 편향)과 비교하기 위한 민감도 버전.
    """
    try:
        scores = load_scores(engine, score_year, signal)
    except SystemExit:
        return None
    if delistings is None:
        delistings = load_delistings(engine)
    if exclude_delisted:
        scores = {c: v for c, v in scores.items() if c not in delistings}

    codes   = list(scores.keys())
    returns, status = load_period_returns(engine, return_year, codes, delistings)
    if len(returns) < 10:
        return None

    kospi_ret  = load_index_return(engine, INDEX_CODES["KOSPI"],  return_year)
    kosdaq_ret = load_index_return(engine, INDEX_CODES["KOSDAQ"], return_year)
    ic_stats   = compute_ic_stats(scores, returns)
    benchmark  = kospi_ret

    # 순위 기준 구간 (점수 척도가 V와 V+S에서 달라 고정 임계값 대신 순위 사용)
    ranked = [returns[c] for c in sorted((c for c in scores if c in returns),
                                         key=lambda c: scores[c], reverse=True)]
    def _slice_avg(lo: float, hi: float):
        n = len(ranked)
        part = ranked[int(n * lo):max(int(n * hi), int(n * lo) + 1)]
        return (sum(part) / len(part) if part else None), len(part)

    a90_avg, a90_n = _slice_avg(0.0, 0.1)   # 상위 10%
    a80_avg, a80_n = _slice_avg(0.1, 0.3)   # 10~30%
    excess90 = (a90_avg - benchmark) if (a90_avg is not None and benchmark is not None) else None

    factor_means = load_factor_means(engine, score_year)

    return {
        "signal":       signal,
        "score_year":   score_year,
        "return_year":  return_year,
        "n_universe":   len(scores),
        "n_used":       len(returns),
        "status":       status_counts(status),
        "n_delist_used": sum(1 for c in returns if status[c] in _DELISTING_STATUSES),
        "kospi_ret":    kospi_ret,
        "kosdaq_ret":   kosdaq_ret,
        "alpha90_avg":  a90_avg,
        "alpha90_n":    a90_n,
        "alpha80_avg":  a80_avg,
        "alpha80_n":    a80_n,
        "excess90":     excess90,
        "ic":           ic_stats["ic"]      if ic_stats else None,
        "p_value":      ic_stats["p_value"] if ic_stats else None,
        "ic_ci_lo":     ic_stats["ci_lo"]   if ic_stats else None,
        "ic_ci_hi":     ic_stats["ci_hi"]   if ic_stats else None,
        "factor_means": factor_means,
    }


# ─── 출력 헬퍼 ───────────────────────────────────────────────────────────────

def _pf(v, dec=1, w=9):
    if v is None: return "N/A".rjust(w)
    return f"{'+' if v>=0 else ''}{v*100:.{dec}f}%".rjust(w)


def _wf(v, w=8):
    if v is None: return "N/A".rjust(w)
    return f"{v*100:.0f}%".rjust(w)


# ─── 단일 기간 상세 리포트 ────────────────────────────────────────────────────

def print_report(
    score_year: int, return_year: int,
    tiers: list[dict], deciles: list[dict],
    ls: dict | None, ic_stats: dict | None,
    kospi_ret: float | None, kosdaq_ret: float | None,
    n_universe: int, n_used: int,
    attribution: dict | None = None,
    safety_filter: dict | None = None,
) -> None:
    n_excl = n_universe - n_used
    benchmark = kospi_ret
    W = 72

    print(f"\n{'═'*W}")
    print(f"  AlphaOS 백테스트  │  Score {score_year}년  →  Return {return_year}년")
    print(f"{'─'*W}")
    print(f"  모델:  {MODEL_SPEC['name']} ({MODEL_SPEC['version']})")
    print(f"  신호:  {MODEL_SPEC['signals']}")
    print(f"  가중:  {MODEL_SPEC['weights']}")
    print(f"  주의:  {MODEL_SPEC['note']}")
    print(f"{'─'*W}")

    bm = []
    if kospi_ret  is not None: bm.append(f"KOSPI {kospi_ret*100:+.1f}%")
    if kosdaq_ret is not None: bm.append(f"KOSDAQ {kosdaq_ret*100:+.1f}%")
    print(f"  벤치마크: {' │ '.join(bm) if bm else 'N/A (IDX_KOSPI 없음 — 지수 수집 필요)'}")
    print(f"  Universe: {n_universe}개  │  사용: {n_used}개  │  "
          f"제외: {n_excl}개 (사유는 [2/5] 처리 상태 참조)")
    print(f"{'═'*W}")

    # ── 구간별 ──
    print(f"\n  [Alpha 구간별 분석]")
    hdr = (f"  {'구간':<8} {'종목':>5} {'평균수익':>9} {'중앙값':>9}"
           f" {'KOSPI초과':>10} {'승률≥0':>7} {'승률>시장':>9}")
    print(hdr)
    print(f"  {'─'*8} {'─'*5} {'─'*9} {'─'*9} {'─'*10} {'─'*7} {'─'*9}")
    for r in tiers:
        if r["n"] == 0:
            print(f"  {r['tier']:<8} {'0':>5}"
                  + "  ─" * 5); continue
        print(
            f"  {r['tier']:<8} {r['n']:>5}"
            f" {_pf(r['avg_ret'])}"
            f" {_pf(r['median_ret'])}"
            f" {_pf(r['excess'], w=10)}"
            f" {_wf(r['win_abs'], w=7)}"
            f" {_wf(r['win_mkt'], w=9)}"
        )

    # ── 10분위 ──
    if deciles:
        print(f"\n  [10분위(Decile) 분석]")
        print(f"  {'분위':<6} {'종목':>5} {'점수범위':>9} {'평균수익':>9} {'KOSPI초과':>10}")
        print(f"  {'─'*6} {'─'*5} {'─'*9} {'─'*9} {'─'*10}")
        for d in deciles:
            lbl = f"Top{d['rank']}" if d["rank"] <= 5 else f"Bot{11 - d['rank']}"
            sc_r = f"{d['score_lo']}-{d['score_hi']}"
            print(f"  {lbl:<6} {d['n']:>5} {sc_r:>9}"
                  f" {_pf(d['avg_ret'])} {_pf(d['excess'], w=10)}")
        mono = sum(1 for i in range(1, len(deciles))
                   if deciles[i-1]["avg_ret"] >= deciles[i]["avg_ret"])
        tot  = len(deciles) - 1
        print(f"  → 단조성: {mono}/{tot}  "
              f"({'✓ 양호' if mono >= 7 else '△ 불규칙' if mono >= 5 else '✗ 비단조'})")

    # ── Long-Short ──
    if ls:
        pct_s = f"{ls['pct']*100:.0f}%"
        print(f"\n  [Long-Short (Top{pct_s} vs Bot{pct_s},  각 {ls['k']}종목)]")
        print(f"  Long  : {ls['long']*100:+.1f}%")
        print(f"  Short : {ls['short']*100:+.1f}%")
        print(f"  Spread: {ls['spread']*100:+.1f}%")

    # ── IC + 통계적 유의성 ──
    print(f"\n  [IC  (Spearman Rank Correlation = Rank IC)]")
    if ic_stats:
        ic  = ic_stats["ic"]
        pv  = ic_stats["p_value"]
        clo = ic_stats["ci_lo"]
        chi = ic_stats["ci_hi"]
        n   = ic_stats["n"]

        sig = ("★★ 강한 예측력" if abs(ic) >= 0.10 else
               "★ 유의미한 예측력" if abs(ic) >= 0.05 else
               "△ 약한 예측력" if abs(ic) >= 0.02 else "✗ 예측력 미약")
        pv_flag = "**" if pv < 0.01 else "*" if pv < 0.05 else ""

        print(f"  IC         = {ic:+.4f}  ({sig})")
        print(f"  p-value    = {pv:.4f} {pv_flag}  ({'유의 (α=0.05)' if pv < 0.05 else '비유의'})")
        print(f"  95% CI     = [{clo:+.4f},  {chi:+.4f}]")
        print(f"  종목 수    = {n}개")
        print(f"  (p<0.05=유의*, p<0.01=고유의**,  |IC|≥0.05=실용적 유의)")
    else:
        print(f"  IC = N/A (종목 수 부족)")

    # ── Safety 필터 효과 ──
    if safety_filter:
        hi = safety_filter.get("high_s")
        lo = safety_filter.get("low_s")
        if hi and lo:
            print(f"\n  [Safety 팩터 — 하방 리스크 필터 효과]")
            print(f"  (High S≥70 vs Low S≤30,  IC보다 손실 방어력으로 평가)")
            print(f"\n  {'그룹':<14} {'종목':>5} {'평균수익':>9} {'변동성':>8}"
                  f" {'하위10%':>8} {'최대손실':>9} {'손실비율':>8}")
            print(f"  {'─'*14} {'─'*5} {'─'*9} {'─'*8} {'─'*8} {'─'*9} {'─'*8}")
            for lbl, g in [("High S (≥70)", hi), ("Low  S (≤30)", lo)]:
                print(
                    f"  {lbl:<14} {g['n']:>5}"
                    f" {_pf(g['avg'])}"
                    f" {g['std']*100:>7.1f}%"
                    f" {_pf(g['p10'], w=8)}"
                    f" {_pf(g['worst'], w=9)}"
                    f" {g['pct_loss']*100:>7.0f}%"
                )
            std_diff = (lo["std"] - hi["std"]) * 100
            loss_diff = (lo["worst"] - hi["worst"]) * 100
            print(f"\n  → 변동성: High S가 {std_diff:+.1f}%p 낮음  │"
                  f"  최대손실: High S가 {loss_diff:+.1f}%p 방어")
            if std_diff > 3 or loss_diff > 5:
                print(f"     ✓ Safety는 평균 수익 예측보다 하방 리스크 억제에 유효")
            else:
                print(f"     △ Safety 필터 효과 미미 — 팩터 재설계 검토")

    # ── Factor Attribution ──
    if attribution and attribution.get("full"):
        full_ic_val = attribution["full"]["ic"]
        print(f"\n  [Factor Attribution  —  단독 IC vs Ablation]")
        print(f"  전체 복합 IC: {full_ic_val:+.4f}")
        print(f"\n  {'신호':<24} {'단독 IC':>9} {'제거 시 IC':>11} {'변화':>8}  기여도")
        print(f"  {'─'*24} {'─'*9} {'─'*11} {'─'*8}  {'─'*10}")

        # ablation 변화 기준으로 내림차순 정렬 (기여 큰 순)
        dims_sorted = sorted(
            _WEIGHTS.keys(),
            key=lambda d: -(
                full_ic_val - (attribution["ablation"][d]["ic"]
                               if attribution["ablation"][d] else full_ic_val)
            ),
        )
        for dim in dims_sorted:
            s_ic  = attribution["single"].get(dim)
            a_ic  = attribution["ablation"].get(dim)
            s_val = s_ic["ic"] if s_ic else None
            a_val = a_ic["ic"] if a_ic else None
            delta = (full_ic_val - a_val) if a_val is not None else None

            s_str = f"{s_val:+.4f}" if s_val is not None else "   N/A"
            a_str = f"{a_val:+.4f}" if a_val is not None else "   N/A"
            d_str = f"{delta:+.4f}" if delta is not None else "   N/A"

            contrib = (
                "★★ 주요 기여" if delta is not None and delta >= 0.020 else
                "★ 기여"       if delta is not None and delta >= 0.008 else
                "△ 소기여"     if delta is not None and delta >= 0.003 else
                "─ 미미"
            )
            print(f"  {_DIM_LABELS[dim]:<24} {s_str:>9} {a_str:>11} {d_str:>8}  {contrib}")

        print(f"\n  해석: '변화'(제거 시 IC 하락폭)가 클수록 해당 신호가 예측력에 핵심 기여.")
        print(f"        단독 IC가 높아도 타 신호와 상관관계 높으면 ablation 변화는 작을 수 있음.")

    # ── Factor Correlation Matrix ──
    corr = attribution.get("corr") if attribution else None
    if corr:
        dims = ["V", "G", "M", "S"]
        n_corr = attribution.get("corr_n", 0)
        print(f"\n  [Factor Correlation Matrix (Spearman,  n={n_corr})]")
        print(f"  {'':>4} " + "  ".join(f"{'  '+d:>7}" for d in dims))
        for d1 in dims:
            row = f"  {d1:>4} "
            for d2 in dims:
                rho = corr.get((d1, d2), 0.0)
                row += f"  {rho:+.3f} " if d1 != d2 else f"   1.000 "
            print(row)

        # 높은 상관 쌍 자동 감지 (|r| > 0.40)
        high_pairs = [
            (d1, d2, corr[(d1, d2)])
            for i, d1 in enumerate(dims)
            for d2 in dims[i+1:]
            if abs(corr.get((d1, d2), 0)) > 0.40
        ]
        if high_pairs:
            high_pairs.sort(key=lambda x: abs(x[2]), reverse=True)
            notes = [f"{d1}↔{d2} ({rho:+.3f})" for d1, d2, rho in high_pairs]
            print(f"\n  ⚠ 높은 상관(|r|>0.40): {', '.join(notes)}")
            print(f"     → 이 팩터 쌍은 유사한 정보를 담고 있어 ablation 변화가 과소평가될 수 있음.")
        else:
            print(f"\n  ✓ 모든 팩터 쌍 상관 |r|≤0.40 — 비교적 독립적인 정보 구성")

    print(f"\n{'═'*W}\n")


# ─── 다중 기간 요약 리포트 ────────────────────────────────────────────────────

def print_multi_summary(summaries: list[dict]) -> None:
    W = 78
    valid = [s for s in summaries if s is not None]
    if not valid:
        print("유효한 기간 없음."); return

    print(f"\n{'═'*W}")
    print(f"  AlphaOS 다중 기간 백테스트 요약 — {SIGNALS[valid[0]['signal']][1]}")
    print(f"{'─'*W}")
    print(f"  모델: {MODEL_SPEC['name']} ({MODEL_SPEC['version']})")
    print(f"  신호: {MODEL_SPEC['signals']}")
    print(f"  보유: 점수 연도 N → N+1년 4월 첫 거래일 매수 ~ N+2년 3월 말 (12개월)")
    for s in valid:
        print(f"    {s['score_year']}→{period_label(s['return_year'])}")
    print(f"{'─'*W}")
    hdr = (f"  {'기간':<12} {'상위10%':>9} {'KOSPI':>8}"
           f" {'초과수익':>9} {'IC':>7} {'p값':>7} {'종목(사용)':>10}")
    print(hdr)
    print(f"  {'─'*12} {'─'*9} {'─'*8} {'─'*9} {'─'*7} {'─'*7} {'─'*10}")

    for s in valid:
        period = f"{s['score_year']}→{s['return_year']}"
        a90    = _pf(s["alpha90_avg"], w=9)
        kospi  = _pf(s["kospi_ret"],   w=8)
        exc    = _pf(s["excess90"],    w=9)
        ic_s   = f"{s['ic']:+.4f}" if s["ic"] is not None else "   N/A"
        pv_s   = (f"{s['p_value']:.3f}" if s["p_value"] is not None else "   N/A")
        pv_flag = "**" if (s["p_value"] or 1) < 0.01 else \
                  "*"  if (s["p_value"] or 1) < 0.05 else " "
        used   = f"{s['n_used']}/{s['n_universe']}"
        print(f"  {period:<12} {a90} {kospi} {exc} {ic_s:>7} {pv_s:>6}{pv_flag} {used:>10}")

    # ── 평균 요약 ──
    if len(valid) >= 2:
        print(f"  {'─'*12} {'─'*9} {'─'*8} {'─'*9} {'─'*7} {'─'*7} {'─'*10}")
        ics = [s["ic"] for s in valid if s["ic"] is not None]
        kospi_list = [s["kospi_ret"] for s in valid if s["kospi_ret"] is not None]
        exc_list   = [s["excess90"] for s in valid if s["excess90"] is not None]
        a90_list   = [s["alpha90_avg"] for s in valid if s["alpha90_avg"] is not None]

        def _mavg(lst):
            return sum(lst) / len(lst) if lst else None
        def _mstd(lst):
            if len(lst) < 2: return None
            m = sum(lst) / len(lst)
            return (sum((x - m) ** 2 for x in lst) / (len(lst) - 1)) ** 0.5

        ic_mean = _mavg(ics)
        ic_std  = _mstd(ics)
        print(f"  {'평균':<12} {_pf(_mavg(a90_list), w=9)} {_pf(_mavg(kospi_list), w=8)}"
              f" {_pf(_mavg(exc_list), w=9)}"
              f" {f'{ic_mean:+.4f}' if ic_mean else '   N/A':>7}")

        if ics:
            print(f"\n  IC 통계 ({len(ics)}개 기간):")
            print(f"    평균:   {ic_mean:+.4f}" if ic_mean else "    평균: N/A")
            if ic_std:
                print(f"    표준편차: {ic_std:.4f}")
                if len(ics) >= 3:
                    t_ic, p_multi = multi_period_t(ics)
                    print(f"    t-stat (IC≠0): {t_ic:+.3f}  p={p_multi:.4f}"
                          f"  (t분포, 자유도 {len(ics) - 1}, 양측)"
                          f" {'* 5% 유의' if p_multi < 0.05 else ''}")

    # ── Feature Drift ──
    drift_data = [(s["score_year"], s.get("factor_means"))
                  for s in valid if s.get("factor_means")]
    # 마지막 return_year도 추가 (score year로 로드)
    if valid:
        last = valid[-1]
        last_ry_means = None
        # return_year의 factor means는 별도 요청 필요 — score_year만 표시
    if drift_data:
        print(f"\n  [Factor Distribution Drift]")
        print(f"  {'Score Year':<12} {'V 평균':>8} {'G 평균':>8}"
              f" {'M 평균':>8} {'S 평균':>8} {'종목수':>7}")
        print(f"  {'─'*12} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*7}")
        prev_means = None
        for yr, fm in drift_data:
            m = fm["mean"]
            print(f"  {yr:<12} {m['V']:>8.1f} {m['G']:>8.1f}"
                  f" {m['M']:>8.1f} {m['S']:>8.1f} {fm['n']:>7}")
            prev_means = m

        # 변화가 큰 팩터 감지 (첫 vs 마지막)
        if len(drift_data) >= 2:
            first_m = drift_data[0][1]["mean"]
            last_m  = drift_data[-1][1]["mean"]
            changes = {d: last_m[d] - first_m[d] for d in ["V", "G", "M", "S"]}
            big = [(d, v) for d, v in changes.items() if abs(v) >= 3.0]
            if big:
                big.sort(key=lambda x: abs(x[1]), reverse=True)
                notes = [f"{d} {v:+.1f}pt" for d, v in big]
                print(f"\n  주목: {', '.join(notes)} 변화 감지")
                print(f"     → LLM 추출 품질 변화, 시장 환경 변화, 또는 배치 기준 변경 가능성")

    print(f"\n{'═'*W}\n")


def print_delisting_comparison(full: list, excl: list) -> None:
    """폐지 포함/제외 IC 비교 + 기간별 처리 상태 (DECISIONS.md 2026-11-13: 항상 함께 보고)."""
    W = 78
    print(f"{'═'*W}")
    print("  상장폐지 포함 vs 제외(현재 생존 종목만) — IC 비교")
    print(f"{'─'*W}")
    print(f"  {'기간':<12} {'IC 포함':>9} {'n':>5} {'IC 제외':>9} {'n':>5} {'차이':>8} {'폐지 사용':>9}")
    diffs = []
    for s, x in zip(full, excl):
        if not s or not x or s["ic"] is None or x["ic"] is None:
            continue
        d = s["ic"] - x["ic"]
        diffs.append(d)
        print(f"  {s['score_year']}→{s['return_year']:<7} {s['ic']:>+9.4f} {s['n_used']:>5} "
              f"{x['ic']:>+9.4f} {x['n_used']:>5} {d:>+8.4f} {s['n_delist_used']:>9}")
    if diffs:
        ics_f = [s["ic"] for s in full if s and s["ic"] is not None]
        ics_x = [x["ic"] for x in excl if x and x["ic"] is not None]
        print(f"  {'평균':<12} {sum(ics_f)/len(ics_f):>+9.4f} {'':>5} "
              f"{sum(ics_x)/len(ics_x):>+9.4f} {'':>5} {sum(diffs)/len(diffs):>+8.4f}")
    print(f"\n  처리 상태 (폐지 포함 버전)")
    keys = [k for k in STATUS_LABELS if any(s and s["status"].get(k) for s in full)]
    for k in keys:
        cells = "  ".join(f"{(s['status'].get(k, 0) if s else 0):>5}" for s in full)
        print(f"    {STATUS_LABELS[k]:<36} {cells}")
    print(f"{'═'*W}\n")


# ─── 메인 ────────────────────────────────────────────────────────────────────

def run_single(engine, score_year: int, return_year: int, top_n: int | None,
               signal: str = "v") -> None:
    if return_year <= score_year:
        print("⚠  경고: --return-year > --score-year 여야 look-ahead bias가 없습니다.")

    print(f"[1/5] alpha_score_history {score_year}년 로드 — {SIGNALS[signal][1]}...")
    scores = load_scores(engine, score_year, signal)
    print(f"  → Universe {len(scores)}개  보유 구간 {period_label(return_year)}")

    if top_n:
        codes_top = sorted(scores, key=lambda c: scores[c], reverse=True)[:top_n]
        scores = {c: scores[c] for c in codes_top}
        print(f"  → Top {top_n} 필터: {len(scores)}개")

    n_universe = len(scores)
    codes = list(scores.keys())

    print(f"[2/5] price_history {return_year}년 수익률 계산 (상장폐지·거래정지 규칙)...")
    delistings = load_delistings(engine)
    returns, status = load_period_returns(engine, return_year, codes, delistings)
    n_used = len(returns)
    print(f"  → 사용: {n_used}개  제외: {n_universe - n_used}개")
    for st, n in sorted(status_counts(status).items()):
        print(f"     {STATUS_LABELS[st]:<36} {n:>4}")
    surv = {c: r for c, r in returns.items() if c not in delistings}
    ic_all, ic_surv = compute_ic_stats(scores, returns), compute_ic_stats(scores, surv)
    if ic_all and ic_surv:
        print(f"  → IC 폐지 포함 {ic_all['ic']:+.4f} (n={ic_all['n']})  │  "
              f"폐지 제외(생존 종목만) {ic_surv['ic']:+.4f} (n={ic_surv['n']})")

    print(f"[3/5] 벤치마크 (KOSPI/KOSDAQ 지수)...")
    kospi_ret  = load_index_return(engine, INDEX_CODES["KOSPI"],  return_year)
    kosdaq_ret = load_index_return(engine, INDEX_CODES["KOSDAQ"], return_year)
    if kospi_ret is None:
        print(f"  ⚠  IDX_KOSPI 없음 — collect_price_history.py 재실행 필요")
    else:
        print(f"  KOSPI: {kospi_ret*100:+.1f}%"
              + (f"  KOSDAQ: {kosdaq_ret*100:+.1f}%" if kosdaq_ret else ""))

    benchmark = kospi_ret

    print(f"[4/5] 구간·분위 분석...")
    # 고정 점수 구간(80+ 등)은 V+S 척도 기준이라 V 단독에서는 생략 (10분위는 순위 기준)
    tiers   = analyze_tiers(scores, returns, benchmark) if signal == "vs" else []
    deciles = analyze_deciles(scores, returns, benchmark)
    ls      = compute_long_short(scores, returns)

    print(f"[5/5] IC + Factor Attribution + Safety Filter 계산...")
    ic_stats      = compute_ic_stats(scores, returns)
    attribution   = compute_factor_attribution(engine, score_year, returns)
    safety_filter = analyze_safety_filter(engine, score_year, returns)

    print_report(
        score_year, return_year, tiers, deciles, ls, ic_stats,
        kospi_ret, kosdaq_ret, n_universe, n_used,
        attribution=attribution,
        safety_filter=safety_filter,
    )


def run_multi(engine, top_n: int | None) -> None:
    years = available_score_years(engine)
    if not years:
        print("alpha_score_history에 데이터가 없습니다.")
        return

    pairs = [(sy, sy + 1) for sy in years]
    print(f"분석 기간: {' │ '.join(f'{s}→{r}' for s, r in pairs)}")

    delistings = load_delistings(engine)
    summaries, summaries_ex, summaries_vs = [], [], []
    for sy, ry in pairs:
        print(f"\n  [{sy}→{ry}] 계산 중...", end="", flush=True)
        s  = collect_year_summary(engine, sy, ry, delistings=delistings, signal="v")
        sx = collect_year_summary(engine, sy, ry, exclude_delisted=True,
                                  delistings=delistings, signal="v")
        sv = collect_year_summary(engine, sy, ry, delistings=delistings, signal="vs")
        summaries.append(s)
        summaries_ex.append(sx)
        summaries_vs.append(sv)
        if s:
            ic_str = f"IC(V)={s['ic']:+.4f}" if s["ic"] is not None else "IC=N/A"
            print(f" 완료  ({ic_str})")
        else:
            print(f" 데이터 부족 — 건너뜀")

    print_multi_summary(summaries)
    print_delisting_comparison(summaries, summaries_ex)
    print_reference_comparison(summaries, summaries_vs)


def print_reference_comparison(v: list, vs: list) -> None:
    """주 검증(V)과 참고치(V+S) IC 나란히. S는 현재 LLM 분석을 써서 미래 정보가 섞여 있다."""
    W = 78
    print(f"{'═'*W}")
    print("  주 검증 V vs 참고 V+S (S = 현재 LLM 분석, 과거 시점 데이터 아님)")
    print(f"{'─'*W}")
    print(f"  {'기간':<12} {'IC V':>9} {'IC V+S':>9} {'차이':>8} {'n':>5}")
    rows = [(a, b) for a, b in zip(v, vs) if a and b and a["ic"] is not None and b["ic"] is not None]
    for a, b in rows:
        print(f"  {a['score_year']}→{a['return_year']:<7} {a['ic']:>+9.4f} {b['ic']:>+9.4f} "
              f"{b['ic'] - a['ic']:>+8.4f} {a['n_used']:>5}")
    if rows:
        ma = sum(a["ic"] for a, _ in rows) / len(rows)
        mb = sum(b["ic"] for _, b in rows) / len(rows)
        print(f"  {'평균':<12} {ma:>+9.4f} {mb:>+9.4f} {mb - ma:>+8.4f}")
    print(f"{'═'*W}\n")


if __name__ == "__main__":
    # Windows 콘솔 UTF-8 출력용. import 시점에 하면 pytest 캡처가 깨져 실행 시에만 적용.
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="AlphaOS 백테스트")
    parser.add_argument("--score-year",  type=int, help="Alpha Score 기준 연도")
    parser.add_argument("--return-year", type=int, help="수익률 검증 연도 (score-year + 1 권장)")
    parser.add_argument("--top", type=int, default=None, help="상위 N개 종목만 (기본: 전체)")
    parser.add_argument("--signal", choices=list(SIGNALS), default="v",
                        help="단일 기간 모드의 점수: v=V 단독(주 검증, 기본) / vs=V+S(참고)")
    parser.add_argument("--multi", action="store_true",
                        help="alpha_score_history 전체 연도로 다중 기간 요약")
    args = parser.parse_args()

    engine = get_engine()

    if args.multi:
        run_multi(engine, args.top)
    elif args.score_year and args.return_year:
        run_single(engine, args.score_year, args.return_year, args.top, args.signal)
    else:
        parser.print_help()
        print("\n사용 예:\n"
              "  python backtest.py --score-year 2022 --return-year 2023\n"
              "  python backtest.py --multi")
