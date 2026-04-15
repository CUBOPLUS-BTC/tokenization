"""In-process metrics collector with Prometheus-compatible exposition."""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from .config import Settings
from .readiness import get_readiness_payload

_DEFAULT_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)


def _label_key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted((str(key), str(value)) for key, value in labels.items()))


def _label_dict(label_key: tuple[tuple[str, str], ...]) -> dict[str, str]:
    return {key: value for key, value in label_key}


def _format_prometheus_labels(
    labels: tuple[tuple[str, str], ...],
    extra: dict[str, str] | None = None,
) -> str:
    merged = dict(labels)
    if extra:
        merged.update(extra)
    if not merged:
        return ""

    ordered = []
    for key, value in sorted(merged.items()):
        escaped = (
            str(value)
            .replace("\\", "\\\\")
            .replace("\n", "\\n")
            .replace('"', '\\"')
        )
        ordered.append(f'{key}="{escaped}"')
    return "{" + ",".join(ordered) + "}"


@dataclass
class _Histogram:
    buckets: tuple[float, ...] = field(default_factory=lambda: _DEFAULT_BUCKETS)
    bucket_counts: list[float] = field(init=False)
    count: float = 0.0
    total_sum: float = 0.0

    def __post_init__(self) -> None:
        self.bucket_counts = [0.0 for _ in self.buckets]

    def observe(self, value: float) -> None:
        self.count += 1.0
        self.total_sum += value
        for index, upper_bound in enumerate(self.buckets):
            if value <= upper_bound:
                self.bucket_counts[index] += 1.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "buckets": [
                {"le": upper_bound, "count": count}
                for upper_bound, count in zip(self.buckets, self.bucket_counts, strict=True)
            ],
            "count": self.count,
            "sum": self.total_sum,
        }


