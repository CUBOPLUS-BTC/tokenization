from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import logging
import os
from typing import Literal
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


logger = logging.getLogger(__name__)

CustodyBackendName = Literal["software", "hsm"]


class CustodyError(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        backend: CustodyBackendName,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.message = message
        self.backend = backend
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True)
class CustodyRecordDescriptor:
    backend: CustodyBackendName
    key_reference: str | None
    cipher: str
    fingerprint: str
    exportable_seed: bool
    envelope_version: int


@dataclass(frozen=True)
class CustodyStatus:
    backend: CustodyBackendName
    signer_backend: CustodyBackendName
    state: Literal["ready", "degraded"]
    key_reference: str | None
    signer_key_reference: str | None
    seed_exportable: bool
    server_compromise_impact: str
    disclaimers: tuple[str, ...]


def _normalize_hex_key(secret: str | bytes | None, *, label: str) -> bytes:
    if secret is None:
        raise CustodyError(
            code="missing_custody_secret",
            message=f"{label} is not configured.",
            backend="software",
        )

    if isinstance(secret, bytes):
        raw = secret
    else:
        try:
            raw = bytes.fromhex(secret)
        except ValueError as exc:
            raise CustodyError(
                code="invalid_custody_secret",
                message=f"{label} must be a valid hex string.",
                backend="software",
            ) from exc

    if len(raw) != 32:
        raise CustodyError(
            code="invalid_custody_secret",
            message=f"{label} must be exactly 32 bytes.",
            backend="software",
        )
    return raw


def _digest_label(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()[:16]


def _envelope_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _parse_envelope(encrypted_seed: bytes) -> dict[str, object] | None:
    try:
        payload = json.loads(encrypted_seed.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict) and payload.get("backend") in {"software", "hsm"}:
        return payload
    return None


def describe_custody_record(encrypted_seed: bytes) -> CustodyRecordDescriptor:
    payload = _parse_envelope(encrypted_seed)
    if payload is None:
        return CustodyRecordDescriptor(
            backend="software",
            key_reference=None,
            cipher="aes-256-gcm",
            fingerprint=_digest_label(encrypted_seed),
            exportable_seed=True,
            envelope_version=0,
        )

    return CustodyRecordDescriptor(
        backend=str(payload.get("backend", "software")),
        key_reference=payload.get("key_reference") if isinstance(payload.get("key_reference"), str) else None,
        cipher=str(payload.get("cipher", "aes-256-gcm")),
        fingerprint=str(payload.get("fingerprint", _digest_label(encrypted_seed))),
        exportable_seed=bool(payload.get("exportable_seed", False)),
        envelope_version=int(payload.get("version", 1)),
    )


class WalletCustodyBackend:
    backend_name: CustodyBackendName
    key_reference: str | None
    exportable_seed: bool

    def generate_seed(self, length: int = 32) -> bytes:
        return os.urandom(length)

    def seal_seed(self, seed: bytes) -> bytes:
        raise NotImplementedError

    def unseal_seed(self, encrypted_seed: bytes) -> bytes:
        raise NotImplementedError

    def get_derivation_path(self, account_index: int = 0, *, bitcoin_network: str) -> str:
        coin_type = "0" if bitcoin_network.lower() == "mainnet" else "1"
        return f"m/86'/{coin_type}'/{account_index}'"


class SoftwareWalletCustody(WalletCustodyBackend):
    backend_name: CustodyBackendName = "software"
    exportable_seed = True

    def __init__(self, encryption_key: str | bytes) -> None:
        self._key = _normalize_hex_key(encryption_key, label="wallet_encryption_key")
        self._cipher = AESGCM(self._key)
        self.key_reference = f"sw:{_digest_label(self._key)}"

    def seal_seed(self, seed: bytes) -> bytes:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, seed, None)
        return _envelope_bytes(
            {
                "version": 1,
                "backend": self.backend_name,
                "cipher": "aes-256-gcm",
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
                "key_reference": self.key_reference,
                "fingerprint": _digest_label(ciphertext),
                "exportable_seed": self.exportable_seed,
            }
        )

    def unseal_seed(self, encrypted_seed: bytes) -> bytes:
        payload = _parse_envelope(encrypted_seed)
        if payload is None:
            if len(encrypted_seed) < 28:
                raise ValueError("Encrypted seed data is too short.")
            nonce = encrypted_seed[:12]
            ciphertext = encrypted_seed[12:]
            try:
                return self._cipher.decrypt(nonce, ciphertext, None)
            except Exception as exc:
                logger.error("Failed to decrypt legacy wallet seed.")
                raise ValueError(f"Seed decryption failed: {str(exc)}") from exc

        try:
            nonce = base64.b64decode(str(payload["nonce"]))
            ciphertext = base64.b64decode(str(payload["ciphertext"]))
            return self._cipher.decrypt(nonce, ciphertext, None)
        except Exception as exc:
            logger.error("Failed to decrypt software-managed wallet seed.")
            raise ValueError(f"Seed decryption failed: {str(exc)}") from exc


