from __future__ import annotations

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel

class TokenBalance(BaseModel):
    token_id: UUID
    liquid_asset_id: str
    asset_name: str
    symbol: str | None = None
    balance: int
    unit_price_sat: int
    accrued_yield_sat: int = 0


class YieldTokenSummary(BaseModel):
    token_id: UUID
    asset_name: str
    total_yield_sat: int


class YieldAccrualOut(BaseModel):
    id: UUID
    token_id: UUID
    asset_name: str
    amount_sat: int
    quantity_held: int
    reference_price_sat: int
    annual_rate_pct: float
    accrued_from: datetime
    accrued_to: datetime
    created_at: datetime

class WalletSummary(BaseModel):
    id: UUID
    onchain_balance_sat: int
    lightning_balance_sat: int
    token_balances: list[TokenBalance]
    total_yield_earned_sat: int
    total_value_sat: int

class WalletResponse(BaseModel):
    wallet: WalletSummary


class YieldSummary(BaseModel):
    total_yield_earned_sat: int
    by_token: list[YieldTokenSummary]
    accruals: list[YieldAccrualOut]


class YieldSummaryResponse(BaseModel):
    yield_summary: YieldSummary
