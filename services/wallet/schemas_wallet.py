from __future__ import annotations

from uuid import UUID
from pydantic import BaseModel, Field

class TokenBalance(BaseModel):
    token_id: UUID
    asset_name: str
    symbol: str | None = None
    balance: int
    unit_price_sat: int

class WalletSummary(BaseModel):
    id: UUID
    onchain_balance_sat: int
    lightning_balance_sat: int
    token_balances: list[TokenBalance]
    total_value_sat: int

class WalletResponse(BaseModel):
    wallet: WalletSummary
