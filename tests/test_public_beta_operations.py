from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_beta_environment_template_targets_signet():
    content = (REPO_ROOT / "infra" / ".env.beta.example").read_text(encoding="utf-8")

    assert "ENV_PROFILE=beta" in content
    assert "BITCOIN_NETWORK=signet" in content
    assert "ALERT_WEBHOOK_URL_FILE=/run/secrets/beta_alert_webhook" in content


def test_public_beta_compose_includes_observability_stack():
    content = (REPO_ROOT / "infra" / "docker-compose.public-beta.yml").read_text(encoding="utf-8")

    assert "docker-compose.observability.yml" in content
    assert "./.env.beta" in content
    assert "prometheus:" in content
    assert "grafana:" in content
    assert "alertmanager:" in content


def test_gateway_exposes_metrics_for_all_services():
    content = (REPO_ROOT / "services" / "gateway" / "default.conf").read_text(encoding="utf-8")

    assert "/metrics/auth" in content
    assert "/metrics/wallet" in content
    assert "/metrics/tokenization" in content
    assert "/metrics/marketplace" in content
    assert "/metrics/education" in content
    assert "/metrics/nostr" in content
    assert "/metrics/admin" in content


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
