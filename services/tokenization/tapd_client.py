from __future__ import annotations

import codecs
import logging
from typing import TYPE_CHECKING

import grpc

from .tapd_grpc import taprootassets as tap
from .tapd_grpc import taprootassetsgrpc as taprpc

if TYPE_CHECKING:
    from services.common.config import Settings

logger = logging.getLogger(__name__)

class TapdClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._stub: taprpc.TaprootAssetsStub | None = None
        self._channel: grpc.Channel | None = None

    def _get_stub(self) -> taprpc.TaprootAssetsStub:
        if self._stub:
            return self._stub

        try:
            # Read TLS certificate
            with open(self.settings.tapd_tls_cert_path, "rb") as f:
                cert = f.read()

            # Read Macaroon
            with open(self.settings.tapd_macaroon_path, "rb") as f:
                macaroon = codecs.encode(f.read(), "hex").decode()

            # Create credentials
            cert_creds = grpc.ssl_channel_credentials(cert)
            
            # Auth interceptor for macaroon
            auth_creds = grpc.metadata_call_credentials(
                multicall=lambda _, callback: callback([("macaroon", macaroon)], None)
            )
            
            combined_creds = grpc.composite_channel_credentials(cert_creds, auth_creds)

            # Create channel
            target = f"{self.settings.tapd_grpc_host}:{self.settings.tapd_grpc_port}"
            self._channel = grpc.secure_channel(target, combined_creds)
            self._stub = taprpc.TaprootAssetsStub(self._channel)
            
            return self._stub
        except FileNotFoundError as e:
            logger.error(f"Tapd credentials not found: {e}")
            raise RuntimeError(f"Tapd credentials not found at {e.filename}") from e
        except Exception as e:
            logger.error(f"Failed to initialize Tapd client: {e}")
            raise

    def get_info(self) -> tap.GetInfoResponse:
        stub = self._get_stub()
        return stub.GetInfo(tap.GetInfoRequest())

    def list_assets(self) -> tap.ListAssetResponse:
        stub = self._get_stub()
        return stub.ListAssets(tap.ListAssetRequest())

    def __del__(self):
        if self._channel:
            self._channel.close()
