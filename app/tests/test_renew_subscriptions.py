from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Subscription
from app.tasks import _expire_subscriptions

NOW = datetime(2026, 7, 6, 0, 0, 0)
PAST = NOW - timedelta(days=1)
FUTURE = NOW + timedelta(days=1)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _add(db, user_id, status, current_period_end):
    sub = Subscription(user_id=user_id, plan="monthly", status=status, current_period_end=current_period_end)
    db.add(sub)
    db.commit()
    return sub


def test_expires_active_past_due(db):
    sub = _add(db, 1, "active", PAST)
    count = _expire_subscriptions(db, now=NOW)
    assert count == 1
    assert sub.status == "expired"


def test_expires_canceled_past_due(db):
    """cancel()은 만료일까지 접근을 허용하므로 canceled도 기간 종료 시 expired로 정리되어야 함."""
    sub = _add(db, 1, "canceled", PAST)
    count = _expire_subscriptions(db, now=NOW)
    assert count == 1
    assert sub.status == "expired"


def test_does_not_expire_active_not_yet_due(db):
    sub = _add(db, 1, "active", FUTURE)
    count = _expire_subscriptions(db, now=NOW)
    assert count == 0
    assert sub.status == "active"


def test_ignores_inactive_and_already_expired(db):
    _add(db, 1, "inactive", PAST)
    _add(db, 2, "expired", PAST)
    count = _expire_subscriptions(db, now=NOW)
    assert count == 0


def test_idempotent_on_rerun(db):
    _add(db, 1, "active", PAST)
    first = _expire_subscriptions(db, now=NOW)
    second = _expire_subscriptions(db, now=NOW)
    assert first == 1
    assert second == 0
