"""
Stage D 배치 실행기: 수집(collect) → Fact 추출(generate) 순차 실행

--collect-only  : 수집만 (DART API 호출, DB 저장)
--generate-only : Fact 추출만 (stage_d_raw 있는 종목 대상)
--force         : 이미 stage_d_sections가 있는 종목도 재생성

기본 (옵션 없음): 수집 → 생성 순서로 실행, 미완료 종목만 처리
"""
from __future__ import annotations

import argparse
import sys
import io
import time
from datetime import datetime

from sqlalchemy import text

sys.path.insert(0, __import__("os").path.dirname(__file__))
from generate_ai_report import get_engine
from collect_stage_d import collect_stage_d_for_stock, ensure_stage_d_column
from generate_stage_d import generate_stage_d_for_stock
from stage_result import now_kst, write_stage_result

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def _load_targets(engine, collect_only: bool, generate_only: bool, force: bool) -> list[tuple]:
    """(stock_code, corp_code, name) 목록 반환."""
    with engine.connect() as conn:
        if generate_only:
            # stage_d_raw 있고 stage_d_sections 없는 종목 (또는 force 시 모두)
            where = "" if force else "AND r.stage_d_sections IS NULL"
            rows = conn.execute(text(f"""
                SELECT r.stock_code, s.corp_code, s.name
                FROM ai_reports r
                JOIN stocks s ON s.code = r.stock_code
                WHERE r.stage_d_raw IS NOT NULL
                  AND s.corp_code IS NOT NULL
                  {where}
                ORDER BY r.stock_code
            """)).fetchall()
        elif collect_only:
            # stage_d_raw 없는 종목 (또는 force 시 모두)
            where = "" if force else "AND r.stage_d_raw IS NULL"
            rows = conn.execute(text(f"""
                SELECT r.stock_code, s.corp_code, s.name
                FROM ai_reports r
                JOIN stocks s ON s.code = r.stock_code
                WHERE s.corp_code IS NOT NULL
                  {where}
                ORDER BY r.stock_code
            """)).fetchall()
        else:
            # 기본: stage_d_sections 없는 종목
            where = "" if force else "AND r.stage_d_sections IS NULL"
            rows = conn.execute(text(f"""
                SELECT r.stock_code, s.corp_code, s.name
                FROM ai_reports r
                JOIN stocks s ON s.code = r.stock_code
                WHERE s.corp_code IS NOT NULL
                  {where}
                ORDER BY r.stock_code
            """)).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def run(collect_only: bool = False, generate_only: bool = False,
        force: bool = False, period_days: int = 180) -> dict:
    engine = get_engine()
    ensure_stage_d_column(engine)

    targets = _load_targets(engine, collect_only, generate_only, force)
    total   = len(targets)

    mode = "collect+generate"
    if collect_only:  mode = "collect only"
    if generate_only: mode = "generate only"
    if force:         mode += " (force)"

    trigger  = "manual_force" if force else "new_stock"
    run_mode = "full_rebuild" if force else "incremental"

    started_at = now_kst()
    print(f"[{started_at.strftime('%H:%M:%S')}] Stage D 배치 시작: {total}개  ({mode})")

    ok = skip = fail = 0
    no_events = 0
    disclosures_collected = 0

    # 집계용
    from collections import Counter
    type_counter   = Counter()
    impact_counter = Counter()

    for i, (code, corp_code, name) in enumerate(targets, 1):
        prefix = f"[{i}/{total}] {code} {name}"

        try:
            # Step 1: 수집
            if not generate_only:
                result_raw = collect_stage_d_for_stock(
                    engine, code, corp_code,
                    period_days=period_days,
                )
                n_disc = len(result_raw.get("disclosures", []))
                disclosures_collected += n_disc
            else:
                n_disc = None

            # Step 2: Fact 추출
            if not collect_only:
                result = generate_stage_d_for_stock(engine, code)
                if result is None:
                    skip += 1
                    print(f"{prefix} 스킵")
                    continue
                n_facts = result.get("fact_count", 0)
                if n_facts == 0:
                    no_events += 1
                for f in result.get("facts", []):
                    type_counter[f.get("type", "other")] += 1
                    impact_counter[f.get("financial_impact", "low")] += 1
                disc_str = f" ({n_disc}공시)" if n_disc is not None else ""
                print(f"{prefix} 성공  공시{disc_str} → facts={n_facts}")
            else:
                print(f"{prefix} 수집완료  공시={n_disc}건")

            ok += 1
            time.sleep(0.3)  # DART API rate limit 여유

        except Exception as e:
            fail += 1
            print(f"{prefix} 실패: {e}")

    finished_at = now_kst()
    print(f"\n완료: {finished_at.isoformat()}")
    print(f"성공 {ok} / 이벤트없음 {no_events} / 스킵 {skip} / 실패 {fail}")

    total_facts = 0
    if not collect_only:
        total_facts  = sum(type_counter.values())
        stocks_with_facts = ok - no_events
        avg_facts = total_facts / stocks_with_facts if stocks_with_facts else 0

        print("\n=== Stage D Batch Summary ===")
        print(f"  대상 종목:    {total:>6}")
        print(f"  수집 성공:    {ok:>6}")
        print(f"  공시 없음:    {no_events:>6}")
        print(f"  스킵:         {skip:>6}")
        print(f"  실패:         {fail:>6}")
        print(f"\n  생성 Fact 수:")
        print(f"    총 Fact:    {total_facts:>6,}")
        print(f"    평균:       {avg_facts:>6.1f} / 종목")
        if type_counter:
            print(f"\n  유형 분포:")
            for t, cnt in sorted(type_counter.items(), key=lambda x: -x[1]):
                bar = "█" * (cnt // max(1, total_facts // 40))
                print(f"    {t:<12} {cnt:>5}건  {bar}")
            print(f"\n  재무 영향:")
            for imp in ["high", "medium", "low", "none"]:
                cnt = impact_counter.get(imp, 0)
                print(f"    {imp:<8} {cnt:>5}건")
            high_cnt = impact_counter.get("high", 0)
            if high_cnt:
                print(f"\n  ⚠ financial_impact=high: {high_cnt}건 → Thesis 반영 필수")

        # 최근성 분포 — DB 집계
        try:
            with engine.connect() as conn:
                row = conn.execute(text("""
                    SELECT
                        COUNT(*) FILTER (WHERE (f->>'recency_days')::int <= 30)  AS d30,
                        COUNT(*) FILTER (WHERE (f->>'recency_days')::int <= 90)  AS d90,
                        COUNT(*) FILTER (WHERE (f->>'recency_days')::int <= 180) AS d180
                    FROM ai_reports,
                         jsonb_array_elements(stage_d_sections->'facts') AS f
                    WHERE stage_d_sections IS NOT NULL
                """)).fetchone()
            if row:
                print(f"\n  최근성 (전체 Fact 기준):")
                print(f"    30일 이내   {row[0]:>5}건")
                print(f"    90일 이내   {row[1]:>5}건")
                print(f"    180일 이내  {row[2]:>5}건")
        except Exception:
            pass

    result = {
        "stage": "stage_d_dart",
        "step": mode,
        "run_mode": run_mode,
        "trigger": trigger,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_sec": round((finished_at - started_at).total_seconds(), 1),
        "target_count": total,
        "success": ok,
        "no_events": no_events,
        "skip": skip,
        "fail": fail,
        "fact_type_dist": dict(type_counter),
        "impact_dist": dict(impact_counter),
        "total_facts": total_facts,
        "disclosures_collected": disclosures_collected,
    }
    write_stage_result("stage_d_dart", result)
    return result


def main() -> dict:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collect-only",  action="store_true")
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--force",         action="store_true")
    parser.add_argument("--period-days",   type=int, default=180)
    args = parser.parse_args()
    return run(args.collect_only, args.generate_only, args.force, args.period_days)


if __name__ == "__main__":
    main()
