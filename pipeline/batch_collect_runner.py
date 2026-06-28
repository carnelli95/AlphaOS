"""
946개 KOSPI 종목 전체에 대해 사업보고서 원문을 배치로 수집한다.

- DART API 호출 제한을 고려해 호출 사이에 sleep을 둔다.
- 이미 처리한 종목은 business_report_collection_log를 보고 건너뛴다.
  -> 중간에 끊겨도 다시 실행하면 이어서 진행됨.
- DB 연결은 SQLAlchemy. 실제 프로젝트의 engine/session 설정에 맞게
  `get_engine()` 부분만 교체하면 된다.

실행:
    python batch_collect_runner.py --year 2025 --sleep 1.5
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from dart_report_collector import collect_business_report

try:
    from dotenv import load_dotenv

    load_dotenv(override=True)  # 세션에 남아있는 잘못된 값보다 .env를 항상 우선
except ImportError:
    print("경고: python-dotenv가 없어서 .env를 자동으로 읽지 못함. "
          "pip install python-dotenv 후 다시 실행하거나, 환경변수를 직접 set 하세요.")


def get_engine() -> Engine:
    import os

    db_url = os.environ["DATABASE_URL"]  # 예: postgresql://user:pw@localhost/quant
    # PostgreSQL이 인증 실패 등의 에러 메시지를 한글(lc_messages)로 보내면
    # psycopg2가 디코딩하다 깨지는 경우가 있어, 에러 메시지만 영어로 강제한다.
    return create_engine(db_url, connect_args={"options": "-c lc_messages=C"})


def get_already_processed(engine: Engine) -> set[str]:
    """완료로 취급할 종목만 반환한다.
    'error'는 일시적 실패일 수 있으므로 제외 -> 다음 실행 때 자동 재시도됨."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT stock_code FROM business_report_collection_log
                WHERE status IN ('success', 'no_report_found')
                """
            )
        ).fetchall()
    return {r[0] for r in rows}


def get_all_stocks(engine: Engine) -> list[tuple[str, str]]:
    """stocks 테이블에서 (code, corp_code) 쌍을 가져온다."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT code, corp_code FROM stocks WHERE corp_code IS NOT NULL")
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def save_success(
    engine: Engine,
    stock_code: str,
    corp_code: str,
    year: int,
    rcept_no: str,
    business_overview: str | None,
    raw_text: str,
    parse_status: str,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO business_reports_raw
                    (stock_code, corp_code, report_year, report_type,
                     rcept_no, section_business_overview, raw_text, parse_status)
                VALUES
                    (:stock_code, :corp_code, :year, 'A001',
                     :rcept_no, :overview, :raw_text, :status)
                ON CONFLICT (stock_code, report_year, report_type)
                DO UPDATE SET
                    section_business_overview = EXCLUDED.section_business_overview,
                    raw_text = EXCLUDED.raw_text,
                    parse_status = EXCLUDED.parse_status,
                    collected_at = now()
                """
            ),
            {
                "stock_code": stock_code,
                "corp_code": corp_code,
                "year": year,
                "rcept_no": rcept_no,
                "overview": business_overview,
                "raw_text": raw_text,
                "status": parse_status,
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO business_report_collection_log (stock_code, status)
                VALUES (:stock_code, 'success')
                ON CONFLICT (stock_code) DO UPDATE SET
                    status = 'success', attempted_at = now()
                """
            ),
            {"stock_code": stock_code},
        )


def save_failure(engine: Engine, stock_code: str, status: str, error_message: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO business_report_collection_log
                    (stock_code, status, error_message)
                VALUES (:stock_code, :status, :error_message)
                ON CONFLICT (stock_code) DO UPDATE SET
                    status = :status, error_message = :error_message, attempted_at = now()
                """
            ),
            {"stock_code": stock_code, "status": status, "error_message": error_message},
        )


def run(year: int, sleep_seconds: float, limit: int | None = None) -> None:
    import os

    api_key = os.environ["DART_API_KEY"]
    engine = get_engine()

    all_stocks = get_all_stocks(engine)
    already_done = get_already_processed(engine)
    todo = [(code, corp_code) for code, corp_code in all_stocks if code not in already_done]
    if limit is not None:
        todo = todo[:limit]

    print(f"전체 {len(all_stocks)}개 중 {len(todo)}개 처리 예정 "
          f"(이미 처리: {len(already_done)}개)")

    for i, (stock_code, corp_code) in enumerate(todo, start=1):
        try:
            result = collect_business_report(
                api_key, corp_code=corp_code, stock_code=stock_code, year=year
            )
        except Exception as e:  # noqa: BLE001 - 배치 작업이므로 한 종목 실패가 전체를 막으면 안 됨
            save_failure(engine, stock_code, "error", str(e))
            print(f"[{i}/{len(todo)}] {stock_code} 실패: {e}")
            time.sleep(sleep_seconds)
            continue

        if result is None:
            save_failure(engine, stock_code, "no_report_found", "해당 연도 사업보고서 없음")
            print(f"[{i}/{len(todo)}] {stock_code} 보고서 없음")
        else:
            save_success(
                engine,
                stock_code=stock_code,
                corp_code=corp_code,
                year=year,
                rcept_no=result.rcept_no,
                business_overview=result.business_overview,
                raw_text=result.raw_text,
                parse_status=result.parse_status,
            )
            print(f"[{i}/{len(todo)}] {stock_code} 성공 (parse_status={result.parse_status})")

        time.sleep(sleep_seconds)

    print(f"완료: {datetime.now().isoformat()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--sleep", type=float, default=1.5, help="API 호출 간 대기 시간(초)")
    parser.add_argument("--limit", type=int, default=None, help="테스트용: 처리할 종목 수 제한")
    args = parser.parse_args()
    run(args.year, args.sleep, args.limit)