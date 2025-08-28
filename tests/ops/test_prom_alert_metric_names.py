from fastapi.testclient import TestClient
import yaml, re
from services.api_edge.src.api_edge.app import app as api_app

def _metrics_names(app):
    body = TestClient(app).get("/metrics").text
    return set(re.findall(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)", body, flags=re.M))

def test_alert_queries_reference_existing_metrics():
    # api_edge is sufficient to smoke-check names (we only assert existence)
    names = _metrics_names(api_app)
    with open("ops/prometheus/alerts.yml", "r") as f:
        cfg = yaml.safe_load(f)
    exprs = []
    for grp in cfg.get("groups", []):
        for rule in grp.get("rules", []):
            e = rule.get("expr")
            if e:
                exprs.append(e)
    # Extract metric-like tokens and assert at least one matches
    referenced = set()
    for e in exprs:
        for tok in re.findall(r"[a-zA-Z_:][a-zA-Z0-9_:]*", e):
            if tok.endswith("_total") or tok.endswith("_bucket") or tok.endswith("_seconds"):
                referenced.add(tok)
    # We only require that api_edge metrics used by rules exist locally
    required = {m for m in referenced if m.startswith("api_edge_")}
    assert required.issubset(names), f"Missing metrics: {sorted(required - names)}"
