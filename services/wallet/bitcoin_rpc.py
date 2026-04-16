import base64
import json
import logging
from typing import Any

import httpx

from common.config import Settings

logger = logging.getLogger(__name__)

class BitcoinRPCError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code

class BitcoinRPCClient:
    """Async Bitcoin Core RPC Client."""

    def __init__(self, settings: Settings):
        self.url = f"http://{settings.bitcoin_rpc_host}:{settings.bitcoin_rpc_port}/"
        auth_string = f"{settings.bitcoin_rpc_user}:{settings.bitcoin_rpc_password or ''}"
        self.auth_header = "Basic " + base64.b64encode(auth_string.encode()).decode("utf-8")
        
        # Determine the wallet name if applicable. Often the default wallet is sufficient,
        # but if using multiple wallets we might need /wallet/name
        # Assuming single default wallet for now.

    async def _call(self, method: str, *params: Any) -> Any:
        headers = {"Authorization": self.auth_header, "Content-Type": "application/json"}
        payload = {
            "jsonrpc": "1.0",
            "id": "wallet_service",
            "method": method,
            "params": list(params),
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self.url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as e:
            try:
                err_data = e.response.json()
                error_msg = err_data.get("error", {}).get("message", "Unknown RPC error")
                error_code = err_data.get("error", {}).get("code")
            except Exception:
                error_msg = f"HTTP Error {e.response.status_code}: {e.response.text}"
                error_code = None
            logger.error("Bitcoin RPC error calling %s: %s", method, error_msg)
            raise BitcoinRPCError(error_msg, error_code) from e
        except Exception as e:
            logger.error("Failed to connect to Bitcoin RPC calling %s: %s", method, e)
            raise BitcoinRPCError(f"Connection error: {e}") from e

        if data.get("error") is not None:
            err = data["error"]
            raise BitcoinRPCError(err.get("message", "RPC Error"), err.get("code"))

        return data.get("result")

    async def importdescriptors(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return await self._call("importdescriptors", requests)

    async def listunspent(self, minconf: int = 1, maxconf: int = 9999999, addresses: list[str] | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [minconf, maxconf]
        if addresses is not None:
            params.append(addresses)
        return await self._call("listunspent", *params)

    async def listreceivedbyaddress(self, minconf: int = 1, include_empty: bool = False, include_watchonly: bool = True) -> list[dict[str, Any]]:
        return await self._call("listreceivedbyaddress", minconf, include_empty, include_watchonly)

    async def walletcreatefundedpsbt(
        self,
        inputs: list[dict[str, Any]],
        outputs: list[dict[str, Any]],
        options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return await self._call("walletcreatefundedpsbt", inputs, outputs, 0, options or {})

    async def walletprocesspsbt(self, psbt: str, sign: bool = True) -> dict[str, Any]:
        return await self._call("walletprocesspsbt", psbt, sign)

    async def finalizepsbt(self, psbt: str) -> dict[str, Any]:
        return await self._call("finalizepsbt", psbt)

    async def sendrawtransaction(self, hexstring: str) -> str:
        return await self._call("sendrawtransaction", hexstring)

    async def estimatesmartfee(self, conf_target: int) -> dict[str, Any]:
        return await self._call("estimatesmartfee", conf_target)

    async def getblockcount(self) -> int:
        return await self._call("getblockcount")

    async def decodepsbt(self, psbt: str) -> dict[str, Any]:
        return await self._call("decodepsbt", psbt)

def get_bitcoin_rpc(settings: Settings) -> BitcoinRPCClient:
    return BitcoinRPCClient(settings)
