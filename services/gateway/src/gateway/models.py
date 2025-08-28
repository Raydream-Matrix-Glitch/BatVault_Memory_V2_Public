"""
Gateway-level shim that re-exports the **canonical** Milestone-3 data model.
Keeps historical imports (`from gateway.models import WhyDecisionResponse`)
working even after we split packages.
"""

from core_models.models import WhyDecisionResponse

__all__: list[str] = ["WhyDecisionResponse"]