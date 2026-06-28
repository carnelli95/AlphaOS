"""
DART company.json API로 전 종목 업종코드(induty_code)를 수집해서
stocks 테이블의 sector 컬럼을 채운다.

실행:
    cd pipeline && python fetch_sector_data.py
    python fetch_sector_data.py --limit 10   # 테스트용
"""

from __future__ import annotations

import os
import time

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

load_dotenv(override=True)

DART_COMPANY_URL = "https://opendart.fss.or.kr/api/company.json"

# KSIC 코드 앞 2자리 → 업종명 매핑
# 출처: 통계청 한국표준산업분류(KSIC) 10차
KSIC_SECTOR_MAP: dict[str, str] = {
    "10": "식품/음료", "11": "식품/음료", "12": "담배",
    "13": "섬유/의류", "14": "섬유/의류", "15": "가죽/신발",
    "16": "목재/종이", "17": "목재/종이", "18": "인쇄/기록매체",
    "19": "석유정제",
    "20": "화학/소재", "21": "의약품", "22": "고무/플라스틱",
    "23": "비금속광물", "24": "철강/금속", "25": "금속가공제품",
    "26": "IT/전자부품", "27": "의료/정밀기기", "28": "전기/전자장비",
    "29": "자동차/부품", "30": "조선/운송장비",
    "31": "가구", "32": "기타제조", "33": "산업용기계",
    "35": "에너지/유틸리티",
    "36": "수도/환경", "37": "수도/환경", "38": "수도/환경", "39": "수도/환경",
    "41": "건설", "42": "건설",
    "45": "도소매", "46": "도소매", "47": "도소매",
    "49": "운수/물류", "50": "운수/물류", "51": "운수/물류", "52": "운수/물류",
    "55": "숙박/음식", "56": "숙박/음식",
    "58": "IT/소프트웨어", "59": "IT/소프트웨어", "60": "IT/소프트웨어",
    "61": "통신",
    "62": "IT/소프트웨어", "63": "IT/소프트웨어",
    "64": "금융/보험", "65": "금융/보험", "66": "금융/보험",
    "68": "부동산",
    "72": "연구개발",
    "73": "광고/전문서비스",
    "86": "의료/바이오", "87": "의료/바이오",
    "90": "문화/엔터", "91": "문화/엔터",
}


def induty_code_to_sector(induty_code: str | None) -> str | None:
    if not induty_code:
        return None
    key = induty_code[:2]
    return KSIC_SECTOR_MAP.get(key)


def get_engine() -> Engine:
    db_url = os.environ["DATABASE_URL"]
    return create_engine(db_url, connect_args={"options": "-c lc_messages=C"})


def get_stocks_without_sector(engine: Engine) -> list[tuple[str, str]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT code, corp_code FROM stocks
                WHERE corp_code IS NOT NULL AND sector IS NULL
                ORDER BY code
                """
            )
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def fetch_induty_code(corp_code: str) -> str | None:
    resp = requests.get(
        DART_COMPANY_URL,
        params={"crtfc_key": os.environ["DART_API_KEY"], "corp_code": corp_code},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "000":
        return None
    return data.get("induty_code")


def update_stock_sector(engine: Engine, code: str, induty_code: str, sector: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE stocks
                SET induty_code = :induty_code, sector = :sector
                WHERE code = :code
                """
            ),
            {"code": code, "induty_code": induty_code, "sector": sector},
        )


def run(sleep_seconds: float = 0.5, limit: int | None = None) -> None:
    engine = get_engine()
    stocks = get_stocks_without_sector(engine)
    if limit:
        stocks = stocks[:limit]

    print(f"업종 미수집 종목: {len(stocks)}개")

    success = error = 0
    for i, (code, corp_code) in enumerate(stocks, start=1):
        try:
            induty_code = fetch_induty_code(corp_code)
            if induty_code:
                sector = induty_code_to_sector(induty_code)
                update_stock_sector(engine, code, induty_code, sector)
                print(f"[{i}/{len(stocks)}] {code} → induty={induty_code} sector={sector}")
            else:
                print(f"[{i}/{len(stocks)}] {code} → 업종코드 없음")
            success += 1
        except Exception as e:  # noqa: BLE001
            error += 1
            print(f"[{i}/{len(stocks)}] {code} 실패: {e}")

        time.sleep(sleep_seconds)

    print(f"\n완료: 성공 {success} / 실패 {error}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--sleep", type=float, default=0.5)
    parser.add_argument("--limit", type=int, default=None, help="테스트용 처리 개수 제한")
    args = parser.parse_args()
    run(args.sleep, args.limit)
