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
    assert "tokenization-local-elementsd" in content
    assert "lnd_data:/run/secrets/lnd:ro" in content


def test_compose_up_helpers_force_repo_project_directory_for_migrate():
    powershell_content = (REPO_ROOT / "scripts" / "compose-up.ps1").read_text(encoding="utf-8")
    shell_content = (REPO_ROOT / "scripts" / "compose-up.sh").read_text(encoding="utf-8")

    assert "--project-directory" in powershell_content
    assert "--force-recreate --build migrate" in powershell_content
    assert "--project-directory" in shell_content
    assert "--force-recreate --build migrate" in shell_content


def test_bitcoin_local_config_exposes_zmq_for_lnd():
    content = (REPO_ROOT / "infra" / "bitcoin" / "bitcoin.conf").read_text(encoding="utf-8")

    assert "zmqpubrawblock=tcp://0.0.0.0:28332" in content
    assert "zmqpubrawtx=tcp://0.0.0.0:28333" in content


def test_regtest_compose_uses_infra_relative_paths():
    content = (REPO_ROOT / "infra" / "docker-compose.regtest.yml").read_text(encoding="utf-8")

    assert "./infra/.env.regtest" in content
    assert "./infra/bitcoin/bitcoin.conf" in content
    assert "context: ./services/gateway" in content
    assert "../:/app" not in content


def test_regtest_environment_template_is_dedicated():
    content = (REPO_ROOT / "infra" / ".env.regtest.example").read_text(encoding="utf-8")

    assert "ENV_PROFILE=regtest" in content
    assert "BITCOIN_NETWORK=regtest" in content
    assert "ELEMENTS_NETWORK=elementsregtest" in content
    assert "LND_MACAROON_PATH=/run/secrets/lnd/data/chain/bitcoin/regtest/admin.macaroon" in content


def test_testnet4_stack_template_and_compose_exist():
    env_content = (REPO_ROOT / "infra" / ".env.testnet4.example").read_text(encoding="utf-8")
    compose_content = (REPO_ROOT / "infra" / "docker-compose.testnet4.yml").read_text(encoding="utf-8")
    bitcoin_content = (REPO_ROOT / "infra" / "bitcoin" / "bitcoin.testnet4.conf").read_text(encoding="utf-8")

    assert "ENV_PROFILE=staging" in env_content
    assert "BITCOIN_NETWORK=testnet4" in env_content
    assert "ELEMENTS_NETWORK=liquidtestnet" in env_content
    assert "bitcoin.testnet=1" in (REPO_ROOT / "infra" / "lnd" / "lnd.testnet4.conf").read_text(encoding="utf-8")
    assert "testnet4=1" in bitcoin_content
    assert "rpcport=48332" in bitcoin_content
    assert "bitcoin-cli" in compose_content
    assert "-testnet4" in compose_content
    assert "liquidtestnet" in compose_content

