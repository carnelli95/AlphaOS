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

[news=0은 실패가 아님]
  소형주·최근 공시만 있는 종목은 관련 뉴스가 없을 수 있음.
  "뉴스 없음"은 정상 케이스이며 오류로 처리하지 않음.
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
            # news facts가 없는 종목만 — source_type=news 확인
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


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force",  action="store_true", help="기존 news facts도 재생성")
    parser.add_argument("--limit",  type=int, default=None, help="처리 종목 수 제한")
    parser.add_argument("--sleep",  type=float, default=1.5, help="종목 간 sleep 초")
    args = parser.parse_args()

    if not NAVER_CLIENT_ID:
        print("❌ NAVER_CLIENT_ID 없음. .env 확인.")
        sys.exit(1)

    engine  = get_engine()
    targets = _load_targets(engine, force=args.force, limit=args.limit)
    total   = len(targets)

    print(f"=== Stage D 뉴스 배치 ===")
    print(f"대상: {total}개 | force={args.force} | sleep={args.sleep}s")
    print(f"시작: {datetime.now().strftime('%H:%M:%S')}\n")

    batch_start = time.time()

    ok = err = skipped = 0
    total_dart  = 0
    total_news  = 0
    news_dist   = Counter()                      # news=N개 종목 수 분포
    elapsed_log: list[tuple[float, str, str]] = []  # (elapsed, name, code)

    for i, (code, name) in enumerate(targets, 1):
        prefix    = f"[{i:4d}/{total}]"
        t0        = time.time()
        try:
            result = add_news_to_stage_d(engine, code)
            elapsed = time.time() - t0

            if result is None:
                print(f"{prefix} ⚠  {name}({code}): 건너뜀  ({elapsed:.1f}s)")
                skipped += 1
            else:
                facts  = result.get("facts", [])
                n_dart = sum(1 for f in facts if f.get("source_type", "dart") == "dart")
                n_news = sum(1 for f in facts if f.get("source_type") == "news")

                total_dart += n_dart
                total_news += n_news
                news_dist[n_news] += 1
                ok += 1
                elapsed_log.append((elapsed, name, code))

                if n_news > 0:
                    print(f"{prefix} ✅  {name}({code}): dart={n_dart} news={n_news} total={len(facts)}  ({elapsed:.1f}s)")
                else:
                    print(f"{prefix} —  {name}({code}): news 없음  ({elapsed:.1f}s)")

        except Exception as exc:
            elapsed = time.time() - t0
            print(f"{prefix} ❌  {name}({code}): {exc}  ({elapsed:.1f}s)")
            err += 1

        if i < total:
            time.sleep(args.sleep)

    # ── 최종 통계 ────────────────────────────────────────────────────────
    wall_time       = time.time() - batch_start
    elapsed_values  = [e for e, _, _ in elapsed_log]
    avg_elapsed     = sum(elapsed_values) / len(elapsed_values) if elapsed_values else 0
    avg_news_per_co = total_news / ok if ok else 0

    print(f"\n{'=' * 44}")
    print(f"=== 완료  {datetime.now().strftime('%H:%M:%S')} ===")
    print(f"{'=' * 44}")
    print(f"  성공: {ok:>5}  |  오류: {err}  |  건너뜀: {skipped}")
    print()
    print(f"  DART facts 합계:  {total_dart:>5}개")
    print(f"  News facts 합계:  {total_news:>5}개")
    print(f"  평균 news/종목:   {avg_news_per_co:>5.2f}개")
    print()
    print(f"  뉴스 분포:")
    for n in sorted(news_dist.keys()):
        label = f"{n}개" if n > 0 else "없음"
        bar   = "█" * min(news_dist[n] // max(1, ok // 30), 20)
        print(f"    news={label:<3}  {news_dist[n]:>5}종목  {bar}")
    print()
    print(f"  총 실행시간:      {_fmt_duration(wall_time)}")
    print(f"  평균 처리시간:    {avg_elapsed:.2f}s/종목")

    if elapsed_log:
        slowest = sorted(elapsed_log, key=lambda x: -x[0])[:5]
        print(f"\n  느린 종목 TOP{len(slowest)}:")
        for rank, (t, n, c) in enumerate(slowest, 1):
            print(f"    {rank}. {n:<12}  {t:.2f}s  ({c})")


if __name__ == "__main__":
    main()
