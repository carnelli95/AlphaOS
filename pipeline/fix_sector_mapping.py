"""
기존 induty_code 재매핑 (API 재호출 없음).
문제: "303" → "항공" 오분류. 자동차부품 회사가 항공으로 잘못 분류됨.
해결: 2자리 prefix 기반으로만 매핑 + 주요 코드 수정.
"""
import sys, io
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\carne\Desktop\AIProjects\AlphaOS\pipeline")

from generate_ai_report import get_engine
from sqlalchemy import text

# 2자리 prefix 기반 매핑 (제조 vs 운영 분리)
KSIC_2DIG = {
    "10": "식품", "11": "음료", "12": "담배",
    "13": "섬유", "14": "의류", "15": "가죽/신발",
    "16": "목재", "17": "종이", "18": "인쇄",
    "19": "석유정제",
    "20": "화학", "21": "바이오/제약", "22": "고무/플라스틱",
    "23": "비금속광물",
    "24": "철강/금속", "25": "금속가공",
    "26": "전기/전자",   # 삼성전자(264), SK하이닉스(2612)
    "27": "전기기계/배터리",
    "28": "기계",
    "29": "자동차제조",  # 현대차가 29xxx면 여기
    "30": "운송장비제조",# 조선/항공부품/자동차부품(303) 제조업
    "31": "기타제조",
    "32": "의료/광학기기",
    "35": "에너지",
    "36": "용수/폐기물",
    "41": "건설", "42": "건설",
    "45": "자동차판매",
    "46": "유통/도매", "47": "소매",
    "49": "육상운수", "50": "해운",
    "51": "항공운수",   # 대한항공(511) — 운송장비제조(30x)와 분리
    "52": "물류/창고",
    "55": "숙박", "56": "음식업",
    "58": "미디어/출판",
    "59": "영상/음악",
    "60": "방송",
    "61": "통신서비스", "62": "IT서비스", "63": "IT서비스",
    "64": "금융",       # KB금융(64992)
    "65": "보험",       # 삼성생명(65110)
    "66": "금융서비스",
    "67": "금융지주",
    "68": "부동산",
    "70": "연구개발",
    "71": "전문서비스", "72": "건축/엔지니어링", "73": "광고",
    "74": "전문서비스",
    "75": "수의업",
    "86": "의료", "87": "사회복지",
    "90": "예술/스포츠", "91": "스포츠시설",
}


def induty_to_sector(code) -> str | None:
    if not code:
        return None
    s = str(code).strip()
    if len(s) < 2:
        return None
    prefix2 = s[:2]
    return KSIC_2DIG.get(prefix2, f"기타({prefix2})")


def run():
    engine = get_engine()

    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT code, induty_code FROM stocks WHERE induty_code IS NOT NULL"
        )).fetchall()

    print(f"재매핑 대상: {len(rows)}개")
    counts = {}
    updates = []
    for code, induty_code in rows:
        sector = induty_to_sector(induty_code)
        updates.append({"code": code, "sector": sector})
        counts[sector] = counts.get(sector, 0) + 1

    with engine.begin() as conn:
        for u in updates:
            conn.execute(
                text("UPDATE stocks SET sector = :sector WHERE code = :code"),
                u
            )

    print(f"업데이트 완료\n")

    # 분포 출력
    print("=== 업종 분포 (상위 25개) ===")
    for sector, cnt in sorted(counts.items(), key=lambda x: -x[1])[:25]:
        print(f"  {str(sector):<20} {cnt}개")

    # 주요 종목 확인
    print("\n=== 주요 종목 업종 확인 ===")
    with engine.connect() as conn:
        rows2 = conn.execute(text("""
            SELECT code, name, induty_code, sector FROM stocks
            WHERE code IN ('005930','000660','035420','105560','068270',
                           '005380','032830','003490','204320','000300')
            ORDER BY code
        """)).fetchall()
    for r in rows2:
        print(f"  {r[0]}  {r[1]:<20}  induty={str(r[2]):<8}  sector={r[3]}")


if __name__ == "__main__":
    run()
