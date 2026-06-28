"""
price_history 수집기 — 종목별 전체 기간 방식 (pykrx 1.2.8 호환).

pykrx 1.2.8 변경 사항:
  get_market_ohlcv(날짜, market=)  → KRX API 오류 (사용 불가)
  get_market_ohlcv(from, to, 티커) → 정상 (이 방식 사용)

수집 전략:
  종목별 1 call:  stock.get_market_ohlcv(from_str, to_str, code)
  지수:           stock.get_index_ohlcv(from_str, to_str, ticker)  ← 범위 호출 유지

재시작 안전:
  각 종목의 price_history 최신 날짜 확인 → 이미 완료된 종목 스킵
  ON CONFLICT DO NOTHING 으로 중복 방지

사용:
  python collect_price_history.py --from 2022-01-01
  python collect_price_history.py --from 2022-01-01 --to 2024-12-31
  python collect_price_history.py --from 2025-01-01  # 증분 업데이트
"""

from __future__ import annotations

import argparse
import os
import sys
import io
import time
from datetime import date, timedelta, datetime

import pandas as pd
from pykrx import stock
from sqlalchemy import text

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(__file__))

from batch_collect_runner import get_engine

MARKET_INDEXES = {
    "IDX_KOSPI":  "KS11",    # FinanceDataReader 심볼
    "IDX_KOSDAQ": "KQ11",
}


# ─── DB 헬퍼 ─────────────────────────────────────────────────────────────────

def ensure_table(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS price_history (
                code   VARCHAR NOT NULL,
                date   DATE    NOT NULL,
                open   BIGINT,
                high   BIGINT,
                low    BIGINT,
                close  BIGINT NOT NULL,
                volume BIGINT,
                PRIMARY KEY (code, date)
            )
        """))
    print("price_history 테이블 확인/생성 완료")


def get_db_codes(engine) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT code FROM stocks")).fetchall()
    return [r[0] for r in rows]


def get_latest_dates(engine, codes: list[str]) -> dict[str, date]:
    """각 종목의 price_history 최신 수집 날짜."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT code, MAX(date)
            FROM price_history
            WHERE code = ANY(:codes)
            GROUP BY code
        """), {"codes": codes}).fetchall()
    return {r[0]: r[1] for r in rows}


# ─── 종목별 수집 ──────────────────────────────────────────────────────────────

def _to_int(v) -> int | None:
    try:
        if v is None or (isinstance(v, float) and (v != v)):  # NaN check
            return None
        iv = int(v)
        return iv if iv > 0 else None
    except Exception:
        return None


def collect_stock(engine, code: str, from_d: date, to_d: date) -> int:
    """
    단일 종목의 OHLCV 데이터를 price_history에 저장.
    pykrx get_market_ohlcv(from, to, ticker) 사용 — 인덱스=날짜, 컬럼=한글.
    """
    from_str = from_d.strftime("%Y%m%d")
    to_str   = to_d.strftime("%Y%m%d")

    try:
        df = stock.get_market_ohlcv(from_str, to_str, code)
    except Exception as e:
        print(f"  {code}: pykrx 오류 — {e}")
        return 0

    if df is None or df.empty:
        return 0

    rows = []
    for idx_date, row in df.iterrows():
        d = idx_date.date() if hasattr(idx_date, "date") else idx_date

        # 컬럼명 한글/영문 모두 대응
        def _get(*keys):
            for k in keys:
                v = row.get(k)
                if v is not None:
                    return v
            return None

        close = _to_int(_get("종가", "Close", "close"))
        if close is None:
            continue

        rows.append({
            "code":   code,
            "date":   d,
            "open":   _to_int(_get("시가",   "Open",   "open")),
            "high":   _to_int(_get("고가",   "High",   "high")),
            "low":    _to_int(_get("저가",   "Low",    "low")),
            "close":  close,
            "volume": _to_int(_get("거래량", "Volume", "volume")),
        })

    if not rows:
        return 0

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO price_history (code, date, open, high, low, close, volume)
            VALUES (:code, :date, :open, :high, :low, :close, :volume)
            ON CONFLICT (code, date) DO NOTHING
        """), rows)

    return len(rows)


# ─── 지수 수집 (FinanceDataReader — pykrx get_index_ohlcv 로그인 필요로 대체) ──

