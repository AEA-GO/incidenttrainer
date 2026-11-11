"""Shared bits for the stand-in shop services: metrics, health and JSON logs.

Metric names match agent/dev/world.yaml (metrics templates) and the alert rules.
"""

import json
import os
import sys
import time

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

SERVICE = os.environ.get("SERVICE_NAME", "unknown")

REQUESTS = Counter(
    "http_requests_total", "HTTP requests", ["service", "route", "status"]
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["service", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)


def log(level: str, message: str, **fields: object) -> None:
    """One JSON line per event on stdout (Loki collects it later)."""
    line = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "level": level}
    line |= {"service": SERVICE, "message": message, **fields}
    print(json.dumps(line), file=sys.stdout, flush=True)


def create_app() -> FastAPI:
    app = FastAPI(title=SERVICE, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def measure(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        # Use the route template (/products/{product_id}), not the raw path,
        # so metrics don't get one series per product.
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        if path not in ("/metrics", "/health"):
            REQUESTS.labels(SERVICE, path, str(response.status_code)).inc()
            LATENCY.labels(SERVICE, path).observe(time.perf_counter() - start)
        return response

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": SERVICE}

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
