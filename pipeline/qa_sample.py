"""
QA 샘플 생성기.

Stage E 배치 완료 후 실행:
  python pipeline/qa_sample.py

5그룹 stratified sample을 출력한다:
  대형주 5 / 중형주 5 / 소형주 5 / Stage D 뉴스 0건 5 / Validator 재시도 종목 (최대 5)
"""

from __future__ import annotations

import io
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv(override=True)

from generate_ai_report import get_engine
from sqlalchemy import text

RETRY_FILE = os.path.join(os.path.dirname(__file__), "..", "thesis_retry_stocks.txt")


def load_retry_stocks() -> list[str]:
    path = os.path.abspath(RETRY_FILE)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def main() -> None:
    engine = get_engine()

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                s.code, s.name, s.marcap,
                jsonb_array_length(
                    COALESCE(r.stage_d_sections->'facts', '[]'::jsonb)
                ) AS d_fact_cnt,
                jsonb_array_length(
                    COALESCE(
                        (SELECT jsonb_agg(f)
                         FROM jsonb_array_elements(r.stage_d_sections->'facts') f
                         WHERE f->>'source_type' != 'dart'),
                        '[]'::jsonb
                    )
                ) AS news_cnt
            FROM stocks s
            JOIN ai_reports r ON r.stock_code = s.code
            WHERE r.thesis_sections IS NOT NULL
              AND s.corp_code IS NOT NULL
        """)).fetchall()

    large, mid, small, no_news = [], [], [], []
    for code, name, marcap, d_cnt, news_cnt in rows:
        entry = (code, name, marcap or 0)
        if (news_cnt or 0) == 0:
            no_news.append(entry)
        if (marcap or 0) >= 1_000_000_000_000:
            large.append(entry)
        elif (marcap or 0) >= 50_000_000_000:
            mid.append(entry)
        else:
            small.append(entry)

    # 재현성을 위해 시드 고정하지 않음 (매번 새 샘플)
    sample_large   = random.sample(large,   min(5, len(large)))
    sample_mid     = random.sample(mid,     min(5, len(mid)))
    sample_small   = random.sample(small,   min(5, len(small)))
    sample_no_news = random.sample(no_news, min(5, len(no_news)))

    retry_codes = load_retry_stocks()
    # retry 종목 중 thesis가 있는 것만 필터
    done_codes = {r[0] for r in rows}
    retry_valid = [c for c in retry_codes if c in done_codes]
    sample_retry = random.sample(retry_valid, min(5, len(retry_valid)))

    groups = [
        ("대형주 (5)", sample_large),
        ("중형주 (5)", sample_mid),
        ("소형주 (5)", sample_small),
        ("Stage D 뉴스 0건 (5)", sample_no_news),
        (f"Validator 재시도 종목 ({len(sample_retry)})", [(c, "?", 0) for c in sample_retry]),
    ]

    print("=" * 60)
    print("QA 샘플  20종목 Stratified")
    print("=" * 60)
    total = 0
    for group_name, items in groups:
        print(f"\n[{group_name}]")
        for code, name, marcap in items:
            mcap_str = f"{marcap // 100_000_000:.0f}억" if marcap else "?"
            print(f"  {code}  {name:<20}  {mcap_str}")
            total += 1

    print()
    print(f"총 {total}종목")
    print()
    print("체크리스트 항목:")
    for item in [
        "사실관계 정확",
        "DART attribution 정확",
        "News attribution 정확",
        "산업 일반론 없음",
        "투자 논리 자연스러움",
        "핵심 이벤트 누락 없음",
    ]:
        print(f"  ☐ {item}")


if __name__ == "__main__":
    main()
