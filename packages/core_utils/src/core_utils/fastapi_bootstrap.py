"""
core_utils.fastapi_bootstrap — one-call FastAPI wiring for BatVault services.

Goals:
  • Single place to apply standard instrumentation and (optional) HTTP hardening.
  • Consistency across services with minimal LOC in each service app.
  • Config via env vars so behavior can be tuned per environment without code changes.
  • Health endpoints are attached explicitly by each service (see notes below).

Environment knobs (all optional):
  CORS_ORIGINS           — Comma/space separated origins (e.g. "https://x, https://y").
  RATE_LIMIT             — "<count>/<unit>", units: second|minute|hour  (e.g. "60/minute").
"""
from __future__ import annotations
import os, re
from typing import Iterable, Optional
try:
    # starlette is already a direct dependency of FastAPI; no extra libs.
    from starlette.middleware.proxy_headers import ProxyHeadersMiddleware  # type: ignore
except Exception:
    ProxyHeadersMiddleware = None  # type: ignore
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from core_observability.fastapi import instrument_app
from core_utils.rate_limit import RateLimitMiddleware

_UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600}

def _parse_origins(s: str | None) -> list[str]:
    if not s:
        return []
    # split on comma or whitespace
    parts = [p.strip() for p in re.split(r"[\s,]+", s) if p.strip()]
    return parts

def _parse_rate_limit(s: str | None) -> tuple[int, float] | None:
    if not s:
        return None
    m = re.match(r"^(\d+)\s*/\s*(second|minute|hour)s?$", s.strip(), flags=re.I)
    if not m:
        return None
    count = int(m.group(1))
    unit = m.group(2).lower()
    seconds = _UNIT_SECONDS[unit]
    refill_per_sec = count / seconds
    return count, refill_per_sec

def setup_service(
    app: FastAPI,
    service_name: str,
    *,
    enable_cors_env: str = "CORS_ORIGINS",
    rate_limit_env: str = "RATE_LIMIT",
    exclude_rate_limit_paths: Iterable[str] = ("/health","/healthz","/readyz","/metrics"),
    ttfb_label_route: bool = True,
    attach_metrics_endpoint: bool = True,
) -> None:
    """
    Apply standard BatVault wiring to `app`:

      • Tracing + request logging (+/metrics) via core_observability.fastapi.instrument_app
      • Health endpoints: services must attach explicitly, e.g.:
          from core_utils.health import attach_health_routes
          attach_health_routes(app, checks={"liveness": ..., "readiness": ...})
      • Optional CORS via starlette CORSMiddleware (origins from env var)
      • Optional token-bucket RateLimitMiddleware (rate from env var)

    This function is idempotent.
    """
    # Observability
    instrument_app(app, service_name, ttfb_label_route=ttfb_label_route, attach_metrics_endpoint=attach_metrics_endpoint)

    # Optional CORS
    origins = _parse_origins(os.getenv(enable_cors_env))
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Honor reverse-proxy headers (scheme/host/port) when enabled
    if os.getenv("PROXY_HEADERS", "1").lower() in ("1", "true", "yes") and ProxyHeadersMiddleware:
        # Trusted hosts are typically enforced by the frontend ingress; we accept all here.
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

    # Optional rate limiting
    rl = _parse_rate_limit(os.getenv(rate_limit_env))
    if rl:
        capacity, refill_per_sec = rl
        app.add_middleware(
            RateLimitMiddleware,
            capacity=capacity,
            refill_per_sec=refill_per_sec,
            exclude_paths=tuple(exclude_rate_limit_paths),
        )

__all__ = ["setup_service"]
