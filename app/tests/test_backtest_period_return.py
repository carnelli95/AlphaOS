"""backtest.period_return 상장폐지·거래정지 규칙 테스트 (DECISIONS.md 2026-11-13, DB 미사용).

거래정지일은 volume=None, close=정지 직전 가격으로 표현한다(price_history와 같음).
"""

import pathlib
import sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "pipeline"))

from backtest import period_return  # noqa: E402

D0 = date(2024, 1, 2)
START_TO = date(2024, 3, 31)
END_FROM = date(2024, 10, 1)
END_TO = date(2024, 12, 31)


def _r(rows, delisting=None):
    return period_return(rows, D0, START_TO, END_FROM, END_TO, delisting)


def test_normal():
    rows = [(D0, 100, 10), (date(2024, 12, 30), 120, 10)]
    ret, st = _r(rows)
    assert st == "ok" and abs(ret - 0.20) < 1e-9


def test_delisted_before_period_excluded():
    rows = [(D0, 100, 10)]
    assert _r(rows, delisting=date(2023, 12, 1)) == (None, "x_not_listed")


def test_halt_at_start_excluded():
    rows = [(D0, 100, None), (date(2024, 12, 30), 120, 10)]
    assert _r(rows) == (None, "x_halt_start")


def test_new_listing_uses_first_row():
    rows = [(date(2024, 2, 15), 50, 10), (date(2024, 12, 30), 60, 10)]
    ret, st = _r(rows)
    assert st == "ok" and abs(ret - 0.20) < 1e-9


def test_mid_period_delisting_uses_last_trade_and_skips_lower_filter():
    # 부실 폐지: 정지 후 정리매매에서 99% 하락 → −95% 필터 예외
    rows = [(D0, 2000, 10), (date(2024, 3, 4), 2000, None), (date(2024, 5, 2), 50, 10),
            (date(2024, 5, 10), 14, 10), (date(2024, 5, 13), 14, None)]
    ret, st = _r(rows, delisting=date(2024, 5, 13))
    assert st == "delisted" and abs(ret - (14 / 2000 - 1)) < 1e-9 and ret < -0.95


def test_merger_delisting_uses_last_trade():
    rows = [(D0, 100, 10), (date(2024, 6, 20), 110, 10), (date(2024, 6, 21), 110, None)]
    ret, st = _r(rows, delisting=date(2024, 7, 9))
    assert st == "delisted" and abs(ret - 0.10) < 1e-9


def test_halt_at_end_then_delisted_uses_liquidation_price():
    # 국보 유형: 연말 정지(종가=정지 직전가) → 다음 해 정리매매 후 폐지
    rows = [(D0, 2110, 10), (date(2024, 12, 30), 2110, None),
            (date(2025, 1, 16), 86, 10), (date(2025, 1, 26), 14, 10)]
    ret, st = _r(rows, delisting=date(2025, 1, 27))
    assert st == "halt_end_delisted" and abs(ret - (14 / 2110 - 1)) < 1e-9


def test_halt_at_end_then_resumed_uses_first_trade_after():
    rows = [(D0, 100, 10), (date(2024, 12, 30), 100, None), (date(2025, 2, 3), 70, 10),
            (date(2025, 2, 4), 90, 10)]
    ret, st = _r(rows)
    assert st == "halt_end_resumed" and abs(ret - (-0.30)) < 1e-9


def test_halt_at_end_unresolved_excluded():
    rows = [(D0, 100, 10), (date(2024, 12, 30), 100, None), (date(2025, 3, 3), 100, None)]
    assert _r(rows) == (None, "x_halt_unresolved")


def test_lower_filter_still_applies_to_non_delisted():
    rows = [(D0, 100, 10), (date(2024, 12, 30), 3, 10)]
    assert _r(rows) == (None, "x_outlier")


def test_upper_filter_applies_to_all():
    rows = [(D0, 100, 10), (date(2024, 6, 1), 1200, 10)]
    assert _r(rows, delisting=date(2024, 6, 3)) == (None, "x_outlier")


def test_no_end_price():
    rows = [(D0, 100, 10), (date(2024, 5, 1), 100, 10)]
    assert _r(rows) == (None, "x_no_end")


# ─── 보유 구간 (DECISIONS.md 2026-11-15) ───────────────────────────────────────

from backtest import compute_ic_stats, period_window  # noqa: E402


def test_period_window_full():
    w = period_window(2024, today=date(2026, 11, 15))
    assert w["start_from"] == date(2024, 4, 1)
    assert w["start_to"] == date(2024, 6, 30)
    assert (w["end_from"], w["end_to"]) == (date(2025, 1, 1), date(2025, 3, 31))
    assert w["partial"] is False


def test_period_window_partial_before_end_window():
    w = period_window(2026, today=date(2026, 11, 15))
    assert w["partial"] is True
    assert w["end_to"] == date(2026, 11, 15)
    assert w["end_from"] == date(2026, 4, 1)


def test_period_window_partial_inside_end_window():
    w = period_window(2025, today=date(2026, 2, 10))
    assert w["partial"] is True
    assert (w["end_from"], w["end_to"]) == (date(2026, 1, 1), date(2026, 2, 10))


def test_ic_handles_ties():
    # 점수 동점이 많아도 평균 순위 Pearson이면 완전 단조일 때 IC=1에 가깝다
    scores = {f"c{i}": i // 3 for i in range(30)}          # 3개씩 동점
    returns = {f"c{i}": float(i // 3) for i in range(30)}  # 같은 순서
    assert abs(compute_ic_stats(scores, returns)["ic"] - 1.0) < 1e-9


def test_multi_period_t_uses_t_distribution():
    from backtest import multi_period_t
    ics = [0.1030, 0.2371, 0.1214, 0.0830]
    t_stat, p = multi_period_t(ics)
    assert abs(t_stat - 3.938) < 0.01
    # 정규 근사였다면 p≈0.0001. 자유도 3 t분포에서는 0.02~0.04 사이
    assert 0.02 < p < 0.04
