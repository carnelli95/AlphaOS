"""
Stage C 전체 종목 배치 생성기 (재시작 가능).

stage_c_sections가 이미 채워진 종목은 건너뛴다.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime

from sqlalchemy import text

import generate_stage_c as _stage_c_mod
from generate_stage_c import generate_stage_c_for_stock, get_engine


def get_all_stock_codes(engine) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT code FROM stocks WHERE corp_code IS NOT NULL ORDER BY code")
        ).fetchall()
    return [r[0] for r in rows]


def get_already_done(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT stock_code FROM ai_reports WHERE stage_c_sections IS NOT NULL")
        ).fetchall()
    return {r[0] for r in rows}


def get_old_format_codes(engine) -> set[str]:
    """importance 필드가 없는 레코드 반환 (--upgrade 용).
    string 형식 또는 object이지만 importance 누락된 경우 모두 포함."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT stock_code FROM ai_reports
            WHERE stage_c_sections IS NOT NULL
              AND (
                jsonb_typeof(stage_c_sections->'growth_drivers'->'facts'->0) = 'string'
                OR (
                  jsonb_typeof(stage_c_sections->'growth_drivers'->'facts'->0) = 'object'
                  AND (stage_c_sections->'growth_drivers'->'facts'->0->'importance') IS NULL
                )
              )
        """)).fetchall()
    return {r[0] for r in rows}


def log_failure(engine, code: str, message: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS stage_c_log (
                    stock_code VARCHAR(10) PRIMARY KEY,
                    status     VARCHAR(20) NOT NULL,
                    message    TEXT,
                    attempted_at TIMESTAMP DEFAULT now()
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO stage_c_log (stock_code, status, message)
                VALUES (:code, 'error', :message)
                ON CONFLICT (stock_code) DO UPDATE SET
                    status = 'error', message = :message, attempted_at = now()
                """
            ),
            {"code": code, "message": message},
        )


def run(sleep_seconds: float, limit: int | None, force: bool = False, upgrade: bool = False) -> None:
    engine = get_engine()
    all_codes = get_all_stock_codes(engine)
    if force:
        todo = all_codes
        print(f"전체 {len(all_codes)}개 강제 재생성 (--force)")
    elif upgrade:
        old_fmt = get_old_format_codes(engine)
        todo = [c for c in all_codes if c in old_fmt]
        print(f"전체 {len(all_codes)}개 중 구 형식 {len(todo)}개만 재처리 (--upgrade)")
    else:
        done = get_already_done(engine)
        todo = [c for c in all_codes if c not in done]
        print(f"전체 {len(all_codes)}개 중 {len(todo)}개 처리 예정 (이미 완료: {len(done)}개)")
    if limit is not None:
        todo = todo[:limit]

    success_count = skip_count = error_count = 0

    # 품질 지표 집계 (배치 완료 후 moat 빈 배열 비율 등 파악)
    quality = {
        "growth_empty": 0,
        "risk_empty": 0,
        "moat_empty": 0,
        "growth_fact_total": 0,
        "risk_fact_total": 0,
        "moat_fact_total": 0,
    }

    for i, code in enumerate(todo, start=1):
        try:
            result = generate_stage_c_for_stock(engine, code)
            if result is None:
                skip_count += 1
                print(f"[{i}/{len(todo)}] {code} 스킵 (데이터 부족)")
            else:
                success_count += 1
                g = len(result.get("growth_drivers", {}).get("facts", []))
                r = len(result.get("risks", {}).get("facts", []))
                m = len(result.get("moat", {}).get("facts", []))
                quality["growth_fact_total"] += g
                quality["risk_fact_total"] += r
                quality["moat_fact_total"] += m
                if g == 0: quality["growth_empty"] += 1
                if r == 0: quality["risk_empty"] += 1
                if m == 0: quality["moat_empty"] += 1
                print(f"[{i}/{len(todo)}] {code} 성공  growth={g} risk={r} moat={m}")
        except Exception as e:  # noqa: BLE001
            error_count += 1
            log_failure(engine, code, str(e))
            print(f"[{i}/{len(todo)}] {code} 실패: {e}")

        time.sleep(sleep_seconds)

    print()
    print(f"완료: {datetime.now().isoformat()}")
    print(f"성공 {success_count} / 스킵 {skip_count} / 실패 {error_count}")

    if success_count > 0:
        rs = _stage_c_mod._retry_stats
        retry_rate = rs["retried"] / success_count * 100

        print()
        print("=== Validator 재시도 통계 ===")
        print(f"재시도 발생:   {rs['retried']}건 / {success_count}건 ({retry_rate:.1f}%)")
        print(f"영구 실패:     {rs['permanent_fail']}건  ← 목표 0건")

        print()
        print("=== Fact 품질 지표 ===")
        print(f"growth 빈 배열: {quality['growth_empty']}/{success_count} ({quality['growth_empty']/success_count*100:.1f}%)")
        print(f"risk   빈 배열: {quality['risk_empty']}/{success_count} ({quality['risk_empty']/success_count*100:.1f}%)")
        print(f"moat   빈 배열: {quality['moat_empty']}/{success_count} ({quality['moat_empty']/success_count*100:.1f}%)  ← 목표 < 5%")
        print(f"평균 Fact 수  — growth: {quality['growth_fact_total']/success_count:.1f}  risk: {quality['risk_fact_total']/success_count:.1f}  moat: {quality['moat_fact_total']/success_count:.1f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--limit", type=int, default=None, help="테스트용 처리 개수 제한")
    parser.add_argument("--force", action="store_true", help="이미 생성된 종목도 덮어씀")
    parser.add_argument("--upgrade", action="store_true", help="구 string 형식만 새 object 형식으로 재처리")
    args = parser.parse_args()
    run(args.sleep, args.limit, args.force, args.upgrade)