class HsmCompatibleWalletCustody(WalletCustodyBackend):
    backend_name: CustodyBackendName = "hsm"
    exportable_seed = False

    def __init__(self, *, wrapping_key: str | bytes, key_label: str) -> None:
        self._key = _normalize_hex_key(wrapping_key, label="custody_hsm_wrapping_key")
        self._cipher = AESGCM(self._key)
        self.key_reference = key_label.strip() or "hsm:wallet-root"

    def seal_seed(self, seed: bytes) -> bytes:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, seed, None)
        return _envelope_bytes(
            {
                "version": 1,
                "backend": self.backend_name,
                "cipher": "aes-256-gcm",
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
                "key_reference": self.key_reference,
                "fingerprint": _digest_label(ciphertext),
                "exportable_seed": self.exportable_seed,
            }
        )

    def unseal_seed(self, encrypted_seed: bytes) -> bytes:
        payload = _parse_envelope(encrypted_seed)
        if payload is None or payload.get("backend") != self.backend_name:
            raise ValueError("Seed decryption failed: incompatible custody record.")
        try:
            nonce = base64.b64decode(str(payload["nonce"]))
            ciphertext = base64.b64decode(str(payload["ciphertext"]))
            return self._cipher.decrypt(nonce, ciphertext, None)
        except Exception as exc:
            logger.error("Failed to decrypt HSM-managed wallet seed.")
            raise ValueError(f"Seed decryption failed: {str(exc)}") from exc


class PlatformSigner:
    backend_name: CustodyBackendName
    key_reference: str | None

    def sign(self, *, purpose: str, message: bytes) -> str:
        raise NotImplementedError


class SoftwarePlatformSigner(PlatformSigner):
    backend_name: CustodyBackendName = "software"

    def __init__(self, secret: str | bytes) -> None:
        if isinstance(secret, bytes):
            self._secret = secret
        else:
            self._secret = secret.encode("utf-8")
        self.key_reference = f"sw-signer:{_digest_label(self._secret)}"

    def sign(self, *, purpose: str, message: bytes) -> str:
        scoped_message = purpose.encode("utf-8") + b":" + message
        return hmac.new(self._secret, scoped_message, hashlib.sha256).hexdigest()


class HsmCompatiblePlatformSigner(PlatformSigner):
    backend_name: CustodyBackendName = "hsm"

    def __init__(self, *, signing_key: str | bytes, key_label: str) -> None:
        self._secret = _normalize_hex_key(signing_key, label="custody_hsm_signing_key")
        self.key_reference = key_label.strip() or "hsm:platform-signer"

    def sign(self, *, purpose: str, message: bytes) -> str:
        scoped_message = purpose.encode("utf-8") + b":" + message
        return hmac.new(self._secret, scoped_message, hashlib.sha256).hexdigest()