class MetricsCollector:
    """Thread-safe in-process metrics store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(dict)
        self._gauges: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(dict)
        self._histograms: dict[str, dict[tuple[tuple[str, str], ...], _Histogram]] = defaultdict(dict)
        self._start_time = time.monotonic()

    def inc(self, name: str, value: float = 1.0, *, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            label_key = _label_key(labels)
            self._counters[name][label_key] = self._counters[name].get(label_key, 0.0) + value

    def set(self, name: str, value: float, *, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            self._gauges[name][_label_key(labels)] = value

    def gauge_inc(self, name: str, delta: float = 1.0, *, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            label_key = _label_key(labels)
            current = self._gauges[name].get(label_key, 0.0)
            self._gauges[name][label_key] = current + delta

    def gauge_dec(self, name: str, delta: float = 1.0, *, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            label_key = _label_key(labels)
            current = self._gauges[name].get(label_key, 0.0)
            self._gauges[name][label_key] = current - delta

    def observe(self, name: str, value: float, *, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            label_key = _label_key(labels)
            histogram = self._histograms[name].get(label_key)
            if histogram is None:
                histogram = _Histogram()
                self._histograms[name][label_key] = histogram
            histogram.observe(value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": {
                    name: {
                        ",".join(f"{key}={value}" for key, value in label_key): amount
                        for label_key, amount in values.items()
                    }
                    for name, values in self._counters.items()
                },
                "gauges": {
                    name: {
                        ",".join(f"{key}={value}" for key, value in label_key): amount
                        for label_key, amount in values.items()
                    }
                    for name, values in self._gauges.items()
                },
                "histograms": {
                    name: {
                        ",".join(f"{key}={value}" for key, value in label_key): histogram.snapshot()
                        for label_key, histogram in values.items()
                    }
                    for name, values in self._histograms.items()
                },
                "uptime_seconds": round(time.monotonic() - self._start_time, 2),
                "collected_at": datetime.now(tz=timezone.utc).isoformat(),
            }

    def render_prometheus(self, *, global_labels: dict[str, str] | None = None) -> str:
        lines: list[str] = []
        with self._lock:
            lines.extend(self._render_family(self._counters, "counter", global_labels=global_labels))
            lines.extend(self._render_family(self._gauges, "gauge", global_labels=global_labels))
            lines.extend(self._render_histograms(global_labels=global_labels))
            uptime_labels = _format_prometheus_labels((), global_labels)
            lines.append("# TYPE process_uptime_seconds gauge")
            lines.append(f"process_uptime_seconds{uptime_labels} {time.monotonic() - self._start_time:.6f}")
        return "\n".join(lines) + "\n"

    def _render_family(
        self,
        store: dict[str, dict[tuple[tuple[str, str], ...], float]],
        metric_type: str,
        *,
        global_labels: dict[str, str] | None,
    ) -> list[str]:
        lines: list[str] = []
        for name in sorted(store):
            lines.append(f"# TYPE {name} {metric_type}")
            for label_key, value in sorted(store[name].items()):
                labels = _format_prometheus_labels(label_key, global_labels)
                lines.append(f"{name}{labels} {value:.6f}")
        return lines

    def _render_histograms(self, *, global_labels: dict[str, str] | None) -> list[str]:
        lines: list[str] = []
        for name in sorted(self._histograms):
            lines.append(f"# TYPE {name} histogram")
            for label_key, histogram in sorted(self._histograms[name].items()):
                cumulative = 0.0
                for upper_bound, count in zip(histogram.buckets, histogram.bucket_counts, strict=True):
                    cumulative += count
                    lines.append(
                        f"{name}_bucket"
                        f"{_format_prometheus_labels(label_key, {**(global_labels or {}), 'le': str(upper_bound)})} "
                        f"{cumulative:.6f}"
                    )
                lines.append(
                    f"{name}_bucket"
                    f"{_format_prometheus_labels(label_key, {**(global_labels or {}), 'le': '+Inf'})} "
                    f"{histogram.count:.6f}"
                )
                lines.append(
                    f"{name}_sum{_format_prometheus_labels(label_key, global_labels)} {histogram.total_sum:.6f}"
                )
                lines.append(
                    f"{name}_count{_format_prometheus_labels(label_key, global_labels)} {histogram.count:.6f}"
                )
        return lines


metrics = MetricsCollector()


def record_business_event(
    event_name: str,
    *,
    outcome: str = "success",
    labels: dict[str, str] | None = None,
    value: float = 1.0,
) -> None:
    event_labels = {"event": event_name, "outcome": outcome}
    if labels:
        event_labels.update({key: str(val) for key, val in labels.items()})
    metrics.inc("business_events_total", value, labels=event_labels)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    if route is not None:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            return path
    return request.url.path


def _refresh_readiness_gauges(settings: Settings) -> dict[str, Any]:
    readiness = get_readiness_payload(settings)
    metrics.set("service_ready", 1.0 if readiness["status"] == "ready" else 0.0)
    for dependency, payload in readiness["dependencies"].items():
        metrics.set(
            "dependency_up",
            1.0 if payload["ok"] else 0.0,
            labels={"dependency": dependency, "target": payload["target"]},
        )
    return readiness


def mount_metrics_endpoint(app: FastAPI, settings: Settings) -> None:
    """Register request instrumentation and the ``/metrics`` endpoint."""

    global_labels = {
        "service": settings.service_name,
        "env_profile": settings.env_profile,
        "bitcoin_network": settings.bitcoin_network,
    }

    metrics.set("service_info", 1.0, labels=global_labels)

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        start = time.perf_counter()
        path = _route_template(request)
        base_labels = {"method": request.method, "path": path}

        metrics.gauge_inc("http_requests_in_progress", labels=base_labels)
        status_code = 500
        error_recorded = False
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            metrics.inc("http_request_errors_total", labels={**base_labels, "status_code": "500"})
            error_recorded = True
            raise
        finally:
            duration = time.perf_counter() - start
            metrics.gauge_dec("http_requests_in_progress", labels=base_labels)
            metrics.inc("http_requests_total", labels={**base_labels, "status_code": str(status_code)})
            metrics.observe("http_request_duration_seconds", duration, labels=base_labels)
            if status_code >= 500 and not error_recorded:
                metrics.inc("http_request_errors_total", labels={**base_labels, "status_code": str(status_code)})

    @app.get("/metrics", tags=["Observability"])
    async def get_metrics(request: Request):
        readiness = _refresh_readiness_gauges(settings)
        if request.query_params.get("format") == "json":
            data = metrics.snapshot()
            data["service"] = settings.service_name
            data["env_profile"] = settings.env_profile
            data["bitcoin_network"] = settings.bitcoin_network
            data["readiness"] = readiness
            return JSONResponse(content=data)

        return PlainTextResponse(
            content=metrics.render_prometheus(global_labels=global_labels),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )


__all__ = [
    "MetricsCollector",
    "metrics",
    "mount_metrics_endpoint",
    "record_business_event",
]
