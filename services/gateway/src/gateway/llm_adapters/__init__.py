"""Subpackage for LLM adapters.

Each adapter exposes a synchronous ``generate(endpoint, envelope, temperature, max_tokens) -> str`` function
that returns a JSON string conforming to the WhyDecisionAnswer schema.  The router
selects the appropriate adapter based on the model configuration.
"""

__all__ = ["vllm", "tgi"]