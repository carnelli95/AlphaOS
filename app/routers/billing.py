from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, Subscription, Payment
from app.schemas import PaymentConfirmRequest
from app.auth import get_current_user
from app.payment import confirm_payment, next_period_end, PLAN_PRICES

router = APIRouter(prefix="/api/billing", tags=["billing"])


@router.post("/confirm")
def confirm(
    req: PaymentConfirmRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if req.plan not in PLAN_PRICES:
        raise HTTPException(400, "유효하지 않은 플랜입니다")

    expected_amount = PLAN_PRICES[req.plan]
    if req.amount != expected_amount:
        raise HTTPException(400, "결제 금액이 일치하지 않습니다")

    result, status_code = confirm_payment(req.payment_key, req.order_id, req.amount)

    payment = Payment(
        user_id=user.id,
        order_id=req.order_id,
        amount=req.amount,
        plan=req.plan,
        status="success" if status_code == 200 else "failed",
        payment_key=req.payment_key,
    )
    db.add(payment)

    if status_code != 200:
        db.commit()
        raise HTTPException(400, result.get("message", "결제 승인 실패"))

    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    if not sub:
        sub = Subscription(user_id=user.id)
        db.add(sub)

    sub.plan = req.plan
    sub.status = "active"
    sub.current_period_end = next_period_end(req.plan)

    db.commit()
    return {"message": "구독이 시작되었습니다", "status": "active"}


@router.get("/me")
def my_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    if not sub:
        return {"plan": None, "status": "inactive", "current_period_end": None}

    return {
        "plan": sub.plan,
        "status": sub.status,
        "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
    }


@router.post("/cancel")
def cancel_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    if not sub or sub.status != "active":
        raise HTTPException(400, "활성화된 구독이 없습니다")

    sub.status = "canceled"
    db.commit()
    return {"message": "구독이 취소되었습니다. 다음 결제일까지는 이용 가능합니다"}