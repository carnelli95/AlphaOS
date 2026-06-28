"""
전체 종목 AI 리포트 배치 생성기 (재시작 가능).

ai_reports에 이미 성공적으로 생성된 종목은 건너뛴다.
실패한 종목은 ai_report_log에 남아서 다음 실행 때 재시도된다.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime

from sqlalchemy import text

from generate_ai_report import generate_report_for_stock, get_engine


def get_all_stock_codes(engine) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT code FROM stocks WHERE corp_code IS NOT NULL ORDER BY code")
        ).fetchall()
    return [r[0] for r in rows]


def get_already_done(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT stock_code FROM ai_reports")).fetchall()
    return {r[0] for r in rows}


def log_failure(engine, code: str, message: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS ai_report_log (
                    stock_code VARCHAR(10) PRIMARY KEY,
                    status VARCHAR(20) NOT NULL,
                    message TEXT,
                    attempted_at TIMESTAMP DEFAULT now()
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO ai_report_log (stock_code, status, message)
                VALUES (:code, 'error', :message)
                ON CONFLICT (stock_code) DO UPDATE SET
                    status = 'error', message = :message, attempted_at = now()
                """
            ),
            {"code": code, "message": message},
        )


def run(sleep_seconds: float, limit: int | None) -> None:
    engine = get_engine()
    all_codes = get_all_stock_codes(engine)
    done = get_already_done(engine)
    todo = [c for c in all_codes if c not in done]
    if limit is not None:
        todo = todo[:limit]

    print(f"전체 {len(all_codes)}개 중 {len(todo)}개 처리 예정 (이미 완료: {len(done)}개)")

    success_count = 0
    skip_count = 0
    error_count = 0

    for i, code in enumerate(todo, start=1):
        try:
            result = generate_report_for_stock(engine, code)
            if result is None:
                skip_count += 1
                print(f"[{i}/{len(todo)}] {code} 스킵 (데이터 부족)")
            else:
                success_count += 1
                print(f"[{i}/{len(todo)}] {code} 성공")
        except Exception as e:  # noqa: BLE001
            error_count += 1
            log_failure(engine, code, str(e))
            print(f"[{i}/{len(todo)}] {code} 실패: {e}")

        time.sleep(sleep_seconds)

    print()
    print(f"완료: {datetime.now().isoformat()}")
    print(f"성공 {success_count} / 스킵 {skip_count} / 실패 {error_count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--limit", type=int, default=None, help="테스트용 처리 개수 제한")
    args = parser.parse_args()
    run(args.sleep, args.limit)
