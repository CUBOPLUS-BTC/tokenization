"""Nostr utility functions for authenticating NIP-98 style requests.

Implements NIP-01 event ID serialization and Schnorr signature verification.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

try:
    import secp256k1
except ImportError:
    secp256k1 = None

from services.auth.schemas import NostrSignedEvent


class NostrValidationError(Exception):
    """Raised when a Nostr event fails validation."""
    pass


_CHALLENGE_PATTERN = re.compile(r"^Sign-in challenge:\s*(\S.*?)\s*$")
_MISSING_CHALLENGE_MESSAGE = (
    "Missing challenge: expected 'Sign-in challenge: <nonce>' in content "
    "or a [\"challenge\", <nonce>] tag."
)


def _extract_challenge_from_content(content: str) -> str | None:
    """Return the nonce embedded in the event content, or None when absent."""
    if not content:
        return None
    match = _CHALLENGE_PATTERN.fullmatch(content.strip())
    if match is None:
        return None
    return match.group(1)


def _extract_challenge_from_tags(tags: list[list[str]]) -> str | None:
    """Return the nonce placed inside a ``["challenge", <nonce>, ...]`` tag."""
    for tag in tags or []:
        if not isinstance(tag, list) or len(tag) < 2:
            continue
        name, value = tag[0], tag[1]
        if name == "challenge" and isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
    return None


def extract_nostr_challenge(content: str) -> str:
    """Back-compat helper: parse the nonce out of a content string only.

    Kept for callers/tests that operate on raw content strings.
    """
    nonce = _extract_challenge_from_content(content)
    if nonce is None:
        raise NostrValidationError("Invalid challenge format in event content.")
    return nonce


def extract_challenge_from_event(event: "NostrSignedEvent") -> str:
    """Extract the challenge nonce from either ``content`` or the ``challenge`` tag.

    Raises:
        NostrValidationError: If neither location carries a valid nonce.
    """
    nonce = _extract_challenge_from_content(event.content)
    if nonce is not None:
        return nonce
    nonce = _extract_challenge_from_tags(event.tags)
    if nonce is not None:
        return nonce
    raise NostrValidationError(_MISSING_CHALLENGE_MESSAGE)


def validate_nostr_event(
    pubkey: str,
    event: NostrSignedEvent,
    *,
    expected_challenge: str | None = None,
) -> None:
    """Validate a NIP-98 style Nostr auth event.

    Raises:
        NostrValidationError: If the event is invalid.
    """
    if event.kind != 22242:
        raise NostrValidationError("Event kind must be 22242 for authentication.")

    challenge = extract_challenge_from_event(event)
    if expected_challenge is not None and challenge != expected_challenge:
        raise NostrValidationError("Event challenge does not match the issued challenge.")

    now = int(time.time())
    if abs(now - event.created_at) > 300:
        raise NostrValidationError("Event timestamp is too old or in the future.")

    # 1. Verify Event ID
    serialized = json.dumps(
        [
            0,
            pubkey,
            event.created_at,
            event.kind,
            event.tags,
            event.content,
        ],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    event_id_bytes = hashlib.sha256(serialized.encode("utf-8")).digest()
    expected_id = event_id_bytes.hex()
    
    if expected_id != event.id:
        raise NostrValidationError("Event ID does not match serialized content.")

    # 2. Verify Schnorr Signature
    if secp256k1 is None:
        raise RuntimeError("secp256k1 library is not installed.")

    try:
        pubkey_bytes = bytes.fromhex(pubkey)
        if len(pubkey_bytes) != 32:
            raise NostrValidationError("Pubkey must be exactly 32 bytes.")
            
        sig_bytes = bytes.fromhex(event.sig)
        if len(sig_bytes) != 64:
            raise NostrValidationError("Signature must be exactly 64 bytes.")

        # python-secp256k1 (bindings) exposes PublicKey but parsing a 32-byte
        # NIP-01 pubkey normally requires instantiating an XOnly pubkey.
        # However, due to API differences, we can just prepend 0x02 to parse it,
        # then extract the schnorr verification out of it.
        # Alternatively, secp256k1.PublicKey supports `schnorr_verify`.
        # Note: In standard python-secp256k1 `secp256k1.PublicKey(b'\\x02' + pub_bytes, raw=True)`
        # `pk.schnorr_verify(msg_bytes, sig_bytes, b'', raw=True)` works.
        b_pubkey = b"\x02" + pubkey_bytes
        pk = secp256k1.PublicKey(b_pubkey, raw=True)
        
        # Verify the signature
        if not pk.schnorr_verify(event_id_bytes, sig_bytes, b"", raw=True):
            raise NostrValidationError("Invalid Schnorr signature.")
            
    except Exception as e:
        if isinstance(e, NostrValidationError):
            raise e
        raise NostrValidationError(f"Invalid signature bytes: {e}")
