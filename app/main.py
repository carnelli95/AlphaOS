from fastapi import FastAPI, Depends, Query, HTTPException
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from app.database import engine, Base, get_db
from app.factor import calculate_factors
import app.models
import FinanceDataReader as fdr
import pandas as pd
import json
from typing import Optional
from app.routers import auth as auth_router
from app.routers import billing as billing_router

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Quant Screener API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router.router)
app.include_router(billing_router.router)

@app.get("/")
def root():
    return {"message": "Quant Screener API 작동 중"}

@app.get("/api/screener")
def screener(
    db: Session = Depends(get_db),
    # PBR 필터
    pbr_min: Optional[float] = Query(None, description="PBR 최솟값"),
    pbr_max: Optional[float] = Query(None, description="PBR 최댓값"),
    # ROE 필터
    roe_min: Optional[float] = Query(None, description="ROE 최솟값 (%)"),
    roe_max: Optional[float] = Query(None, description="ROE 최댓값 (%)"),
    # PER 필터
    per_min: Optional[float] = Query(None, description="PER 최솟값"),
    per_max: Optional[float] = Query(None, description="PER 최댓값"),
    # 결과 개수
    limit: int = Query(50, ge=1, le=500, description="반환할 종목 수"),
):
    df_kospi = fdr.StockListing('KOSPI')
    marcap_dict = dict(zip(df_kospi['Code'], df_kospi['Marcap']))

    results = calculate_factors(db)

    output = []
    for r in results:
        marcap = marcap_dict.get(r['code'])
        if not marcap or marcap == 0:
            continue
        equity = r['equity']
        profit = r['profit']
        if not equity or equity <= 0:
            continue

        pbr = round(marcap / equity, 2)
        roe = round(profit / equity * 100, 2)
        per = round(marcap / profit, 2) if profit and profit > 0 else None

        if roe <= 0:
            continue

        # ── 필터 적용 ──────────────────────────────
        if pbr_min is not None and pbr < pbr_min:
            continue
        if pbr_max is not None and pbr > pbr_max:
            continue
        if roe_min is not None and roe < roe_min:
            continue
        if roe_max is not None and roe > roe_max:
            continue
        if per is not None:
            if per_min is not None and per < per_min:
                continue
            if per_max is not None and per > per_max:
                continue
        # ───────────────────────────────────────────

        output.append({
            "code": r['code'],
            "name": r['name'],
            "pbr": pbr,
            "per": per,
            "roe": roe,
        })

    if not output:
        return []

    df = pd.DataFrame(output)

    # ROE 이상치 클리핑 — 자본총계가 매우 작은 종목은 ROE가 비정상적으로 치솟아
    # score(순위) 계산을 왜곡시킬 수 있음. 화면에 보여주는 roe 값(원본)은 그대로 두고,
    # 순위 계산에만 쓰는 별도 컬럼(roe_capped)으로 영향력만 제한.
    df['roe_capped'] = df['roe'].clip(upper=50)

    df['pbr_rank'] = df['pbr'].rank(ascending=True)
    df['roe_rank'] = df['roe_capped'].rank(ascending=False)
    df['score'] = df['pbr_rank'] * 0.5 + df['roe_rank'] * 0.5
    df = df.sort_values('score').head(limit)

    return df[['code', 'name', 'pbr', 'per', 'roe']].to_dict(orient='records')

# app/main.py 하단에 추가 (import는 기존 것 그대로)

@app.get("/api/stock/{code}")
def stock_detail(code: str, db: Session = Depends(get_db)):
    # 종목 기본 정보
    stock_query = text("SELECT code, name FROM stocks WHERE code = :code")
    stock = db.execute(stock_query, {"code": code}).fetchone()
    if not stock:
        raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다")

    # 연도별 재무 데이터 — CFS 우선, 없으면 OFS
    fin_query = text("""
        SELECT year, account_nm, amount
        FROM financial_data
        WHERE code = :code
          AND fs_type = (
            SELECT CASE WHEN EXISTS (
                SELECT 1 FROM financial_data WHERE code = :code AND fs_type = 'CFS' LIMIT 1
            ) THEN 'CFS' ELSE 'OFS' END
          )
        ORDER BY year ASC, account_nm
    """)
    rows = db.execute(fin_query, {"code": code}).fetchall()

    # 연도별로 그룹핑
    from collections import defaultdict
    by_year = defaultdict(dict)
    for row in rows:
        by_year[row.year][row.account_nm] = row.amount

    # 현재 시세 / 시가총액
    df_kospi = fdr.StockListing('KOSPI')
    marcap_row = df_kospi[df_kospi['Code'] == code]
    marcap = int(marcap_row['Marcap'].values[0]) if not marcap_row.empty else None
    price  = int(marcap_row['Close'].values[0])  if not marcap_row.empty else None

    # 최신 연도 기준 PBR / ROE / PER 계산
    latest_year = max(by_year.keys()) if by_year else None
    ratios = {}
    if latest_year and marcap:
        equity = by_year[latest_year].get('자본총계')
        profit = by_year[latest_year].get('당기순이익(손실)')
        if equity and equity > 0:
            ratios['pbr'] = round(marcap / equity, 2)
            ratios['roe'] = round(profit / equity * 100, 2) if profit else None
        if profit and profit > 0:
            ratios['per'] = round(marcap / profit, 2)

    # 차트용 시계열 데이터
    years = sorted(by_year.keys())
    def series(account):
        return [by_year[y].get(account) for y in years]

    chart_data = {
        "years": years,
        "revenue":       series('매출액'),
        "operating":     series('영업이익'),
        "net_income":    series('당기순이익(손실)'),
        "assets":        series('자산총계'),
        "liabilities":   series('부채총계'),
        "equity":        series('자본총계'),
    }

    return {
        "code":       stock.code,
        "name":       stock.name,
        "price":      price,
        "marcap":     marcap,
        "ratios":     ratios,
        "chart_data": chart_data,
        "latest_year": latest_year,
    }


