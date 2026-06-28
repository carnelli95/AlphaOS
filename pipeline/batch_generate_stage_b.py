"""
Stage B v2 전체 종목 배치 생성기 (재시작 가능, LLM 없음).

v2 변경: ROA + Op_margin 추가 → --force로 전체 재생성 필요.
sector가 있고 peer_count >= 10인 종목만 처리.
이미 stage_b_sections가 채워진 종목은 건너뜀 (--force 없이는).
"""

from __future__ import annotations

import argparse
import sys
import io
from datetime import datetime

from sqlalchemy import text

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from generate_stage_b import generate_stage_b_for_stock, save_stage_b, get_engine


def get_all_codes(engine) -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT code FROM stocks WHERE sector IS NOT NULL ORDER BY code"
        )).fetchall()
    return [r[0] for r in rows]


def get_done_codes(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT stock_code FROM ai_reports WHERE stage_b_sections IS NOT NULL"
        )).fetchall()
    return {r[0] for r in rows}


def run(limit: int | None = None, force: bool = False) -> None:
    engine = get_engine()
    all_codes = get_all_codes(engine)
    done = get_done_codes(engine) if not force else set()

    targets = [c for c in all_codes if c not in done]
    if limit:
        targets = targets[:limit]

    total = len(targets)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Stage B 배치 시작: {total}개 (전체 {len(all_codes)}개, 완료 {len(done)}개)")

    success = skipped = fail = 0

    for i, code in enumerate(targets, 1):
        try:
            data = generate_stage_b_for_stock(engine, code)
            if data is None:
                skipped += 1
                if i <= 10 or i % 100 == 0:
                    print(f"  [{i}/{total}] {code} → SKIP (sector 없음 또는 peer 부족)")
            else:
                save_stage_b(engine, code, data)
                success += 1
                if i <= 5 or i % 100 == 0:
                    print(f"  [{i}/{total}] {code} → {data['peer_group']} ({data['peer_count']}개 peer)")
        except Exception as e:
            fail += 1
            print(f"  [{i}/{total}] {code} → ERROR: {e}")

    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 완료 — 성공: {success} / 스킵: {skipped} / 실패: {fail}")

    # 결과 통계
    with engine.connect() as conn:
        cnt = conn.execute(text(
            "SELECT COUNT(*) FROM ai_reports WHERE stage_b_sections IS NOT NULL"
        )).scalar()
    print(f"  DB 내 stage_b_sections 총: {cnt}개")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="이미 완료된 종목도 재처리")
    args = parser.parse_args()
    run(limit=args.limit, force=args.force)
