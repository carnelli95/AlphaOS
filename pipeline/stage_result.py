"""
배치 스크립트가 실행 결과를 구조화된 JSON으로 남기기 위한 공유 헬퍼.

stdout print는 사람이 보는 로그일 뿐 시스템 간 인터페이스로 쓰지 않는다.
QA Generator(generate_qa_report.py)는 이 모듈이 저장한 JSON만 읽는다.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
from datetime import datetime, timedelta, timezone

STAGE_RUNS_DIR = pathlib.Path(__file__).parent.parent / "qa_reports" / "stage_runs"
KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def git_commit_short() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=pathlib.Path(__file__).parent.parent,
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def git_describe() -> str | None:
    """가장 최근 태그 기준 버전 표기 (예: beta-v1-15-gabc1234). 태그 없으면 커밋 SHA로 대체."""
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            cwd=pathlib.Path(__file__).parent.parent,
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def write_stage_result(stage: str, result: dict, *, date: str | None = None) -> pathlib.Path:
    """qa_reports/stage_runs/{date}_{stage}.json 저장 (같은 날 재실행 시 덮어씀)."""
    STAGE_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    date = date or now_kst().strftime("%Y-%m-%d")
    payload = {**result, "git_commit": git_commit_short()}
    path = STAGE_RUNS_DIR / f"{date}_{stage}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
