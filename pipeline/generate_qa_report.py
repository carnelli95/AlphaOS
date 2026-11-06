"""
QA Generator — 파이프라인(뉴스~Stage E)과 독립된 마지막 단계.

그날 각 배치 스크립트가 남긴 qa_reports/stage_runs/{date}_{stage}.json (구조화된
결과, stdout 파싱 아님)을 읽어 qa_reports/{date}.json + latest.json을 생성한다.
"AI가 맞았는지" 판정하지 않고, 사람이 볼 가치가 있는 이상신호만 추린다.

스펙: docs/qa_report_spec.md
사용: python generate_qa_report.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import io
import json
import pathlib
import sys
from datetime import datetime, timedelta

from sqlalchemy import text

from generate_ai_report import get_engine
from stage_result import STAGE_RUNS_DIR, git_commit_short, git_describe, now_kst

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

QA_REPORTS_DIR = pathlib.Path(__file__).parent.parent / "qa_reports"

# compute_confidence()의 caution 문자열 → category (pipeline/generate_thesis.py:170-176)
_CAUTION_CATEGORY = {
    "데이터 부족": "data_missing",
    "정량 데이터 없음 — DART 근거만 확인됨": "quant_missing",
    "정량은 우수하나 DART 사업보고서 근거 미확인": "dart_missing",
}

_ACTION = {
    "warning": "Thesis 검토 권장",
    "critical": "로그 확인 후 재실행",
    "info": "조치 불요 (자동 복구됨)",
}

_DELTA_KEYS = [
    ("collection", "news_count"),
    ("collection", "disclosure_count"),
    ("summary", "issue_count"),
]


def _load_stage_run(date: str, stage: str) -> dict | None:
    path = STAGE_RUNS_DIR / f"{date}_{stage}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _stage_summary(stage: dict | None) -> dict | None:
    if stage is None:
        return None
    return {
        "run_mode": stage.get("run_mode"),
        "trigger": stage.get("trigger"),
        "error": stage.get("error"),
        "started_at": stage.get("started_at"),
        "finished_at": stage.get("finished_at"),
        "duration_sec": stage.get("duration_sec"),
        "target_count": stage.get("target_count"),
        "success": stage.get("success"),
        "fail": stage.get("fail"),
        "skip": stage.get("skip"),
    }


def _load_names(engine, codes: list[str]) -> dict[str, str]:
    if not codes:
        return {}
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT code, name FROM stocks WHERE code = ANY(:codes)"),
            {"codes": codes},
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def _build_issues(engine, stages: dict[str, dict | None]) -> list[dict]:
    issues: list[dict] = []

    for stage_name, stage in stages.items():
        if not stage:
            continue
        if stage.get("error"):
            issues.append({
                "severity": "critical",
                "stock_code": None,
                "name": None,
                "category": "pipeline_failure",
                "reason": f"{stage_name} 중단: {stage['error']}",
                "action": _ACTION["critical"],
            })
        elif stage.get("fail", 0) > 0:
            issues.append({
                "severity": "critical",
                "stock_code": None,
                "name": None,
                "category": "stage_failure",
                "reason": f"{stage_name} 배치 실패 {stage['fail']}건",
                "action": _ACTION["critical"],
            })

    stage_e = stages.get("stage_e")
    if stage_e:
        low_conf = stage_e.get("low_confidence", [])
        codes = [item["stock_code"] for item in low_conf]
        names = _load_names(engine, codes)
        for item in low_conf:
            category = _CAUTION_CATEGORY.get(item["caution"], "quality_warning")
            issues.append({
                "severity": "warning",
                "stock_code": item["stock_code"],
                "name": names.get(item["stock_code"]),
                "category": category,
                "reason": item["caution"],
                "action": _ACTION["warning"],
            })

        attribution = stage_e.get("attribution")
        if attribution and attribution.get("permanent_fail", 0) > 0:
            issues.append({
                "severity": "critical",
                "stock_code": None,
                "name": None,
                "category": "attribution_retry",
                "reason": f"Attribution validator 영구 실패 {attribution['permanent_fail']}건",
                "action": _ACTION["critical"],
            })

    return issues


def _extract(report: dict, section: str, field: str):
    return (report.get(section) or {}).get(field)


def _compute_delta(today: dict, yesterday: dict | None) -> dict | None:
    if yesterday is None:
        return None
    delta: dict[str, int] = {}
    for section, field in _DELTA_KEYS:
        t, y = _extract(today, section, field), _extract(yesterday, section, field)
        if t is not None and y is not None:
            delta[f"{section}.{field}"] = t - y

    t_low = ((today.get("stages") or {}).get("stage_e") or {}).get("quality", {}).get("low")
    y_low = ((yesterday.get("stages") or {}).get("stage_e") or {}).get("quality", {}).get("low")
    if t_low is not None and y_low is not None:
        delta["stage_e.quality.low"] = t_low - y_low

    t_retry = (today.get("attribution") or {}).get("retried")
    y_retry = (yesterday.get("attribution") or {}).get("retried")
    if t_retry is not None and y_retry is not None:
        delta["attribution.retried"] = t_retry - y_retry

    return delta or None


def _prev_date(date: str) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")


def generate(date: str | None = None) -> dict:
    engine = get_engine()
    date = date or now_kst().strftime("%Y-%m-%d")

    stage_c      = _load_stage_run(date, "stage_c")
    stage_d_dart = _load_stage_run(date, "stage_d_dart")
    stage_d_news = _load_stage_run(date, "stage_d_news")
    stage_e      = _load_stage_run(date, "stage_e")
    stages = {
        "stage_c": stage_c, "stage_d_dart": stage_d_dart,
        "stage_d_news": stage_d_news, "stage_e": stage_e,
    }

    # 근사치: news_count는 병합 후 남은 news fact 수(수집 원본 API 건수 아님),
    # disclosure_count는 DART 수집 단계에서 실제로 받아온 공시 건수.
    news_count = (stage_d_news or {}).get("news_facts", 0)
    disclosure_count = (stage_d_dart or {}).get("disclosures_collected", 0)

    quality = (stage_e or {}).get("quality", {"high": 0, "medium": 0, "low": 0})
    total_theses = (stage_e or {}).get("success", 0)

    issues = _build_issues(engine, stages)
    issue_count = len(issues)

    ran_stages = [s for s in stages.values() if s]
    run_mode = (
        "full_rebuild" if any(s.get("run_mode") == "full_rebuild" for s in ran_stages)
        else "incremental" if ran_stages else None
    )
    triggers = sorted({s["trigger"] for s in ran_stages if s.get("trigger")})

    report = {
        "date": date,
        "generated_at": now_kst().isoformat(),
        "run_mode": run_mode,
        "triggers": triggers,
        "pipeline_version": {
            "git_commit": git_commit_short(),
            "git_describe": git_describe(),
        },
        "collection": {
            "news_count": news_count,
            "disclosure_count": disclosure_count,
        },
        "stages": {
            "stage_c": _stage_summary(stage_c),
            "stage_d_dart": _stage_summary(stage_d_dart),
            "stage_d_news": _stage_summary(stage_d_news),
            "stage_e": (
                {**_stage_summary(stage_e), "quality": quality} if stage_e else None
            ),
        },
        "attribution": (stage_e or {}).get("attribution"),
        "issues": issues,
        "summary": {
            "total_theses": total_theses,
            "issue_count": issue_count,
            "issue_rate_pct": (
                round(issue_count / total_theses * 100, 1) if total_theses else 0.0
            ),
        },
    }

    yesterday_path = QA_REPORTS_DIR / f"{_prev_date(date)}.json"
    yesterday = (
        json.loads(yesterday_path.read_text(encoding="utf-8"))
        if yesterday_path.exists() else None
    )
    delta = _compute_delta(report, yesterday)
    if delta:
        report["delta_vs_previous"] = delta

    QA_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    (QA_REPORTS_DIR / f"{date}.json").write_text(payload, encoding="utf-8")
    (QA_REPORTS_DIR / "latest.json").write_text(payload, encoding="utf-8")

    print(f"QA Report 생성 완료 → qa_reports/{date}.json")
    print(f"수집 뉴스 {news_count}건 / 공시 {disclosure_count}건")
    print(f"오늘 생성된 Thesis {total_theses}개 / 주의 필요 {issue_count}건")
    if delta:
        print("전일 대비: " + ", ".join(f"{k} {v:+d}" for k, v in delta.items()))
    if issues:
        print("\n주의 필요:")
        for iss in issues:
            label = iss["name"] or iss["stock_code"] or "(전체)"
            print(f"  [{iss['severity']}] {label} — {iss['reason']}")

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QA Report 생성 (docs/qa_report_spec.md 참고)")
    parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD (기본: 오늘)")
    args = parser.parse_args()
    generate(args.date)
