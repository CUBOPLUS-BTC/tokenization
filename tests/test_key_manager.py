import sys
import os
import logging
import pytest
from io import StringIO
from pathlib import Path

# Add services directory to path
sys.path.append(str(Path(__file__).resolve().parents[1] / "services"))
sys.path.append(str(Path(__file__).resolve().parents[1] / "services" / "wallet"))

from common.custody import HsmCompatibleWalletCustody, describe_custody_record
from key_manager import KeyManager
from log_filter import SensitiveDataFilter

@pytest.fixture
def encryption_key():
    return "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"

@pytest.fixture
def key_manager(encryption_key):
    return KeyManager(encryption_key=encryption_key, bitcoin_network="regtest")

def test_generate_seed_length(key_manager):
    seed = key_manager.generate_seed(32)
    assert len(seed) == 32
    assert isinstance(seed, bytes)

def test_generate_seed_uniqueness(key_manager):
    seed1 = key_manager.generate_seed(32)
    seed2 = key_manager.generate_seed(32)
    assert seed1 != seed2

def test_encrypt_decrypt_roundtrip(key_manager):
    original_seed = os.urandom(32)
    encrypted = key_manager.encrypt_seed(original_seed)

    descriptor = describe_custody_record(encrypted)
    assert descriptor.backend == "software"
    assert descriptor.envelope_version == 1

    decrypted = key_manager.decrypt_seed(encrypted)
    assert decrypted == original_seed

def test_decrypt_with_wrong_key(encryption_key):
    # Use a different key for decryption
    km1 = KeyManager(encryption_key=encryption_key)
    km2 = KeyManager(encryption_key="ff" * 32)
    
    seed = os.urandom(32)
    encrypted = km1.encrypt_seed(seed)
    
    with pytest.raises(ValueError, match="Seed decryption failed"):
        km2.decrypt_seed(encrypted)

def test_decrypt_tampered_data(key_manager):
    seed = os.urandom(32)
    encrypted = bytearray(key_manager.encrypt_seed(seed))
    
    # Tamper with the ciphertext/tag
    encrypted[-1] ^= 0xFF
    
    with pytest.raises(ValueError, match="Seed decryption failed"):
        key_manager.decrypt_seed(bytes(encrypted))

def test_derivation_path_regtest(key_manager):
    # Regtest should use coin_type 1
    path = key_manager.get_derivation_path(0)
    assert path == "m/86'/1'/0'"

def test_derivation_path_mainnet(encryption_key):
    km_main = KeyManager(encryption_key=encryption_key, bitcoin_network="mainnet")
    path = km_main.get_derivation_path(5)
    assert path == "m/86'/0'/5'"

def test_hsm_compatible_backend_marks_seed_non_exportable():
    backend = HsmCompatibleWalletCustody(
        wrapping_key="11" * 32,
        key_label="hsm:test-wallet-root",
    )

    encrypted = backend.seal_seed(os.urandom(32))
    descriptor = describe_custody_record(encrypted)

    assert descriptor.backend == "hsm"
    assert descriptor.key_reference == "hsm:test-wallet-root"
    assert descriptor.exportable_seed is False

def test_log_filter_redaction():
    # Setup test logger
    log_stream = StringIO()
    handler = logging.StreamHandler(log_stream)
    logger = logging.getLogger("test_redaction")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.addFilter(SensitiveDataFilter())

    sensitive_hex = "a" * 64
    safe_hex = "b" * 32
    
    logger.info(f"Seed is {sensitive_hex}")
    logger.info(f"TXID is {safe_hex}")
    
    output = log_stream.getvalue()
    
    assert sensitive_hex not in output
    assert "[REDACTED]" in output
    assert safe_hex in output
    assert f"TXID is {safe_hex}" in output

def test_key_manager_invalid_key():
    with pytest.raises(ValueError, match="exactly 32 bytes"):
        KeyManager(encryption_key="aabbcc")
    
    with pytest.raises(ValueError, match="valid hex string"):
        KeyManager(encryption_key="not hex" * 8)

def test_derive_taproot_address(key_manager):
    # Test vector for BIP-86 (sort of, using known fixed seed)
    seed = b"0123456789abcdef" * 4
    address, spk = key_manager.derive_taproot_address(seed, 0)
    
    # Check regtest/testnet address format
    assert address.startswith("bcrt1p")
    assert len(spk) == 68 # 34 bytes hex = 0x5120 + 32-byte pubkey
    assert spk.startswith("5120")
