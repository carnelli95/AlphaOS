"""
사업보고서 원본 제출일 점검 (읽기 전용 — DB는 SELECT만, DART 공시 목록 API 조회).

backtest 보유 구간은 N+1년 4월 첫 거래일에 시작한다(DECISIONS.md 2026-11-15).
사업보고서가 3/31 이후에 처음 제출된 종목은 그 시점에도 미래 정보가 남는다.
이 스크립트는 그런 종목 수를 사업연도별로 센다.

  · 대상: stocks(corp_code 있음) ∪ delisted_stocks
  · DART list.json, pblntf_detail_ty=A001(사업보고서), 종목별 1회 조회
  · 원본 = report_nm이 '['로 시작하지 않는 것(기재정정·첨부정정 제외)
  · 사업연도는 report_nm의 '(YYYY.MM)'. 12월 결산만 집계하고 그 외 결산월은 따로 센다.

사용:
  cd pipeline && python check_filing_dates.py [--out 파일.csv]
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from datetime import date

import requests
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)
from batch_collect_runner import get_engine

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
YEARS = (2022, 2023, 2024, 2025)
_PERIOD = re.compile(r"\((\d{4})\.(\d{2})\)")


def parse_report(report_nm: str) -> tuple[int, int, bool] | None:
    """'[기재정정]사업보고서 (2023.12)' → (2023, 12, is_original=False)."""
    m = _PERIOD.search(report_nm or "")
    if not m or "사업보고서" not in report_nm:
        return None
    return int(m.group(1)), int(m.group(2)), not report_nm.strip().startswith("[")


def is_late(fiscal_year: int, rcept_dt: str) -> bool:
    """12월 결산 사업연도 N의 원본이 N+1년 3/31 이후 제출됐으면 True."""
    return rcept_dt > f"{fiscal_year + 1}0331"


def fetch_annual_reports(corp_code: str, key: str) -> list[dict] | None:
    params = {"crtfc_key": key, "corp_code": corp_code, "bgn_de": "20230101",
              "end_de": date.today().strftime("%Y%m%d"), "pblntf_detail_ty": "A001",
              "page_count": 100}
    for _ in range(3):
        try:
            data = requests.get(DART_LIST_URL, params=params, timeout=15).json()
            if data.get("status") == "013":   # 조회 결과 없음
                return []
            if data.get("status") != "000":
                return None
            return data.get("list", [])
        except Exception:  # noqa: BLE001 — URL(키 포함) 출력 방지
            time.sleep(1)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="종목·연도별 원본 제출일 CSV 저장 경로")
    args = ap.parse_args()
    key = os.getenv("DART_API_KEY")
    if not key:
        raise SystemExit("오류: DART_API_KEY 환경변수가 설정되지 않았습니다.")

    with get_engine().connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        corps = conn.execute(text("""
            SELECT code, corp_code FROM stocks WHERE corp_code IS NOT NULL
            UNION
            SELECT code, corp_code FROM delisted_stocks WHERE corp_code IS NOT NULL
        """)).fetchall()
        conn.rollback()

    first: dict[tuple[str, int], str] = {}   # (code, FY) → 원본 최초 제출일 (12월 결산)
    non_dec: set[str] = set()
    errors = 0
    for i, (code, corp_code) in enumerate(corps, 1):
        reports = fetch_annual_reports(corp_code, key)
        if reports is None:
            errors += 1
        for r in reports or []:
            p = parse_report(r.get("report_nm", ""))
            if not p:
                continue
            fy, mm, original = p
            if mm != 12:
                non_dec.add(code)
                continue
            if original and fy in YEARS:
                k = (code, fy)
                first[k] = min(first.get(k, "99999999"), r["rcept_dt"])
        if i % 200 == 0:
            print(f"  [{i}/{len(corps)}]", flush=True)
        time.sleep(0.12)

    print(f"\n대상 {len(corps)}개 법인 (조회 오류 {errors}), 12월 외 결산 {len(non_dec)}개")
    print(f"  {'사업연도':<8} {'원본 확인':>8} {'3/31 이후':>9} {'비율':>6}  늦은 제출일 분포")
    for fy in YEARS:
        dts = [d for (c, y), d in first.items() if y == fy]
        late = sorted(d for d in dts if is_late(fy, d))
        rng = f"{late[0]}~{late[-1]}" if late else "-"
        print(f"  {fy:<8} {len(dts):>8} {len(late):>9} {len(late) / max(len(dts), 1):>6.1%}  {rng}")

    if args.out:
        with open(args.out, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["code", "fiscal_year", "first_rcept_dt", "late"])
            for (c, y), d in sorted(first.items()):
                w.writerow([c, y, d, is_late(y, d)])
        print(f"\nCSV 저장: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
