"""
Investment Thesis 전체 종목 배치 생성기 (재시작 가능).

thesis_sections가 이미 채워진 종목은 건너뛴다 (--force로 강제 덮어쓰기).

사용:
  python batch_generate_thesis.py --limit 5        # 테스트: 5종목만
  python batch_generate_thesis.py                  # 전체 (미완료만)
  python batch_generate_thesis.py --fix-missing    # confidence 없는 기존 레코드만 재생성
  python batch_generate_thesis.py --fix-underrisk  # thesis_risks=1이지만 dart_risks>=2인 종목 재생성
  [명령어만 제시, 직접 실행 금지] python batch_generate_thesis.py --force
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime

from sqlalchemy import text

import generate_thesis as _thesis_mod
from generate_thesis import (
    ensure_thesis_column,
    generate_thesis_for_stock,
    get_engine,
)


def get_all_stock_codes(engine) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT code FROM stocks WHERE corp_code IS NOT NULL ORDER BY code")
        ).fetchall()
    return [r[0] for r in rows]


def get_already_done(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT stock_code FROM ai_reports WHERE thesis_sections IS NOT NULL")
        ).fetchall()
    return {r[0] for r in rows}


def get_missing_confidence(engine) -> list[str]:
    """thesis_sections는 있지만 confidence 필드가 없는 종목 코드 반환."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT stock_code FROM ai_reports
                WHERE thesis_sections IS NOT NULL
                  AND thesis_sections->'confidence' IS NULL
                ORDER BY stock_code
            """)
        ).fetchall()
    return [r[0] for r in rows]


def get_codes_from_file(filepath: str) -> list[str]:
    """violations.txt 등 한 줄에 코드 하나씩 기록된 파일 읽기."""
    with open(filepath, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def get_underrisk_codes(engine) -> list[str]:
    """thesis_risks=1이지만 DART에 risk_factors 2개 이상인 종목 코드 반환."""
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT stock_code FROM ai_reports
                WHERE thesis_sections IS NOT NULL
                  AND jsonb_typeof(thesis_sections->'risks') = 'array'
                  AND jsonb_array_length(thesis_sections->'risks') = 1
                  AND jsonb_typeof(stage_c_sections->'risks'->'facts') = 'array'
                  AND jsonb_array_length(stage_c_sections->'risks'->'facts') >= 2
                ORDER BY stock_code
            """)
        ).fetchall()
    return [r[0] for r in rows]


def run(sleep_seconds: float, limit: int | None, force: bool = False,
        fix_missing: bool = False, fix_underrisk: bool = False,
        codes_file: str | None = None) -> None:
    engine = get_engine()
    ensure_thesis_column(engine)

    all_codes = get_all_stock_codes(engine)

    if codes_file:
        todo = get_codes_from_file(codes_file)
        print(f"파일({codes_file})에서 {len(todo)}개 재생성")
    elif fix_underrisk:
        todo = get_underrisk_codes(engine)
        print(f"risks 부족 종목 {len(todo)}개 재생성 (--fix-underrisk)")
    elif fix_missing:
        todo = get_missing_confidence(engine)
        print(f"confidence 누락 {len(todo)}개 재생성 (--fix-missing)")
    elif force:
        todo = all_codes
        print(f"전체 {len(all_codes)}개 강제 재생성 (--force)")
    else:
        done = get_already_done(engine)
        todo = [c for c in all_codes if c not in done]
        print(f"전체 {len(all_codes)}개 중 {len(todo)}개 처리 예정 (이미 완료: {len(done)}개)")

    if limit is not None:
        todo = todo[:limit]
        print(f"  → --limit {limit}: {len(todo)}개만 처리")

    if not todo:
        print("처리할 종목 없음.")
        return

    success_count = skip_count = error_count = 0

    for i, code in enumerate(todo, start=1):
        try:
            result = generate_thesis_for_stock(engine, code)
            if result is None:
                skip_count += 1
                print(f"[{i}/{len(todo)}] {code} 스킵 (데이터 부족)")
            else:
                s_count = len(result.get("strengths", []))
                r_count = len(result.get("risks", []))
                qd      = len(result.get("reason_graph", {}).get("quant_drivers", []))
                dd      = len(result.get("reason_graph", {}).get("dart_drivers", []))
                print(f"[{i}/{len(todo)}] {code} 성공  "
                      f"strengths={s_count} risks={r_count}  "
                      f"reason_graph(quant={qd} dart={dd})")
                success_count += 1
        except Exception as e:
            error_count += 1
            print(f"[{i}/{len(todo)}] {code} 실패: {e}")

        time.sleep(sleep_seconds)

    print()
    print(f"완료: {datetime.now().isoformat()}")
    print(f"성공 {success_count} / 스킵 {skip_count} / 실패 {error_count}")

    if success_count > 0:
        rs = _thesis_mod._retry_stats
        retry_rate = rs["retried"] / success_count * 100
        print()
        print("=== Attribution Validator 통계 ===")
        print(f"재시도 발생:   {rs['retried']}건 / {success_count}건 ({retry_rate:.1f}%)")
        print(f"영구 실패:     {rs['permanent_fail']}건  ← 목표 0건")

        retry_stocks = _thesis_mod._retry_stocks
        if retry_stocks:
            out_path = "thesis_retry_stocks.txt"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("\n".join(retry_stocks))
            print(f"재시도 종목 목록 → {out_path} ({len(retry_stocks)}건, QA 샘플용)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Investment Thesis 배치 생성")
    parser.add_argument("--sleep", type=float, default=0.5, help="종목 간 대기 (초)")
    parser.add_argument("--limit", type=int, default=None, help="처리 개수 제한 (테스트용)")
    parser.add_argument("--force", action="store_true", help="이미 생성된 종목도 덮어씀")
    parser.add_argument("--fix-missing", action="store_true",
                        help="thesis_sections 있지만 confidence 누락된 종목만 재생성")
    parser.add_argument("--fix-underrisk", action="store_true",
                        help="thesis_risks=1이지만 DART risk 2개+ 종목 재생성 (프롬프트 수정 후 활용)")
    parser.add_argument("--codes-file", type=str, default=None,
                        help="재생성할 종목 코드 파일 (한 줄에 코드 하나, diagnose_quality.py --save 결과 활용)")
    args = parser.parse_args()
    run(args.sleep, args.limit, args.force, args.fix_missing, args.fix_underrisk, args.codes_file)
