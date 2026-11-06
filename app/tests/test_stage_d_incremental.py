import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "pipeline"))

from collect_stage_d import _is_periodic, merge_disclosures  # noqa: E402
from generate_stage_d_news import combine_news  # noqa: E402

TODAY = date(2026, 11, 6)


def _disc(rcept_no: str, rcept_dt: str, name: str = "단일판매ㆍ공급계약체결") -> dict:
    return {"rcept_no": rcept_no, "rcept_dt": rcept_dt, "report_nm": name,
            "recency_days": 0, "corp_cls": "Y"}


def _news(url: str, d: str, score: float, event_type: str = "other") -> dict:
    return {"news_url": url, "date": d, "fact": url, "weighted_score": score,
            "event_type": event_type, "source_type": "news", "recency_days": 0}


def test_is_periodic_strips_amendment_tags():
    assert _is_periodic("사업보고서 (2025.12)")
    assert _is_periodic("[기재정정]사업보고서 (2025.12)")
    assert _is_periodic("[첨부정정][기재정정]분기보고서 (2026.03)")
    assert not _is_periodic("[기재정정]단일판매ㆍ공급계약체결")
    assert not _is_periodic("주요사항보고서(자기주식취득결정)")


def test_merge_disclosures_dedupes_recomputes_recency_and_drops_old():
    existing = [
        _disc("1", "20260801"),
        _disc("old", "20260401"),                          # 180일 초과 → 제거
        _disc("p", "20260901", "[기재정정]반기보고서 (2026.06)"),  # 정기보고서 유입분 → 제거
    ]
    new = [_disc("1", "20260801"), _disc("2", "20261105")]
    out = merge_disclosures(existing, new, period_days=180, today=TODAY)

    assert [d["rcept_no"] for d in out] == ["2", "1"]       # 최신순, 중복 1건
    assert out[0]["recency_days"] == 1
    assert out[1]["recency_days"] == (TODAY - date(2026, 8, 1)).days


def test_combine_news_keeps_old_important_news_and_caps():
    old = [_news("a", "20260901", 0.9, "major_contract")]
    new = [_news(f"n{i}", "20261101", 0.1 * i, "other") for i in range(1, 6)]
    out = combine_news(old, new, today=TODAY)

    urls = [f["news_url"] for f in out]
    assert urls[0] == "a"                                   # 재검색에 없어도 유지
    assert len(out) <= 5
    assert sum(f["event_type"] == "other" for f in out) <= 2  # event_type당 최대 2개


def test_combine_news_drops_out_of_window_and_dedupes_by_url():
    old = [_news("a", "20260101", 0.9), _news("b", "20261001", 0.5)]
    new = [_news("b", "20261001", 0.6)]
    out = combine_news(old, new, today=TODAY, window_days=180)

    assert [f["news_url"] for f in out] == ["b"]
    assert out[0]["weighted_score"] == 0.6                  # 새 수집본 우선


def test_drop_routine_removes_noise_and_same_day_amendment_pairs():
    from generate_stage_d import _drop_routine
    disc = [
        _disc("4", "20261029", "[기재정정]연결재무제표기준영업(잠정)실적(공정공시)"),
        _disc("3", "20261029", "연결재무제표기준영업(잠정)실적(공정공시)"),
        _disc("2", "20261029", "기타경영사항(자율공시)"),
        _disc("1", "20261104", "최대주주등소유주식변동신고서        "),
        _disc("0", "20260901", "연결재무제표기준영업(잠정)실적(공정공시)"),  # 다른 날 → 유지
    ]
    assert [d["rcept_no"] for d in _drop_routine(disc)] == ["4", "0"]