def build_wallet_custody(settings: object) -> WalletCustodyBackend:
    backend_name = str(getattr(settings, "custody_backend", "software")).lower()
    if backend_name == "hsm":
        return HsmCompatibleWalletCustody(
            wrapping_key=getattr(settings, "custody_hsm_wrapping_key", None),
            key_label=getattr(settings, "custody_hsm_key_label", None) or "hsm:wallet-root",
        )

    encryption_key = getattr(settings, "wallet_encryption_key", None)
    if encryption_key:
        return SoftwareWalletCustody(encryption_key)

    fallback_secret = (
        getattr(settings, "jwt_secret", None)
        or getattr(settings, "service_name", None)
        or "local-wallet-custody"
    )
    if isinstance(fallback_secret, bytes):
        fallback_material = fallback_secret
    else:
        fallback_material = str(fallback_secret).encode("utf-8")
    return SoftwareWalletCustody(hashlib.sha256(fallback_material).hexdigest())


def build_platform_signer(settings: object) -> PlatformSigner:
    backend_name = str(getattr(settings, "custody_backend", "software")).lower()
    if backend_name == "hsm":
        return HsmCompatiblePlatformSigner(
            signing_key=getattr(settings, "custody_hsm_signing_key", None),
            key_label=getattr(settings, "custody_hsm_key_label", None) or "hsm:platform-signer",
        )

    secret = (
        getattr(settings, "wallet_encryption_key", None)
        or getattr(settings, "jwt_secret", None)
        or getattr(settings, "service_name", "platform")
    )
    return SoftwarePlatformSigner(secret)


def describe_custody_settings(settings: object) -> CustodyStatus:
    wallet_backend = build_wallet_custody(settings)
    signer = build_platform_signer(settings)
    backend_name = str(getattr(settings, "custody_backend", "software")).lower()
    state: Literal["ready", "degraded"] = "ready"
    if backend_name == "hsm" and not getattr(settings, "custody_hsm_key_label", None):
        state = "degraded"

    if backend_name == "hsm":
        server_compromise_impact = (
            "Wallet seeds remain wrapped under the configured HSM-compatible key reference; "
            "the application only handles opaque custody envelopes and a separate signer path."
        )
        disclaimers = (
            "HSM mode depends on externally managed key rotation and access policies.",
            "Withdrawal approval still requires user 2FA and provider-side controls.",
        )
    else:
        server_compromise_impact = (
            "Seeds are wrapped with an application-managed AES key; deploy file-backed secrets and isolate signing secrets to minimize blast radius."
        )
        disclaimers = (
            "Software custody is intended for local, staging, or transitional deployments.",
            "Production profiles require file-backed custody secrets.",
        )

    return CustodyStatus(
        backend=wallet_backend.backend_name,
        signer_backend=signer.backend_name,
        state=state,
        key_reference=wallet_backend.key_reference,
        signer_key_reference=signer.key_reference,
        seed_exportable=wallet_backend.exportable_seed,
        server_compromise_impact=server_compromise_impact,
        disclaimers=disclaimers,
    )


def derive_wallet_escrow_material(
    *,
    user_id: str | uuid.UUID,
    derivation_path: str,
    encrypted_seed: bytes,
) -> bytes:
    descriptor = describe_custody_record(encrypted_seed)
    user_uuid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
    return (
        b"user-escrow-pubkey:"
        + user_uuid.bytes
        + b":"
        + derivation_path.encode("utf-8")
        + b":"
        + descriptor.backend.encode("utf-8")
        + b":"
        + descriptor.fingerprint.encode("utf-8")
    )


def derive_platform_signing_material(settings: object, *, purpose: str) -> bytes:
    signer = build_platform_signer(settings)
    message = f"pubkey:{purpose}:{signer.key_reference}".encode("utf-8")
    digest = signer.sign(purpose=purpose, message=message)
    return digest.encode("utf-8")