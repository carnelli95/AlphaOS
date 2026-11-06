"""
Stage A: 기업개요 + 밸류에이션 AI 리포트 생성기

흐름 (2단계 구조):
    Step 1. 사업보고서 원문 -> LLM -> 구조화된 facts JSON 추출 (사업보고서 기반)
    Step 2. facts JSON + 정량 신호 -> LLM -> 서술 생성 (LLM 추론)
    이 분리 덕분에 각 단계의 입력/출력을 독립적으로 검증하고,
    data_basis 태깅도 자연스럽게 구현된다.

핵심 원칙:
    - 숫자만 나열 금지, 반드시 "왜"를 설명
    - 업종/경쟁사 비교는 Stage B 이후로 미룸 (지금은 자기 historical 추세로만 근거 제시)
    - 신호(signal)는 결정론적으로 Python에서 계산, LLM은 서술만 담당
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

load_dotenv(override=True)

# 밸류에이션 계산에 필요한 계정명 (financial_data.account_nm과 정확히 일치해야 함)
ACCOUNT_REVENUE = "매출액"
ACCOUNT_OPERATING_PROFIT_CANDIDATES = ("영업이익", "영업이익(손실)")
ACCOUNT_NET_INCOME = "당기순이익(손실)"
ACCOUNT_EQUITY = "자본총계"
ACCOUNT_ASSETS = "자산총계"
ACCOUNT_LIABILITIES = "부채총계"


def get_engine() -> Engine:
    db_url = os.environ["DATABASE_URL"]
    return create_engine(db_url, connect_args={"options": "-c lc_messages=C"})


@dataclass
class YearlyFinancials:
    year: str
    revenue: float | None
    operating_profit: float | None
    net_income: float | None
    equity: float | None
    assets: float | None
    liabilities: float | None = None


def get_financial_history(engine: Engine, stock_code: str, num_years: int = 3) -> list[YearlyFinancials]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT year, account_nm, amount
                FROM financial_data
                WHERE code = :code
                  AND fs_type = (
                    SELECT CASE WHEN EXISTS (
                        SELECT 1 FROM financial_data WHERE code = :code AND fs_type = 'CFS' LIMIT 1
                    ) THEN 'CFS' ELSE 'OFS' END
                  )
                ORDER BY year DESC
                """
            ),
            {"code": stock_code},
        ).fetchall()

    by_year: dict[str, dict[str, float]] = {}
    for year, account_nm, amount in rows:
        by_year.setdefault(year, {})[account_nm] = float(amount)

    years_sorted = sorted(by_year.keys(), reverse=True)[:num_years]

    result = []
    for year in years_sorted:
        accounts = by_year[year]
        op_profit = None
        for candidate in ACCOUNT_OPERATING_PROFIT_CANDIDATES:
            if candidate in accounts:
                op_profit = accounts[candidate]
                break
        result.append(
            YearlyFinancials(
                year=year,
                revenue=accounts.get(ACCOUNT_REVENUE),
                operating_profit=op_profit,
                net_income=accounts.get(ACCOUNT_NET_INCOME),
                equity=accounts.get(ACCOUNT_EQUITY),
                assets=accounts.get(ACCOUNT_ASSETS),
                liabilities=accounts.get(ACCOUNT_LIABILITIES),
            )
        )
    # 오래된 연도 -> 최근 연도 순으로 반환 (추세 서술에 자연스러움)
    return list(reversed(result))


def get_stock_market_data(engine: Engine, stock_code: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT name, price, marcap, sector FROM stocks WHERE code = :code"),
            {"code": stock_code},
        ).fetchone()
    if row is None:
        return None
    return {"name": row[0], "price": row[1], "marcap": row[2], "sector": row[3]}


