from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env_profile: Literal["local", "staging", "beta", "production"] = "local"

    service_name: str
    service_host: str = "0.0.0.0"
    service_port: int

    wallet_service_url: str
    tokenization_service_url: str
    marketplace_service_url: str
    education_service_url: str
    nostr_service_url: str

    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str | None = None
    postgres_password_file: str | None = None
    database_url: str

    redis_url: str

    bitcoin_rpc_host: str
    bitcoin_rpc_port: int
    bitcoin_rpc_user: str
    bitcoin_rpc_password: str | None = None
    bitcoin_rpc_password_file: str | None = None
    bitcoin_network: str
    bitcoin_rpc_required: bool = True

    elements_rpc_host: str = "localhost"
    elements_rpc_port: int = 7041
    elements_rpc_user: str = "user"
    elements_rpc_password: str | None = None
    elements_rpc_password_file: str | None = None
    elements_network: str = "elementsregtest"
    elements_wallet_name: str = ""
    elements_rpc_required: bool | None = None

    lnd_grpc_host: str
    lnd_grpc_port: int
    lnd_macaroon_path: str
    lnd_tls_cert_path: str
    lnd_grpc_required: bool | None = None

    nostr_relays: str
    nostr_private_key: str | None = None
    nostr_private_key_file: str | None = None

    jwt_secret: str | None = None
    jwt_secret_file: str | None = None
    jwt_access_token_expire_minutes: int
    jwt_refresh_token_expire_days: int
    totp_issuer: str

    openai_api_key: str | None = None
    openai_api_key_file: str | None = None
    custody_backend: Literal["software", "hsm"] = "software"
    wallet_encryption_key: str | None = None
    wallet_encryption_key_file: str | None = None
    custody_hsm_key_label: str | None = None
    custody_hsm_wrapping_key: str | None = None
    custody_hsm_wrapping_key_file: str | None = None
    custody_hsm_signing_key: str | None = None
    custody_hsm_signing_key_file: str | None = None
    alert_webhook_url: str | None = None
    alert_webhook_url_file: str | None = None

    log_level: str
    rate_limit_window_seconds: int = 60
    rate_limit_write_requests: int = 60
    rate_limit_sensitive_requests: int = 10

    # KYC: trade value threshold (sats) above which KYC verification is required.
    # Set to 0 to disable enforcement.  Default 10 000 000 sats (~0.1 BTC).
    kyc_trade_threshold_sat: int = 10_000_000
    marketplace_escrow_watch_interval_seconds: int = 30
    marketplace_escrow_fee_reserve_sat: int = 5_000

    @property
    def nostr_relay_list(self) -> list[str]:
        return [relay.strip() for relay in self.nostr_relays.split(",") if relay.strip()]

    @property
    def resolved_elements_rpc_required(self) -> bool:
        if self.elements_rpc_required is not None:
            return self.elements_rpc_required
        return self.env_profile != "local"

    @property
    def resolved_lnd_grpc_required(self) -> bool:
        if self.lnd_grpc_required is not None:
            return self.lnd_grpc_required
        return self.env_profile != "local"

    @staticmethod
    def _resolve_secret(secret_value: str | None, file_path: str | None) -> str | None:
        if file_path:
            secret_path = Path(file_path)
            if not secret_path.is_absolute():
                secret_path = (_repo_root() / file_path).resolve()
            if not secret_path.exists():
                raise ValueError(f"Secret file does not exist: {secret_path}")
            return secret_path.read_text(encoding="utf-8").strip()
        return secret_value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper_value = value.upper()
        if upper_value not in allowed:
            raise ValueError(f"log_level must be one of: {', '.join(sorted(allowed))}")
        return upper_value

    @model_validator(mode="after")
    def _hydrate_secrets_and_validate(self) -> Settings:
        self.postgres_password = self._resolve_secret(self.postgres_password, self.postgres_password_file)
        self.bitcoin_rpc_password = self._resolve_secret(self.bitcoin_rpc_password, self.bitcoin_rpc_password_file)
        self.elements_rpc_password = self._resolve_secret(self.elements_rpc_password, self.elements_rpc_password_file)
        self.jwt_secret = self._resolve_secret(self.jwt_secret, self.jwt_secret_file)
        self.openai_api_key = self._resolve_secret(self.openai_api_key, self.openai_api_key_file)
        self.wallet_encryption_key = self._resolve_secret(self.wallet_encryption_key, self.wallet_encryption_key_file)
        self.custody_hsm_wrapping_key = self._resolve_secret(
            self.custody_hsm_wrapping_key,
            self.custody_hsm_wrapping_key_file,
        )
        self.custody_hsm_signing_key = self._resolve_secret(
            self.custody_hsm_signing_key,
            self.custody_hsm_signing_key_file,
        )
        self.nostr_private_key = self._resolve_secret(self.nostr_private_key, self.nostr_private_key_file)
        self.alert_webhook_url = self._resolve_secret(self.alert_webhook_url, self.alert_webhook_url_file)

        if self.env_profile in {"staging", "beta", "production"}:
            if not self.jwt_secret:
                raise ValueError("JWT secret is required for staging/beta/production")
            if self.custody_backend == "software":
                if not self.wallet_encryption_key:
                    raise ValueError("wallet_encryption_key or wallet_encryption_key_file is required for software custody in staging/beta/production")
            else:
                if not self.custody_hsm_key_label:
                    raise ValueError("custody_hsm_key_label is required for HSM custody in staging/beta/production")
                if not self.custody_hsm_wrapping_key or not self.custody_hsm_signing_key:
                    raise ValueError("HSM custody requires wrapping and signing keys in staging/beta/production")
                if not self.custody_hsm_wrapping_key_file or not self.custody_hsm_signing_key_file:
                    raise ValueError("HSM custody requires file-backed wrapping and signing keys in staging/beta/production")
            if "user:pass@localhost" in self.database_url:
                raise ValueError("database_url must be overridden for staging/beta/production")

        if self.bitcoin_network.lower() not in {"mainnet", "testnet", "signet", "regtest"}:
            raise ValueError("bitcoin_network must be one of: mainnet, testnet, signet, regtest")

        if self.elements_network.lower() not in {"liquidv1", "liquidtestnet", "elementsregtest"}:
            raise ValueError("elements_network must be one of: liquidv1, liquidtestnet, elementsregtest")

        return self


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _infer_env_profile() -> str:
    profile = os.getenv("ENV_PROFILE", "local").strip().lower()
    return profile if profile in {"local", "staging", "beta", "production"} else "local"


def _env_files_for_profile(profile: str) -> list[Path]:
    infra_dir = _repo_root() / "infra"
    return [
        _repo_root() / ".env",
        infra_dir / ".env",
        infra_dir / f".env.{profile}",
    ]


@lru_cache(maxsize=16)
def get_settings(service_name: str, default_port: int) -> Settings:
    profile = _infer_env_profile()
    env_files = [path for path in _env_files_for_profile(profile) if path.exists()]
    return Settings(
        _env_file=env_files if env_files else None,
        service_name=service_name,
        service_port=default_port,
        env_profile=profile,
    )
