from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import hmac
from typing import Literal
from urllib.parse import urlencode, urlparse
import uuid


OnRampState = Literal["ready", "pending_redirect", "kyc_required", "limited", "unavailable"]


class OnRampError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        provider_id: str,
        status_code: int,
        state: OnRampState,
    ) -> None:
        self.code = code
        self.message = message
        self.provider_id = provider_id
        self.status_code = status_code
        self.state = state
        super().__init__(message)


@dataclass(frozen=True)
class OnRampProviderDefinition:
    provider_id: str
    display_name: str
    handoff_base_url: str
    supported_fiat_currencies: tuple[str, ...]
    supported_countries: tuple[str, ...]
    payment_methods: tuple[str, ...]
    min_fiat_amount: Decimal
    max_fiat_amount: Decimal
    requires_kyc: bool
    disclaimer: str


@dataclass(frozen=True)
class OnRampProviderStatusView:
    provider_id: str
    display_name: str
    state: OnRampState
    supported_fiat_currencies: tuple[str, ...]
    supported_countries: tuple[str, ...]
    payment_methods: tuple[str, ...]
    min_fiat_amount: Decimal
    max_fiat_amount: Decimal
    requires_kyc: bool
    disclaimer: str
    external_handoff_url: str


@dataclass(frozen=True)
class OnRampSession:
    session_id: str
    provider_id: str
    state: OnRampState
    handoff_url: str
    deposit_address: str
    destination_wallet_id: str
    expires_at: datetime
    disclaimer: str
    compliance_action: Literal["review_terms", "complete_kyc"]


_PROVIDERS: tuple[OnRampProviderDefinition, ...] = (
    OnRampProviderDefinition(
        provider_id="bank-bridge",
        display_name="Bank Bridge",
        handoff_base_url="https://bank-bridge.partner.example/checkout",
        supported_fiat_currencies=("USD", "EUR", "GBP"),
        supported_countries=("US", "GB", "DE", "FR", "NL", "ES"),
        payment_methods=("bank_transfer",),
        min_fiat_amount=Decimal("25.00"),
        max_fiat_amount=Decimal("5000.00"),
        requires_kyc=True,
        disclaimer="Bank Bridge completes cardholder checks and may ask the user to complete provider-hosted KYC before the BTC purchase is released.",
    ),
    OnRampProviderDefinition(
        provider_id="card-bridge",
        display_name="Card Bridge",
        handoff_base_url="https://card-bridge.partner.example/buy",
        supported_fiat_currencies=("USD", "EUR"),
        supported_countries=("US", "CA", "GB", "IE", "PT"),
        payment_methods=("card",),
        min_fiat_amount=Decimal("20.00"),
        max_fiat_amount=Decimal("1500.00"),
        requires_kyc=True,
        disclaimer="Card Bridge is an external hosted checkout. Rates, payment acceptance, and settlement timing are controlled by the provider.",
    ),
)


def _provider_map() -> dict[str, OnRampProviderDefinition]:
    return {provider.provider_id: provider for provider in _PROVIDERS}


def _normalize_secret(secret: str | None) -> bytes:
    if secret:
        return secret.encode("utf-8")
    return b"dev-fiat-onramp-secret"


def _validate_redirect_url(url: str, *, field_name: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.netloc:
        return url
    if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}:
        return url
    raise OnRampError(
        code=f"invalid_{field_name}",
        message=f"{field_name} must be an HTTPS URL or a localhost callback.",
        provider_id="unknown",
        status_code=422,
        state="limited",
    )


def list_onramp_provider_views(*, kyc_verified: bool) -> list[OnRampProviderStatusView]:
    state: OnRampState = "ready" if kyc_verified else "ready"
    return [
        OnRampProviderStatusView(
            provider_id=provider.provider_id,
            display_name=provider.display_name,
            state=state,
            supported_fiat_currencies=provider.supported_fiat_currencies,
            supported_countries=provider.supported_countries,
            payment_methods=provider.payment_methods,
            min_fiat_amount=provider.min_fiat_amount,
            max_fiat_amount=provider.max_fiat_amount,
            requires_kyc=provider.requires_kyc,
            disclaimer=provider.disclaimer,
            external_handoff_url=provider.handoff_base_url,
        )
        for provider in _PROVIDERS
    ]