def get_peer_metrics(engine: Engine, stock_code: str, sector: str) -> dict | None:
    """같은 업종 내 종목들의 PER/PBR/ROE 중위수를 반환한다 (자기 자신 제외)."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    s.code,
                    s.marcap,
                    eq.amount  AS equity,
                    pr.amount  AS net_income
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
                ) pr ON true
                WHERE s.sector = :sector
                  AND s.code  != :code
                  AND s.marcap IS NOT NULL
                  AND eq.amount > 0
                """
            ),
            {"sector": sector, "code": stock_code},
        ).fetchall()

    if len(rows) < 3:
        return None

    pbrs, pers, roes = [], [], []
    for row in rows:
        marcap, equity, net_income = row[1], row[2], row[3]
        if marcap and equity and equity > 0:
            pbrs.append(marcap / equity)
        if marcap and net_income and net_income > 0:
            pers.append(marcap / net_income)
        if net_income and equity and equity != 0:
            roes.append(net_income / equity * 100)

    def median(lst: list[float]) -> float | None:
        if not lst:
            return None
        s = sorted(lst)
        mid = len(s) // 2
        return round((s[mid - 1] + s[mid]) / 2 if len(s) % 2 == 0 else s[mid], 2)

    return {
        "sector": sector,
        "peer_count": len(rows),
        "median_pbr": median(pbrs),
        "median_per": median(pers),
        "median_roe": median(roes),
    }


