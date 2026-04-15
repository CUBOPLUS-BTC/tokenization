"""In-process metrics collector and ``/metrics`` endpoint builder.

Provides lightweight counters, gauges, and histograms that can be queried via
a ``/metrics`` JSON endpoint on each service.  The design intentionally avoids
a Prometheus client dependency so the module works in any environment while
still being compatible with Prometheus-style scraping via a simple JSON→text
adapter or a future migration to ``prometheus_client``.

Usage in a service ``main.py``::

    from common.metrics import metrics, mount_metrics_endpoint

    mount_metrics_endpoint(app, settings)

    # Increment a counter
    metrics.inc("http_requests_total", labels={"method": "POST", "path": "/orders"})

    # Set a gauge
    metrics.set("active_websocket_connections", 3)
"""

from __future__ import annotations

import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import Settings


@dataclass
class _Metric:
    value: float = 0.0
    labels: dict[str, str] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.monotonic)


class MetricsCollector:
    """Thread-safe in-process metrics store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._labeled_counters: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._start_time = time.monotonic()

    # -- Counters --------------------------------------------------------------

    def inc(self, name: str, value: float = 1.0, *, labels: dict[str, str] | None = None) -> None:
        """Increment a counter by *value* (default 1)."""
        with self._lock:
            if labels:
                key = _label_key(labels)
                self._labeled_counters[name][key] += value
            else:
                self._counters[name] += value

    # -- Gauges ----------------------------------------------------------------

    def set(self, name: str, value: float) -> None:
        """Set a gauge to an absolute *value*."""
        with self._lock:
            self._gauges[name] = value

    def gauge_inc(self, name: str, delta: float = 1.0) -> None:
        with self._lock:
            self._gauges[name] = self._gauges.get(name, 0.0) + delta

    def gauge_dec(self, name: str, delta: float = 1.0) -> None:
        with self._lock:
            self._gauges[name] = self._gauges.get(name, 0.0) - delta

    # -- Snapshot --------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of all metrics."""
        with self._lock:
            counters: dict[str, Any] = dict(self._counters)
            for name, labeled in self._labeled_counters.items():
                counters[name] = {k: v for k, v in labeled.items()}

            return {
                "counters": counters,
                "gauges": dict(self._gauges),
                "uptime_seconds": round(time.monotonic() - self._start_time, 2),
                "collected_at": datetime.now(tz=timezone.utc).isoformat(),
            }


def _label_key(labels: dict[str, str]) -> str:
    """Deterministic string key from a label dict."""
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))


# Module-level singleton used by all services in the same process.
metrics = MetricsCollector()


def mount_metrics_endpoint(app: FastAPI, settings: Settings) -> None:
    """Register ``/metrics`` on *app*, returning the collector snapshot."""

    @app.get("/metrics", tags=["Observability"])
    async def get_metrics():
        data = metrics.snapshot()
        data["service"] = settings.service_name
        data["env_profile"] = settings.env_profile
        return JSONResponse(content=data)


__all__ = [
    "MetricsCollector",
    "metrics",
    "mount_metrics_endpoint",
]
