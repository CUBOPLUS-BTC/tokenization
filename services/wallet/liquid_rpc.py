from __future__ import annotations

from services.common.config import Settings
from services.common.elements_rpc import ElementsRPCClient, ElementsRPCError


def get_liquid_rpc(settings: Settings) -> ElementsRPCClient:
    return ElementsRPCClient(settings)


__all__ = ["ElementsRPCError", "get_liquid_rpc"]