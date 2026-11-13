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
