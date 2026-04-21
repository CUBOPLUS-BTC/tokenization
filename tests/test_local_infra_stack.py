from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_local_environment_template_requires_real_lnd_and_elements():
    content = (REPO_ROOT / "infra" / ".env.local.example").read_text(encoding="utf-8")

    assert "LND_GRPC_REQUIRED=true" in content
    assert "ELEMENTS_RPC_REQUIRED=true" in content
    assert "LND_MACAROON_PATH=/run/secrets/lnd/data/chain/bitcoin/regtest/admin.macaroon" in content
    assert "LND_TLS_CERT_PATH=/run/secrets/lnd/tls.cert" in content
    assert "ELEMENTS_WALLET_NAME=platform" in content


def test_local_compose_includes_real_lnd_and_elements_services():
    content = (REPO_ROOT / "infra" / "docker-compose.local.yml").read_text(encoding="utf-8")

    assert "  lnd:" in content
    assert "lightninglabs/lnd:v0.20.0-beta" in content
    assert "  elementsd:" in content
    assert "tokenization-elementsd" in content
    assert "lnd_data:/run/secrets/lnd:ro" in content


def test_bitcoin_local_config_exposes_zmq_for_lnd():
    content = (REPO_ROOT / "infra" / "bitcoin" / "bitcoin.conf").read_text(encoding="utf-8")

    assert "zmqpubrawblock=tcp://0.0.0.0:28332" in content
    assert "zmqpubrawtx=tcp://0.0.0.0:28333" in content

