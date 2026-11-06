"""
Stage D: 최근 주요 이벤트 수집 (DART 수시공시)

DART OpenAPI /list.json 을 사용해 최근 N일 이내 수시공시를 수집한다.
사업보고서(Stage C)와 달리, 최근 이벤트 포착이 목적이므로
공시 유형 필터를 '수시공시'(pblntf_ty=A)로 제한한다.

수집 후 ai_reports.stage_d_raw 에 저장 (JSONB).
Fact 추출은 generate_stage_d.py 가 별도로 수행.

스키마 (stage_d_raw):
{
  "collected_at": "2026-06-29T10:00:00",
  "period_days": 180,
  "source": "dart_disclosure",
  "disclosures": [
    {
      "rcept_no": "20260515000123",
      "rcept_dt": "20260515",
      "report_nm": "주요사항보고서(공급계약체결)",
      "recency_days": 44,
      "corp_cls": "Y"          -- Y=유가증권, K=코스닥
    }
  ]
}
"""

from __future__ import annotations

import os
import sys
import time
import json
from datetime import date, datetime, timedelta

import requests
from dotenv import load_dotenv
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(__file__))
from generate_ai_report import get_engine

load_dotenv(override=True)

DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
_API_KEY = os.environ.get("DART_API_KEY", "")

# 수시공시 유형 필터 (pblntf_ty)
# A=수시공시, B=주요사항보고, F=자율공시 모두 포함
_PBLNTF_TYPES = ["A", "B", "F"]

# Stage C(사업보고서)와 중복되는 정기보고서 제목 접두사 — Stage D에서 제외
_PERIODIC_REPORT_PREFIXES = ("사업보고서", "분기보고서", "반기보고서")


def _days_since(rcept_dt: str, today: date) -> int:
    """'YYYYMMDD' 문자열 → 오늘 기준 경과 일수."""
    try:
        d = date(int(rcept_dt[:4]), int(rcept_dt[4:6]), int(rcept_dt[6:8]))
        return (today - d).days
    except Exception:
        return 999


def fetch_disclosures(
    corp_code: str,
    period_days: int,
    today: date,
) -> list[dict]:
    """
    DART OpenAPI로 corp_code 의 최근 수시공시 목록을 가져온다.
    period_days 이내 공시만 반환.
    """
    bgn_de = (today - timedelta(days=period_days)).strftime("%Y%m%d")
    disclosures = []

    for pblntf_ty in _PBLNTF_TYPES:
        params = {
            "crtfc_key": _API_KEY,
            "corp_code":  corp_code,
            "bgn_de":     bgn_de,
            "pblntf_ty":  pblntf_ty,
            "page_count": 40,
        }
        try:
            resp = requests.get(DART_LIST_URL, params=params, timeout=15)
            data = resp.json()
        except Exception as e:
            print(f"    DART API 오류 ({corp_code}, {pblntf_ty}): {e}")
            continue

        if data.get("status") != "000":
            continue

        for item in data.get("list", []):
            rcept_dt  = item.get("rcept_dt", "")
            report_nm = item.get("report_nm", "")
            # 사업/분기/반기보고서는 Stage C에서 이미 처리 → 제외
            if report_nm.startswith(_PERIODIC_REPORT_PREFIXES):
                continue
            days = _days_since(rcept_dt, today)
            if days > period_days:
                continue
            disclosures.append({
                "rcept_no":    item.get("rcept_no", ""),
                "rcept_dt":    rcept_dt,
                "report_nm":   item.get("report_nm", ""),
                "recency_days": days,
                "corp_cls":    item.get("corp_cls", ""),
            })

    # 날짜 내림차순 정렬 (최신순)
    disclosures.sort(key=lambda x: x["rcept_dt"], reverse=True)
    return disclosures


def ensure_stage_d_column(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE ai_reports ADD COLUMN IF NOT EXISTS stage_d_raw JSONB"
        ))
        conn.execute(text(
            "ALTER TABLE ai_reports ADD COLUMN IF NOT EXISTS stage_d_sections JSONB"
        ))


def collect_stage_d_for_stock(
    engine,
    stock_code: str,
    corp_code: str,
    period_days: int = 180,
    today: date | None = None,
) -> dict | None:
    """단일 종목의 수시공시를 수집해 stage_d_raw에 저장하고 반환한다."""
    if not _API_KEY:
        raise RuntimeError("DART_API_KEY 환경변수가 없습니다.")

    today = today or date.today()
    disclosures = fetch_disclosures(corp_code, period_days, today)

    result = {
        "collected_at": datetime.now().isoformat(),
        "period_days":  period_days,
        "source":       "dart_disclosure",
        "disclosures":  disclosures,
    }

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO ai_reports (stock_code, stage_d_raw)
            VALUES (:code, CAST(:data AS JSONB))
            ON CONFLICT (stock_code) DO UPDATE
              SET stage_d_raw = CAST(:data AS JSONB)
        """), {"code": stock_code, "data": json.dumps(result, ensure_ascii=False)})

    return result


if __name__ == "__main__":
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    target = sys.argv[1] if len(sys.argv) > 1 else "005930"
    engine = get_engine()
    ensure_stage_d_column(engine)

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT name, corp_code FROM stocks WHERE code = :c"),
            {"c": target}
        ).fetchone()

    if not row or not row[1]:
        print(f"corp_code 없음: {target}")
        sys.exit(1)

    name, corp_code = row
    print(f"=== {name} ({target}) corp_code={corp_code} ===")

    ensure_stage_d_column(engine)
    result = collect_stage_d_for_stock(engine, target, corp_code)

    print(f"수집된 공시: {len(result['disclosures'])}건")
    for d in result["disclosures"][:10]:
        print(f"  [{d['rcept_dt']}] +{d['recency_days']}일  {d['report_nm']}")
