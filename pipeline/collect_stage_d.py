"""
Stage D: 최근 주요 이벤트 수집 (DART 수시공시)

DART OpenAPI /list.json 을 사용해 최근 N일 이내 이벤트성 공시를 수집한다.
사업보고서(Stage C)와 달리 최근 이벤트 포착이 목적이므로 정기공시는 제외한다.

수집 경로 2가지:
  - 종목별 (fetch_disclosures)         : 신규 종목 최초 수집
  - 시장 전체 (fetch_market_disclosures): 일일 증분 — corp_code 없이 기간 조회 후
                                          유니버스 종목만 골라 stage_d_raw에 병합

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
import re
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

# 공시 유형 필터 (pblntf_ty) — DART 코드 체계:
#   A=정기공시  B=주요사항보고  C=발행공시  D=지분공시  E=기타공시
#   F=외부감사관련  I=거래소공시(공급계약·잠정실적·배당·소각 등 수시공시)
# A는 Stage C 영역이라 제외. 이벤트 대부분은 I와 B에 있다.
_PBLNTF_TYPES = ["B", "I", "F"]

# Stage C(사업보고서)와 중복되는 정기보고서 제목 접두사 — Stage D에서 제외
_PERIODIC_REPORT_PREFIXES = ("사업보고서", "분기보고서", "반기보고서")
_TAG_PREFIX = re.compile(r"^(\[[^\]]*\]\s*)+")

# corp_code 없이 조회할 때 DART가 허용하는 최대 기간은 3개월 — 여유 있게 분할
_MARKET_CHUNK_DAYS = 60
_PAGE_COUNT = 100


def _is_periodic(report_nm: str) -> bool:
    """'[기재정정]사업보고서 (2025.12)'처럼 앞에 붙은 태그를 떼고 정기보고서 여부 판단."""
    return _TAG_PREFIX.sub("", report_nm).startswith(_PERIODIC_REPORT_PREFIXES)


def _to_disclosure(item: dict, today: date) -> dict:
    rcept_dt = item.get("rcept_dt", "")
    return {
        "rcept_no":     item.get("rcept_no", ""),
        "rcept_dt":     rcept_dt,
        "report_nm":    item.get("report_nm", "").strip(),
        "recency_days": _days_since(rcept_dt, today),
        "corp_cls":     item.get("corp_cls", ""),
    }


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
            "page_count": _PAGE_COUNT,
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
            # 사업/분기/반기보고서는 Stage C에서 이미 처리 → 제외
            if _is_periodic(item.get("report_nm", "")):
                continue
            d = _to_disclosure(item, today)
            if d["recency_days"] > period_days:
                continue
            disclosures.append(d)

    # 날짜 내림차순 정렬 (최신순)
    disclosures.sort(key=lambda x: x["rcept_dt"], reverse=True)
    return disclosures


def fetch_market_disclosures(bgn: date, end: date, today: date | None = None) -> dict[str, list[dict]]:
    """
    corp_code 없이 [bgn, end] 기간의 시장 전체 공시를 조회해 corp_code별로 묶어 반환.

    종목별 조회(3유형 × 종목 수)보다 호출 수가 훨씬 적다 — 하루치면 유형당 몇 페이지.
    API 오류는 예외로 올린다: 일부 구간이 빠진 채 워터마크가 전진하면 그 구간
    공시가 영구 누락되기 때문.
    """
    if not _API_KEY:
        raise RuntimeError("DART_API_KEY 환경변수가 없습니다.")
    today = today or date.today()
    by_corp: dict[str, list[dict]] = {}

    chunk_start = bgn
    while chunk_start <= end:
        chunk_end = min(end, chunk_start + timedelta(days=_MARKET_CHUNK_DAYS - 1))
        for pblntf_ty in _PBLNTF_TYPES:
            page_no, total_page = 1, 1
            while page_no <= total_page:
                params = {
                    "crtfc_key":  _API_KEY,
                    "bgn_de":     chunk_start.strftime("%Y%m%d"),
                    "end_de":     chunk_end.strftime("%Y%m%d"),
                    "pblntf_ty":  pblntf_ty,
                    "page_no":    page_no,
                    "page_count": _PAGE_COUNT,
                }
                data = requests.get(DART_LIST_URL, params=params, timeout=30).json()
                status = data.get("status")
                if status == "013":  # 조회된 데이터 없음
                    break
                if status != "000":
                    raise RuntimeError(
                        f"DART list 오류 {status} {data.get('message')} "
                        f"({chunk_start}~{chunk_end}, {pblntf_ty}, p{page_no})"
                    )
                total_page = int(data.get("total_page") or 1)
                for item in data.get("list", []):
                    corp_code = item.get("corp_code")
                    if not corp_code or _is_periodic(item.get("report_nm", "")):
                        continue
                    by_corp.setdefault(corp_code, []).append(_to_disclosure(item, today))
                page_no += 1
                time.sleep(0.1)
        chunk_start = chunk_end + timedelta(days=1)
    return by_corp


def merge_disclosures(existing: list[dict], new: list[dict], period_days: int, today: date) -> list[dict]:
    """rcept_no 기준 중복 제거 + recency 재계산 + 기간 밖·정기보고서 제거 + 최신순 정렬."""
    merged: dict[str, dict] = {}
    for d in list(existing) + list(new):
        if _is_periodic(d.get("report_nm", "")):
            continue
        merged[d["rcept_no"]] = dict(d, recency_days=_days_since(d["rcept_dt"], today))
    out = [d for d in merged.values() if d["recency_days"] <= period_days]
    out.sort(key=lambda x: (x["rcept_dt"], x["rcept_no"]), reverse=True)
    return out


def save_stage_d_raw(engine, stock_code: str, disclosures: list[dict], period_days: int,
                     pending_generate: bool = False) -> dict:
    """pending_generate=True: Fact 재생성이 필요하다는 표시. 생성 성공 시 generate_stage_d가 해제."""
    result = {
        "collected_at": datetime.now().isoformat(),
        "period_days":  period_days,
        "source":       "dart_disclosure",
        "disclosures":  disclosures,
    }
    if pending_generate:
        result["pending_generate"] = True
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO ai_reports (stock_code, stage_d_raw)
            VALUES (:code, CAST(:data AS JSONB))
            ON CONFLICT (stock_code) DO UPDATE
              SET stage_d_raw = CAST(:data AS JSONB)
        """), {"code": stock_code, "data": json.dumps(result, ensure_ascii=False)})
    return result


# ─── 증분 수집 워터마크 (pipeline_state) ─────────────────────────────────

def _ensure_state_table(conn) -> None:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS pipeline_state (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))


def get_state(engine, key: str) -> str | None:
    with engine.begin() as conn:
        _ensure_state_table(conn)
        row = conn.execute(text("SELECT value FROM pipeline_state WHERE key = :k"), {"k": key}).fetchone()
    return row[0] if row else None


def set_state(engine, key: str, value: str) -> None:
    with engine.begin() as conn:
        _ensure_state_table(conn)
        conn.execute(text("""
            INSERT INTO pipeline_state (key, value, updated_at) VALUES (:k, :v, now())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """), {"k": key, "v": value})


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
    return save_stage_d_raw(engine, stock_code, disclosures, period_days)


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
