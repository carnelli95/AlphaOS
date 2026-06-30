"""
QA 점수 집계기.

qa_sample.py 실행 후 qa_results.csv를 채운 뒤:
  python pipeline/qa_score.py

각 항목별 통과율 + 평균 총점 출력.
"""

from __future__ import annotations

import csv
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "qa_results.csv")

CHECKLIST = [
    "사실관계_정확",
    "DART_attribution",
    "News_attribution",
    "산업일반론_없음",
    "투자논리_자연스러움",
    "핵심이벤트_누락없음",
]

LABELS = [
    "사실관계 정확",
    "DART attribution",
    "News attribution",
    "산업 일반론 없음",
    "투자 논리 자연스러움",
    "핵심 이벤트 누락 없음",
]


def main() -> None:
    path = os.path.abspath(CSV_PATH)
    if not os.path.exists(path):
        print(f"파일 없음: {path}")
        print("먼저 python pipeline/qa_sample.py 를 실행하세요.")
        return

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    filled = [r for r in rows if any(r.get(col, "").strip() in ("0", "1") for col in CHECKLIST)]
    if not filled:
        print("아직 점수가 입력되지 않았습니다. qa_results.csv를 채운 뒤 다시 실행하세요.")
        return

    n = len(filled)
    col_pass = {col: 0 for col in CHECKLIST}
    totals = []

    for r in filled:
        row_total = 0
        for col in CHECKLIST:
            val = r.get(col, "").strip()
            if val == "1":
                col_pass[col] += 1
                row_total += 1
        totals.append(row_total)

    avg_total = sum(totals) / n
    perfect = sum(1 for t in totals if t == 6)

    print("=" * 56)
    print(f"QA 점수 집계  ({n}종목)")
    print("=" * 56)
    print(f"평균 총점:   {avg_total:.2f} / 6")
    print(f"6/6 비율:    {perfect}/{n} ({perfect/n*100:.0f}%)")
    print()
    print(f"{'항목':<28}  {'통과':<8}  {'실패':<8}  통과율")
    print("-" * 56)
    for col, label in zip(CHECKLIST, LABELS):
        passed = col_pass[col]
        failed = n - passed
        rate = passed / n * 100
        print(f"{label:<28}  {passed}/{n:<5}   {failed}/{n:<5}   {rate:.0f}%")

    # 그룹별 집계
    groups: dict[str, list[int]] = {}
    for r, total in zip(filled, totals):
        g = r.get("group", "기타")
        groups.setdefault(g, []).append(total)

    if len(groups) > 1:
        print()
        print("그룹별 평균:")
        for g, scores in groups.items():
            print(f"  {g:<16}  {sum(scores)/len(scores):.2f} / 6  ({len(scores)}종목)")


if __name__ == "__main__":
    main()
