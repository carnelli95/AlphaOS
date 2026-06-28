"""
AlphaOS Coverage Report

파이프라인 완료율 + 품질 지표 분포를 출력합니다.
QA Sprint 시작 전 현황 파악 및 배치 완료 후 품질 비교 기준으로 활용합니다.

사용:
  python coverage_report.py
  python coverage_report.py --sector 전기/전자
"""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import text

sys.path.insert(0, os.path.dirname(__file__))
from generate_ai_report import get_engine

W = 54  # 출력 너비


def run(sector_filter: str | None = None) -> None:
    engine = get_engine()

    sector_cond = "AND s.sector = :sector" if sector_filter else ""
    params: dict = {}
    if sector_filter:
        params["sector"] = sector_filter

    with engine.connect() as conn:
        total_row = conn.execute(text(f"""
            SELECT COUNT(*) FROM stocks s
            JOIN ai_reports r ON r.stock_code = s.code
            WHERE 1=1 {sector_cond}
        """), params).fetchone()
        total = total_row[0] if total_row else 0

    if total == 0:
        print("데이터 없음.")
        return

    with engine.connect() as conn:
        cov = conn.execute(text(f"""
            SELECT
                COUNT(r.alpha_score)                                         AS alpha_n,
                COUNT(r.stage_b_sections)                                    AS stageb_n,
                COUNT(r.stage_c_sections)                                    AS stagec_n,
                COUNT(r.thesis_sections)                                     AS thesis_n,
                SUM(CASE WHEN r.thesis_sections->'reason_graph' IS NOT NULL
                         THEN 1 ELSE 0 END)                                  AS rg_n,
                SUM(CASE WHEN r.thesis_sections->'confidence' IS NOT NULL
                         THEN 1 ELSE 0 END)                                  AS conf_n
            FROM stocks s
            JOIN ai_reports r ON r.stock_code = s.code
            WHERE 1=1 {sector_cond}
        """), params).fetchone()

    alpha_n, stageb_n, stagec_n, thesis_n, rg_n, conf_n = cov
    thesis_total = int(thesis_n or 0)

    # 분포 쿼리 — Thesis 생성 종목 기준
    dist_rows = {}
    if thesis_total > 0:
        dist_sql = f"""
            SELECT
                -- Strengths 분포 (0~4+)
                SUM(CASE WHEN jsonb_array_length(COALESCE(r.thesis_sections->'strengths','[]'::jsonb))=0 THEN 1 ELSE 0 END) AS s0,
                SUM(CASE WHEN jsonb_array_length(COALESCE(r.thesis_sections->'strengths','[]'::jsonb))=1 THEN 1 ELSE 0 END) AS s1,
                SUM(CASE WHEN jsonb_array_length(COALESCE(r.thesis_sections->'strengths','[]'::jsonb))=2 THEN 1 ELSE 0 END) AS s2,
                SUM(CASE WHEN jsonb_array_length(COALESCE(r.thesis_sections->'strengths','[]'::jsonb))=3 THEN 1 ELSE 0 END) AS s3,
                SUM(CASE WHEN jsonb_array_length(COALESCE(r.thesis_sections->'strengths','[]'::jsonb))>=4 THEN 1 ELSE 0 END) AS s4p,

                -- Risks 분포 (0~3+)
                SUM(CASE WHEN jsonb_array_length(COALESCE(r.thesis_sections->'risks','[]'::jsonb))=0 THEN 1 ELSE 0 END) AS r0,
                SUM(CASE WHEN jsonb_array_length(COALESCE(r.thesis_sections->'risks','[]'::jsonb))=1 THEN 1 ELSE 0 END) AS r1,
                SUM(CASE WHEN jsonb_array_length(COALESCE(r.thesis_sections->'risks','[]'::jsonb))=2 THEN 1 ELSE 0 END) AS r2,
                SUM(CASE WHEN jsonb_array_length(COALESCE(r.thesis_sections->'risks','[]'::jsonb))>=3 THEN 1 ELSE 0 END) AS r3p,

                -- Quant 드라이버 분포 (0~3+)
                SUM(CASE WHEN jsonb_array_length(COALESCE(r.thesis_sections->'reason_graph'->'quant_drivers','[]'::jsonb))=0 THEN 1 ELSE 0 END) AS q0,
                SUM(CASE WHEN jsonb_array_length(COALESCE(r.thesis_sections->'reason_graph'->'quant_drivers','[]'::jsonb))=1 THEN 1 ELSE 0 END) AS q1,
                SUM(CASE WHEN jsonb_array_length(COALESCE(r.thesis_sections->'reason_graph'->'quant_drivers','[]'::jsonb))=2 THEN 1 ELSE 0 END) AS q2,
                SUM(CASE WHEN jsonb_array_length(COALESCE(r.thesis_sections->'reason_graph'->'quant_drivers','[]'::jsonb))>=3 THEN 1 ELSE 0 END) AS q3p,

                -- DART 드라이버 분포 (0~3+)
                SUM(CASE WHEN jsonb_array_length(COALESCE(r.thesis_sections->'reason_graph'->'dart_drivers','[]'::jsonb))=0 THEN 1 ELSE 0 END) AS d0,
                SUM(CASE WHEN jsonb_array_length(COALESCE(r.thesis_sections->'reason_graph'->'dart_drivers','[]'::jsonb))=1 THEN 1 ELSE 0 END) AS d1,
                SUM(CASE WHEN jsonb_array_length(COALESCE(r.thesis_sections->'reason_graph'->'dart_drivers','[]'::jsonb))=2 THEN 1 ELSE 0 END) AS d2,
                SUM(CASE WHEN jsonb_array_length(COALESCE(r.thesis_sections->'reason_graph'->'dart_drivers','[]'::jsonb))>=3 THEN 1 ELSE 0 END) AS d3p,

                -- Confidence 레벨 분포
                SUM(CASE WHEN (r.thesis_sections->'confidence'->>'level')='High'   THEN 1 ELSE 0 END) AS conf_high,
                SUM(CASE WHEN (r.thesis_sections->'confidence'->>'level')='Medium' THEN 1 ELSE 0 END) AS conf_med,
                SUM(CASE WHEN (r.thesis_sections->'confidence'->>'level')='Low'    THEN 1 ELSE 0 END) AS conf_low,
                SUM(CASE WHEN r.thesis_sections->'confidence' IS NULL              THEN 1 ELSE 0 END) AS conf_none

            FROM stocks s
            JOIN ai_reports r ON r.stock_code = s.code
            WHERE r.thesis_sections IS NOT NULL {sector_cond}
        """
        with engine.connect() as conn:
            dist = conn.execute(text(dist_sql), params).fetchone()
        if dist:
            keys = ["s0","s1","s2","s3","s4p","r0","r1","r2","r3p",
                    "q0","q1","q2","q3p","d0","d1","d2","d3p",
                    "conf_high","conf_med","conf_low","conf_none"]
            dist_rows = dict(zip(keys, dist))

    with engine.connect() as conn:
        missing = conn.execute(text(f"""
            SELECT s.code, s.name, s.sector
            FROM stocks s
            JOIN ai_reports r ON r.stock_code = s.code
            WHERE r.thesis_sections IS NULL
              AND r.alpha_score IS NOT NULL
              {sector_cond}
            ORDER BY (r.alpha_score->>'score')::int DESC NULLS LAST
            LIMIT 10
        """), params).fetchall()

    # ── 출력 ─────────────────────────────────────────
    def pct(n):
        n = int(n or 0)
        return f"{n:>5} / {total} ({n/total*100:5.1f}%)"

    header = " Coverage Report"
    if sector_filter:
        header += f" [{sector_filter}]"

    print()
    print("=" * W)
    print(header)
    print("=" * W)
    print(f"  대상 종목           {total:>5}개")
    print()
    print("  [ 파이프라인 완료율 ]")
    print(f"  Alpha Score       {pct(alpha_n)}")
    print(f"  Stage B (Peer)    {pct(stageb_n)}")
    print(f"  Stage C (DART)    {pct(stagec_n)}")
    print(f"  Investment Thesis {pct(thesis_n)}")
    print(f"  Reason Graph      {pct(rg_n)}")
    print(f"  Confidence 산출   {pct(conf_n)}")

    if thesis_total > 0 and dist_rows:
        t = thesis_total

        def bar(n):
            n = int(n or 0)
            b = round(n / t * 20)
            flag = "  !! 이상" if n / t > 0.25 and n == dist_rows.get("r0") else ""
            return f"{n:>5}개 ({n/t*100:4.1f}%) {'|' * b}{flag}"

        print()
        print("  [ Strengths 분포 ]")
        for label, key in [("0개", "s0"), ("1개", "s1"), ("2개", "s2"),
                            ("3개", "s3"), ("4개+", "s4p")]:
            print(f"    {label:<5} {bar(dist_rows.get(key, 0))}")

        risk0_ratio = (dist_rows.get("r0", 0) or 0) / t
        print()
        print("  [ Risks 분포 ]")
        for label, key in [("0개", "r0"), ("1개", "r1"), ("2개", "r2"), ("3개+", "r3p")]:
            n = int(dist_rows.get(key, 0) or 0)
            is_r0_anomaly = (key == "r0" and risk0_ratio > 0.25)
            flag = "  !! Risk 0개 비율 높음 — 프롬프트 점검 필요" if is_r0_anomaly else ""
            b = round(n / t * 20)
            print(f"    {label:<5} {n:>5}개 ({n/t*100:4.1f}%) {'|' * b}{flag}")

        print()
        print("  [ Reason Graph — 정량 드라이버 분포 ]")
        for label, key in [("0개", "q0"), ("1개", "q1"), ("2개", "q2"), ("3개+", "q3p")]:
            n = int(dist_rows.get(key, 0) or 0)
            b = round(n / t * 20)
            print(f"    {label:<5} {n:>5}개 ({n/t*100:4.1f}%) {'|' * b}")

        print()
        print("  [ Reason Graph — DART 드라이버 분포 ]")
        for label, key in [("0개", "d0"), ("1개", "d1"), ("2개", "d2"), ("3개+", "d3p")]:
            n = int(dist_rows.get(key, 0) or 0)
            b = round(n / t * 20)
            print(f"    {label:<5} {n:>5}개 ({n/t*100:4.1f}%) {'|' * b}")

        print()
        print("  [ Confidence 분포 ]")
        for label, key in [("High  ", "conf_high"), ("Medium", "conf_med"),
                            ("Low   ", "conf_low"), ("(없음)", "conf_none")]:
            n = int(dist_rows.get(key, 0) or 0)
            b = round(n / t * 20)
            flag = "  !! --fix-missing 권장" if key == "conf_none" and n > 0 else ""
            print(f"    {label}  {n:>5}개 ({n/t*100:4.1f}%) {'|' * b}{flag}")

    if missing:
        print()
        print("  [ Thesis 미생성 상위 10 (Alpha Score 순) ]")
        for code, name, sector in missing:
            print(f"    {code}  {name:<16}  {sector or '-'}")

    print()
    print("=" * W)
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AlphaOS Coverage Report")
    parser.add_argument("--sector", type=str, default=None, help="특정 업종만 (예: 전기/전자)")
    args = parser.parse_args()
    run(args.sector)
