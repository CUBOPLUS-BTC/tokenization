from __future__ import annotations

import base64
import logging
from typing import Any
from urllib.parse import quote

import httpx

from .config import Settings


logger = logging.getLogger(__name__)


class ElementsRPCError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class ElementsRPCClient:
    """Async Elements / Liquid Core JSON-RPC client."""

    def __init__(
        self,
        settings: Settings,
        *,
        wallet_name: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        base_url = f"http://{settings.elements_rpc_host}:{settings.elements_rpc_port}"
        resolved_wallet = settings.elements_wallet_name if wallet_name is None else wallet_name
        if resolved_wallet:
            self._url = f"{base_url}/wallet/{quote(resolved_wallet, safe='')}"
        else:
            self._url = f"{base_url}/"
        auth_string = f"{settings.elements_rpc_user}:{settings.elements_rpc_password or ''}"
        self._auth_header = "Basic " + base64.b64encode(auth_string.encode("utf-8")).decode("ascii")
        self._timeout_seconds = timeout_seconds

    async def _call(self, method: str, *params: Any) -> Any:
        payload = {
            "jsonrpc": "1.0",
            "id": "liquid-platform",
            "method": method,
            "params": list(params),
        }
        headers = {
            "Authorization": self._auth_header,
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(self._url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            try:
                error_payload = exc.response.json().get("error") or {}
            except ValueError:
                error_payload = {}
            message = error_payload.get("message") or exc.response.text or "Elements RPC HTTP error"
            code = error_payload.get("code")
            logger.error("Elements RPC %s failed with HTTP error: %s", method, message)
            raise ElementsRPCError(message, code) from exc
        except Exception as exc:
            logger.error("Failed to connect to Elements RPC calling %s: %s", method, exc)
            raise ElementsRPCError(f"Connection error: {exc}") from exc

        rpc_error = data.get("error")
        if rpc_error is not None:
            raise ElementsRPCError(rpc_error.get("message", "RPC error"), rpc_error.get("code"))

        return data.get("result")

    async def getblockchaininfo(self) -> dict[str, Any]:
        return await self._call("getblockchaininfo")

    async def getsidechaininfo(self) -> dict[str, Any]:
        return await self._call("getsidechaininfo")

    async def getwalletinfo(self) -> dict[str, Any]:
        return await self._call("getwalletinfo")

    async def getbalances(self) -> dict[str, Any]:
        return await self._call("getbalances")

    async def estimatesmartfee(self, conf_target: int) -> dict[str, Any]:
        return await self._call("estimatesmartfee", conf_target)

    async def issueasset(
        self,
        asset_amount: int | float,
        token_amount: int | float = 0,
        blind: bool = True,
        contract_hash: str | None = None,
    ) -> dict[str, Any]:
        params: list[Any] = [asset_amount, token_amount, blind]
        if contract_hash is not None:
            params.append(contract_hash)
        return await self._call("issueasset", *params)

    async def listissuances(self, asset: str | None = None) -> list[dict[str, Any]]:
        if asset is None:
            return await self._call("listissuances")
        return await self._call("listissuances", asset)

    async def gettransaction(self, txid: str) -> dict[str, Any]:
        return await self._call("gettransaction", txid)

    async def getnewaddress(self, label: str = "", address_type: str = "bech32") -> str:
        return await self._call("getnewaddress", label, address_type)

    async def importaddress(
        self,
        address: str,
        label: str = "",
        rescan: bool = False,
        p2sh: bool = False,
    ) -> None:
        await self._call("importaddress", address, label, rescan, p2sh)

    async def importblindingkey(self, address: str, blinding_key: str) -> None:
        await self._call("importblindingkey", address, blinding_key)

    async def listunspent(
        self,
        minconf: int = 1,
        maxconf: int = 9_999_999,
        addresses: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        params: list[Any] = [minconf, maxconf]
        if addresses is not None:
            params.append(addresses)
        return await self._call("listunspent", *params)

    async def walletcreatefundedpsbt(
        self,
        inputs: list[dict[str, Any]],
        outputs: list[dict[str, Any]],
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._call("walletcreatefundedpsbt", inputs, outputs, 0, options or {})

    async def walletprocesspsbt(self, psbt: str, sign: bool = True) -> dict[str, Any]:
        return await self._call("walletprocesspsbt", psbt, sign)

    async def finalizepsbt(self, psbt: str) -> dict[str, Any]:
        return await self._call("finalizepsbt", psbt)

    async def sendrawtransaction(self, hexstring: str) -> str:
        return await self._call("sendrawtransaction", hexstring)

    async def sendtoaddress(
        self,
        address: str,
        amount_btc: float,
        *,
        asset_label: str | None = None,
        ignore_blind_fail: bool = False,
    ) -> str:
        params: list[Any] = [address, amount_btc, "", "", False, False, 1, "unset", False]
        if asset_label is not None:
            params.extend([asset_label, ignore_blind_fail])
        return await self._call("sendtoaddress", *params)

    async def scantxoutset(self, descriptors: list[str]) -> dict[str, Any]:
        return await self._call("scantxoutset", "start", descriptors)

    async def getpeginaddress(self) -> dict[str, Any]:
        return await self._call("getpeginaddress")

    async def claimpegin(
        self,
        raw_tx: str,
        proof: str,
        claim_script: str,
    ) -> dict[str, Any]:
        return await self._call("claimpegin", raw_tx, proof, claim_script)

    async def sendtomainchain(self, mainchain_address: str, amount_btc: float) -> str:
        return await self._call("sendtomainchain", mainchain_address, amount_btc)