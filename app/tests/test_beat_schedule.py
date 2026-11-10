import copy
from datetime import datetime, timedelta
from numbers import Number
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from celery.canvas import _chain
from celery.schedules import crontab

from app.celery_app import (
    DAILY_COLLECTION_TASKS,
    build_daily_collection,
    celery_app,
    daily_collection,
)

KST = ZoneInfo("Asia/Seoul")
SCHEDULE = celery_app.conf.beat_schedule


def _next_run(schedule: crontab, now: datetime) -> datetime:
    # remaining_estimate는 schedule.now()(실제 시계) 기준이라 복사본의 시계를 now로 고정
    fixed = copy.copy(schedule)
    fixed.nowfun = lambda: now
    return (now + fixed.remaining_estimate(now)).astimezone(KST)


def test_all_entries_are_crontab():
    assert set(SCHEDULE) == {"daily-collection", "renew-subscriptions"}
    for name, entry in SCHEDULE.items():
        sched = entry["schedule"]
        assert isinstance(sched, crontab), name
        assert not isinstance(sched, (Number, timedelta)), name


@pytest.mark.parametrize(
    "now, expected",
    [
        (datetime(2026, 11, 10, 12, 0, tzinfo=KST), datetime(2026, 11, 10, 16, 30, tzinfo=KST)),  # 화 → 같은 날
        (datetime(2026, 11, 10, 17, 0, tzinfo=KST), datetime(2026, 11, 11, 16, 30, tzinfo=KST)),  # 화 → 수
        (datetime(2026, 11, 13, 17, 0, tzinfo=KST), datetime(2026, 11, 16, 16, 30, tzinfo=KST)),  # 금 → 월
    ],
)
def test_daily_collection_next_run(now, expected):
    assert now.strftime("%a") in {"Tue", "Fri"}
    assert _next_run(SCHEDULE["daily-collection"]["schedule"], now) == expected


def test_renew_subscriptions_next_run():
    now = datetime(2026, 11, 14, 12, 0, tzinfo=KST)  # 토요일도 실행
    expected = datetime(2026, 11, 15, 0, 10, tzinfo=KST)
    assert _next_run(SCHEDULE["renew-subscriptions"]["schedule"], now) == expected


def test_daily_collection_is_ordered_immutable_chain():
    assert SCHEDULE["daily-collection"]["task"] == daily_collection.name
    assert daily_collection.name in celery_app.tasks

    c = build_daily_collection()
    assert isinstance(c, _chain)
    assert [t.task for t in c.tasks] == [
        "app.tasks.update_stock_prices",
        "app.tasks.update_financial_data",
        "app.tasks.run_daily_pipeline",
    ]
    assert all(t.immutable for t in c.tasks)


def test_task_names_are_registered():
    import app.tasks  # noqa: F401  (등록만 확인, 실행하지 않음)

    for name in (*DAILY_COLLECTION_TASKS, SCHEDULE["renew-subscriptions"]["task"]):
        assert name in celery_app.tasks, name


def test_dispatcher_sends_chain_without_running():
    with patch.object(_chain, "apply_async") as mocked:
        mocked.return_value.id = "fake-id"
        assert daily_collection.run() == "fake-id"
    mocked.assert_called_once_with()
