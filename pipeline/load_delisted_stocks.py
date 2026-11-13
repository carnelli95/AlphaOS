"""
상장폐지 종목 메타데이터 적재 → delisted_stocks.

stocks 테이블은 현재 상장 종목 스냅샷이라 기간 중 폐지된 종목이 빠져 있다(생존 편향).
FDR KRX-DELISTING에서 KOSPI 보통주 폐지 종목을 가져와 DART corp_code를 붙여 저장한다.
시세·재무 수집은 하지 않는다(별도 단계).

대상: Market='KOSPI', SecuGroup='주권', 코드 끝자리 '0'(보통주), 폐지일 >= --since.
  · 우선주는 제외한다. 점수를 매기는 유니버스(corp_code 보유 종목)가 보통주뿐이다.

업종: DART company.json의 induty_code(KSIC)를 받아 stocks.sector와 같은 규칙으로 변환한다.
  stocks의 sector는 KSIC 앞 2자리로 정해진다(2026-11-13 확인, 54개 접두어 모두 일대일).
  그 대응표를 stocks에서 읽어 쓰고, stocks에 없는 접두어는 '기타(xx)'로 둔다(기존 표기와 같음).

사용:
  cd pipeline
  python load_delisted_stocks.py --dry-run          # 저장 없이 출력만
  python load_delisted_stocks.py                    # 저장 (업서트, 재실행 안전)
  python load_delisted_stocks.py --since 2021-01-01
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
import zipfile
import xml.etree.ElementTree as ET

import requests
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(__file__))
from dotenv import load_dotenv

load_dotenv(override=True)
from batch_collect_runner import get_engine

DART_CORPCODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
DART_COMPANY_URL = "https://opendart.fss.or.kr/api/company.json"

# 사유 분류. 순서대로 첫 매칭을 쓴다. 수익률 규칙에는 쓰지 않고 보고·민감도 분석용.
_CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("기타",   ("SPAC", "스팩")),
    ("비부실", ("완전자회사화", "해산 사유", "신청에 의한", "합병", "지주회사")),
    ("부실",   ("감사의견", "감사범위", "계속성", "시가총액 미달", "자본잠식",
               "매출액 미달", "부도", "회생", "파산", "영업정지", "공시")),
]


def classify_reason(reason: str | None) -> str:
    """폐지 사유 문자열 → '부실' / '비부실' / '기타'. 매칭 없으면 '기타'."""
    r = (reason or "").strip()
    for category, keywords in _CATEGORY_RULES:
        if any(k in r for k in keywords):
            return category
    return "기타"


def is_target(market: str, secu_group: str, symbol: str, delisting_date: str, since: str) -> bool:
    return (
        market == "KOSPI"
        and secu_group == "주권"
        and isinstance(symbol, str) and len(symbol) == 6 and symbol.endswith("0")
        and str(delisting_date)[:10] >= since
    )


def fetch_delisted(since: str) -> list[dict]:
    import FinanceDataReader as fdr

    df = fdr.StockListing("KRX-DELISTING")
    out = []
    for _, r in df.iterrows():
        dd = r.get("DelistingDate")
        if dd is None or str(dd) in ("NaT", "nan", ""):
            continue
        if not is_target(r.get("Market"), r.get("SecuGroup"), r.get("Symbol"), str(dd), since):
            continue
        ld = r.get("ListingDate")
        reason = r.get("Reason")
        reason = None if reason is None or str(reason) == "nan" else str(reason).strip()
        out.append({
            "code": r["Symbol"],
            "name": str(r["Name"]).strip(),
            "market": "KOSPI",
            "listing_date": None if ld is None or str(ld) in ("NaT", "nan", "") else str(ld)[:10],
            "delisting_date": str(dd)[:10],
            "reason": reason,
            "reason_category": classify_reason(reason),
        })
    return out


def fetch_corp_codes() -> dict[str, str]:
    """DART 기업코드 목록: stock_code → corp_code. 폐지 회사도 종목코드가 남아 있다."""
    key = os.getenv("DART_API_KEY")
    if not key:
        raise SystemExit("오류: DART_API_KEY 환경변수가 설정되지 않았습니다.")
    try:
        res = requests.get(DART_CORPCODE_URL, params={"crtfc_key": key}, timeout=60)
        root = ET.fromstring(zipfile.ZipFile(io.BytesIO(res.content)).read("CORPCODE.xml"))
    except Exception as e:  # noqa: BLE001 — URL(키 포함)을 출력하지 않기 위해 유형만 표시
        raise SystemExit(f"DART 기업코드 조회 실패: {type(e).__name__}")
    out = {}
    for e in root.iter("list"):
        sc = (e.findtext("stock_code") or "").strip()
        if sc:
            out[sc] = e.findtext("corp_code").strip()
    return out


def sector_from_induty(induty_code: str | None, prefix_map: dict[str, str]) -> str | None:
    """KSIC 업종코드 → 업종명. stocks의 앞 2자리 대응표를 쓰고 없으면 '기타(xx)'."""
    if not induty_code:
        return None
    p = str(induty_code).strip()[:2]
    return prefix_map.get(p, f"기타({p})")


def load_prefix_map(engine) -> dict[str, str]:
    """stocks에서 KSIC 앞 2자리 → sector 대응표. 한 접두어에 업종이 둘 이상이면 중단."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT left(induty_code, 2), array_agg(DISTINCT sector)
            FROM stocks WHERE induty_code IS NOT NULL AND sector IS NOT NULL
            GROUP BY 1
        """)).fetchall()
    bad = [(p, s) for p, s in rows if len(s) != 1]
    if bad:
        raise SystemExit(f"stocks.sector가 KSIC 앞 2자리로 정해지지 않음: {bad}")
    return {p: s[0] for p, s in rows}


def fetch_induty(corp_code: str) -> str | None:
    key = os.getenv("DART_API_KEY")
    for _ in range(3):
        try:
            data = requests.get(DART_COMPANY_URL,
                                params={"crtfc_key": key, "corp_code": corp_code}, timeout=15).json()
            return data.get("induty_code") if data.get("status") == "000" else None
        except Exception:  # noqa: BLE001 — URL(키 포함) 출력 방지
            time.sleep(1)
    return None


def main() -> int:
    p = argparse.ArgumentParser(description="상장폐지 종목 메타데이터 적재")
    p.add_argument("--since", default="2022-01-01", help="폐지일 하한 (기본 2022-01-01)")
    p.add_argument("--dry-run", action="store_true", help="저장하지 않고 출력만")
    args = p.parse_args()

    rows = fetch_delisted(args.since)
    corp = fetch_corp_codes()
    prefix_map = load_prefix_map(get_engine())
    for r in rows:
        r["corp_code"] = corp.get(r["code"])
        r["induty_code"] = fetch_induty(r["corp_code"]) if r["corp_code"] else None
        r["sector"] = sector_from_induty(r["induty_code"], prefix_map)
        time.sleep(0.2)

    by_year: dict[str, dict[str, int]] = {}
    for r in sorted(rows, key=lambda x: x["delisting_date"]):
        by_year.setdefault(r["delisting_date"][:4], {}).setdefault(r["reason_category"], 0)
        by_year[r["delisting_date"][:4]][r["reason_category"]] += 1
        print(f"  {r['delisting_date']} {r['code']} {r['name']:<14} "
              f"[{r['reason_category']}] corp_code={'있음' if r['corp_code'] else '없음'} "
              f"업종={r['sector']}({r['induty_code']})  {r['reason'] or ''}")
    print(f"\n대상 {len(rows)}개 (폐지일 >= {args.since}), corp_code 매핑 "
          f"{sum(1 for r in rows if r['corp_code'])}/{len(rows)}, 업종 {sum(1 for r in rows if r['sector'])}/{len(rows)}")
    for y, cats in by_year.items():
        print(f"  {y}: {sum(cats.values())}개  {cats}")

    if args.dry_run:
        print("\n--dry-run: 저장하지 않음")
        return 0

    with get_engine().begin() as conn:
        conn.execute(text("""
            INSERT INTO delisted_stocks
                (code, name, market, corp_code, listing_date, delisting_date, reason, reason_category,
                 induty_code, sector)
            VALUES
                (:code, :name, :market, :corp_code, :listing_date, :delisting_date, :reason, :reason_category,
                 :induty_code, :sector)
            ON CONFLICT (code, delisting_date) DO UPDATE SET
                name = EXCLUDED.name, corp_code = EXCLUDED.corp_code,
                listing_date = EXCLUDED.listing_date, reason = EXCLUDED.reason,
                reason_category = EXCLUDED.reason_category,
                induty_code = EXCLUDED.induty_code, sector = EXCLUDED.sector, updated_at = now()
        """), rows)
        n = conn.execute(text("SELECT count(*) FROM delisted_stocks")).scalar()
    print(f"\n저장 완료. delisted_stocks 누적 {n}행")
    return 0


if __name__ == "__main__":
    sys.exit(main())
