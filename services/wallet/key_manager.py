from __future__ import annotations

import os
import logging
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from typing import Any

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
        if isinstance(encryption_key, str):
            try:
                self._key = bytes.fromhex(encryption_key)
            except ValueError:
                raise ValueError("Wallet encryption key must be a valid hex string.")
        else:
            self._key = encryption_key

        if len(self._key) != 32:
            raise ValueError("Wallet encryption key must be exactly 32 bytes (256 bits).")

        self.bitcoin_network = bitcoin_network.lower()
        self.aes_gcm = AESGCM(self._key)

    def generate_seed(self, length: int = 32) -> bytes:
        """
        Generates a high-entropy cryptographically random seed.
        """
        return os.urandom(length)

    def encrypt_seed(self, seed: bytes) -> bytes:
        """
        Encrypts a seed using AES-256-GCM.
        Returns: nonce (12 bytes) + ciphertext (includes tag).
        """
        nonce = os.urandom(12)
        try:
            # None for associated_data as we don't have additional context to bind to yet
            ciphertext = self.aes_gcm.encrypt(nonce, seed, None)
            return nonce + ciphertext
        except Exception as e:
            logger.error("Failed to encrypt seed.")
            raise RuntimeError(f"Seed encryption failed: {str(e)}")

    def decrypt_seed(self, encrypted_seed: bytes) -> bytes:
        """
        Decrypts an encrypted seed using AES-256-GCM.
        Expects: nonce (12 bytes) + ciphertext (includes tag).
        """
        if len(encrypted_seed) < 28: # 12 nonce + 16 tag (min)
            raise ValueError("Encrypted seed data is too short.")

        nonce = encrypted_seed[:12]
        ciphertext = encrypted_seed[12:]
        try:
            return self.aes_gcm.decrypt(nonce, ciphertext, None)
        except Exception as e:
            logger.error("Failed to decrypt seed. Authentication tag might be invalid.")
            raise ValueError(f"Seed decryption failed: {str(e)}")

    def get_derivation_path(self, account_index: int = 0) -> str:
        """
        Returns the BIP-86 Taproot derivation path for the configured network.
        m / purpose' / coin_type' / account'
        """
        # coin_type: 0 for mainnet, 1 for testnet/regtest
        coin_type = "0" if self.bitcoin_network == "mainnet" else "1"
        return f"m/86'/{coin_type}'/{account_index}'"
