"""
Stage B v2: 동종 업종 Peer Benchmark (LLM 없음, 순수 계산).

v1 → v2 변경: ROA + Op_margin 추가 (V 신호 5지표 확장, ceiling cluster 구조적 해소)

schema:
{
  "peer_group":  "전기/전자",
  "peer_count":  45,
  "market_cap":  {"value": 4.0e14, "percentile": 98},
  "roe":         {"value": 18.5,   "percentile": 72},
  "per":         {"value": 12.3,   "percentile": 45},   -- None if net_income <= 0
  "pbr":         {"value": 1.8,    "percentile": 65},
  "roa":         {"value": 7.2,    "percentile": 60},   -- None if total_assets missing
  "op_margin":   {"value": 12.5,   "percentile": 70}    -- None if revenue missing
}

percentile 의미: 동종 업체 중 상위 몇 %인가 (0~100, 높을수록 좋음)
  - PER/PBR은 낮을수록 저평가 → lower value = higher percentile
  - ROA/Op_margin은 높을수록 고품질 → higher value = higher percentile

peer_count < 10 → None 반환 (동종 기업 부족)
"""

from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import text
from generate_ai_report import get_engine


def _pct(values: list[float], own: float, higher_is_better: bool) -> int | None:
    """0~100 percentile. higher_is_better=True 이면 값이 클수록 높은 percentile."""
    if own is None or not values:
        return None
    n = len(values)
    if higher_is_better:
        rank = sum(1 for v in values if v < own)
    else:
        rank = sum(1 for v in values if v > own)
    return round(rank / n * 100)


def _median(vals: list[float], round_digits: int = 1) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    m = (s[mid - 1] + s[mid]) / 2 if n % 2 == 0 else s[mid]
    return round(m, round_digits)


def _metric(own_val: float | None, all_vals: list[float], higher_is_better: bool,
            round_digits: int = 1) -> dict | None:
    if own_val is None:
        return None
    pct = _pct(all_vals, own_val, higher_is_better)
    return {
        "value":      round(own_val, round_digits),
        "percentile": pct,
        "sector_avg": _median(all_vals, round_digits),  # 평균 대신 중앙값 (이상치 내성)
    }


def _get_peers(engine, sector: str) -> list[dict]:
    """동종 업종 전체 종목의 핵심 재무 지표를 반환 (v2: ROA + Op_margin 추가)."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                s.code,
                s.marcap,
                eq.amount  AS equity,
                ni.amount  AS net_income,
                oi.amount  AS op_income,
                ta.amount  AS total_assets,
                rev.amount AS revenue
            FROM stocks s
            JOIN LATERAL (
                SELECT amount FROM financial_data
                WHERE code = s.code
                  AND account_nm = '자본총계'
                ORDER BY CASE fs_type WHEN 'CFS' THEN 0 ELSE 1 END, year DESC
                LIMIT 1
            ) eq ON true
            JOIN LATERAL (
                SELECT amount FROM financial_data
                WHERE code = s.code
                  AND account_nm LIKE '%당기순이익%'
                ORDER BY CASE fs_type WHEN 'CFS' THEN 0 ELSE 1 END, year DESC
                LIMIT 1
            ) ni ON true
            LEFT JOIN LATERAL (
                SELECT amount FROM financial_data
                WHERE code = s.code
                  AND account_nm = '영업이익'
                ORDER BY CASE fs_type WHEN 'CFS' THEN 0 ELSE 1 END, year DESC
                LIMIT 1
            ) oi ON true
            LEFT JOIN LATERAL (
                SELECT amount FROM financial_data
                WHERE code = s.code
                  AND account_nm = '자산총계'
                ORDER BY CASE fs_type WHEN 'CFS' THEN 0 ELSE 1 END, year DESC
                LIMIT 1
            ) ta ON true
            LEFT JOIN LATERAL (
                SELECT amount FROM financial_data
                WHERE code = s.code
                  AND account_nm = '매출액'
                ORDER BY CASE fs_type WHEN 'CFS' THEN 0 ELSE 1 END, year DESC
                LIMIT 1
            ) rev ON true
            WHERE s.sector = :sector
              AND s.marcap  IS NOT NULL
              AND s.marcap   > 0
              AND eq.amount IS NOT NULL
        """), {"sector": sector}).fetchall()

    result = []
    for code, marcap, equity, net_income, op_income, total_assets, revenue in rows:
        marcap       = float(marcap)       if marcap       is not None else None
        equity       = float(equity)       if equity       is not None else None
        net_income   = float(net_income)   if net_income   is not None else None
        op_income    = float(op_income)    if op_income    is not None else None
        total_assets = float(total_assets) if total_assets is not None else None
        revenue      = float(revenue)      if revenue      is not None else None

        roe       = (net_income / equity * 100)       if (equity and equity != 0 and net_income is not None) else None
        per       = (marcap / net_income)              if (net_income and net_income > 0 and marcap) else None
        pbr       = (marcap / equity)                  if (equity and equity > 0 and marcap) else None
        roa       = (net_income / total_assets * 100)  if (total_assets and total_assets > 0 and net_income is not None) else None
        op_margin = (op_income / revenue * 100)        if (revenue and revenue > 0 and op_income is not None) else None

        result.append({
            "code":       code,
            "marcap":     marcap,
            "roe":        roe,
            "per":        per,
            "pbr":        pbr,
            "roa":        roa,
            "op_margin":  op_margin,
        })
    return result


