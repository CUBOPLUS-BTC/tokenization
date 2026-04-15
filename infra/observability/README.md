# Observability Stack

The observability stack is provisioned with:

- Prometheus for application and infrastructure scraping
- Alertmanager for environment-specific escalation routes
- Grafana dashboards for production and public beta
- Blackbox Exporter for HTTP and TCP reachability probes
- cAdvisor for container resource metrics

## Files

- `../docker-compose.observability.yml` - monitoring stack services
- `prometheus/prometheus.yml` - scrape targets for production and beta
- `prometheus/alerts/platform-rules.yml` - uptime, latency, error-rate, and settlement alerts
- `alertmanager/alertmanager.yml` - production and beta alert routes
- `grafana/dashboards/platform-overview.json` - shared SLO and business-event dashboard
- `grafana/dashboards/beta-release-gate.json` - beta-only release gate dashboard

## Startup

```bash
docker compose -f infra/docker-compose.observability.yml up -d
```

## Required Follow-Up

- Replace the placeholder `*.internal` targets in `prometheus/prometheus.yml` with your real production and beta addresses.
- Replace the placeholder webhook receivers in `alertmanager/alertmanager.yml` with the production and beta escalation endpoints.
- Keep beta and production receivers separate to preserve operational boundaries.
