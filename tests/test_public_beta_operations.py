from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_beta_environment_template_targets_signet():
    content = (REPO_ROOT / "infra" / ".env.beta.example").read_text(encoding="utf-8")

    assert "ENV_PROFILE=beta" in content
    assert "BITCOIN_NETWORK=signet" in content
    assert "ALERT_WEBHOOK_URL_FILE=/run/secrets/beta_alert_webhook" in content


def test_public_beta_compose_includes_observability_stack():
    beta_content = (REPO_ROOT / "infra" / "docker-compose.public-beta.yml").read_text(encoding="utf-8")
    observability_content = (REPO_ROOT / "infra" / "docker-compose.observability.yml").read_text(encoding="utf-8")

    assert "./infra/.env.beta" in beta_content
    assert "gateway:" in beta_content
    assert "prometheus:" in observability_content
    assert "grafana:" in observability_content
    assert "alertmanager:" in observability_content


def test_gateway_exposes_metrics_for_all_services():
    content = (REPO_ROOT / "services" / "gateway" / "gateway.conf").read_text(encoding="utf-8")

    assert "/metrics/auth" in content
    assert "/metrics/wallet" in content
    assert "/metrics/tokenization" in content
    assert "/metrics/marketplace" in content
    assert "/metrics/nostr" in content
    assert "/metrics/admin" in content


def test_gateway_handles_base_service_paths_without_redirects():
    content = (REPO_ROOT / "services" / "gateway" / "gateway.conf").read_text(encoding="utf-8")

    assert "location = /v1/auth {" in content
    assert "location = /v1/wallet {" in content
    assert "location = /v1/tokenization {" in content
    assert "location = /v1/marketplace {" in content
    assert "location = /v1/nostr {" in content
    assert "location = /v1/admin {" in content


def test_gateway_docs_describe_api_only_local_stack_and_api_key_browser_headers():
    content = (REPO_ROOT / "services" / "gateway" / "README.md").read_text(encoding="utf-8")

    assert "does not boot a bundled frontend service" in content
    assert "X-API-Key" in content
    assert "X-2FA-Code" in content
    assert "X-Idempotency-Key" in content


def test_public_beta_runbook_documents_release_gate():
    content = (REPO_ROOT / "deploy" / "public-beta" / "README.md").read_text(encoding="utf-8")

    assert "signet" in content
    assert "Safety Boundaries" in content
    assert "Release Gate Checklist" in content
    assert "Mainnet Promotion Rule" in content


def test_prometheus_and_alertmanager_cover_production_and_beta():
    prometheus = (REPO_ROOT / "infra" / "observability" / "prometheus" / "prometheus.yml").read_text(encoding="utf-8")
    alerts = (REPO_ROOT / "infra" / "observability" / "prometheus" / "alerts" / "platform-rules.yml").read_text(encoding="utf-8")
    alertmanager = (REPO_ROOT / "infra" / "observability" / "alertmanager" / "alertmanager.yml").read_text(encoding="utf-8")

    assert "environment: production" in prometheus
    assert "environment: beta" in prometheus
    assert "SettlementFailureDetected" in alerts
    assert "production-settlement" in alertmanager
    assert "beta-settlement" in alertmanager


