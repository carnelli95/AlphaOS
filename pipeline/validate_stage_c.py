"""
Stage C 품질 검증 스크립트 — 업종별 대표 종목 재생성 후 지표 비교.

사용법:
    python validate_stage_c.py                  # 기본 대표 종목 목록
    python validate_stage_c.py 005930 000660    # 특정 코드 지정
"""

from __future__ import annotations

import sys
import time
import json
from datetime import datetime

from sqlalchemy import text

from generate_stage_c import generate_stage_c_for_stock, get_engine

# 업종별 대표 종목 (기본 목록)
DEFAULT_CODES = [
    "005930",  # 삼성전자   (전자/반도체)
    "000660",  # SK하이닉스 (반도체)
    "005380",  # 현대차     (자동차)
    "066570",  # LG전자     (가전/전자)
    "035420",  # NAVER      (IT 플랫폼)
    "035720",  # 카카오     (IT 플랫폼)
    "068270",  # 셀트리온   (바이오)
    "005490",  # POSCO홀딩스 (철강)
    "000150",  # 두산       (지주회사)
    "001200",  # 유진투자증권 (금융)
    "051910",  # LG화학     (화학)
    "003550",  # LG         (지주회사)
    "012330",  # 현대모비스 (자동차부품)
    "034730",  # SK         (지주회사)
    "207940",  # 삼성바이오로직스 (바이오)
    "096770",  # SK이노베이션 (에너지)
    "032830",  # 삼성생명   (보험)
    "105560",  # KB금융     (금융지주)
    "003490",  # 대한항공   (항공)
    "010130",  # 고려아연   (비철금속)
]

def run(codes: list[str], sleep_secs: float = 1.0) -> None:
    engine = get_engine()

    quality = {
        "growth_empty": 0, "risk_empty": 0, "moat_empty": 0,
        "growth_total": 0, "risk_total": 0, "moat_total": 0,
    }
    success = skip = error = 0

    print(f"=== Stage C 품질 검증 — {len(codes)}개 종목 ({datetime.now():%H:%M}) ===\n")

    for i, code in enumerate(codes, 1):
        try:
            result = generate_stage_c_for_stock(engine, code)
            if result is None:
                skip += 1
                print(f"[{i:>2}/{len(codes)}] {code}  스킵")
            else:
                g = len(result.get("growth_drivers", {}).get("facts", []))
                r = len(result.get("risks", {}).get("facts", []))
                m = len(result.get("moat", {}).get("facts", []))
                quality["growth_total"] += g
                quality["risk_total"]   += r
                quality["moat_total"]   += m
                if g == 0: quality["growth_empty"] += 1
                if r == 0: quality["risk_empty"]   += 1
                if m == 0: quality["moat_empty"]   += 1

                # 종목명 조회
                with engine.connect() as conn:
                    row = conn.execute(
                        text("SELECT name FROM stocks WHERE code = :c"), {"c": code}
                    ).fetchone()
                name = row.name if row else code

                flag_r = " ⚠" if r == 0 else ""
                flag_m = " ⚠" if m == 0 else ""
                print(f"[{i:>2}/{len(codes)}] {code}  {name:<20}  growth={g}  risk={r}{flag_r}  moat={m}{flag_m}")
                success += 1
        except Exception as e:
            error += 1
            print(f"[{i:>2}/{len(codes)}] {code}  실패: {e}")

        time.sleep(sleep_secs)

    print()
    print(f"완료: {datetime.now():%H:%M:%S}  성공 {success} / 스킵 {skip} / 실패 {error}")

    if success > 0:
        print()
        print("=== 품질 지표 ===")
        fmt = lambda n, t: f"{n}/{t} ({n/t*100:.1f}%)" if t else "N/A"
        print(f"growth 빈 배열: {fmt(quality['growth_empty'], success)}")
        print(f"risk   빈 배열: {fmt(quality['risk_empty'],   success)}  ← 이전 배치 12%")
        print(f"moat   빈 배열: {fmt(quality['moat_empty'],   success)}  ← 이전 배치 2.3%")
        print(f"평균 Fact 수  — growth: {quality['growth_total']/success:.1f}  "
              f"risk: {quality['risk_total']/success:.1f}  moat: {quality['moat_total']/success:.1f}")


if __name__ == "__main__":
    codes = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_CODES
    run(codes)