@app.get("/api/search")
def search_stocks(q: str = Query("", min_length=1), db: Session = Depends(get_db)):
    rows = db.execute(
        text("SELECT code, name FROM stocks WHERE name LIKE :q LIMIT 20"),
        {"q": f"%{q}%"},
    ).fetchall()
    return [{"code": r.code, "name": r.name} for r in rows]


SIGNAL_COLOR = {
    "turnaround":           "green",
    "net_income_growth":    "green",
    "low_pbr_high_roe":     "green",
    "margin_improving":     "green",
    "profitability_strong": "green",
    "high_per_with_growth": "yellow",
    "debt_stable":          "green",
    "deteriorating":        "red",
    "earnings_declining":   "red",
    "high_per_without_growth": "red",
    "low_roe_high_pbr":     "red",
    "margin_declining":     "red",
    "debt_high":            "red",
}


@app.get("/api/ranking")
def alpha_ranking(
    db: Session = Depends(get_db),
    sector: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    params: dict = {"limit": limit}
    sector_filter = "AND s.sector = :sector" if sector else ""
    if sector:
        params["sector"] = sector

    rows = db.execute(text(f"""
        SELECT s.code, s.name, s.sector,
               (r.alpha_score->>'score')::int        AS score,
               (r.alpha_score->>'rank_global')::int  AS rank_global,
               (r.alpha_score->>'total_global')::int AS total_global,
               (r.alpha_score->>'rank_sector')::int  AS rank_sector,
               (r.alpha_score->>'total_sector')::int AS total_sector,
               r.alpha_score->'breakdown'            AS breakdown
        FROM ai_reports r
        JOIN stocks s ON s.code = r.stock_code
        WHERE r.alpha_score IS NOT NULL {sector_filter}
        ORDER BY (r.alpha_score->>'score')::int DESC
        LIMIT :limit
    """), params).fetchall()

    sector_rows = db.execute(text("""
        SELECT DISTINCT s.sector
        FROM ai_reports r
        JOIN stocks s ON s.code = r.stock_code
        WHERE r.alpha_score IS NOT NULL AND s.sector IS NOT NULL
        ORDER BY s.sector
    """)).fetchall()

    return {
        "stocks": [
            {
                "code":         r.code,
                "name":         r.name,
                "sector":       r.sector,
                "score":        r.score,
                "rank_global":  r.rank_global,
                "total_global": r.total_global,
                "rank_sector":  r.rank_sector,
                "total_sector": r.total_sector,
                "breakdown":    r.breakdown or {},
            }
            for r in rows
        ],
        "sectors": [r[0] for r in sector_rows],
    }


@app.get("/api/stock/{code}/report")
def stock_report(code: str, db: Session = Depends(get_db)):
    row = db.execute(
        text(
            "SELECT overview, valuation_opinion, metrics_snapshot, "
            "valuation_sections, stage_c_sections, stage_b_sections, alpha_score, "
            "thesis_sections "
            "FROM ai_reports WHERE stock_code = :code"
        ),
        {"code": code},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="리포트 없음")

    snapshot = row.metrics_snapshot if row.metrics_snapshot else {}
    signals = [
        {
            "signal": s["signal"],
            "text":   s["text"],
            "color":  SIGNAL_COLOR.get(s["signal"], "gray"),
        }
        for s in snapshot.get("signals", [])
    ]

    return {
        "overview":           row.overview,
        "valuation_opinion":  row.valuation_opinion,
        "valuation_sections": row.valuation_sections or {},
        "signals":            signals,
        "stage_c_sections":   row.stage_c_sections or {},
        "stage_b_sections":   row.stage_b_sections or {},
        "alpha_score":        row.alpha_score or {},
        "thesis_sections":    row.thesis_sections or {},
    }
