from __future__ import annotations

from dataclasses import dataclass
import hashlib
from collections.abc import Sequence

from embit import ec, script
from embit.liquid import addresses, networks


_SECP256K1_ORDER = int(
    "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141",
    16,
)
_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


@dataclass(frozen=True)
class LiquidEscrowDetails:
    confidential_address: str
    unconfidential_address: str
    witness_script_hex: str
    script_pubkey_hex: str
    blinding_private_key: str
    blinding_pubkey: str


def normalize_participant_pubkey(pubkey: str) -> str:
    normalized = pubkey.strip().lower()

    if len(normalized) == 64:
        normalized = f"02{normalized}"

    if len(normalized) != 66 or normalized[:2] not in {"02", "03"}:
        raise ValueError("participant_pubkey_invalid")

    try:
        pubkey_bytes = bytes.fromhex(normalized)
    except ValueError as exc:
        raise ValueError("participant_pubkey_invalid") from exc

    if len(pubkey_bytes) != 33:
        raise ValueError("participant_pubkey_invalid")

    return normalized


def derive_compressed_pubkey(secret_material: bytes) -> str:
    return derive_private_key(secret_material).get_public_key().sec().hex()


def derive_private_key(secret_material: bytes) -> ec.PrivateKey:
    digest = hashlib.sha256(secret_material).digest()
    secret_scalar = (int.from_bytes(digest, "big") % (_SECP256K1_ORDER - 1)) + 1
    return ec.PrivateKey(secret_scalar.to_bytes(32, "big"))


def compress_xonly_pubkey(pubkey: str) -> str:
    normalized = normalize_participant_pubkey(pubkey)
    return normalized


def build_liquid_2of3_escrow(pubkeys: Sequence[str], network: str, blinding_seed: bytes) -> LiquidEscrowDetails:
    witness_script = _build_2of3_witness_script(list(pubkeys))
    witness_script_obj = script.Script(witness_script)
    script_pubkey = script.p2wsh(witness_script_obj)
    blinding_key = derive_private_key(blinding_seed)
    liquid_network = networks.NETWORKS[_network_name(network)]
    confidential_address = addresses.address(
        script_pubkey,
        blinding_key=blinding_key.get_public_key(),
        network=liquid_network,
    )
    unconfidential_address = addresses.address(script_pubkey, network=liquid_network)
    return LiquidEscrowDetails(
        confidential_address=confidential_address,
        unconfidential_address=unconfidential_address,
        witness_script_hex=witness_script.hex(),
        script_pubkey_hex=script_pubkey.data.hex(),
        blinding_private_key=blinding_key.secret.hex(),
        blinding_pubkey=blinding_key.get_public_key().sec().hex(),
    )


def _build_2of3_witness_script(pubkeys: list[str]) -> bytes:
    normalized_pubkeys = sorted(normalize_participant_pubkey(pubkey) for pubkey in pubkeys)
    script = bytearray()
    script.append(0x52)
    for pubkey in normalized_pubkeys:
        script.append(0x21)
        script.extend(bytes.fromhex(pubkey))
    script.extend((0x53, 0xAE))
    return bytes(script)


def _network_name(network: str) -> str:
    normalized = network.strip().lower()
    if normalized in networks.NETWORKS:
        return normalized
    if normalized == "mainnet":
        return "liquidv1"
    if normalized in {"testnet", "testnet4", "signet"}:
        return "liquidtestnet"
    return "elementsregtest"


def _network_hrp(network: str) -> str:
    normalized = network.strip().lower()
    if normalized == "mainnet":
        return "bc"
    if normalized == "regtest":
        return "bcrt"
    return "tb"


def _bech32_polymod(values: list[int]) -> int:
    generator = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = ((checksum & 0x1FFFFFF) << 5) ^ value
        for index in range(5):
            if (top >> index) & 1:
                checksum ^= generator[index]
    return checksum


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(char) >> 5 for char in hrp] + [0] + [ord(char) & 31 for char in hrp]


def _bech32_create_checksum(hrp: str, data: list[int]) -> list[int]:
    values = _bech32_hrp_expand(hrp) + data
    polymod = _bech32_polymod(values + [0, 0, 0, 0, 0, 0]) ^ 1
    return [(polymod >> 5 * (5 - index)) & 31 for index in range(6)]


def _bech32_encode(hrp: str, data: list[int]) -> str:
    combined = data + _bech32_create_checksum(hrp, data)
    return f"{hrp}1{''.join(_BECH32_CHARSET[value] for value in combined)}"


def _convertbits(data: bytes, from_bits: int, to_bits: int, *, pad: bool) -> list[int]:
    accumulator = 0
    bits = 0
    output: list[int] = []
    max_value = (1 << to_bits) - 1
    max_accumulator = (1 << (from_bits + to_bits - 1)) - 1

    for value in data:
        if value < 0 or value >> from_bits:
            raise ValueError("invalid_convertbits_value")
        accumulator = ((accumulator << from_bits) | value) & max_accumulator
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            output.append((accumulator >> bits) & max_value)

    if pad:
        if bits:
            output.append((accumulator << (to_bits - bits)) & max_value)
    elif bits >= from_bits or ((accumulator << (to_bits - bits)) & max_value):
        raise ValueError("invalid_convertbits_padding")

    return output


def _encode_segwit_address(hrp: str, witness_version: int, witness_program: bytes) -> str:
    if witness_version != 0:
        raise ValueError("unsupported_witness_version")
    if len(witness_program) not in {20, 32}:
        raise ValueError("unsupported_witness_program_length")

    data = [witness_version] + _convertbits(witness_program, 8, 5, pad=True)
    return _bech32_encode(hrp, data)