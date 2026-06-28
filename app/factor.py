# app/factor.py
from sqlalchemy.orm import Session
from sqlalchemy import text

def calculate_factors(db: Session):
    """PBR, PER, ROE 팩터 계산 — 단일 JOIN 쿼리로 최적화"""

    query = text("""
        SELECT
            s.code,
            s.name,
            eq.amount  AS equity,
            pr.amount  AS profit
        FROM stocks s
        JOIN LATERAL (
            SELECT amount FROM financial_data
            WHERE code = s.code AND account_nm = '자본총계'
            ORDER BY
                CASE fs_type WHEN 'CFS' THEN 0 ELSE 1 END,
                year DESC
            LIMIT 1
        ) eq ON true
        JOIN LATERAL (
            SELECT amount FROM financial_data
            WHERE code = s.code AND account_nm LIKE '%당기순이익%'
            ORDER BY
                CASE fs_type WHEN 'CFS' THEN 0 ELSE 1 END,
                year DESC
            LIMIT 1
        ) pr ON true
        WHERE s.corp_code IS NOT NULL
    """)

    rows = db.execute(query).fetchall()

    return [
        {
            "code": row.code,
            "name": row.name,
            "equity": row.equity,
            "profit": row.profit,
        }
        for row in rows
    ]