def get_business_overview(engine: Engine, stock_code: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT section_business_overview FROM business_reports_raw
                WHERE stock_code = :code AND parse_status = 'ok'
                ORDER BY report_year DESC LIMIT 1
                """
            ),
            {"code": stock_code},
        ).fetchone()
    return row[0] if row else None


def compute_metrics(history: list[YearlyFinancials], market: dict) -> dict:
    """가장 최근 연도 기준 PER/PBR/ROE + 추세 정보를 계산한다."""
    if not history:
        return {}

    latest = history[-1]
    marcap = market.get("marcap")

    per = (marcap / latest.net_income) if (marcap and latest.net_income and latest.net_income > 0) else None
    pbr = (marcap / latest.equity) if (marcap and latest.equity and latest.equity > 0) else None
    roe = (latest.net_income / latest.equity * 100) if (latest.net_income and latest.equity) else None
    operating_margin = (
        (latest.operating_profit / latest.revenue * 100)
        if (latest.operating_profit is not None and latest.revenue)
        else None
    )

    revenue_trend = [h.revenue for h in history]
    net_income_trend = [h.net_income for h in history]

    return {
        "latest_year": latest.year,
        "PER": round(per, 2) if per else None,
        "PBR": round(pbr, 2) if pbr else None,
        "ROE": round(roe, 2) if roe else None,
        "operating_margin": round(operating_margin, 2) if operating_margin else None,
        "revenue_trend_by_year": dict(zip([h.year for h in history], revenue_trend)),
        "net_income_trend_by_year": dict(zip([h.year for h in history], net_income_trend)),
    }


def _pct_change(prev: float | None, latest: float | None) -> float | None:
    if prev is None or latest is None or prev == 0:
        return None
    return (latest - prev) / abs(prev) * 100


def _year_gap(prev_year: str, latest_year: str) -> int:
    try:
        gap = int(latest_year) - int(prev_year)
    except ValueError:
        gap = 1
    return max(gap, 1)


def _annualized_pct_change(prev_year: str, latest_year: str, prev: float | None, latest: float | None) -> float | None:
    """
    연도 간격이 1년이 아닐 수 있으므로(예: 데이터 공백으로 2023->2025),
    단순 증감률 대신 연 평균 성장률(CAGR 방식)로 정규화한다.
    둘 다 양수일 때만 정확히 계산 가능하고, 부호가 섞이면 None을 반환
    (그런 경우는 turnaround/deteriorating 신호로 별도 처리됨).
    """
    if prev is None or latest is None or prev <= 0 or latest <= 0:
        return None
    gap = _year_gap(prev_year, latest_year)
    return ((latest / prev) ** (1 / gap) - 1) * 100


def _period_label(prev_year: str, latest_year: str) -> str:
    gap = _year_gap(prev_year, latest_year)
    return "전년 대비" if gap == 1 else f"최근 {gap}년간 연평균"


# 신호 판단 기준값 (한곳에 모아둬서 나중에 튜닝하기 쉽게)
HIGH_PER_THRESHOLD = 30
HIGH_GROWTH_THRESHOLD_PCT = 20
LOW_ROE_THRESHOLD = 8
HIGH_PBR_THRESHOLD = 3

# 신규 신호 기준값
LOW_PBR_THRESHOLD = 1.5
HIGH_ROE_FOR_VALUE_THRESHOLD = 15
EARNINGS_DECLINE_THRESHOLD_PCT = -20
MARGIN_IMPROVEMENT_THRESHOLD_PP = 3.0
STRONG_ROE_THRESHOLD = 15
STRONG_MARGIN_THRESHOLD = 10
DEBT_RATIO_STABLE_MAX = 70.0  # 부채총계/자본총계 * 100, 이 이하면 안정적으로 판단
DEBT_RATIO_HIGH_THRESHOLD = 200.0  # 이 이상이면 고부채로 판단 (stable 기준과 독립적으로 관리)
DEBT_RATIO_CHANGE_THRESHOLD_PP = 30.0  # 연평균 부채비율 변화가 이 이상이면 불안정으로 판단


def compute_signals(history: list[YearlyFinancials], metrics: dict) -> list[dict]:
    """
    LLM에게 "어떻게 해석할지"를 맡기지 않고, 결정론적 규칙으로
    미리 판단한 신호 목록을 만든다. LLM은 이 신호들을 문장으로
    풀어쓰기만 하면 돼서 근거 없는 합리화(환각) 위험이 줄어든다.
    """
    signals: list[dict] = []
    if len(history) < 2:
        return signals

    prev, latest = history[-2], history[-1]
    period_label = _period_label(prev.year, latest.year)
    revenue_growth = _annualized_pct_change(prev.year, latest.year, prev.revenue, latest.revenue)
    net_income_growth = _annualized_pct_change(prev.year, latest.year, prev.net_income, latest.net_income)
    per = metrics.get("PER")
    roe = metrics.get("ROE")
    pbr = metrics.get("PBR")

    # 1. 턴어라운드: 전년 영업적자 -> 올해 흑자
    if prev.operating_profit is not None and latest.operating_profit is not None:
        if prev.operating_profit < 0 and latest.operating_profit > 0:
            signals.append({
                "signal": "turnaround",
                "text": f"{prev.year}년 영업적자에서 {latest.year}년 영업흑자로 전환됨",
            })
        elif prev.operating_profit > 0 and latest.operating_profit < 0:
            signals.append({
                "signal": "deteriorating",
                "text": f"{prev.year}년 영업흑자에서 {latest.year}년 영업적자로 악화됨",
            })

    # 1.5 순이익 부호전환 (영업이익과는 별개로 체크해야 함 - 영업외손익/일회성
    # 항목으로 영업이익은 흑자인데 순이익만 적자로 전환되는 경우가 있고,
    # _annualized_pct_change는 부호가 섞이면 None을 반환해 이 경우를 놓친다.
    # ROE가 음수로 나오는 원인이 보통 이거라서, 신호로 잡아주는 게 중요함.
    if prev.net_income is not None and latest.net_income is not None:
        if prev.net_income > 0 and latest.net_income < 0:
            signals.append({
                "signal": "net_income_turned_negative",
                "text": f"{prev.year}년 순이익 흑자에서 {latest.year}년 순손실로 전환됨",
            })
        elif prev.net_income < 0 and latest.net_income > 0:
            signals.append({
                "signal": "net_income_turned_positive",
                "text": f"{prev.year}년 순손실에서 {latest.year}년 순이익 흑자로 전환됨",
            })

    # 2. 고PER + 고성장: 시장이 미래 성장을 선반영했을 가능성
    if per is not None and per > HIGH_PER_THRESHOLD:
        if revenue_growth is not None and revenue_growth > HIGH_GROWTH_THRESHOLD_PCT:
            signals.append({
                "signal": "high_per_with_growth",
                "text": (
                    f"PER {per}배로 높은 편이나, 매출이 {period_label} {revenue_growth:.1f}% 증가하는 "
                    f"고성장 구간이라 시장이 향후 실적 개선을 선반영했을 가능성이 있음"
                ),
            })
        else:
            growth_desc = f"{revenue_growth:.1f}%" if revenue_growth is not None else "확인 어려움"
            signals.append({
                "signal": "high_per_without_growth",
                "text": (
                    f"PER {per}배로 높은데 매출 성장은 뚜렷하지 않아({period_label} {growth_desc}), "
                    f"고평가 가능성에 대한 주의가 필요함"
                ),
            })

    # 3. 낮은 ROE + 높은 PBR: 자본 효율 대비 비싼 가격일 가능성
    if roe is not None and pbr is not None and roe < LOW_ROE_THRESHOLD and pbr > HIGH_PBR_THRESHOLD:
        signals.append({
            "signal": "low_roe_high_pbr",
            "text": f"ROE {roe}%로 낮은데 PBR은 {pbr}배로 높아, 자본 효율 대비 비싼 가격일 가능성이 있음",
        })

    # 4. 순이익 고성장: 성장 동력 서술에 활용 가능
    if net_income_growth is not None and net_income_growth > HIGH_GROWTH_THRESHOLD_PCT:
        signals.append({
            "signal": "net_income_growth",
            "text": f"순이익이 {period_label} {net_income_growth:.1f}% 증가하는 성장세를 보임",
        })
    elif net_income_growth is not None and net_income_growth < EARNINGS_DECLINE_THRESHOLD_PCT:
        signals.append({
            "signal": "earnings_declining",
            "text": f"순이익이 {period_label} {net_income_growth:.1f}% 감소하며 실적 악화가 진행 중임",
        })

    # 5. 저PBR + 고ROE: 자본효율은 좋은데 장부가 대비 저평가됐을 가능성 (가치주 후보)
    if pbr is not None and roe is not None and pbr < LOW_PBR_THRESHOLD and roe > HIGH_ROE_FOR_VALUE_THRESHOLD:
        signals.append({
            "signal": "low_pbr_high_roe",
            "text": f"ROE {roe}%로 자본효율이 우수한데 PBR은 {pbr}배로 낮아, 장부가 대비 저평가 가능성이 있음",
        })

    # 6. 영업마진 개선: 매출 성장보다 마진 개선이 질적으로 더 중요한 신호
    if (
        prev.operating_profit is not None and prev.revenue
        and latest.operating_profit is not None and latest.revenue
    ):
        gap = _year_gap(prev.year, latest.year)
        prev_margin = prev.operating_profit / prev.revenue * 100
        latest_margin = latest.operating_profit / latest.revenue * 100
        margin_change_per_year = (latest_margin - prev_margin) / gap
        if margin_change_per_year > MARGIN_IMPROVEMENT_THRESHOLD_PP:
            signals.append({
                "signal": "margin_improving",
                "text": (
                    f"영업마진이 {prev.year}년 {prev_margin:.1f}%에서 {latest.year}년 "
                    f"{latest_margin:.1f}%로 개선됨 (매출 성장보다 수익성 개선이 더 두드러짐)"
                ),
            })
        elif margin_change_per_year < -MARGIN_IMPROVEMENT_THRESHOLD_PP:
            signals.append({
                "signal": "margin_declining",
                "text": (
                    f"영업마진이 {prev.year}년 {prev_margin:.1f}%에서 {latest.year}년 "
                    f"{latest_margin:.1f}%로 악화됨"
                ),
            })

    # 7. 수익성 우수: ROE와 영업마진 둘 다 높은 수준
    operating_margin = metrics.get("operating_margin")
    if roe is not None and operating_margin is not None:
        if roe > STRONG_ROE_THRESHOLD and operating_margin > STRONG_MARGIN_THRESHOLD:
            signals.append({
                "signal": "profitability_strong",
                "text": f"ROE {roe}%, 영업마진 {operating_margin}%로 수익성이 우수한 수준임",
            })

    # 8. 부채비율 안정성 (데이터 있는 경우만)
    if (
        prev.liabilities is not None and prev.equity
        and latest.liabilities is not None and latest.equity
    ):
        gap = _year_gap(prev.year, latest.year)
        prev_debt_ratio = prev.liabilities / prev.equity * 100
        latest_debt_ratio = latest.liabilities / latest.equity * 100
        debt_ratio_change_per_year = abs(latest_debt_ratio - prev_debt_ratio) / gap
        if latest_debt_ratio < DEBT_RATIO_STABLE_MAX and debt_ratio_change_per_year < DEBT_RATIO_CHANGE_THRESHOLD_PP:
            signals.append({
                "signal": "debt_stable",
                "text": f"부채비율이 {latest_debt_ratio:.1f}%로 낮고 {period_label} 큰 변동 없이 안정적임",
            })
        elif latest_debt_ratio > DEBT_RATIO_HIGH_THRESHOLD:
            signals.append({
                "signal": "debt_high",
                "text": f"부채비율이 {latest_debt_ratio:.1f}%로 높은 편이라 재무 리스크 점검이 필요함",
            })

    return signals


def _peer_comparison_text(label: str, own: float | None, median: float | None) -> str:
    """업종 중위수 대비 위치를 한 단어로 표현."""
    if own is None or median is None or median == 0:
        return "비교 불가"
    ratio = own / median
    if ratio < 0.8:
        return f"업종 대비 낮음 ({ratio:.1f}x)"
    if ratio > 1.2:
        return f"업종 대비 높음 ({ratio:.1f}x)"
    return f"업종 수준 ({ratio:.1f}x)"


# 신호 0개일 때 사용할 예시 문구 (그대로 베끼지 말고 톤만 참고하라고 LLM에게 지시).
# 매번 랜덤으로 하나를 골라 앵커로 줘서, 110여개 무신호 종목이 똑같은 문장으로
# 도배되는 것을 방지한다.
# 신호 -> 카테고리 매핑 (LLM이 임의로 분류하지 않도록 코드에서 결정론적으로 고정)
SIGNAL_CATEGORY_MAP = {
    "high_per_without_growth": "valuation",
    "high_per_with_growth": "valuation",
    "low_pbr_high_roe": "valuation",
    "low_roe_high_pbr": "valuation",
    "net_income_growth": "growth",
    "earnings_declining": "growth",
    "net_income_turned_negative": "growth",
    "net_income_turned_positive": "growth",
    "margin_improving": "growth",
    "margin_declining": "growth",
    "turnaround": "growth",
    "deteriorating": "growth",
    "profitability_strong": "profitability",
    "debt_stable": "stability",
    "debt_high": "stability",
}

CATEGORY_META = {
    "valuation": {"title": "밸류에이션", "emoji": "⚠️"},
    "growth": {"title": "성장성", "emoji": "📈"},
    "profitability": {"title": "수익성", "emoji": "💪"},
    "stability": {"title": "재무안정성", "emoji": "🛡️"},
    "summary": {"title": "요약", "emoji": "ℹ️"},
}
CATEGORY_ORDER = ["valuation", "growth", "profitability", "stability", "summary"]


def group_signals_by_category(signals: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for s in signals:
        category = SIGNAL_CATEGORY_MAP.get(s["signal"], "summary")
        grouped.setdefault(category, []).append(s)
    return grouped


NO_SIGNAL_EXAMPLE_PHRASES = [
    "특별히 두드러진 재무 신호는 관찰되지 않습니다.",
    "현재 재무지표는 전반적으로 중립적인 수준으로 평가됩니다.",
    "신호 기반 분석 결과 뚜렷한 강점이나 위험요인은 확인되지 않습니다.",
]


def build_extraction_prompt(company_name: str, business_overview: str) -> str:
    """Step 1 프롬프트: 사업보고서 원문 -> 구조화된 facts JSON."""
    excerpt = (business_overview or "")[:3000]
    return f"""너는 사업보고서 원문 파서야. 아래 "{company_name}" 사업보고서 원문에서
핵심 사업 정보를 추출해줘.

[원문]
{excerpt}

[규칙]
- 문서에 명시된 사실만 추출. 추론이나 평가는 금지
- 산업 전망, 미래 계획은 notable_facts에 포함하지 말 것
- 모르는 항목은 null 또는 빈 배열로 처리

[출력 형식 - JSON만]
{{
  "business_model": "무엇을 팔고 어떻게 돈을 버는지 핵심 (1문장)",
  "key_products_or_services": ["제품/서비스 목록 (최대 5개)"],
  "revenue_structure": "내수/수출 비중, B2B/B2C 등 (1문장, 알 수 없으면 null)",
  "notable_facts": ["문서에서 발견된 주목할 사실 (최대 3개, 없으면 빈 배열)"]
}}
"""


def build_narrative_prompt(
    company_name: str,
    facts: dict,
    metrics: dict,
    signals: list[dict],
    peer: dict | None = None,
) -> str:
    """Step 2 프롬프트: 구조화된 facts + 신호 + 피어 비교 -> 기업개요/밸류에이션 서술."""
    grouped = group_signals_by_category(signals)

    facts_block = f"""
- 사업모델: {facts.get('business_model', '정보 없음')}
- 주요 제품/서비스: {', '.join(facts.get('key_products_or_services') or []) or '정보 없음'}
- 수익 구조: {facts.get('revenue_structure') or '정보 없음'}
- 주요 사실: {'; '.join(facts.get('notable_facts') or []) or '없음'}
""".strip()

    if peer:
        per_vs = _peer_comparison_text("PER", metrics.get("PER"), peer.get("median_per"))
        pbr_vs = _peer_comparison_text("PBR", metrics.get("PBR"), peer.get("median_pbr"))
        roe_vs = _peer_comparison_text("ROE(%)", metrics.get("ROE"), peer.get("median_roe"))
        peer_block = f"""

[업종 비교 - {peer['sector']} 업종 {peer['peer_count']}개 종목 중위수 대비]
- PER:  당사 {metrics.get('PER') or 'N/A'} / 업종 중위수 {peer.get('median_per') or 'N/A'} → {per_vs}
- PBR:  당사 {metrics.get('PBR') or 'N/A'} / 업종 중위수 {peer.get('median_pbr') or 'N/A'} → {pbr_vs}
- ROE:  당사 {metrics.get('ROE') or 'N/A'}% / 업종 중위수 {peer.get('median_roe') or 'N/A'}% → {roe_vs}
이 비교 수치가 있을 때만 "valuation" 섹션에 업종 대비 설명을 1문장 추가할 것.
없는 항목(N/A)은 언급하지 말 것."""
    else:
        peer_block = ""

    if not signals:
        category_block = f"""
[summary] 카테고리만 사용. 신호가 없으므로 아래와 비슷한 톤의 짧은 중립적
문장 하나만 쓸 것 (그대로 베끼지 말고 자연스럽게 표현을 바꿔서 쓸 것):
예시 톤: "{random.choice(NO_SIGNAL_EXAMPLE_PHRASES)}"
절대로 스스로 PER/PBR/ROE 수치를 만들어내거나 그것을 근거로 저평가/고평가
판단을 내리지 말 것 (그런 숫자는 너에게 주어지지 않았다)
"""
        output_keys = '"summary": "신호 없음 안내 1문장"'
    else:
        blocks = []
        keys = []
        for category in CATEGORY_ORDER:
            if category not in grouped:
                continue
            sigs = grouped[category]
            sig_lines = "\n".join(f"  - {s['text']}" for s in sigs)
            blocks.append(f"[{category}] 카테고리 신호:\n{sig_lines}")
            keys.append(f'"{category}": "이 카테고리 신호만 사용한 1~2문장"')
        category_block = "\n\n" + "\n\n".join(blocks)
        output_keys = ",\n  ".join(keys)

    return f"""너는 한국 주식 투자 리서치 애널리스트야. 아래 정보를 바탕으로 "{company_name}"에 대한
기업개요와 밸류에이션 판단을 작성해줘.

[원칙]
- 숫자만 나열하지 말고, 반드시 "왜 그런지"를 설명할 것
- [판단된 신호]는 이미 공시된 과거 실적을 코드로 계산한 결과다. 반드시 과거/현재
  시제로 서술할 것. "~될 것으로 예상됩니다" 같은 미래 예측 어조는 사용하지 말 것
- 기업개요 섹션: 아래 [사업 정보]만 참고. 부차적 사실(본사 위치, 종속기업 수 등)은 빼고
  "무엇을 팔고 어떻게 돈을 버는지" 핵심만 2문장 이내로 압축할 것
- 밸류에이션 판단은 아래 [신호 카테고리]별로 독립 섹션으로 작성. 카테고리 간 교차 언급 금지
- 신호에 없는 이유(산업 전망, 경쟁사 동향 등)를 임의로 만들어내지 말 것
{"- 업종/경쟁사 비교 데이터는 아직 없으므로 언급하지 말 것" if not peer else ""}

[사업 정보 - 기업개요 작성용 (Step 1에서 추출된 구조화 데이터)]
{facts_block}
{peer_block}

[신호 카테고리 - 여기 적힌 숫자 외의 PER/PBR/ROE 숫자는 절대로 스스로 추정하거나 언급하지 말 것]{category_block}

[출력 형식]
다른 텍스트 없이 아래 JSON 형식으로만 응답해:
{{
  "overview": "기업개요 핵심만 2문장 이내",
  "valuation_sections": {{
  {output_keys}
  }}
}}
"""


class LLMQuotaExhausted(RuntimeError):
    """API 크레딧/쿼터 소진 — 이후 모든 호출이 실패하므로 배치는 종목 단위로 넘기지 말고 즉시 중단."""


def call_llm(prompt: str) -> dict:
    """OpenAI GPT API 호출. response_format=json_object로 JSON 깨짐 방지.
    모델을 교체하려면 이 함수만 수정하면 됨."""
    from openai import OpenAI, RateLimitError

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
    except RateLimitError as exc:
        if getattr(exc, "code", None) in ("insufficient_quota", "credit_balance_exhausted") \
                or "insufficient_quota" in str(exc):
            raise LLMQuotaExhausted(f"LLM API 크레딧 소진: {exc}") from exc
        raise
    return json.loads(response.choices[0].message.content)


def extract_structured_facts(company_name: str, business_overview: str) -> dict:
    """Step 1: 사업보고서 원문 -> 구조화된 facts JSON."""
    prompt = build_extraction_prompt(company_name, business_overview)
    try:
        return call_llm(prompt)
    except Exception:
        return {
            "business_model": None,
            "key_products_or_services": [],
            "revenue_structure": None,
            "notable_facts": [],
        }


def save_report(
    engine: Engine,
    stock_code: str,
    llm_result: dict,
    metrics: dict,
    signals: list[dict],
    extracted_facts: dict,
) -> None:
    # data_basis: Step 1 / Step 2 출처를 명시적으로 기록 (디버깅/모델 비교용)
    data_basis = {
        "overview": {
            "basis": "사업보고서 기반 (Step 1 LLM 추출)",
            "extracted_facts": extracted_facts,
        },
        "valuation_sections": {
            "basis": "정량데이터 기반 신호 + LLM 문장화 (Step 2)",
        },
    }
    metrics_snapshot = {**metrics, "signals": signals}
    valuation_sections = llm_result.get("valuation_sections", {})
    fallback_text = " ".join(valuation_sections.values()) if valuation_sections else ""

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO ai_reports
                    (stock_code, overview, valuation_opinion, valuation_sections, metrics_snapshot, data_basis)
                VALUES
                    (:code, :overview, :valuation, :sections, :metrics, :basis)
                ON CONFLICT (stock_code) DO UPDATE SET
                    overview           = EXCLUDED.overview,
                    valuation_opinion  = EXCLUDED.valuation_opinion,
                    valuation_sections = EXCLUDED.valuation_sections,
                    metrics_snapshot   = EXCLUDED.metrics_snapshot,
                    data_basis         = EXCLUDED.data_basis,
                    generated_at       = now()
                """
            ),
            {
                "code":     stock_code,
                "overview": llm_result.get("overview"),
                "valuation": fallback_text,
                "sections": json.dumps(valuation_sections, ensure_ascii=False),
                "metrics":  json.dumps(metrics_snapshot, ensure_ascii=False),
                "basis":    json.dumps(data_basis, ensure_ascii=False),
            },
        )