def collect_all_indexes(engine, from_d: date, to_d: date) -> None:
    """
    KOSPI(KS11) / KOSDAQ(KQ11) 지수를 price_history에 저장.
    pykrx get_index_ohlcv는 1.2.8에서 KRX 로그인 필요 → FinanceDataReader 사용.
    Close 값은 지수 포인트(소수 포함)를 정수로 반올림해 저장.
    """
    try:
        import FinanceDataReader as fdr
    except ImportError:
        print("  FinanceDataReader 미설치 — pip install finance-datareader")
        return

    from_str = from_d.strftime("%Y%m%d")
    to_str   = to_d.strftime("%Y%m%d")

    for code_key, fdr_symbol in MARKET_INDEXES.items():
        try:
            df = fdr.DataReader(fdr_symbol, from_str, to_str)
        except Exception as e:
            print(f"  지수 오류 ({code_key}): {e}")
            continue

        if df is None or df.empty:
            print(f"  {code_key}: 데이터 없음")
            continue

        def _to_int_f(v):
            try:
                fv = float(v)
                return int(round(fv)) if fv > 0 else None
            except Exception:
                return None

        rows = []
        for idx_date, row in df.iterrows():
            d = idx_date.date() if hasattr(idx_date, "date") else idx_date
            close = _to_int_f(row.get("Close"))
            if close is None:
                continue
            rows.append({
                "code":   code_key,
                "date":   d,
                "open":   _to_int_f(row.get("Open")),
                "high":   _to_int_f(row.get("High")),
                "low":    _to_int_f(row.get("Low")),
                "close":  close,
                "volume": None,
            })

        if not rows:
            continue

        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO price_history (code, date, open, high, low, close, volume)
                VALUES (:code, :date, :open, :high, :low, :close, :volume)
                ON CONFLICT (code, date) DO NOTHING
            """), rows)
        print(f"  {code_key} ({fdr_symbol}): {len(rows)}일 적재")


# ─── 메인 ────────────────────────────────────────────────────────────────────

def run(from_d: date, to_d: date) -> None:
    engine = get_engine()
    ensure_table(engine)

    codes = get_db_codes(engine)
    print(f"DB 종목 수: {len(codes)}개")

    # 이미 to_d까지 수집 완료된 종목 스킵
    latest_dates = get_latest_dates(engine, codes)
    targets = []
    skipped = 0
    for code in codes:
        latest = latest_dates.get(code)
        if latest and latest >= to_d:
            skipped += 1
        else:
            # 이미 일부 수집된 경우 → 다음 날부터 증분 수집
            fetch_from = (latest + timedelta(days=1)) if latest else from_d
            targets.append((code, fetch_from))

    print(f"수집 대상: {len(targets)}개  스킵(완료): {skipped}개")
    print(f"기간: {from_d} ~ {to_d}")

    if not targets:
        print("모두 완료됨.")
    else:
        total = 0
        errors = 0
        for i, (code, fetch_from) in enumerate(targets, 1):
            n = collect_stock(engine, code, fetch_from, to_d)
            total += n
            if n == 0:
                errors += 1
            if i <= 5 or i % 50 == 0 or i == len(targets):
                print(f"  [{i:>4}/{len(targets)}] {code}  ({fetch_from}~)  → {n:>5}행")
            # pykrx 레이트리밋 방지
            time.sleep(0.05)

        print(f"\n종목 수집 완료")
        print(f"  적재: {total:,}행  오류/빈값: {errors}개")
        with engine.connect() as conn:
            cnt = conn.execute(text("SELECT COUNT(*) FROM price_history")).scalar()
        print(f"  price_history 누적: {cnt:,}행")

    print(f"\n지수 수집 중 (KOSPI, KOSDAQ)...")
    collect_all_indexes(engine, from_d, to_d)
    print("완료")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="pykrx OHLCV price_history 수집")
    parser.add_argument("--from", dest="from_date",
                        default="2022-01-01", help="수집 시작일 (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date",
                        default=date.today().strftime("%Y-%m-%d"),
                        help="수집 종료일 (YYYY-MM-DD, 기본=오늘)")
    args = parser.parse_args()

    run(date.fromisoformat(args.from_date),
        date.fromisoformat(args.to_date))
