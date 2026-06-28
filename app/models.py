from sqlalchemy import Column, Integer, String, Boolean, DateTime, BigInteger, Date
from sqlalchemy.sql import func
from app.database import Base
from sqlalchemy import ForeignKey

class Stock(Base):
    __tablename__ = "stocks"
    code        = Column(String, primary_key=True)
    name        = Column(String)
    market      = Column(String)
    corp_code   = Column(String)
    price       = Column(BigInteger)
    marcap      = Column(BigInteger)
    induty_code = Column(String(10))
    sector      = Column(String(50))

class FinancialData(Base):
    __tablename__ = "financial_data"
    id         = Column(Integer, primary_key=True)
    code       = Column(String)
    year       = Column(Integer)
    fs_type    = Column(String(3), nullable=False, default="CFS")
    account_nm = Column(String)
    amount     = Column(BigInteger)

class User(Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True)
    email         = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    is_active     = Column(Boolean, default=True)
    created_at    = Column(DateTime, server_default=func.now())

class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    plan = Column(String(20))
    status = Column(String(20), default="inactive")
    billing_key = Column(String(255))
    customer_key = Column(String(255))
    current_period_end = Column(DateTime)
    created_at = Column(DateTime, server_default=func.now())

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    order_id = Column(String(100), unique=True)
    amount = Column(Integer)
    status = Column(String(20))
    payment_key = Column(String(255))
    plan = Column(String(20))
    created_at = Column(DateTime, server_default=func.now())

class PriceHistory(Base):
    __tablename__ = "price_history"
    code   = Column(String,    primary_key=True)
    date   = Column(Date,      primary_key=True)
    open   = Column(BigInteger)
    high   = Column(BigInteger)
    low    = Column(BigInteger)
    close  = Column(BigInteger, nullable=False)
    volume = Column(BigInteger)