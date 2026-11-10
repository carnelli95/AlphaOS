import os

from celery import Celery, chain
from celery.schedules import crontab

# Windows에서 WSL localhost 포워딩이 안 될 때 WSL IP로 덮어쓸 수 있게 환경변수 우선
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "quant_screener",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.tasks"],
)

# 일일 수집 순서: 시세 → 재무 → 파이프라인(Stage C~E + QA Report)
DAILY_COLLECTION_TASKS = (
    "app.tasks.update_stock_prices",
    "app.tasks.update_financial_data",
    "app.tasks.run_daily_pipeline",
)


def build_daily_collection():
    """일일 수집 chain. 앞 단계가 (재시도 소진 후) 실패하면 뒤 단계는 실행되지 않는다.

    app.tasks가 `from app.celery_app import celery_app`을 하므로 여기서 task 객체를
    import하면 순환 import가 된다 → 이름 기반 immutable 시그니처(.si()와 동등)로 구성.
    """
    return chain(*(celery_app.signature(name, immutable=True) for name in DAILY_COLLECTION_TASKS))


@celery_app.task(name="app.celery_app.daily_collection")
def daily_collection():
    """beat 트리거용. beat는 chain을 직접 보낼 수 없다(chain의 task 이름 'celery.chain'은
    Celery 5에서 NotImplementedError만 던지는 호환용 스텁) → 여기서 chain을 발행한다."""
    return build_daily_collection().apply_async().id


celery_app.conf.update(
    timezone="Asia/Seoul",
    enable_utc=True,
    beat_schedule={
        # 평일(월~금) 16:30 KST, 장 마감 후: 시세 → 재무 → 파이프라인 순차 실행
        "daily-collection": {
            "task": "app.celery_app.daily_collection",
            "schedule": crontab(hour=16, minute=30, day_of_week="mon-fri", app=celery_app),
        },
        # 매일 00:10 KST: 만료 구독 정리
        "renew-subscriptions": {
            "task": "app.tasks.renew_subscriptions",
            "schedule": crontab(hour=0, minute=10, app=celery_app),
        },
    },
)
