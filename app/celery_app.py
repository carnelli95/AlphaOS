import os

from celery import Celery

# Windows에서 WSL localhost 포워딩이 안 될 때 WSL IP로 덮어쓸 수 있게 환경변수 우선
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "quant_screener",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.tasks"],
)

celery_app.conf.update(
    timezone="Asia/Seoul",
    enable_utc=True,
    # 매일 자정에 데이터 업데이트
    beat_schedule={
        "update-financial-data-daily": {
            "task": "app.tasks.update_financial_data",
            "schedule": 60 * 60 * 24,  # 24시간마다
        },
        "update-stock-prices-daily": {
            "task": "app.tasks.update_stock_prices",
            "schedule": 60 * 60 * 24,  # 24시간마다
        },
        "renew-subscriptions-daily": {
            "task": "app.tasks.renew_subscriptions",
            "schedule": 60 * 60 * 24,  # 24시간마다
        },
        "run-daily-pipeline": {
            "task": "app.tasks.run_daily_pipeline",
            "schedule": 60 * 60 * 24,  # 24시간마다 — Stage C~E + QA Report
        },
    },
)