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

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv(override=True)
sys.path.insert(0, os.path.dirname(__file__))

from generate_ai_report import get_engine
from collect_news import fetch_news_events, merge_with_dart_facts

NAVER_CLIENT_ID     = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")


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

    if not news_facts:
        if verbose:
            print(f"  → 뉴스 없음, 변경 없음")
        return sections

    # 우선순위 병합 (dart > news)
    merged = merge_with_dart_facts(dart_facts, news_facts)

    new_dart_cnt = sum(1 for f in merged if f.get("source_type") == "dart")
    new_news_cnt = sum(1 for f in merged if f.get("source_type") == "news")

    # 기존과 동일하면 건너뜀
    old_news_cnt = sum(1 for f in sections.get("facts", []) if f.get("source_type") == "news")
    if new_news_cnt == old_news_cnt and len(merged) == len(sections.get("facts", [])):
        if verbose:
            print(f"  → 변경 없음 (news={new_news_cnt})")
        return sections

    updated = dict(sections)
    updated["facts"]      = merged
    updated["fact_count"] = len(merged)

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
