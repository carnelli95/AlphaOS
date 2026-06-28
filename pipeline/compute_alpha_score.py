"""
Alpha Score v2.2 — 재무 앵커 레이어 추가

v1 문제:
  1. Growth 포화: linear sum → fact 4개면 즉시 만점 → threshold 변수화
  2. Safety 역전: no risk facts → safety 100 → missingness = signal 오류
  3. 분포 압축: std 9.6, 모든 종목이 54점대 군집 → ranking loss

v2 수정:
  1. Quality-dominant signal: top-k 평균 importance × diversity (log 기반 체감)
  2. Safety neutral default: 데이터 없음 = 0.5(중립), 증거 있을 때만 이동
  3. Percentile → sigmoid 변환: 700개 모집단 내 상대 분포로 최종 점수 결정

v2.1 수정:
  4. Safety 과민 해소: linear(-20 for 1 fact) → rational decay(-8 for 1 fact)

v2.2 수정 — Ontology mismatch 해소:
  V = ground truth (재무 데이터)
  G/M = hypothesis (LLM 서술, 반드시 V로 검증)
  S = risk filter (재무 구조 리스크 포함)

  문제: G/M이 재무 현실과 단절 — 적자 기업도 G=93 달성 가능
  수정:
    A-hard: ROE < 0 → G×0.60, M×0.70 (서술 신뢰도 강제 하향)
    A-soft: 0 ≤ ROE < 5% + G/M > 0.80 → contradiction scale-down
    B: ROE < 0 → 구조적 손실을 safety risk_fact로 주입

저장 스키마:
{
  "score":        75,        # 0-100, 전체 모집단 내 percentile × sigmoid
  "rank_global":  45,
  "rank_sector":  3,
  "total_global": 700,
  "total_sector": 45,
  "breakdown": {
    "valuation": 62,         # 0-100 각 구성요소 raw signal
    "growth":    71,
    "moat":      48,
    "safety":    50          # 50 = neutral (데이터 없음), <50 = 위험, >50 = 안전
  }
}
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import io
from datetime import datetime

from sqlalchemy import text

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from generate_ai_report import get_engine


# ─── Signal 함수 ──────────────────────────────────────────────────────────────

def _imp(f) -> int:
    return f.get("importance", 2) if isinstance(f, dict) else 2


def _fact_signal(facts: list, top_k: int = 5) -> float:
    """
    0-1 신호 강도. 품질 우선, 다양성 체감 보너스.

    - 상위 top_k fact의 평균 importance (quality, 1-3 → 0-1)
    - log 기반 diversity factor (fact 수가 늘수록 체감 증가)
    - 두 요소의 가중 결합 (quality 70%, diversity 30%)

    → '많이 나온 것' 아니라 '잘 분포된 신호'가 높은 점수
    """
    if not facts:
        return 0.0

    importances = sorted([_imp(f) for f in facts], reverse=True)[:top_k]
    avg_imp = sum(importances) / len(importances)
    strength = (avg_imp - 1) / 2                                       # 1-3 → 0-1

    diversity = math.log1p(len(importances)) / math.log1p(top_k)      # 0-1 체감
    return strength * (0.70 + 0.30 * diversity)                        # 0-1


def _safety_signal(risk_facts: list, k: float = 0.5) -> float:
    """
    0~0.5 안전성 신호. Rational decay로 연속 완화.

    기존 linear(0.5 * (1 - r)) 문제:
      - 리스크 1개(imp=2) → S=30 (-20): 이산적 첫 트리거에 과민
      - 리스크 1개(imp=3) → S= 9 (-41): 단일 강한 리스크가 폭탄급 감점

    rational decay 0.5 / (1 + k * r) 특성:
      - no facts  → 0.5 (중립, 변화 없음)
      - 1개 imp=2 → ~0.42 (penalty ≈ -8, 완만)
      - 3개 imp=3 → ~0.34 (penalty ≈ -16, 심각 리스크에만 강한 페널티)
      - 분모 성장으로 x=0 근처 slope 억제 → 소량 리스크 과민 해소
    """
    if not risk_facts:
        return 0.5                                       # neutral: missing ≠ safe

    risk_strength = _fact_signal(risk_facts)             # 0-1
    return 0.5 / (1.0 + k * risk_strength)              # rational decay


def _valuation_signal(stage_b: dict) -> float:
    """Stage B percentile 기반 밸류에이션 신호 (0-1).

    v2 (roa + op_margin 데이터 존재 시): 5지표 균형 공식
      roe×0.25 + roa×0.15 + per×0.25 + pbr×0.15 + op_margin×0.20
    v1 폴백 (Stage B v2 배치 전): roe×0.50 + per×0.30 + pbr×0.20
    """
    b = stage_b or {}
    roe_pct = ((b.get("roe") or {}).get("percentile") or 50) / 100
    per_pct = ((b.get("per") or {}).get("percentile") or 50) / 100
    pbr_pct = ((b.get("pbr") or {}).get("percentile") or 50) / 100

    roa_data = b.get("roa")
    opm_data = b.get("op_margin")
    if roa_data is not None and opm_data is not None:
        roa_pct = (roa_data.get("percentile") or 50) / 100
        opm_pct = (opm_data.get("percentile") or 50) / 100
        return (roe_pct * 0.25 + roa_pct * 0.15
                + per_pct * 0.25 + pbr_pct * 0.15
                + opm_pct * 0.20)

    return roe_pct * 0.50 + per_pct * 0.30 + pbr_pct * 0.20


_VALID_MOAT_TYPES = {"pricing_power", "switching_cost", "network_barrier"}


def _moat_signal(moat_facts: list) -> float:
    """
    3차원 해자 신호 (moat_type 레이블 기반 분해).

    pricing_power × 0.40 + switching_cost × 0.30 + network_barrier × 0.30

    분해 모드 진입 조건 (엄격):
      - 모든 facts에 유효한 moat_type 존재 (len(typed) == len(moat_facts))
      - 최소 2개 이상 typed facts 필요
    → Stage C re-batch 이전에는 100% 폴백 보장.
       partial moat_type(구형 데이터 일부 보유)도 폴백 처리.
    """
    if not moat_facts:
        return _fact_signal(moat_facts)                            # = 0.0

    typed = [f for f in moat_facts
             if isinstance(f, dict) and f.get("moat_type") in _VALID_MOAT_TYPES]

    # 전체 커버 + 최소 2개 → 그 이하는 legacy fallback
    if len(typed) < 2 or len(typed) < len(moat_facts):
        return _fact_signal(moat_facts)

    def _dim(t: str) -> float:
        facts = [f for f in typed if f.get("moat_type") == t]
        return _fact_signal(facts) if facts else 0.25              # no evidence ≠ zero

    pp = _dim("pricing_power")
    sc = _dim("switching_cost")
    nb = _dim("network_barrier")
    return pp * 0.40 + sc * 0.30 + nb * 0.30


def _financial_anchor(stage_b: dict, growth: float, moat: float) -> tuple[float, float]:
    """
    A: G/M 서술 신호를 재무 현실로 검증 — 양방향 연속 앵커.

    ROE 10%를 중립점(scale=1.0)으로 두는 연속 함수:
      ROE < 0    → hard suppression: G×0.60, M×0.70 (false positive 차단)
      ROE 0%     → scale 0.80
      ROE 10%    → scale 1.00 (중립, 변화 없음)
      ROE 20%    → scale 1.15 (성장 기업 G/M 회복)
      ROE 30%+   → scale 1.30 (cap, 무한 증폭 방지)

    효과: "좋은 기업만 growth narrative가 살아남고, 적자 기업은 차단"
    """
    b = stage_b or {}
    roe_val = ((b.get("roe") or {}).get("value")) or 0.0

    if roe_val < 0:
        return growth * 0.60, moat * 0.70

    if roe_val <= 10.0:
        scale = 0.80 + 0.02 * roe_val               # 0.80(ROE=0%) → 1.00(ROE=10%)
    else:
        scale = 1.00 + 0.015 * (roe_val - 10.0)     # 1.00(ROE=10%) → 1.30(ROE=30%)
        scale = min(scale, 1.30)                     # cap: 무한 증폭 방지

    return min(growth * scale, 1.0), min(moat * scale, 1.0)


def _inject_financial_risks(stage_b: dict, risk_facts: list) -> list:
    """
    B: 구조적 재무 손실을 safety 계산에 반영 (synthetic risk_fact 주입).

    ROE < 0 → importance=3 (심각, ROE<-10%) or importance=2 (소폭 적자)
    """
    b = stage_b or {}
    roe_val = ((b.get("roe") or {}).get("value")) or 0.0
    if roe_val < 0:
        severity = 3 if roe_val < -10 else 2
        return list(risk_facts) + [{"importance": severity}]
    return list(risk_facts)


def compute_raw_signal(stage_b: dict, stage_c: dict) -> dict:
    """
    개별 종목 raw signal 계산 (0-1). 최종 점수는 전체 모집단 percentile 적용 후 결정.
    """
    c = stage_c or {}

    valuation = _valuation_signal(stage_b)
    growth    = _fact_signal((c.get("growth_drivers") or {}).get("facts", []))
    moat      = _moat_signal((c.get("moat")            or {}).get("facts", []))

    # A: 재무 앵커 — G/M을 ROE로 검증 (서술 ≠ 현실 gap 보정)
    growth, moat = _financial_anchor(stage_b, growth, moat)

    # B: 구조적 손실 → safety에 주입 (적자 기업 S 추가 하향)
    raw_risks  = (c.get("risks") or {}).get("facts", [])
    risk_facts = _inject_financial_risks(stage_b, raw_risks)
    safety     = _safety_signal(risk_facts)

    # v2.6: V+S 기준 모델. G/M은 Research Layer (백테스트 기각, breakdown에는 저장됨)
    raw = valuation * 0.55 + safety * 0.45

    return {
        "raw": raw,
        "breakdown": {
            "valuation": round(valuation * 100),
            "growth":    round(growth    * 100),
            "moat":      round(moat      * 100),
            "safety":    round(safety    * 100),   # 50 = neutral
        },
    }


# ─── 분포 정규화 ──────────────────────────────────────────────────────────────

def _sigmoid(x: float, k: float = 3.0) -> float:
    """중앙(0.5) 기준 sigmoid 확장. k↑ = 더 강한 분리."""
    return 1.0 / (1.0 + math.exp(-k * (x - 0.5)))


def _percentile_sigmoid(raw_scores: list[float], k: float = 3.0) -> list[int]:
    """rank → percentile → sigmoid. 참조용 (현재 미사용, _zscore_sigmoid로 교체)."""
    n = len(raw_scores)
    if n == 0:
        return []
    ranked = sorted(range(n), key=lambda i: raw_scores[i])
    pcts = [0.0] * n
    for rank, idx in enumerate(ranked):
        pcts[idx] = rank / (n - 1) if n > 1 else 0.5
    return [round(_sigmoid(p, k) * 100) for p in pcts]


def _zscore_sigmoid(raw_scores: list[float], k: float = 1.0) -> list[int]:
    """z-score 기반 연속 스코어링 — ranking layer 제거.

    기존 _percentile_sigmoid 구조:
      raw → rank (거리 파괴) → percentile → sigmoid(k=3) (상단 압축)
      → top 5: raw gap 0.062 → pct gap 0.006 → all 82

    신규 구조:
      raw → z-score (raw 거리 보존) → sigmoid(k=1.0)
      → mean(z)=0 → mean(score)=50 보장 (population-relative 유지)
      → top 5 raw distance가 score difference로 직접 반영됨
    """
    n = len(raw_scores)
    if n == 0:
        return []
    if n == 1:
        return [50]

    mean_r = sum(raw_scores) / n
    var_r = sum((r - mean_r) ** 2 for r in raw_scores) / n
    std_r = var_r ** 0.5
    if std_r == 0:
        return [50] * n

    z_scores = [(r - mean_r) / std_r for r in raw_scores]
    return [round(1.0 / (1.0 + math.exp(-k * z)) * 100) for z in z_scores]


# ─── DB 로드 & 저장 (현재 모드) ───────────────────────────────────────────────

def _load_all(engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT r.stock_code, r.stage_b_sections, r.stage_c_sections, s.sector
            FROM ai_reports r
            JOIN stocks s ON s.code = r.stock_code
            WHERE r.stage_b_sections IS NOT NULL
              AND r.stage_c_sections IS NOT NULL
        """)).fetchall()
    return [{"code": r[0], "stage_b": r[1], "stage_c": r[2], "sector": r[3]} for r in rows]


