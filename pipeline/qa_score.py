"""
QA 점수 집계기.

qa_sample.py 실행 후 qa_results.csv를 채운 뒤:
  python pipeline/qa_score.py

각 항목별 통과율 + 평균 총점 + 베타 공개 판정을 출력.
"""

from __future__ import annotations

import csv
import io
import os
import sys

if hasattr(sys.stdout, "buffer"):
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

# 베타 공개 통과 기준
BETA_CRITERIA = {
    "avg_min":        5.7,   # 평균 총점
    "perfect_pct":    0.80,  # 6/6 비율
    "fact_errors":    0,     # 사실관계_정확 실패 허용 수
    "attr_errors":    0,     # DART + News attribution 실패 허용 합계
}


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
    failed_items: list[tuple[str, str, list[str], str]] = []  # (code, name, failed_cols, memo)

    for r in filled:
        row_total = 0
        row_failed = []
        for col in CHECKLIST:
            val = r.get(col, "").strip()
            if val == "1":
                col_pass[col] += 1
                row_total += 1
            elif val == "0":
                row_failed.append(col)
        totals.append(row_total)
        if row_failed:
            memo = r.get("memo", "").strip()
            failed_items.append((r.get("code", ""), r.get("name", ""), row_failed, memo))

    avg_total = sum(totals) / n
    perfect = sum(1 for t in totals if t == 6)

    print("=" * 60)
    print(f"QA 점수 집계  ({n}종목)")
    print("=" * 60)
    print(f"평균 총점:   {avg_total:.2f} / 6")
    print(f"6/6 비율:    {perfect}/{n} ({perfect/n*100:.0f}%)")
    print()
    print(f"{'항목':<28}  {'통과':<8}  {'실패':<6}  통과율")
    print("-" * 56)
    for col, label in zip(CHECKLIST, LABELS):
        passed = col_pass[col]
        failed = n - passed
        rate = passed / n * 100
        flag = "  ⚠" if failed > 0 else ""
        print(f"{label:<28}  {passed}/{n:<5}   {failed}/{n:<5}  {rate:.0f}%{flag}")

    # 그룹별 집계
    groups: dict[str, list[int]] = {}
    for r, total in zip(filled, totals):
        g = r.get("group", "기타")
        groups.setdefault(g, []).append(total)

    if len(groups) > 1:
        print()
        print("그룹별 평균:")
        for g, scores in groups.items():
            avg = sum(scores) / len(scores)
            flag = "  ⚠" if avg < 5.5 else ""
            print(f"  {g:<16}  {avg:.2f} / 6  ({len(scores)}종목){flag}")

    # 실패 항목 메모
    if failed_items:
        print()
        print("실패 항목 상세:")
        for code, name, failed_cols, memo in failed_items:
            failed_labels = [LABELS[CHECKLIST.index(c)] for c in failed_cols]
            print(f"  {code} {name}")
            print(f"    실패: {', '.join(failed_labels)}")
            if memo:
                print(f"    메모: {memo}")

    # 베타 통과 판정
    fact_errors   = n - col_pass["사실관계_정확"]
    attr_errors   = (n - col_pass["DART_attribution"]) + (n - col_pass["News_attribution"])
    perfect_ratio = perfect / n

    checks = [
        (f"평균 총점 ≥ {BETA_CRITERIA['avg_min']}",    avg_total >= BETA_CRITERIA["avg_min"],    f"실제 {avg_total:.2f}"),
        (f"6/6 비율 ≥ {BETA_CRITERIA['perfect_pct']*100:.0f}%", perfect_ratio >= BETA_CRITERIA["perfect_pct"], f"실제 {perfect_ratio*100:.0f}%"),
        (f"사실관계 오류 = {BETA_CRITERIA['fact_errors']}건",    fact_errors <= BETA_CRITERIA["fact_errors"],  f"실제 {fact_errors}건"),
        (f"Attribution 오류 = {BETA_CRITERIA['attr_errors']}건", attr_errors <= BETA_CRITERIA["attr_errors"],  f"실제 {attr_errors}건"),
    ]
    passed_all = all(ok for _, ok, _ in checks)

    print()
    print("=" * 60)
    print(f"베타 공개 판정: {'✅ 통과' if passed_all else '❌ 미통과'}")
    print("=" * 60)
    for label, ok, detail in checks:
        mark = "✅" if ok else "❌"
        print(f"  {mark} {label:<30}  ({detail})")

    if not passed_all:
        print()
        print("  → 미통과 항목을 수정한 뒤 Stage E 부분 재생성 후 재검토하세요.")
    else:
        print()
        print("  → git tag beta-v1 && git push origin beta-v1")


if __name__ == "__main__":
    main()
