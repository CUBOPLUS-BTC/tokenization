from __future__ import annotations

import codecs
import logging
from typing import TYPE_CHECKING, Any

import grpc

from .lnd_grpc import lightning_pb2 as ln
from .lnd_grpc import lightning_pb2_grpc as lnrpc

if TYPE_CHECKING:
    from services.common.config import Settings

logger = logging.getLogger(__name__)

class LNDClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._stub: lnrpc.LightningStub | None = None
        self._channel: grpc.Channel | None = None

    def _get_stub(self) -> lnrpc.LightningStub:
        if self._stub:
            return self._stub

        try:
            # Read TLS certificate
            with open(self.settings.lnd_tls_cert_path, "rb") as f:
                cert = f.read()

            # Read Macaroon
            with open(self.settings.lnd_macaroon_path, "rb") as f:
                macaroon = codecs.encode(f.read(), "hex").decode()

            # Create credentials
            cert_creds = grpc.ssl_channel_credentials(cert)
            
            # Auth interceptor for macaroon
            auth_creds = grpc.metadata_call_credentials(
                multicall=lambda _, callback: callback([("macaroon", macaroon)], None)
            )
            
            combined_creds = grpc.composite_channel_credentials(cert_creds, auth_creds)

            # Create channel
            target = f"{self.settings.lnd_grpc_host}:{self.settings.lnd_grpc_port}"
            self._channel = grpc.secure_channel(target, combined_creds)
            self._stub = lnrpc.LightningStub(self._channel)
            
            return self._stub
        except FileNotFoundError as e:
            logger.error(f"LND credentials not found: {e}")
            raise RuntimeError(f"LND credentials not found at {e.filename}") from e
        except Exception as e:
            logger.error(f"Failed to initialize LND client: {e}")
            raise

    def create_invoice(self, memo: str, amount_sats: int) -> ln.AddInvoiceResponse:
        stub = self._get_stub()
        invoice = ln.Invoice(memo=memo, value=amount_sats)
        return stub.AddInvoice(invoice)

    def pay_invoice(self, payment_request: str) -> ln.SendResponse:
        stub = self._get_stub()
        req = ln.SendRequest(payment_request=payment_request)
        return stub.SendPaymentSync(req)

    def lookup_invoice(self, r_hash_str: str) -> ln.Invoice:
        """r_hash_str should be hex encoded payment hash"""
        stub = self._get_stub()
        req = ln.PaymentHash(r_hash_str=r_hash_str)
        return stub.LookupInvoice(req)

    def get_info(self) -> ln.GetInfoResponse:
        stub = self._get_stub()
        return stub.GetInfo(ln.GetInfoRequest())

    def decode_pay_req(self, payment_request: str) -> ln.PayReq:
        stub = self._get_stub()
        return stub.DecodePayReq(ln.PayReqString(pay_req=payment_request))

    def channel_balance(self) -> ln.ChannelBalanceResponse:
        stub = self._get_stub()
        return stub.ChannelBalance(ln.ChannelBalanceRequest())

    def __del__(self):
        if self._channel:
            self._channel.close()
