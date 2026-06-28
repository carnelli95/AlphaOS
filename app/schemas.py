from pydantic import BaseModel, EmailStr

class RegisterRequest(BaseModel):
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserResponse(BaseModel):
    id: int
    email: str
    is_active: bool

    class Config:
        from_attributes = True

class BillingRegisterRequest(BaseModel):
    auth_key: str
    plan: str  # 'monthly' or 'yearly'

class SubscriptionResponse(BaseModel):
    plan: str
    status: str
    current_period_end: str | None = None

class PaymentConfirmRequest(BaseModel):
    payment_key: str
    order_id: str
    amount: int
    plan: str