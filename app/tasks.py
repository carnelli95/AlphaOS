from app.celery_app import celery_app
from app.database import SessionLocal
from app.models import Subscription
from sqlalchemy import text
import FinanceDataReader as fdr
import requests
import zipfile
import io
import pathlib
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

DART_API_KEY = os.getenv("DART_API_KEY")

PIPELINE_DIR = str(pathlib.Path(__file__).resolve().parent.parent / "pipeline")


@celery_app.task(bind=True)
def run_daily_pipeline(self):
    """
    일일 파이프라인: Stage C → Stage D(DART) → Stage E(Thesis) → QA Report.
    Stage D(뉴스)는 비활성 — 호출하지 않는다(Naver·OpenAI 비용, DECISIONS.md 2026-11-15).

    각 단계는 이전 단계가 예외 없이 끝난 경우에만 실행한다. 종목 단위 실패는 정상
    범위(각 run()이 반환하는 fail 카운트로 집계될 뿐 체인을 막지 않음) — 여기서
    말하는 "실패"는 Stage 자체가 통째로 죽는 경우(DB 연결 끊김, API 키 누락, 모듈
    import 실패 등)다. 그 경우 이후 단계는 건너뛰고, 실패한 Stage 자리에 fail=1짜리
    결과를 남겨 QA Report의 기존 stage_failure 감지 로직이 그대로 잡게 한다.

    각 Stage 모듈의 import 자체도 실패 격리 대상에 포함한다(모듈 내부의
    import-time 부작용이 언제든 있을 수 있음 — 예: sys.stdout 관련 코드가
    Celery의 LoggingProxy 환경에서 예상과 다르게 동작하는 경우).

    QA Report는 실패 여부와 무관하게 항상 마지막에 생성한다 — 원인 추적용.

    자동 재시도(self.retry) 없음: LLM/DART/Naver API 호출 비용이 크고, 원인 파악
    전에 실패한 배치를 반복 실행하면 비용·API 쿼터를 낭비할 위험이 있어 실패 시
    사람이 로그(qa_reports/stage_runs/, QA Report issues[])를 보고 수동 재실행한다.
    """
    import importlib

    if PIPELINE_DIR not in sys.path:
        sys.path.insert(0, PIPELINE_DIR)

    import generate_qa_report
    from stage_result import now_kst, write_stage_result

    stage_error: str | None = None

    def _run(label: str, module_name: str, **kwargs):
        nonlocal stage_error
        if stage_error:
            return None
        t0 = now_kst()
        try:
            module = importlib.import_module(module_name)
            return module.run(**kwargs)
        except Exception as exc:  # noqa: BLE001
            stage_error = f"{label}: {exc}"
            finished = now_kst()
            write_stage_result(label, {
                "stage": label,
                "run_mode": "incremental",
                "trigger": "pipeline_error",
                "started_at": t0.isoformat(),
                "finished_at": finished.isoformat(),
                "duration_sec": round((finished - t0).total_seconds(), 1),
                "target_count": 0, "success": 0, "skip": 0, "fail": 1,
                "error": str(exc),
            })
            return None

    _run("stage_c", "batch_generate_stage_c", sleep_seconds=0.5, limit=None)
    _run("stage_d_dart", "batch_generate_stage_d")
    # Stage D 뉴스는 비활성 (DECISIONS.md 2026-11-15, CLAUDE.md "Stage D 비활성").
    # 코드는 그대로 두고 호출만 뺀다. 다시 켤 때 아래 줄을 복구한다:
    #   _run("stage_d_news", "batch_generate_stage_d_news", limit=150)
    _run("stage_e", "batch_generate_thesis", sleep_seconds=0.5, limit=None)

    report = generate_qa_report.generate()

    if stage_error:
        return f"파이프라인 중단 ({stage_error}) — QA Report는 생성됨: 주의 필요 {report['summary']['issue_count']}건"
    return f"파이프라인 완료 — 주의 필요 {report['summary']['issue_count']}건"

def _expire_subscriptions(db, now: datetime | None = None) -> int:
    """current_period_end이 지난 active/canceled 구독을 expired로 전환.

    자동 재결제(빌링키 청구)는 하지 않음 — 만료된 구독을 상태만 정리한다.
    이미 expired인 행은 필터에 안 걸리므로 재실행해도 중복 처리되지 않는다.
    """
    now = now or datetime.utcnow()
    targets = (
        db.query(Subscription)
        .filter(
            Subscription.status.in_(["active", "canceled"]),
            Subscription.current_period_end < now,
        )
        .all()
    )
    for sub in targets:
        sub.status = "expired"
    db.commit()
    return len(targets)


@celery_app.task(bind=True, max_retries=3)
def renew_subscriptions(self):
    """만료된 구독을 expired 상태로 정리 (자동 재결제 없음, 매일)"""
    try:
        db = SessionLocal()
        count = _expire_subscriptions(db)
        db.close()
        return f"구독 만료 처리 완료: {count}건"

    except Exception as exc:
        raise self.retry(exc=exc, countdown=300)


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
                ON CONFLICT ON CONSTRAINT uq_financial_data_fs
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
