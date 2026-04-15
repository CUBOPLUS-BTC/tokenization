from __future__ import annotations

import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.custody import CustodyError, build_wallet_custody

logger = logging.getLogger(__name__)


class KeyManager:
    """
    Manages wallet key material, encryption, and derivation paths.
    Uses AES-256-GCM for authenticated encryption of HD seeds.
    """

    def __init__(self, encryption_key: str | bytes, bitcoin_network: str = "regtest"):
        """
        Initialize the KeyManager.
        :param encryption_key: A 32-byte hex string or bytes object for AES-256.
        :param bitcoin_network: The bitcoin network (mainnet, regtest, testnet) to determine derivation path.
        """
        self.bitcoin_network = bitcoin_network.lower()
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
        Returns the BIP-86 Taproot derivation path for the configured network.
        m / purpose' / coin_type' / account'
        """
        return self._backend.get_derivation_path(account_index, bitcoin_network=self.bitcoin_network)
