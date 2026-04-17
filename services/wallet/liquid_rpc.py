from __future__ import annotations

from common.config import Settings
from common.elements_rpc import ElementsRPCClient, ElementsRPCError


def get_liquid_rpc(settings: Settings) -> ElementsRPCClient:
    return ElementsRPCClient(settings)


__all__ = ["ElementsRPCError", "get_liquid_rpc"]