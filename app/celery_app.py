from celery import Celery

celery_app = Celery(
    "quant_screener",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/0",
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
    },
)