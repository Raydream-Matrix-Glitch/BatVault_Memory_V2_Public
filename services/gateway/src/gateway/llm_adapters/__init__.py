"""Subpackage for LLM adapters.

Each adapter exposes an async ``generate_async(endpoint, envelope, temperature, max_tokens) -> str`` function
that returns a JSON string conforming to the WhyDecisionAnswer schema. The router
selects the appropriate adapter based on the model configuration.

NOTE:
    We eagerly import submodules here so ``gateway.llm_adapters.vllm`` / ``.tgi`` attribute
    access works reliably across package boundaries. Without these imports, Python does not
    automatically load submodules and attribute access raises ``AttributeError`` at runtime.
"""

# Eager imports for attribute access (kept simple & explicit)
from . import vllm as vllm  # noqa: F401
from . import tgi as tgi    # noqa: F401

__all__ = ["vllm", "tgi"]