def _save_scores(engine, scored: list[dict]) -> None:
    with engine.begin() as conn:
        for item in scored:
            conn.execute(text("""
                UPDATE ai_reports
                SET alpha_score = CAST(:data AS JSONB)
                WHERE stock_code = :code
            """), {"code": item["code"], "data": json.dumps(item["alpha_score"], ensure_ascii=False)})


# ─── --year 모드: 역사적 Alpha Score 계산 ─────────────────────────────────────

def ensure_history_table(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS alpha_score_history (
                code         VARCHAR NOT NULL,
                year         INTEGER NOT NULL,
                score        INTEGER,
                valuation    INTEGER,
                growth       INTEGER,
                moat         INTEGER,
                safety       INTEGER,
                rank_global  INTEGER,
                rank_sector  INTEGER,
                total_global INTEGER,
                total_sector INTEGER,
                PRIMARY KEY (code, year)
            )
        """))


def _load_financials_year(engine, year: int) -> dict:
    """year의 재무 데이터를 로드. CFS 우선, 없으면 OFS 사용."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT code, account_nm, amount, fs_type
            FROM financial_data
            WHERE year = :year
              AND account_nm IN (
                  '자본총계', '자산총계', '영업이익', '매출액',
                  '당기순이익', '연결당기순이익', '이익잉여금'
              )
        """), {"year": str(year)}).fetchall()

    # {code → {account_nm → (amount, fs_type)}} - CFS 우선
    raw: dict = {}
    for code, account_nm, amount, fs_type in rows:
        acct = raw.setdefault(code, {})
        key = "net_income" if "당기순이익" in account_nm else account_nm
        existing = acct.get(key)
        if existing is None or (fs_type == "CFS" and existing[1] != "CFS"):
            acct[key] = (amount, fs_type)

    return {code: {k: v[0] for k, v in acct.items()} for code, acct in raw.items()}


def _compute_year_val_signals(financials: dict, stocks_info: dict) -> dict:
    """
    역사적 밸류에이션 신호 (PER/PBR 제외, 재무 펀더멘털만).

    V_historical v2.5 = ROE×0.30 + ROA×0.20 + OpMargin×0.30 + RE/Assets×0.20
                        (업종 내 percentile 기준)

    RE/Assets 추가 근거: optimize_v.py 백테스트에서 평균 IC +0.0876 → +0.1057 (+20.8%)
                         두 기간 모두 일관된 개선 확인.
    가중치 설계: 기존 0.40/0.25/0.35 구조를 비례 축소 후 RE/Assets 0.20 흡수.
    PER/PBR 제외: 과거 시총 데이터 미보유 (look-ahead bias 방지).
    """
    # 각 종목 지표 계산
    metrics: dict = {}
    for code, fin in financials.items():
        def _sf(k):
            try: return float(fin[k])
            except: return None

        eq  = _sf("자본총계")
        ta  = _sf("자산총계")
        ni  = _sf("net_income")
        oi  = _sf("영업이익")
        rev = _sf("매출액")
        re  = _sf("이익잉여금")

        roe       = (ni / eq  * 100) if (eq and eq != 0 and ni is not None) else None
        roa       = (ni / ta  * 100) if (ta and ta  > 0 and ni is not None) else None
        op_margin = (oi / rev * 100) if (rev and rev > 0 and oi is not None) else None
        re_assets = (re / ta  * 100) if (ta and ta  > 0 and re is not None) else None

        metrics[code] = {
            "roe": roe, "roa": roa, "op_margin": op_margin, "re_assets": re_assets
        }

    # 업종 그룹
    sectors: dict = {}
    for code, info in stocks_info.items():
        sectors.setdefault(info.get("sector") or "기타", []).append(code)

    def _pct(own, vals):
        if own is None or not vals:
            return 50
        return round(sum(1 for v in vals if v < own) / len(vals) * 100)

    # 업종별 percentile → val_signal (v2.5 가중치)
    result: dict = {}
    for sector, codes in sectors.items():
        peer_ms  = [metrics.get(c, {}) for c in codes]
        roe_vals = [m["roe"]       for m in peer_ms if m.get("roe")       is not None]
        roa_vals = [m["roa"]       for m in peer_ms if m.get("roa")       is not None]
        opm_vals = [m["op_margin"] for m in peer_ms if m.get("op_margin") is not None]
        rea_vals = [m["re_assets"] for m in peer_ms if m.get("re_assets") is not None]

        for code in codes:
            m = metrics.get(code, {})
            roe_pct = _pct(m.get("roe"),       roe_vals) / 100
            roa_pct = _pct(m.get("roa"),       roa_vals) / 100
            opm_pct = _pct(m.get("op_margin"), opm_vals) / 100
            rea_pct = _pct(m.get("re_assets"), rea_vals) / 100

            val_sig = roe_pct * 0.30 + roa_pct * 0.20 + opm_pct * 0.30 + rea_pct * 0.20
            result[code] = {"val_signal": val_sig, "roe_val": m.get("roe") or 0.0}

    return result


def _save_history(engine, year: int, scored: list[dict]) -> None:
    with engine.begin() as conn:
        for item in scored:
            a = item["alpha_score"]
            bd = a["breakdown"]
            conn.execute(text("""
                INSERT INTO alpha_score_history
                    (code, year, score, valuation, growth, moat, safety,
                     rank_global, rank_sector, total_global, total_sector)
                VALUES
                    (:code, :year, :score, :valuation, :growth, :moat, :safety,
                     :rank_global, :rank_sector, :total_global, :total_sector)
                ON CONFLICT (code, year) DO UPDATE SET
                    score=EXCLUDED.score, valuation=EXCLUDED.valuation,
                    growth=EXCLUDED.growth, moat=EXCLUDED.moat, safety=EXCLUDED.safety,
                    rank_global=EXCLUDED.rank_global, rank_sector=EXCLUDED.rank_sector,
                    total_global=EXCLUDED.total_global, total_sector=EXCLUDED.total_sector
            """), {
                "code": item["code"], "year": year,
                "score":      a.get("score"),
                "valuation":  bd.get("valuation"),
                "growth":     bd.get("growth"),
                "moat":       bd.get("moat"),
                "safety":     bd.get("safety"),
                "rank_global":  a.get("rank_global"),
                "rank_sector":  a.get("rank_sector"),
                "total_global": a.get("total_global"),
                "total_sector": a.get("total_sector"),
            })


def run_year(engine, year: int) -> None:
    """--year 모드: 특정 연도 재무 데이터로 역사적 Alpha Score 계산."""
    ensure_history_table(engine)
    print(f"[{datetime.now():%H:%M:%S}] Alpha Score {year}년 역사적 계산 시작")
    print(f"  ※ V신호: ROE/ROA/op_margin 업종 percentile (PER/PBR 제외)")
    print(f"  ※ G/M/S: 현재 ai_reports.stage_c 사용 (proxy)")

    # 1. 재무 데이터 (year년)
    financials = _load_financials_year(engine, year)
    print(f"  재무 데이터: {len(financials)}개 종목")

    # 2. 종목 정보 (sector) + stage_c
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT s.code, s.sector, r.stage_c_sections
            FROM stocks s
            LEFT JOIN ai_reports r ON r.stock_code = s.code
            WHERE s.code = ANY(:codes)
        """), {"codes": list(financials.keys())}).fetchall()

    stocks_info = {r[0]: {"sector": r[1]} for r in rows}
    stage_c_map = {r[0]: r[2] for r in rows}

    # 3. 역사적 V신호
    val_signals = _compute_year_val_signals(financials, stocks_info)

    # 4. G/M/S + composite raw signal
    signals = []
    for code, vs in val_signals.items():
        c = stage_c_map.get(code) or {}
        growth = _fact_signal((c.get("growth_drivers") or {}).get("facts", []))
        moat   = _moat_signal((c.get("moat")           or {}).get("facts", []))

        # financial anchor (역사적 ROE 사용)
        fake_stage_b = {"roe": {"value": vs["roe_val"]}}
        growth, moat = _financial_anchor(fake_stage_b, growth, moat)

        raw_risks  = (c.get("risks") or {}).get("facts", [])
        risk_facts = _inject_financial_risks(fake_stage_b, raw_risks)
        safety     = _safety_signal(risk_facts)

        raw = vs["val_signal"] * 0.55 + safety * 0.45  # v2.6: V+S only
        signals.append({
            "code": code,
            "sector": stocks_info.get(code, {}).get("sector") or "기타",
            "signal": {
                "raw": raw,
                "breakdown": {
                    "valuation": round(vs["val_signal"] * 100),
                    "growth":    round(growth * 100),
                    "moat":      round(moat   * 100),
                    "safety":    round(safety  * 100),
                },
            },
        })

    print(f"  신호 계산: {len(signals)}개")

    # 5. z-score 정규화
    raw_list    = [s["signal"]["raw"] for s in signals]
    final_scores = _zscore_sigmoid(raw_list, k=1.0)

    scored = []
    for i, s in enumerate(signals):
        scored.append({
            "code":   s["code"],
            "sector": s["sector"],
            "alpha_score": {
                "score":     final_scores[i],
                "breakdown": s["signal"]["breakdown"],
            },
        })

    # 6. 랭킹 + 저장
    _add_ranks(scored)
    _save_history(engine, year, scored)

    scores = [s["alpha_score"]["score"] for s in scored]
    import statistics
    print(f"\n[{datetime.now():%H:%M:%S}] 완료 — {len(scored)}개")
    print(f"  평균: {statistics.mean(scores):.1f}  "
          f"중앙값: {statistics.median(scores):.1f}  "
          f"표준편차: {statistics.stdev(scores):.1f}")
    print(f"  90+: {sum(1 for s in scores if s>=90)}개  "
          f"80+: {sum(1 for s in scores if 80<=s<90)}개  "
          f"70+: {sum(1 for s in scores if 70<=s<80)}개  "
          f"<50: {sum(1 for s in scores if s<50)}개")