def generate_report_for_stock(engine: Engine, stock_code: str) -> dict | None:
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

    metrics = compute_metrics(history, market)
    signals = compute_signals(history, metrics)

    # 업종 피어 비교 (sector가 있을 때만)
    peer = None
    if market.get("sector"):
        peer = get_peer_metrics(engine, stock_code, market["sector"])

    # Step 1: 사업보고서 원문 -> 구조화된 facts (사업보고서 기반)
    extracted_facts = extract_structured_facts(market["name"], overview_text)

    # Step 2: facts + 신호 + 피어 -> 서술 생성 (LLM 추론)
    narrative_prompt = build_narrative_prompt(market["name"], extracted_facts, metrics, signals, peer)
    llm_result = call_llm(narrative_prompt)

    save_report(engine, stock_code, llm_result, metrics, signals, extracted_facts)
    return llm_result


if __name__ == "__main__":
    import sys

    target_code = sys.argv[1] if len(sys.argv) > 1 else "005930"

    engine = get_engine()
    market = get_stock_market_data(engine, target_code)
    history = get_financial_history(engine, target_code)
    overview_text = get_business_overview(engine, target_code)
    metrics = compute_metrics(history, market)
    signals = compute_signals(history, metrics)

    print(f"=== {market['name']} ({target_code}) ===")
    print(f"신호 {len(signals)}개:")
    for s in signals:
        print(f"  [{s['signal']}] {s['text']}")
    print()

    print("--- Step 1: 사업보고서 원문 -> facts 추출 ---")
    facts = extract_structured_facts(market["name"], overview_text or "")
    print(json.dumps(facts, ensure_ascii=False, indent=2))
    print()

    print("--- Step 2: facts + 신호 -> 서술 생성 ---")
    result = generate_report_for_stock(engine, target_code)
    if result:
        print("[overview]")
        print(result.get("overview"))
        print()
        print("[valuation_sections]")
        for category, sentence in result.get("valuation_sections", {}).items():
            meta = CATEGORY_META.get(category, {"title": category, "emoji": "•"})
            print(f"{meta['emoji']} {meta['title']}")
            print(f"  {sentence}")