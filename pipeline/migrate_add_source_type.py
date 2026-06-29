"""
마이그레이션: stage_d_sections facts에 source_type="dart" 백필

기존 DART 이벤트 레코드에 source_type 필드가 없으므로
모든 기존 fact에 "dart"를 추가한다.

실행:
    python migrate_add_source_type.py [--dry-run]
"""
from __future__ import annotations

import json
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import os
import argparse
import psycopg2

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)
except ImportError:
    pass

_raw_db_url = os.environ.get("DATABASE_URL", "postgresql://postgres:1234@localhost:5432/quant_screener")
# SQLAlchemy driver prefix (postgresql+psycopg2://) → psycopg2 DSN 변환
DB_URL = _raw_db_url.replace("postgresql+psycopg2://", "postgresql://", 1)


def migrate(dry_run: bool = False) -> None:
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    # 대상: stage_d_sections이 있고 facts 배열 내 source_type이 없는 레코드
    cur.execute("""
        SELECT stock_code, stage_d_sections
        FROM ai_reports
        WHERE stage_d_sections IS NOT NULL
          AND stage_d_sections ? 'facts'
    """)
    rows = cur.fetchall()

    updated = 0
    skipped = 0

    for stock_code, sections in rows:
        facts = sections.get("facts", [])
        changed = False
        new_facts = []
        for f in facts:
            if "source_type" not in f:
                f = dict(f, source_type="dart")
                changed = True
            new_facts.append(f)

        if not changed:
            skipped += 1
            continue

        sections["facts"] = new_facts
        sections["fact_count"] = len(new_facts)  # fact_count 갱신

        if not dry_run:
            cur.execute("""
                UPDATE ai_reports
                SET stage_d_sections = %s::jsonb
                WHERE stock_code = %s
            """, (json.dumps(sections, ensure_ascii=False), stock_code))

        updated += 1
        if updated <= 5 or dry_run:
            print(f"  {'[DRY]' if dry_run else ''} {stock_code}: {len(new_facts)}개 fact → source_type=dart 추가")

    if not dry_run:
        conn.commit()
    conn.close()

    print(f"\n{'[DRY RUN] ' if dry_run else ''}완료: {updated}개 업데이트, {skipped}개 이미 ok")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="실제 변경 없이 대상만 확인")
    args = parser.parse_args()
    migrate(dry_run=args.dry_run)
