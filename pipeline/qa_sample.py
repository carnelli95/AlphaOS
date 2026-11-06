"""
QA 샘플 생성기.

Stage E 배치 완료 후 실행:
  python pipeline/qa_sample.py

5그룹 stratified sample과 점수 기록 파일(qa_results.csv)을 생성한다:
  대형주 5 / 중형주 5 / 소형주 5 / Stage D 뉴스 0건 5 / Validator 대상 종목 5
"""

from __future__ import annotations

import csv
import io
import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv(override=True)

from generate_ai_report import get_engine
from sqlalchemy import text

CHECKLIST = [
    "사실관계_정확",
    "DART_attribution",
    "News_attribution",
    "산업일반론_없음",
    "투자논리_자연스러움",
    "핵심이벤트_누락없음",
]

OUT_CSV = os.path.join(os.path.dirname(__file__), "..", "qa_results.csv")


def main(n: int = 5) -> None:
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

        if (d_total or 0) == 0:
            no_news.append(entry)

        if (c_dart or 0) == 0 and (d_dart or 0) == 0:
            validator_targets.append(entry)

        if marcap >= 1_000_000_000_000:
            large.append(entry)
        elif marcap >= 50_000_000_000:
            mid.append(entry)
        else:
            small.append(entry)

    sample_large     = random.sample(large,             min(n, len(large)))
    used = {e[0] for e in sample_large}

    mid_pool = [e for e in mid if e[0] not in used]
    sample_mid = random.sample(mid_pool, min(n, len(mid_pool)))
    used |= {e[0] for e in sample_mid}

    small_pool = [e for e in small if e[0] not in used]
    sample_small = random.sample(small_pool, min(n, len(small_pool)))
    used |= {e[0] for e in sample_small}

    no_news_pool = [e for e in no_news if e[0] not in used]
    sample_no_news = random.sample(no_news_pool, min(n, len(no_news_pool)))
    used |= {e[0] for e in sample_no_news}

    val_pool = [e for e in validator_targets if e[0] not in used]
    sample_validator = random.sample(val_pool, min(n, len(val_pool)))

    groups = [
        ("대형주",          sample_large),
        ("중형주",          sample_mid),
        ("소형주",          sample_small),
        ("뉴스0건",         sample_no_news),
        ("Validator대상",   sample_validator),
    ]

    print("=" * 60)
    print("QA 샘플  Stratified")
    print("=" * 60)
    print(f"(Validator 대상 모집단: {len(validator_targets)}건)")

    all_stocks = []
    for group_name, items in groups:
        print(f"\n[{group_name}]")
        for code, name, marcap in items:
            mcap_str = f"{marcap // 100_000_000:.0f}억" if marcap else "?"
            print(f"  {code}  {name:<20}  {mcap_str}")
            all_stocks.append((code, name, group_name))

    # 점수 기록 CSV 생성
    csv_path = os.path.abspath(OUT_CSV)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["code", "name", "group"] + CHECKLIST + ["total", "memo"])
        for code, name, group in all_stocks:
            writer.writerow([code, name, group] + [""] * len(CHECKLIST) + ["", ""])

    print()
    print(f"총 {len(all_stocks)}종목")
    print(f"점수 기록 파일 생성: qa_results.csv")
    print()
    print("사용법:")
    print("  1. qa_results.csv를 열어 각 항목에 1(통과) 또는 0(실패) 입력")
    print("  2. python pipeline/qa_score.py  실행 → 집계 결과 출력")
    print()
    print("체크리스트 항목 (각 0 또는 1):")
    labels = [
        "사실관계 정확",
        "DART attribution 정확",
        "News attribution 정확",
        "산업 일반론 없음",
        "투자 논리 자연스러움",
        "핵심 이벤트 누락 없음",
    ]
    for col, label in zip(CHECKLIST, labels):
        print(f"  {col:<28}  {label}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=5, help="그룹당 종목 수 (기본 5, 총 5×그룹수)")
    args = parser.parse_args()
    main(n=args.count)