# ─── 랭킹 계산 ────────────────────────────────────────────────────────────────

def _add_ranks(scored: list[dict]) -> None:
    by_score = sorted(scored, key=lambda x: x["alpha_score"]["score"], reverse=True)
    total = len(by_score)
    for rank, item in enumerate(by_score, 1):
        item["alpha_score"]["rank_global"] = rank
        item["alpha_score"]["total_global"] = total

    sectors: dict[str, list] = {}
    for item in scored:
        sectors.setdefault(item.get("sector") or "기타", []).append(item)

    for sec_items in sectors.values():
        sec_sorted = sorted(sec_items, key=lambda x: x["alpha_score"]["score"], reverse=True)
        for rank, item in enumerate(sec_sorted, 1):
            item["alpha_score"]["rank_sector"]  = rank
            item["alpha_score"]["total_sector"] = len(sec_sorted)


# ─── 메인 ─────────────────────────────────────────────────────────────────────

def run() -> None:
    engine = get_engine()
    print(f"[{datetime.now():%H:%M:%S}] Alpha Score v2 배치 시작")

    rows = _load_all(engine)
    print(f"  대상: {len(rows)}개")

    # 1단계: 전체 raw signal 계산
    signals = []
    for r in rows:
        sig = compute_raw_signal(r["stage_b"], r["stage_c"])
        signals.append({"code": r["code"], "sector": r["sector"], "signal": sig})

    # 2단계: z-score 기반 연속 정규화 (ranking layer 제거, raw 거리 보존)
    raw_list = [s["signal"]["raw"] for s in signals]
    final_scores = _zscore_sigmoid(raw_list, k=1.0)

    scored = []
    for i, s in enumerate(signals):
        scored.append({
            "code":   s["code"],
            "sector": s["sector"],
            "_raw":   s["signal"]["raw"],           # 진단용 (DB 비저장)
            "alpha_score": {
                "score":     final_scores[i],
                "breakdown": s["signal"]["breakdown"],
            },
        })

    # 3단계: 랭킹 삽입 후 저장
    _add_ranks(scored)
    _save_scores(engine, scored)

    scores = [s["alpha_score"]["score"] for s in scored]
    import statistics
    mean_r = sum(raw_list) / len(raw_list)
    var_r  = sum((r - mean_r)**2 for r in raw_list) / len(raw_list)
    std_r  = var_r ** 0.5

    print(f"\n[{datetime.now():%H:%M:%S}] 완료 — {len(scored)}개")
    print(f"  평균: {statistics.mean(scores):.1f}  중앙값: {statistics.median(scores):.1f}  "
          f"표준편차: {statistics.stdev(scores):.1f}")
    print(f"  BUY(≥70): {sum(1 for s in scores if s>=70)}개  "
          f"HOLD(50-69): {sum(1 for s in scores if 50<=s<70)}개  "
          f"CAUTION(<50): {sum(1 for s in scores if s<50)}개")
    print(f"  raw 분포  mean={mean_r:.4f}  std={std_r:.4f}  "
          f"min={min(raw_list):.4f}  max={max(raw_list):.4f}")

    # 히스토그램 (10점 단위)
    buckets = [0] * 11  # 0-9, 10-19, …, 90-99, 100
    for sc in scores:
        buckets[min(sc // 10, 10)] += 1
    print("\n  점수 분포 (히스토그램):")
    for i, cnt in enumerate(buckets):
        lo = i * 10
        hi = 99 if i < 10 else 100
        bar = "█" * (cnt // 3)
        print(f"    {lo:>3}-{hi:<3}  {cnt:>4}개  {bar}")

    # 구간별 해상도 확인
    print(f"\n  구간 확인:")
    print(f"    90점 이상:  {sum(1 for s in scores if s>=90)}개")
    print(f"    80-89점:    {sum(1 for s in scores if 80<=s<90)}개")
    print(f"    70-79점:    {sum(1 for s in scores if 70<=s<80)}개")
    print(f"    50-59점:    {sum(1 for s in scores if 50<=s<60)}개")
    print(f"    40-49점:    {sum(1 for s in scores if 40<=s<50)}개")

    # Top 20: raw → z-score → 최종 점수 단조성 확인
    top20 = sorted(scored, key=lambda x: x["alpha_score"]["score"], reverse=True)[:20]
    print(f"\n  Top 20 (raw→z→score 단조성 확인):")
    print(f"    {'code':<8} {'raw':>7} {'z':>6} {'score':>6}  V  G  M  S")
    for s in top20:
        raw = s["_raw"]
        z   = (raw - mean_r) / std_r
        a   = s["alpha_score"]
        bd  = a["breakdown"]
        print(f"    {s['code']:<8} {raw:.4f}  {z:+.2f}  {a['score']:>5}  "
              f"{bd['valuation']} {bd['growth']} {bd['moat']} {bd['safety']}")

    # Bottom 5: 음의 꼬리 과도 여부
    bot5 = sorted(scored, key=lambda x: x["alpha_score"]["score"])[:5]
    print(f"\n  Bottom 5:")
    for s in bot5:
        raw = s["_raw"]
        z   = (raw - mean_r) / std_r
        print(f"    {s['code']}  raw={raw:.4f}  z={z:+.2f}  score={s['alpha_score']['score']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", help="단일 종목 raw signal 테스트")
    parser.add_argument("--year", type=int,
                        help="역사적 Alpha Score 계산 (alpha_score_history 저장). 예: --year 2022")
    args = parser.parse_args()

    if args.code:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT stage_b_sections, stage_c_sections FROM ai_reports WHERE stock_code = :c"
            ), {"c": args.code}).fetchone()
        if not row:
            print("종목 없음")
        else:
            result = compute_raw_signal(row[0], row[1])
            print(json.dumps(result, ensure_ascii=False, indent=2))
            print(f"\n※ 최종 score는 전체 배치 실행 후 모집단 z-score 기준 결정됩니다.")
    elif args.year:
        engine = get_engine()
        run_year(engine, args.year)
    else:
        run()
