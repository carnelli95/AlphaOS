"""
QA 샘플 생성기.

Stage E 배치 완료 후 실행:
  python pipeline/qa_sample.py

5그룹 stratified sample을 출력한다:
  대형주 5 / 중형주 5 / 소형주 5 / Stage D 뉴스 0건 5 / Validator 대상 종목 5
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


def main() -> None:
    engine = get_engine()

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                s.code, s.name, s.marcap,
                (r.thesis_sections->'confidence'->>'dart_count')::int AS c_dart,
                (SELECT COUNT(*) FROM jsonb_array_elements(
                    COALESCE(r.stage_d_sections->'facts', '[]'::jsonb)
                ) f WHERE f->>'source_type' = 'dart') AS d_dart,
                jsonb_array_length(
                    COALESCE(r.stage_d_sections->'facts', '[]'::jsonb)
                ) AS d_total
            FROM stocks s
            JOIN ai_reports r ON r.stock_code = s.code
            WHERE r.thesis_sections IS NOT NULL
              AND s.corp_code IS NOT NULL
        """)).fetchall()

    large, mid, small, no_news, validator_targets = [], [], [], [], []

    for code, name, marcap, c_dart, d_dart, d_total in rows:
        entry = (code, name, marcap or 0)
        marcap = marcap or 0

        # Stage D 뉴스 0건: dart 포함 전체 fact가 없거나, news fact 없는 경우
        if (d_total or 0) == 0:
            no_news.append(entry)

        # Validator 대상: stage_c dart=0 AND stage_d dart=0 (attribution validator가 실제로 동작한 종목)
        if (c_dart or 0) == 0 and (d_dart or 0) == 0:
            validator_targets.append(entry)

        if marcap >= 1_000_000_000_000:
            large.append(entry)
        elif marcap >= 50_000_000_000:
            mid.append(entry)
        else:
            small.append(entry)

    sample_large     = random.sample(large,             min(5, len(large)))
    sample_mid       = random.sample(mid,               min(5, len(mid)))
    sample_small     = random.sample(small,             min(5, len(small)))
    sample_no_news   = random.sample(no_news,           min(5, len(no_news)))
    sample_validator = random.sample(validator_targets, min(5, len(validator_targets)))

    groups = [
        ("대형주 (5)",                  sample_large),
        ("중형주 (5)",                  sample_mid),
        ("소형주 (5)",                  sample_small),
        ("Stage D 뉴스 0건 (5)",        sample_no_news),
        (f"Validator 대상 종목 ({len(sample_validator)})", sample_validator),
    ]

    print("=" * 60)
    print("QA 샘플  20종목 Stratified")
    print("=" * 60)
    print(f"(Validator 대상 모집단: {len(validator_targets)}건 중 최대 5건 샘플)")
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
    print("체크리스트 항목 (종목마다 아래 6개 항목을 체크):")
    for item in [
        "사실관계 정확",
        "DART attribution 정확",
        "News attribution 정확",
        "산업 일반론 없음",
        "투자 논리 자연스러움",
        "핵심 이벤트 누락 없음",
    ]:
        print(f"  [ ] {item}")


if __name__ == "__main__":
    main()
