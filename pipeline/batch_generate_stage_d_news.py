"""
Stage D 뉴스 배치 실행기

뉴스 수집(collect_news) → DART facts와 병합(generate_stage_d_news) → DB 저장

대상 선정 (기본):
  마지막 뉴스 검색(stage_d_sections.news_scanned_at)이 --rescan-days 이전이거나
  한 번도 검색 안 된 종목. 오래된 순으로 --limit개까지.
  → 뉴스 유무와 무관하게 모든 종목이 주기적으로 재검색되고, 매일 같은 종목을
    반복 검색하지 않는다. 일일 파이프라인은 limit을 걸어 부하를 분산.

옵션:
  --force         : 재검색 주기와 무관하게 전 종목
  --rescan-days N : 재검색 주기 (기본 7일)
  --limit N       : 최대 N개 종목만 처리
  --sleep N       : 종목 사이 sleep 초 (기본 1.5초, Naver API 레이트 리밋 방지)

실행 예시:
  python batch_generate_stage_d_news.py --limit 10       # 검증
  python batch_generate_stage_d_news.py                  # 재검색 주기 도래 종목 전체
  python batch_generate_stage_d_news.py --force          # 전 종목 재검색

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
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from generate_ai_report import LLMQuotaExhausted
from generate_ai_report import get_engine
from generate_stage_d_news import add_news_to_stage_d
from stage_result import now_kst, write_stage_result

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

NAVER_CLIENT_ID     = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")


def _load_targets(engine, force: bool, limit: int | None, rescan_days: int = 7) -> list[tuple]:
    """
    처리 대상 (stock_code, name) 목록 반환 — 마지막 검색이 오래된 순.

    force=False  → news_scanned_at 없음 또는 rescan_days 경과
    force=True   → ai_reports가 있는 모든 종목
    """
    where = "" if force else """
        WHERE r.stage_d_sections->>'news_scanned_at' IS NULL
           OR (r.stage_d_sections->>'news_scanned_at')::timestamp
              < now() - make_interval(days => :rescan_days)
    """
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            SELECT r.stock_code, s.name
            FROM ai_reports r
            JOIN stocks s ON s.code = r.stock_code
            {where}
            ORDER BY (r.stage_d_sections->>'news_scanned_at') NULLS FIRST, r.stock_code
        """), {"rescan_days": rescan_days}).fetchall()

    result = [(r[0], r[1]) for r in rows]
    if limit:
        result = result[:limit]
    return result


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s"


def run(force: bool = False, limit: int | None = None, sleep: float = 1.5,
        rescan_days: int = 7) -> dict:
    if not NAVER_CLIENT_ID:
        raise RuntimeError("NAVER_CLIENT_ID 없음. .env 확인.")

    engine  = get_engine()
    targets = _load_targets(engine, force=force, limit=limit, rescan_days=rescan_days)
    total   = len(targets)

    trigger  = "manual_force" if force else "rescan_due"
    run_mode = "full_rebuild" if force else "incremental"

    started_at = now_kst()
    print(f"=== Stage D 뉴스 배치 ===")
    print(f"대상: {total}개 | force={force} | sleep={sleep}s")
    print(f"시작: {started_at.strftime('%H:%M:%S')}\n")

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

        except LLMQuotaExhausted:
            print("LLM API 크레딧 소진 — 배치 중단 (남은 종목은 다음 실행에서 처리)")
            raise
        except Exception as exc:
            elapsed = time.time() - t0
            print(f"{prefix} ❌  {name}({code}): {exc}  ({elapsed:.1f}s)")
            err += 1

        if i < total:
            time.sleep(sleep)

    # ── 최종 통계 ────────────────────────────────────────────────────────
    finished_at     = now_kst()
    wall_time       = time.time() - batch_start
    elapsed_values  = [e for e, _, _ in elapsed_log]
    avg_elapsed     = sum(elapsed_values) / len(elapsed_values) if elapsed_values else 0
    avg_news_per_co = total_news / ok if ok else 0

    print(f"\n{'=' * 44}")
    print(f"=== 완료  {finished_at.strftime('%H:%M:%S')} ===")
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

    result = {
        "stage": "stage_d_news",
        "run_mode": run_mode,
        "trigger": trigger,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_sec": round(wall_time, 1),
        "target_count": total,
        "success": ok,
        "skip": skipped,
        "fail": err,
        "dart_facts": total_dart,
        "news_facts": total_news,
    }
    write_stage_result("stage_d_news", result)
    return result


def main() -> dict:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force",  action="store_true", help="재검색 주기와 무관하게 전 종목")
    parser.add_argument("--rescan-days", type=int, default=7, help="재검색 주기 (일)")
    parser.add_argument("--limit",  type=int, default=None, help="처리 종목 수 제한")
    parser.add_argument("--sleep",  type=float, default=1.5, help="종목 간 sleep 초")
    args = parser.parse_args()
    return run(args.force, args.limit, args.sleep, args.rescan_days)


if __name__ == "__main__":
    main()
