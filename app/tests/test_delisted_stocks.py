"""load_delisted_stocks.py의 사유 분류·대상 필터 테스트 (DB·네트워크 미사용)."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "pipeline"))

from load_delisted_stocks import classify_reason, is_target  # noqa: E402


def test_classify_distressed():
    for r in ["감사의견 의견거절 2년 계속",
              "기업의 계속성, 경영의 투명성 및 기타 공익과 투자자 보호",
              "감사범위제한으로인한 감사의견한정 2년 계속",
              "시가총액 미달"]:
        assert classify_reason(r) == "부실", r


def test_classify_non_distressed():
    for r in ["지주회사(최대주주등)의 완전자회사화 등",
              "해산 사유 발생",
              "신청에 의한 상장폐지"]:
        assert classify_reason(r) == "비부실", r


def test_classify_other():
    assert classify_reason("SPAC 상장예비심사청구서 미제출 등") == "기타"
    assert classify_reason(None) == "기타"
    assert classify_reason("") == "기타"


def test_is_target_filters():
    assert is_target("KOSPI", "주권", "015540", "2023-11-07", "2022-01-01")
    assert not is_target("KOSDAQ", "주권", "015540", "2023-11-07", "2022-01-01")
    assert not is_target("KOSPI", "신주인수권증서", "0791601D", "2023-08-25", "2022-01-01")
    assert not is_target("KOSPI", "주권", "005935", "2023-11-07", "2022-01-01")  # 우선주
    assert not is_target("KOSPI", "주권", "015540", "2021-12-31", "2022-01-01")
