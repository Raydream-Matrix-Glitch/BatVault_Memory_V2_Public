# AUTO: exports runtime shims + generated models. DO NOT EDIT generated files.
from .models import (
    WhyDecisionAnchor,
    GraphEdgesModel,
    MemoryMetaModel,
    WhyDecisionAnswer,
    CompletenessFlags,
    WhyDecisionEvidence,
    WhyDecisionResponse,
)
def _opt(module_name: str):
    try:
        return __import__(f"{__name__}.{module_name}", fromlist=["*"])
    except Exception:
        return None

# Optional, generated modules:
bundles_exec_summary     = _opt("models_bundles_exec_summary")
bundles_view             = _opt("models_bundles_view")
bundles_trace            = _opt("models_bundles_trace")
bundle_manifest          = _opt("models_bundle_manifest")
receipt                  = _opt("models_receipt")
memory_meta              = _opt("models_memory_meta")
memory_graph_view        = _opt("models_memory_graph_view")
edge_wire                = _opt("models_edge_wire")
gateway_plan             = _opt("models_gateway_plan")
policy_input             = _opt("models_policy_input")
policy_decision          = _opt("models_policy_decision")
memory_query_request     = _opt("models_memory_query_request")
memory_resolve_response  = _opt("models_memory_resolve_response")
meta_inputs              = _opt("models_meta_inputs")
__all__ = [
    "WhyDecisionAnchor","GraphEdgesModel","MemoryMetaModel",
    "WhyDecisionAnswer","CompletenessFlags","WhyDecisionEvidence","WhyDecisionResponse",
    "bundles_exec_summary","bundles_view","bundles_trace","bundle_manifest","receipt",
    "memory_meta","memory_graph_view","edge_wire",
    "gateway_plan","policy_input","policy_decision",
    "memory_query_request","memory_resolve_response","meta_inputs",
]