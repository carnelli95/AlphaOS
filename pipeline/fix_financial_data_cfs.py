"""
financial_data CFS/OFS 혼용 버그 수정 스크립트.

문제: DART fnlttSinglAcnt.json은 fs_div 파라미터와 무관하게 CFS+OFS를
     한 응답에 같이 반환한다. 기존 수집 코드가 account_nm 기준으로만
     저장하면서 나중에 처리된 OFS 값이 CFS 값을 덮어쓴 것으로 추정됨
     (실측 확인됨: 삼성전자 2023 영업이익 CFS=+6.57조 vs OFS=-11.5조,
     DB에는 OFS 값이 저장돼 있었음).

해결: 응답에서 CFS 항목을 우선 사용하고, CFS가 없는 회사(종속회사가
     없어 연결재무제표를 작성하지 않는 소형주)만 OFS를 사용한다.

대상: financial_data에 이미 있는 (code, year) 조합을 그대로 재조회해서
     정확한 값으로 UPSERT (덮어쓰기) 한다. 새 연도를 추가하진 않는다
     (그건 기존 수집 파이프라인의 별도 역할).
"""

from __future__ import annotations

import os
import time

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

load_dotenv(override=True)

DART_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
REPRT_CODE_ANNUAL = "11011"  # 사업보고서(연간)


def get_engine() -> Engine:
    db_url = os.environ["DATABASE_URL"]
    return create_engine(db_url, connect_args={"options": "-c lc_messages=C"})


def get_code_year_pairs(engine: Engine) -> list[tuple[str, str, str]]:
    """financial_data에 이미 있는 (code, year) 조합 + corp_code를 가져온다."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT DISTINCT fd.code, fd.year, s.corp_code
                FROM financial_data fd
                JOIN stocks s ON s.code = fd.code
                WHERE s.corp_code IS NOT NULL
                ORDER BY fd.code, fd.year
                """
            )
        ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def get_already_fixed(engine: Engine) -> set[tuple[str, str]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT code, year FROM financial_data_fix_log
                WHERE status = 'success'
                """
            )
        ).fetchall()
    return {(r[0], r[1]) for r in rows}


def log_result(engine: Engine, code: str, year: str, status: str, message: str = "") -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO financial_data_fix_log (code, year, status, message)
                VALUES (:code, :year, :status, :message)
                ON CONFLICT (code, year) DO UPDATE SET
                    status = :status, message = :message, attempted_at = now()
                """
            ),
            {"code": code, "year": year, "status": status, "message": message},
        )


def fetch_all_items(corp_code: str, bsns_year: str) -> list[dict]:
    """DART에서 CFS+OFS 항목을 모두 반환한다. 스키마 레벨에서 fs_type으로 구분 저장."""
    resp = requests.get(
        DART_URL,
        params={
            "crtfc_key": os.environ["DART_API_KEY"],
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": REPRT_CODE_ANNUAL,
            "fs_div": "CFS",  # DART는 파라미터 무관하게 CFS+OFS 모두 반환
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "000":
        raise RuntimeError(f"DART status={data.get('status')} message={data.get('message')}")

    return [i for i in data.get("list", []) if i.get("fs_div") in ("CFS", "OFS")]


def parse_amount(raw: str | None) -> int | None:
    if not raw:
        return None
    cleaned = raw.replace(",", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def upsert_financial_data(engine: Engine, code: str, year: str, items: list[dict]) -> int:
    updated = 0
    with engine.begin() as conn:
        for item in items:
            fs_type = item.get("fs_div", "OFS")
            amount = parse_amount(item.get("thstrm_amount"))
            if amount is None:
                continue
            conn.execute(
                text(
                    """
                    INSERT INTO financial_data (code, year, fs_type, account_nm, amount)
                    VALUES (:code, :year, :fs_type, :account_nm, :amount)
                    ON CONFLICT (code, year, fs_type, account_nm) DO UPDATE SET
                        amount = EXCLUDED.amount
                    """
                ),
                {"code": code, "year": year, "fs_type": fs_type,
                 "account_nm": item["account_nm"], "amount": amount},
            )
            updated += 1
    return updated


def run(sleep_seconds: float = 1.0, limit: int | None = None, only_code: str | None = None) -> None:
    engine = get_engine()
    pairs = get_code_year_pairs(engine)
    if only_code is not None:
        pairs = [p for p in pairs if p[0] == only_code]
    already_fixed = get_already_fixed(engine)
    todo = [(code, year, corp_code) for code, year, corp_code in pairs if (code, year) not in already_fixed]
    if limit is not None:
        todo = todo[:limit]

    print(f"전체 {len(pairs)}개 (code,year) 중 {len(todo)}개 처리 예정 "
          f"(이미 처리: {len(already_fixed)}개)")

    for i, (code, year, corp_code) in enumerate(todo, start=1):
        try:
            items = fetch_all_items(corp_code, year)
            count = upsert_financial_data(engine, code, year, items)
            log_result(engine, code, year, "success", f"{count}개 계정 갱신")
            print(f"[{i}/{len(todo)}] {code} {year}년 성공 ({count}개 계정)")
        except Exception as e:  # noqa: BLE001
            log_result(engine, code, year, "error", str(e))
            print(f"[{i}/{len(todo)}] {code} {year}년 실패: {e}")

        time.sleep(sleep_seconds)

    print("완료")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--limit", type=int, default=None, help="테스트용 처리 개수 제한")
    parser.add_argument("--code", type=str, default=None, help="특정 종목코드만 처리 (테스트용)")
    args = parser.parse_args()
    run(args.sleep, args.limit, args.code)