def _get_named_peers(engine, stock_code: str, sector: str, own_marcap: float, n: int = 5) -> list[dict]:
    """동종 업종 Named Peer 목록 반환.

    선정 기준 (2단계):
      1차: 시총 ±10x 범위에서 자기 포함 n개 이상이면 그 중 시총 상위 n개 선택
      fallback: 부족하면 전체 업종에서 시총 상위 n개 선택
    표시 정렬: ROE 내림차순 (자기 종목 항상 포함)
    """
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT s.code, s.name, s.marcap,
                   eq.amount AS equity,
                   ni.amount AS net_income
            FROM stocks s
            JOIN LATERAL (
                SELECT amount FROM financial_data
                WHERE code = s.code AND account_nm = '자본총계'
                ORDER BY CASE fs_type WHEN 'CFS' THEN 0 ELSE 1 END, year DESC
                LIMIT 1
            ) eq ON true
            JOIN LATERAL (
                SELECT amount FROM financial_data
                WHERE code = s.code AND account_nm LIKE '%당기순이익%'
                ORDER BY CASE fs_type WHEN 'CFS' THEN 0 ELSE 1 END, year DESC
                LIMIT 1
            ) ni ON true
            WHERE s.sector = :sector
              AND s.marcap IS NOT NULL AND s.marcap > 0
              AND eq.amount IS NOT NULL
        """), {"sector": sector}).fetchall()

    all_peers = []
    for code, name, marcap, equity, net_income in rows:
        marcap     = float(marcap)
        equity     = float(equity)     if equity     is not None else None
        net_income = float(net_income) if net_income is not None else None
        roe = (net_income / equity * 100) if (equity and equity != 0 and net_income is not None) else None
        per = (marcap / net_income)       if (net_income and net_income > 0) else None
        pbr = (marcap / equity)           if (equity and equity > 0) else None
        if per and per > 500:
            per = None
        all_peers.append({
            "code":    code,
            "name":    name,
            "marcap":  marcap,
            "roe":     round(roe, 1) if roe is not None else None,
            "per":     round(per, 1) if per is not None else None,
            "pbr":     round(pbr, 2) if pbr is not None else None,
            "is_self": code == stock_code,
        })

    # 1차: ±10x 범위 후보
    lo, hi = own_marcap / 10, own_marcap * 10
    candidates = [p for p in all_peers if lo <= p["marcap"] <= hi]

    # 타사 < 2이면 전체 업종으로 fallback (초대형주/소형주 처리)
    if len([p for p in candidates if not p["is_self"]]) < 2:
        candidates = all_peers

    # 시총 상위 n개 선택 (자기 종목 보장)
    by_marcap = sorted(candidates, key=lambda x: x["marcap"], reverse=True)
    top = by_marcap[:n]
    if not any(p["is_self"] for p in top):
        self_peer = next((p for p in by_marcap if p["is_self"]), None)
        if self_peer:
            top = by_marcap[:n - 1] + [self_peer]

    # 최종 표시: ROE 내림차순
    return sorted(top, key=lambda x: (x["roe"] is not None, x["roe"] or 0), reverse=True)


def _peer_confidence(n: int) -> str:
    if n >= 20: return "very_high"
    if n >= 10: return "high"
    if n >= 5:  return "medium"
    return "low"  # 3~4개


def generate_stage_b_for_stock(engine, stock_code: str) -> dict | None:
    """
    단일 종목의 Stage B Peer Benchmark를 계산해 반환한다.
    저장은 호출자(batch)가 담당.
    """
    # 1. 종목 기본 정보
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT name, marcap, sector FROM stocks WHERE code = :c"
        ), {"c": stock_code}).fetchone()

    if row is None:
        return None
    name, marcap, sector = row
    if not sector:
        return None

    # 2. 동종 업종 peer 데이터
    peers = _get_peers(engine, sector)
    peer_count = len(peers)
    if peer_count < 3:
        return None

    # 3. 자기 종목 데이터
    own = next((p for p in peers if p["code"] == stock_code), None)
    if own is None:
        return None

    # 4. 분포 벡터 구성 (None 제외)
    all_marcaps   = [p["marcap"]    for p in peers if p["marcap"]    is not None]
    all_roes      = [p["roe"]       for p in peers if p["roe"]       is not None]
    all_pers      = [p["per"]       for p in peers if p["per"]       is not None]
    all_pbrs      = [p["pbr"]       for p in peers if p["pbr"]       is not None]
    all_roas      = [p["roa"]       for p in peers if p["roa"]       is not None]
    all_op_margins= [p["op_margin"] for p in peers if p["op_margin"] is not None]

    # 5. Named Peer 목록
    named_peers = []
    if own["marcap"]:
        named_peers = _get_named_peers(engine, stock_code, sector, own["marcap"])

    # 6. 결과 구성
    return {
        "peer_group":       sector,
        "peer_count":       peer_count,
        "peer_confidence":  _peer_confidence(peer_count),
        "market_cap":  _metric(own["marcap"],    all_marcaps,    higher_is_better=True,  round_digits=0),
        "roe":         _metric(own["roe"],        all_roes,       higher_is_better=True,  round_digits=1),
        "per":         _metric(own["per"],        all_pers,       higher_is_better=False, round_digits=1),
        "pbr":         _metric(own["pbr"],        all_pbrs,       higher_is_better=False, round_digits=2),
        "roa":         _metric(own["roa"],        all_roas,       higher_is_better=True,  round_digits=1),
        "op_margin":   _metric(own["op_margin"],  all_op_margins, higher_is_better=True,  round_digits=1),
        "named_peers": named_peers,
    }


def save_stage_b(engine, stock_code: str, data: dict) -> None:
    import json
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO ai_reports (stock_code, stage_b_sections)
            VALUES (:code, CAST(:data AS JSONB))
            ON CONFLICT (stock_code) DO UPDATE
              SET stage_b_sections = CAST(:data AS JSONB)
        """), {"code": stock_code, "data": json.dumps(data, ensure_ascii=False)})


if __name__ == "__main__":
    import sys, io, json
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    code = sys.argv[1] if len(sys.argv) > 1 else "005930"
    engine = get_engine()
    result = generate_stage_b_for_stock(engine, code)
    print(json.dumps(result, ensure_ascii=False, indent=2))
