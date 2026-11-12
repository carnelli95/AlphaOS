import FinanceDataReader as fdr
import requests
import os
from dotenv import load_dotenv
from sqlalchemy.orm import Session
from app.models import Stock, FinancialData

load_dotenv()
DART_API_KEY = os.getenv("DART_API_KEY")

def save_stock_list(db: Session):
    """코스피 종목 리스트 DB 저장"""
    df = fdr.StockListing('KOSPI')
    
    for _, row in df.iterrows():
        stock = Stock(
            code=row['Code'],
            name=row['Name'],
            market='KOSPI'
        )
        db.merge(stock)
    
    db.commit()
    print(f"종목 {len(df)}개 저장 완료")

def save_financial_data(db: Session, corp_code: str, stock_code: str, year: str):
    """DART에서 재무데이터 가져와서 DB 저장"""
    url = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
    params = {
        "crtfc_key": DART_API_KEY,
        "corp_code": corp_code,
        "bsns_year": year,
        "reprt_code": "11011"
    }
    
    response = requests.get(url, params=params)
    data = response.json()
    
    if data['status'] != '000':
        print(f"{stock_code} 데이터 없음")
        return

    from sqlalchemy import text as sa_text

    for item in data['list']:
        fs_type = item.get('fs_div', 'OFS')
        if fs_type not in ('CFS', 'OFS'):
            continue
        amount_str = (item.get('thstrm_amount') or '').replace(',', '').strip()
        if not amount_str or amount_str == '-':
            continue
        try:
            amount = int(amount_str)
        except ValueError:
            continue
        db.execute(
            sa_text("""
                INSERT INTO financial_data (code, year, fs_type, account_nm, amount)
                VALUES (:code, :year, :fs_type, :account_nm, :amount)
                ON CONFLICT ON CONSTRAINT uq_financial_data_fs
                DO UPDATE SET amount = EXCLUDED.amount
            """),
            {"code": stock_code, "year": year, "fs_type": fs_type,
             "account_nm": item['account_nm'], "amount": amount},
        )

    db.commit()
    print(f"{stock_code} 재무데이터 저장 완료")