import requests
import base64
import os
from datetime import datetime, timedelta

TOSS_SECRET_KEY = os.getenv("TOSS_SECRET_KEY")

def _auth_header():
    encoded = base64.b64encode(f"{TOSS_SECRET_KEY}:".encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Content-Type": "application/json",
    }

PLAN_PRICES = {
    "monthly": 9900,
    "yearly": 99000,
}

def confirm_payment(payment_key: str, order_id: str, amount: int):
    """결제창에서 결제 후 승인 요청"""
    res = requests.post(
        "https://api.tosspayments.com/v1/payments/confirm",
        headers=_auth_header(),
        json={
            "paymentKey": payment_key,
            "orderId": order_id,
            "amount": amount,
        },
    )
    return res.json(), res.status_code


def next_period_end(plan: str) -> datetime:
    if plan == "monthly":
        return datetime.utcnow() + timedelta(days=30)
    return datetime.utcnow() + timedelta(days=365)