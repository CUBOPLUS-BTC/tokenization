from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.custody import CustodyError, build_wallet_custody

try:
    from embit import bip32
    from embit.liquid import slip77
    from embit.liquid.addresses import address as liquid_address
    from embit.liquid.networks import NETWORKS as LIQUID_NETWORKS
    from embit.script import p2wpkh
except ImportError:
    bip32 = None
    slip77 = None
    liquid_address = None
    LIQUID_NETWORKS = {}
    p2wpkh = None

logger = logging.getLogger(__name__)


def _require_embit() -> None:
    if (
        bip32 is None
        or slip77 is None
        or liquid_address is None
        or p2wpkh is None
        or not LIQUID_NETWORKS
    ):
        raise RuntimeError("embit is required for Liquid key derivation")


@dataclass(frozen=True)
class DerivedLiquidAddress:
    confidential_address: str
    unconfidential_address: str
    script_pubkey: str
    blinding_private_key: str
    blinding_pubkey: str
    derivation_path: str


def _network_name(bitcoin_network: str) -> str:
    normalized = bitcoin_network.lower()
    if normalized == "mainnet":
        return "liquidv1"
    if normalized in {"testnet", "signet"}:
        return "liquidtestnet"
    return "elementsregtest"


class KeyManager:
    """
    Manages wallet key material, encryption, and derivation paths.
    Uses AES-256-GCM for authenticated encryption of HD seeds.
    """

    def __init__(
        self,
        encryption_key: str | bytes,
        bitcoin_network: str = "regtest",
        *,
        elements_network: str | None = None,
    ):
        """
        Initialize the KeyManager.
        :param encryption_key: A 32-byte hex string or bytes object for AES-256.
        :param bitcoin_network: The paired bitcoin network (mainnet, regtest, testnet) for legacy compatibility.
        """
        self.bitcoin_network = bitcoin_network.lower()
        self.elements_network = (elements_network or _network_name(bitcoin_network)).lower()
        try:
            self._backend = build_wallet_custody(
                type(
                    "SettingsProxy",
                    (),
                    {
                        "custody_backend": "software",
                        "wallet_encryption_key": encryption_key,
                        "jwt_secret": None,
                        "custody_hsm_wrapping_key": None,
                        "custody_hsm_key_label": None,
                    },
                )()
            )
        except CustodyError as exc:
            raise ValueError(exc.message) from exc

    def generate_seed(self, length: int = 32) -> bytes:
        """
        Generates a high-entropy cryptographically random seed.
        """
        return self._backend.generate_seed(length)

    def encrypt_seed(self, seed: bytes) -> bytes:
        """
        Encrypts a seed using AES-256-GCM.
        Returns: nonce (12 bytes) + ciphertext (includes tag).
        """
        try:
            return self._backend.seal_seed(seed)
        except Exception as e:
            logger.error("Failed to encrypt seed.")
            raise RuntimeError(f"Seed encryption failed: {str(e)}")

    def decrypt_seed(self, encrypted_seed: bytes) -> bytes:
        """
        Decrypts an encrypted seed using AES-256-GCM.
        Expects: nonce (12 bytes) + ciphertext (includes tag).
        """
        try:
            return self._backend.unseal_seed(encrypted_seed)
        except Exception as e:
            logger.error("Failed to decrypt seed. Authentication tag might be invalid.")
            raise ValueError(f"Seed decryption failed: {str(e)}")

    def get_derivation_path(self, account_index: int = 0) -> str:
        """
        Returns the BIP-44-style Liquid derivation path for the configured network.
        m / 44' / coin_type' / account'
        """
        return self._backend.get_derivation_path(account_index, liquid_network=self.elements_network)

    def derive_liquid_address(self, seed: bytes, derivation_index: int) -> DerivedLiquidAddress:
        """
        Derives a Liquid confidential receive address and scriptPubKey from the given seed.
        """
        _require_embit()
        network = LIQUID_NETWORKS[self.elements_network]
        root = bip32.HDKey.from_seed(seed, version=network["xprv"])
        coin_type = 1776 if self.elements_network == "liquidv1" else 1
        path = f"m/44'/{coin_type}'/0'/0/{derivation_index}"
        derived = root.derive(path)
        script_pubkey = p2wpkh(derived.get_public_key())

        master_blinding_key = slip77.master_blinding_from_seed(seed)
        blinding_private_key = slip77.blinding_key(master_blinding_key, script_pubkey)
        blinding_pubkey = blinding_private_key.get_public_key()

        confidential_address = liquid_address(script_pubkey, blinding_pubkey, network=network)
        unconfidential_address = liquid_address(script_pubkey, network=network)

        return DerivedLiquidAddress(
            confidential_address=confidential_address,
            unconfidential_address=unconfidential_address,
            script_pubkey=script_pubkey.data.hex(),
            blinding_private_key=blinding_private_key.to_string(),
            blinding_pubkey=blinding_pubkey.to_string(),
            derivation_path=path,
        )