def create_onramp_session(
    *,
    provider_id: str,
    user_id: str,
    wallet_id: str,
    deposit_address: str,
    fiat_currency: str,
    fiat_amount: Decimal,
    country_code: str,
    return_url: str,
    cancel_url: str,
    kyc_verified: bool,
    signing_secret: str | None,
) -> OnRampSession:
    provider = _provider_map().get(provider_id)
    if provider is None:
        raise OnRampError(
            code="unsupported_onramp_provider",
            message="The requested fiat on-ramp provider is not supported.",
            provider_id=provider_id,
            status_code=404,
            state="unavailable",
        )

    normalized_currency = fiat_currency.strip().upper()
    normalized_country = country_code.strip().upper()
    if normalized_currency not in provider.supported_fiat_currencies:
        raise OnRampError(
            code="unsupported_fiat_currency",
            message=f"{provider.display_name} does not support {normalized_currency} purchases.",
            provider_id=provider_id,
            status_code=422,
            state="limited",
        )
    if normalized_country not in provider.supported_countries:
        raise OnRampError(
            code="unsupported_country",
            message=f"{provider.display_name} is not available in {normalized_country}.",
            provider_id=provider_id,
            status_code=422,
            state="limited",
        )
    if fiat_amount < provider.min_fiat_amount or fiat_amount > provider.max_fiat_amount:
        raise OnRampError(
            code="fiat_amount_out_of_range",
            message=(
                f"{provider.display_name} supports orders between {provider.min_fiat_amount} "
                f"and {provider.max_fiat_amount} {normalized_currency}."
            ),
            provider_id=provider_id,
            status_code=422,
            state="limited",
        )
    if provider.requires_kyc and not kyc_verified:
        raise OnRampError(
            code="provider_kyc_required",
            message=f"{provider.display_name} requires a verified KYC profile before launching checkout.",
            provider_id=provider_id,
            status_code=409,
            state="kyc_required",
        )

    validated_return_url = _validate_redirect_url(return_url, field_name="return_url")
    validated_cancel_url = _validate_redirect_url(cancel_url, field_name="cancel_url")
    session_id = str(uuid.uuid4())
    expires_at = datetime.now(tz=timezone.utc) + timedelta(minutes=20)
    state_payload = (
        f"{session_id}:{provider.provider_id}:{user_id}:{wallet_id}:{deposit_address}:"
        f"{normalized_currency}:{fiat_amount}:{normalized_country}:{expires_at.isoformat()}"
    ).encode("utf-8")
    state_token = hmac.new(
        _normalize_secret(signing_secret),
        state_payload,
        hashlib.sha256,
    ).hexdigest()
    handoff_url = provider.handoff_base_url + "?" + urlencode(
        {
            "session_id": session_id,
            "state": state_token,
            "wallet_address": deposit_address,
            "wallet_id": wallet_id,
            "fiat_currency": normalized_currency,
            "fiat_amount": str(fiat_amount),
            "country_code": normalized_country,
            "return_url": validated_return_url,
            "cancel_url": validated_cancel_url,
        }
    )
    return OnRampSession(
        session_id=session_id,
        provider_id=provider.provider_id,
        state="pending_redirect",
        handoff_url=handoff_url,
        deposit_address=deposit_address,
        destination_wallet_id=wallet_id,
        expires_at=expires_at,
        disclaimer=provider.disclaimer,
        compliance_action="review_terms",
    )


def default_onramp_notices() -> list[str]:
    return [
        "Fiat purchases complete on a provider-hosted checkout outside platform custody.",
        "Provider fees, exchange rates, KYC, and settlement timelines are determined by the selected partner.",
        "BTC purchases are delivered to a platform-generated on-chain address to keep wallet operations compatible during custody migrations.",
    ]