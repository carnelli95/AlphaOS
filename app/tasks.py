from app.celery_app import celery_app
from app.database import SessionLocal
from sqlalchemy import text
import FinanceDataReader as fdr
import requests
import zipfile
import io
import xml.etree.ElementTree as ET
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

DART_API_KEY = os.getenv("DART_API_KEY")

@celery_app.task(bind=True, max_retries=3)
def update_stock_prices(self):
    """FinanceDataReader로 코스피 시가총액 최신화 (매일)"""
    try:
        db = SessionLocal()
        df = fdr.StockListing('KOSPI')

        updated = 0
        for _, row in df.iterrows():
            code = row.get('Code')
            if not code:
                continue
            db.execute(
                text("""
                    UPDATE stocks
                    SET marcap = :marcap, price = :price
                    WHERE code = :code
                """),
                {
                    "marcap": int(row['Marcap']) if row['Marcap'] else None,
                    "price":  int(row['Close'])  if row['Close']  else None,
                    "code":   code,
                }
            )
            updated += 1

        db.commit()
        db.close()
        return f"시세 업데이트 완료: {updated}개 종목"

    except Exception as exc:
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(bind=True, max_retries=3)
def update_financial_data(self):
    """DART API로 전 종목 재무데이터 업데이트 (매일)"""
    try:
        db = SessionLocal()
        current_year = datetime.now().year
        target_year  = current_year - 1  # 전년도 확정 재무제표

        # 종목 목록 가져오기
        stocks = db.execute(
            text("SELECT code, corp_code FROM stocks WHERE corp_code IS NOT NULL")
        ).fetchall()

        updated = 0
        for stock in stocks:
            _fetch_and_save(db, stock.code, stock.corp_code, target_year)
            updated += 1

        db.commit()
        db.close()
        return f"재무데이터 업데이트 완료: {updated}개 종목"

    except Exception as exc:
        raise self.retry(exc=exc, countdown=300)


def _fetch_and_save(db, code, corp_code, year):
    """단일 종목 DART 재무데이터 저장"""
    url = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
    params = {
        "crtfc_key": DART_API_KEY,
        "corp_code":  corp_code,
        "bsns_year":  str(year),
        "reprt_code": "11011",  # 사업보고서
        "fs_div":     "CFS",    # 연결재무제표
    }

    res = requests.get(url, params=params, timeout=10)
    data = res.json()

    if data.get("status") != "000":
        return

    TARGET_ACCOUNTS = {
        '매출액', '영업이익', '당기순이익(손실)',
        '자본총계', '부채총계', '자산총계',
        '자본금', '이익잉여금', '유동자산',
        '비유동자산', '유동부채', '비유동부채',
        '법인세차감전 순이익', '총포괄손익',
    }

    for item in data.get("list", []):
        account_nm = item.get("account_nm", "").strip()
        fs_type = item.get("fs_div", "OFS")
        if account_nm not in TARGET_ACCOUNTS:
            continue
        if fs_type not in ("CFS", "OFS"):
            continue

        amount_str = item.get("thstrm_amount", "").replace(",", "").strip()
        if not amount_str:
            continue

        try:
            amount = int(amount_str)
        except ValueError:
            continue

        db.execute(
            text("""
                INSERT INTO financial_data (code, year, fs_type, account_nm, amount)
                VALUES (:code, :year, :fs_type, :account_nm, :amount)
                ON CONFLICT (code, year, fs_type, account_nm)
                DO UPDATE SET amount = EXCLUDED.amount
            """),
            {
                "code":       code,
                "year":       year,
                "fs_type":    fs_type,
                "account_nm": account_nm,
                "amount":     amount,
            }
        )
