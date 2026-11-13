"""
DART 재무데이터 수집 파이프라인 스크립트.

app/tasks.py의 _fetch_and_save 로직을 독립 실행 가능하게 분리.
G_quant (YoY 성장률) 계산을 위해 2022, 2024년 재무 데이터 수집에 사용.

사용:
  python collect_financial_data.py --year 2022
  python collect_financial_data.py --year 2024
  python collect_financial_data.py --year 2022 --limit 10  # 테스트
  python collect_financial_data.py --year 2025 --refresh   # 기존 종목도 재수집
  python collect_financial_data.py --year 2023 --delisted  # delisted_stocks 중 stocks에 없는 종목

DART API 제한: 초당 ~10건 → sleep 0.12s 기본값
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dotenv import load_dotenv

import requests
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(__file__))
from batch_collect_runner import get_engine

load_dotenv()
DART_API_KEY = os.getenv("DART_API_KEY")

DART_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"

TARGET_ACCOUNTS = {
    "매출액", "영업이익", "영업이익(손실)",
    "당기순이익(손실)", "당기순이익",
    "자본총계", "부채총계", "자산총계",
    "자본금", "이익잉여금",
    "유동자산", "비유동자산",
    "유동부채", "비유동부채",
    "법인세차감전 순이익", "총포괄손익",
}


def get_stocks(engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT code, corp_code FROM stocks WHERE corp_code IS NOT NULL"
        )).fetchall()
    return [{"code": r[0], "corp_code": r[1]} for r in rows]


def get_delisted_stocks(engine) -> list[dict]:
    """delisted_stocks 중 stocks에 없는 종목 (폐지 종목 보충용)."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT DISTINCT d.code, d.corp_code FROM delisted_stocks d
            WHERE d.corp_code IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM stocks s WHERE s.code = d.code)
        """)).fetchall()
    return [{"code": r[0], "corp_code": r[1]} for r in rows]


def already_collected(engine, year: int) -> set[str]:
    """해당 연도에 데이터가 이미 있는 종목 코드 반환."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT code FROM financial_data WHERE year = :y"
        ), {"y": str(year)}).fetchall()
    return {r[0] for r in rows}


def fetch_and_save(engine, code: str, corp_code: str, year: int) -> int:
    """단일 종목 DART 재무데이터 수집 → financial_data 저장. 반환값=저장 건수."""
    params = {
        "crtfc_key":  DART_API_KEY,
        "corp_code":  corp_code,
        "bsns_year":  str(year),
        "reprt_code": "11011",  # 사업보고서
        "fs_div":     "CFS",
    }

    try:
        res  = requests.get(DART_URL, params=params, timeout=15)
        data = res.json()
    except Exception as e:
        return -1  # 네트워크 오류

    if data.get("status") != "000":
        return 0  # 데이터 없음 (비상장/IFRS 미적용 등)

    rows = []
    for item in data.get("list", []):
        account_nm = item.get("account_nm", "").strip()
        fs_type    = item.get("fs_div", "OFS")

        if account_nm not in TARGET_ACCOUNTS:
            continue
        if fs_type not in ("CFS", "OFS"):
            continue

        amount_str = item.get("thstrm_amount", "").replace(",", "").strip()
        if not amount_str:
            continue
        try:
            amount = int(amount_str)
        except ValueError:
            continue

        rows.append({
            "code":       code,
            "year":       str(year),
            "fs_type":    fs_type,
            "account_nm": account_nm,
            "amount":     amount,
        })

    if not rows:
        return 0

    # CFS/OFS 둘 다 저장 — uq_financial_data_fs (code, year, fs_type, account_nm).
    # 조회 쪽에서 CFS 우선 적용. 같은 응답 안의 동일 키 중복만 첫 행 기준으로 제거.
    seen: set = set()
    deduped = []
    for r in rows:
        k = (r["code"], r["year"], r["fs_type"], r["account_nm"])
        if k not in seen:
            seen.add(k)
            deduped.append(r)

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO financial_data (code, year, fs_type, account_nm, amount)
            VALUES (:code, :year, :fs_type, :account_nm, :amount)
            ON CONFLICT ON CONSTRAINT uq_financial_data_fs
            DO UPDATE SET amount = EXCLUDED.amount
        """), deduped)

    return len(deduped)


def run(year: int, sleep_sec: float, limit: int | None, refresh: bool = False,
        delisted: bool = False) -> None:
    if not DART_API_KEY:
        print("오류: DART_API_KEY 환경변수가 설정되지 않았습니다.")
        sys.exit(1)

    engine = get_engine()
    stocks = get_delisted_stocks(engine) if delisted else get_stocks(engine)
    done   = set() if refresh else already_collected(engine, year)

    targets = [s for s in stocks if s["code"] not in done]
    skipped = len(stocks) - len(targets)

    if limit:
        targets = targets[:limit]

    print(f"[{year}년 재무데이터 수집]{' (폐지 종목)' if delisted else ''}")
    print(f"  전체: {len(stocks)}개  스킵(기존): {skipped}개  수집 대상: {len(targets)}개")
    if limit:
        print(f"  --limit {limit} 적용")

    total_rows = 0
    errors     = 0
    no_data    = 0

    for i, s in enumerate(targets, 1):
        n = fetch_and_save(engine, s["code"], s["corp_code"], year)

        if n == -1:
            errors += 1
            result = "오류"
        elif n == 0:
            no_data += 1
            result = "데이터없음"
        else:
            total_rows += n
            result = f"{n}건"

        if i <= 5 or i % 100 == 0 or i == len(targets):
            print(f"  [{i:>4}/{len(targets)}] {s['code']}  {result}")

        time.sleep(sleep_sec)

    print(f"\n완료")
    print(f"  저장: {total_rows:,}건  데이터없음: {no_data}개  오류: {errors}개")

    with engine.connect() as conn:
        cnt = conn.execute(text(
            "SELECT COUNT(*) FROM financial_data WHERE year = :y"
        ), {"y": str(year)}).scalar()
    print(f"  financial_data {year}년 누적: {cnt:,}건")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DART 재무데이터 수집")
    parser.add_argument("--year",  type=int, required=True, help="수집 연도 (예: 2022)")
    parser.add_argument("--sleep", type=float, default=0.12, help="종목간 대기(초), 기본 0.12")
    parser.add_argument("--limit", type=int, default=None, help="테스트용 종목 수 제한")
    parser.add_argument("--refresh", action="store_true",
                        help="이미 수집된 종목도 다시 수집 (덮어써진 연도 복구용)")
    parser.add_argument("--delisted", action="store_true",
                        help="delisted_stocks 중 stocks에 없는 종목만 수집")
    args = parser.parse_args()

    run(args.year, args.sleep, args.limit, args.refresh, args.delisted)
