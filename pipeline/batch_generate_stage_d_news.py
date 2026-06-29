"""
Stage D 뉴스 배치 실행기

뉴스 수집(collect_news) → DART facts와 병합(generate_stage_d_news) → DB 저장

옵션:
  --force       : 이미 news facts가 있는 종목도 재생성
  --limit N     : 처음 N개 종목만 처리 (검증용)
  --sleep N     : 종목 사이 sleep 초 (기본 1.5초, Naver API 레이트 리밋 방지)

실행 예시:
  python batch_generate_stage_d_news.py --limit 10       # 검증
  python batch_generate_stage_d_news.py                  # 전체
  python batch_generate_stage_d_news.py --force          # 전체 재생성
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import time
from collections import Counter
from datetime import datetime

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(__file__))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from generate_ai_report import get_engine
from generate_stage_d_news import add_news_to_stage_d

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

NAVER_CLIENT_ID     = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")


def _load_targets(engine, force: bool, limit: int | None) -> list[tuple]:
    """
    처리 대상 (stock_code, name) 목록 반환.

    force=False  → news facts가 없는 종목만 (stage_d_sections 없거나 news fact 0개)
    force=True   → ai_reports가 있는 모든 종목
    """
    with engine.connect() as conn:
        if force:
            rows = conn.execute(text("""
                SELECT r.stock_code, s.name
                FROM ai_reports r
                JOIN stocks s ON s.code = r.stock_code
                ORDER BY r.stock_code
            """)).fetchall()
        else:
            # news facts가 없는 종목만 — jsonb_path_exists로 source_type=news 확인
            rows = conn.execute(text("""
                SELECT r.stock_code, s.name
                FROM ai_reports r
                JOIN stocks s ON s.code = r.stock_code
                WHERE r.stage_d_sections IS NULL
                   OR NOT EXISTS (
                       SELECT 1
                       FROM jsonb_array_elements(
                           COALESCE(r.stage_d_sections->'facts', '[]'::jsonb)
                       ) AS f
                       WHERE f->>'source_type' = 'news'
                   )
                ORDER BY r.stock_code
            """)).fetchall()

    result = [(r[0], r[1]) for r in rows]
    if limit:
        result = result[:limit]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force",  action="store_true", help="기존 news facts도 재생성")
    parser.add_argument("--limit",  type=int, default=None, help="처리 종목 수 제한")
    parser.add_argument("--sleep",  type=float, default=1.5, help="종목 간 sleep 초")
    args = parser.parse_args()

    if not NAVER_CLIENT_ID:
        print("❌ NAVER_CLIENT_ID 없음. .env 확인.")
        sys.exit(1)

    engine = get_engine()
    targets = _load_targets(engine, force=args.force, limit=args.limit)

    print(f"=== Stage D 뉴스 배치 ===")
    print(f"대상: {len(targets)}개 | force={args.force} | sleep={args.sleep}s")
    print(f"시작: {datetime.now().isoformat()}\n")

    ok = err = skipped = 0
    news_added = Counter()   # news가 몇 개 추가된 종목 카운트

    for i, (code, name) in enumerate(targets, 1):
        prefix = f"[{i:4d}/{len(targets)}]"
        try:
            result = add_news_to_stage_d(engine, code)

            if result is None:
                print(f"{prefix} ⚠  {name}({code}): 건너뜀")
                skipped += 1
            else:
                facts    = result.get("facts", [])
                n_dart   = sum(1 for f in facts if f.get("source_type", "dart") == "dart")
                n_news   = sum(1 for f in facts if f.get("source_type") == "news")
                news_added[n_news] += 1
                ok += 1
                if n_news > 0:
                    print(f"{prefix} ✅  {name}({code}): dart={n_dart} news={n_news} total={len(facts)}")
                else:
                    print(f"{prefix} —  {name}({code}): news 없음")

        except Exception as exc:
            print(f"{prefix} ❌  {name}({code}): {exc}")
            err += 1

        if i < len(targets):
            time.sleep(args.sleep)

    print(f"\n=== 완료 ===")
    print(f"성공: {ok} | 오류: {err} | 건너뜀: {skipped}")
    print(f"종료: {datetime.now().isoformat()}")

    if news_added:
        print(f"\n뉴스 추가 분포:")
        for n_news in sorted(news_added.keys()):
            label = f"news={n_news}개" if n_news > 0 else "뉴스 없음(0개)"
            print(f"  {label}: {news_added[n_news]}종목")


if __name__ == "__main__":
    main()
