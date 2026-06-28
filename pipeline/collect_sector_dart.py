"""
DART company API로 업종코드(induty_code) 수집 → stocks.sector 업데이트.

KSIC 3자리 코드 → 사람이 읽을 수 있는 업종명 매핑.
매핑 없는 코드는 코드 앞 2자리 기반 대분류로 폴백.
"""

from __future__ import annotations

import os
import time
import requests
from dotenv import load_dotenv
from sqlalchemy import text

import sys
sys.path.insert(0, r"C:\Users\carne\Desktop\AIProjects\AlphaOS\pipeline")
from generate_ai_report import get_engine

load_dotenv(override=True)
DART_KEY = os.getenv("DART_API_KEY")

# KSIC 3자리 → 업종명 (주요 코드만, 나머지는 2자리 대분류로 폴백)
KSIC_MAP: dict[str, str] = {
    # 제조업 - 화학/소재
    "201": "석유화학",  "202": "화학",     "203": "화학",     "204": "화학",
    "205": "화학",      "206": "화학",
    "210": "바이오/제약", "212": "바이오/제약",
    "222": "고무/플라스틱", "232": "비금속광물",
    # 금속/철강
    "241": "철강",      "242": "철강",     "251": "금속가공",
    # 반도체/전자
    "261": "반도체",    "262": "전자부품", "263": "통신장비",
    "264": "전기/전자", "265": "전기/전자", "266": "의료기기",
    # 전기/에너지
    "271": "전력기기",  "272": "배터리",   "273": "전선",
    # 기계
    "281": "기계",      "282": "기계",     "291": "자동차",
    "292": "자동차부품",
    # 운수/조선
    "301": "선박/조선", "302": "철도차량", "303": "항공",
    # 기타 제조
    "311": "가구",      "321": "의료기기", "322": "의료기기",
    "323": "광학기기",  "324": "시계",
    # 건설
    "410": "건설",      "421": "건설",     "422": "건설",     "429": "건설",
    # 도소매/유통
    "461": "유통",      "462": "유통",     "463": "유통",     "464": "유통",
    "471": "유통",      "472": "유통",     "478": "유통",     "479": "유통",
    # 운수
    "491": "육상운수",  "492": "육상운수", "501": "해운",     "511": "항공",
    "521": "창고/물류",
    # 미디어/통신
    "581": "미디어",    "591": "영상/음악", "601": "방송",
    "612": "통신서비스", "613": "통신서비스",
    # IT 서비스
    "620": "IT서비스",  "631": "IT서비스",
    # 금융
    "641": "은행",      "642": "은행",
    "651": "보험",      "652": "보험",
    "661": "증권",      "662": "자산운용", "663": "증권",     "664": "증권",
    "671": "금융지주",
    # 부동산
    "681": "부동산",    "682": "부동산",
    # 전문서비스
    "701": "연구개발",  "702": "연구개발",
    "711": "법률/회계", "713": "광고",
    # 에너지
    "351": "전기",      "352": "가스",
    # 음식료
    "101": "식품",      "102": "식품",     "103": "음료",     "110": "담배",
    "131": "섬유",      "141": "의류",     "151": "가죽/신발",
    "161": "목재",      "171": "종이",     "181": "인쇄",
    "191": "석유정제",  "192": "석유제품",
}

# 2자리 대분류 폴백
KSIC_2DIG: dict[str, str] = {
    "10": "식품",   "11": "음료",   "12": "담배",   "13": "섬유",
    "14": "의류",   "15": "가죽",   "16": "목재",   "17": "종이",
    "18": "인쇄",   "19": "석유",   "20": "화학",   "21": "바이오/제약",
    "22": "고무",   "23": "비금속", "24": "철강",   "25": "금속",
    "26": "전기/전자", "27": "전기기계", "28": "기계", "29": "자동차",
    "30": "기타운송", "31": "기타제조", "32": "의료/광학",
    "35": "에너지",
    "41": "건설",   "42": "건설",
    "46": "유통",   "47": "소매",
    "49": "운수",   "50": "해운",   "51": "항공",   "52": "물류",
    "58": "미디어", "59": "영상",   "60": "방송",
    "61": "통신",   "62": "IT서비스", "63": "IT서비스",
    "64": "금융",   "65": "보험",   "66": "금융서비스", "67": "금융지주",
    "68": "부동산",
    "70": "전문서비스", "71": "전문서비스", "72": "연구개발",
    "73": "광고",   "74": "전문서비스",
}


def induty_to_sector(code: str | None) -> str | None:
    if not code:
        return None
    code = str(code).strip().zfill(3)
    # 3자리 정확 매핑
    if code in KSIC_MAP:
        return KSIC_MAP[code]
    # 2자리 대분류 폴백
    prefix2 = code[:2]
    return KSIC_2DIG.get(prefix2, f"기타({prefix2})")


def fetch_induty(corp_code: str) -> str | None:
    try:
        r = requests.get(
            "https://opendart.fss.or.kr/api/company.json",
            params={"crtfc_key": DART_KEY, "corp_code": corp_code},
            timeout=10,
        )
        data = r.json()
        if data.get("status") == "000":
            return data.get("induty_code")
    except Exception:
        pass
    return None


def run() -> None:
    engine = get_engine()

    # corp_code 있고 sector가 아직 NULL인 종목만 대상
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT code, corp_code FROM stocks
            WHERE corp_code IS NOT NULL AND sector IS NULL
            ORDER BY code
        """)).fetchall()

    print(f"수집 대상: {len(rows)}개")
    success = skipped = fail = 0

    for i, (code, corp_code) in enumerate(rows, 1):
        induty_code = fetch_induty(corp_code)
        sector = induty_to_sector(induty_code)

        if sector:
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE stocks SET induty_code = :ic, sector = :s WHERE code = :c"),
                    {"ic": induty_code, "s": sector, "c": code},
                )
            success += 1
            if i % 50 == 0 or i <= 5:
                print(f"[{i}/{len(rows)}] {code} → {induty_code} → {sector}")
        else:
            fail += 1
            if i <= 10:
                print(f"[{i}/{len(rows)}] {code} → 업종코드 없음")

        time.sleep(0.3)

    print(f"\n완료 — 성공: {success} / 실패: {fail}")

    # 결과 확인
    with engine.connect() as conn:
        dist = conn.execute(text("""
            SELECT sector, COUNT(*) AS cnt FROM stocks
            WHERE sector IS NOT NULL
            GROUP BY sector ORDER BY cnt DESC LIMIT 20
        """)).fetchall()

    print("\n=== 업종 분포 (상위 20개) ===")
    for row in dist:
        print(f"  {str(row[0]):<20} {row[1]}개")


if __name__ == "__main__":
    run()
