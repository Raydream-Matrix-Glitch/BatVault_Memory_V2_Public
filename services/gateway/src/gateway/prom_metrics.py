from prometheus_client import REGISTRY, Gauge, Counter, Histogram

# --------------------------------------------------------------------------- #
# internal helper – *get-or-create* to avoid duplicate registration           #
# --------------------------------------------------------------------------- #

def _get_or_create_metric(klass, name: str, doc: str, **kwargs):
    """Return an existing collector if already registered, otherwise create."""
    existing = REGISTRY._names_to_collectors.get(name)          # type: ignore[attr-defined]
    if existing is not None:
        return existing
    return klass(name, doc, registry=REGISTRY, **kwargs)

# public convenience wrappers ------------------------------------------------ #

def gauge(name: str, doc: str, **kwargs):
    return _get_or_create_metric(Gauge, name, doc, **kwargs)

def counter(name: str, doc: str, **kwargs):
    return _get_or_create_metric(Counter, name, doc, **kwargs)

def histogram(name: str, doc: str, **kwargs):
    return _get_or_create_metric(Histogram, name, doc, **kwargs)

# --------------------------------------------------------------------------- #
# Gateway evidence-bundle metrics (tech-spec §B5)                             #
# --------------------------------------------------------------------------- #

gateway_total_neighbors_found     = gauge(
    "gateway_total_neighbors_found",
    "k-1 neighbors collected before any filtering",
)
gateway_selector_truncation_total = counter(
    "gateway_selector_truncation_total",
    "Requests where selector dropped evidence to fit the prompt budget",
)
gateway_final_evidence_count      = gauge(
    "gateway_final_evidence_count",
    "Evidence items after potential truncation",
)
gateway_bundle_size_bytes         = gauge(
    "gateway_bundle_size_bytes",
    "Size of evidence bundle (bytes)",
)
gateway_dropped_evidence_ids      = gauge(
    "gateway_dropped_evidence_ids",
    "Number of evidence IDs removed by selector (cardinality-safe gauge)",
)

# Fallbacks – count how often we deliver deterministic answers
gateway_llm_fallback_total = _get_or_create_metric(
    Counter,
    "gateway_llm_fallback_total",
    "Number of responses where deterministic templater fallback was used",
)

# ensure gauges are present in /metrics even before first use
for _g in (
    gateway_total_neighbors_found,
    gateway_final_evidence_count,
    gateway_bundle_size_bytes,
    gateway_dropped_evidence_ids,
):
    try:
        _g.set(0)
    except ValueError:        # Counters don't support .set()
        pass