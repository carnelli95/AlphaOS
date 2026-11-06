"""
Stage D 뉴스 통합기

흐름:
  1. DB에서 기존 stage_d_sections (DART facts) 읽기
  2. 네이버 뉴스 API로 기업 뉴스 수집 (collect_news.fetch_news_events)
  3. merge_with_dart_facts()로 DART(100) > news(60) 우선순위 병합
  4. stage_d_sections 업데이트 (source_type 필드로 출처 보존)

실행:
    python generate_stage_d_news.py 005930        # 단일 종목
    python generate_stage_d_news.py               # __main__ 데모
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv(override=True)
sys.path.insert(0, os.path.dirname(__file__))

from collections import Counter
from datetime import date

from generate_ai_report import get_engine
from collect_news import DEFAULT_WINDOW_DAYS, fetch_news_events, merge_with_dart_facts

NAVER_CLIENT_ID     = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")

MAX_NEWS_FACTS       = 5
MAX_PER_EVENT_TYPE   = 2


def _news_key(f: dict) -> str:
    return f.get("news_url") or f"{f.get('date', '')}|{f.get('fact', '')}"


def _recency(date_str: str, today: date) -> int:
    try:
        return (today - date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))).days
    except (ValueError, TypeError):
        return 9999


def combine_news(old_news: list[dict], new_news: list[dict], today: date | None = None,
                 window_days: int = DEFAULT_WINDOW_DAYS) -> list[dict]:
    """
    기존 news facts + 새로 수집한 news facts 합집합.

    재검색은 최신 기사 N건만 가져오므로 덮어쓰면 기간 내 과거 주요 기사가 사라진다.
    URL 기준 중복 제거(새 수집본 우선) → recency 재계산 → 기간 밖 제거 →
    weighted_score 순 + event_type당 최대 2개 → 최대 5개.
    """
    today = today or date.today()
    by_key: dict[str, dict] = {}
    for f in list(old_news) + list(new_news):
        by_key[_news_key(f)] = f
    pool = []
    for f in by_key.values():
        rec = _recency(f.get("date", ""), today)
        if rec <= window_days:
            pool.append(dict(f, recency_days=rec))
    pool.sort(key=lambda f: (-f.get("weighted_score", 0), f.get("recency_days", 9999)))
    seen: Counter = Counter()
    out: list[dict] = []
    for f in pool:
        et = f.get("event_type", "other")
        if seen[et] < MAX_PER_EVENT_TYPE:
            out.append(f)
            seen[et] += 1
    return out[:MAX_NEWS_FACTS]


def add_news_to_stage_d(
    engine,
    stock_code: str,
    verbose: bool = False,
) -> dict | None:
    """
    기존 stage_d_sections에 뉴스 facts를 병합하여 저장한다.
    DART facts는 절대 제거하지 않음 (source_type="dart" 보존).

    반환: 업데이트된 stage_d_sections dict, 또는 None(건너뜀)
    """
    if not NAVER_CLIENT_ID:
        print(f"  {stock_code}: NAVER_CLIENT_ID 없음 → 건너뜀")
        return None

    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT s.name, r.stage_d_sections
            FROM stocks s
            LEFT JOIN ai_reports r ON r.stock_code = s.code
            WHERE s.code = :c
        """), {"c": stock_code}).fetchone()

    if not row:
        print(f"  {stock_code}: stocks 테이블에 없음")
        return None

    company_name, sections = row

    # stage_d_sections 없으면 빈 구조로 시작
    if sections is None:
        sections = {"fact_count": 0, "facts": [], "source": "dart_disclosure"}

    dart_facts = [
        f for f in sections.get("facts", [])
        if f.get("source_type", "dart") == "dart"
    ]

    if verbose:
        print(f"  {company_name}({stock_code}): DART facts={len(dart_facts)}개")

    # 뉴스 수집
    news_facts = fetch_news_events(
        company_name, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET
    )
    if verbose:
        print(f"  뉴스 후보: {len(news_facts)}개")
        for f in news_facts:
            print(f"    [{f['date']}] [{f['type']}] {f['fact'][:60]}")

    scanned_at = datetime.now().isoformat()
    old_facts  = sections.get("facts", [])
    old_news   = [f for f in old_facts if f.get("source_type") == "news"]

    # 기존 뉴스와 합집합 후 우선순위 병합 (dart > news)
    merged = merge_with_dart_facts(dart_facts, combine_news(old_news, news_facts))

    new_dart_cnt = sum(1 for f in merged if f.get("source_type") == "dart")
    new_news_cnt = sum(1 for f in merged if f.get("source_type") == "news")

    # 내용 기준 비교 — 바뀐 게 없으면 스캔 시각만 기록 (generated_at을 건드리면 Thesis가 재생성됨)
    def _ids(facts: list[dict]) -> list[str]:
        return [f.get("dart_id") or _news_key(f) for f in facts]

    if _ids(merged) == _ids(old_facts):
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE ai_reports
                SET stage_d_sections = jsonb_set(stage_d_sections, '{news_scanned_at}',
                                                 to_jsonb(CAST(:ts AS TEXT)))
                WHERE stock_code = :code AND stage_d_sections IS NOT NULL
            """), {"code": stock_code, "ts": scanned_at})
        if verbose:
            print(f"  → 변경 없음 (news={new_news_cnt})")
        return dict(sections, news_scanned_at=scanned_at)

    updated = dict(sections)
    updated["facts"]           = merged
    updated["fact_count"]      = len(merged)
    updated["generated_at"]    = scanned_at
    updated["news_scanned_at"] = scanned_at

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO ai_reports (stock_code, stage_d_sections)
            VALUES (:code, CAST(:data AS JSONB))
            ON CONFLICT (stock_code) DO UPDATE
              SET stage_d_sections = CAST(:data AS JSONB)
        """), {"code": stock_code, "data": json.dumps(updated, ensure_ascii=False)})

    if verbose:
        print(f"  ✅ {company_name}({stock_code}): dart={new_dart_cnt} news={new_news_cnt} total={len(merged)}")
    return updated


if __name__ == "__main__":
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    engine = get_engine()
    target = sys.argv[1] if len(sys.argv) > 1 else "005930"

    print(f"=== Stage D 뉴스 통합: {target} ===")
    result = add_news_to_stage_d(engine, target, verbose=True)

    if result:
        facts = result.get("facts", [])
        print(f"\n최종 facts ({len(facts)}개):")
        for f in facts:
            src = f.get("source_type", "?")
            print(f"  [{f['date']}] [{src}] [{f['type']}] {f['fact'][:60]}")
