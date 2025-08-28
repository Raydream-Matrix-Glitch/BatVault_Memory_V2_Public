from __future__ import annotations
from typing import Any, Callable, Dict, List, Tuple
import random

from shared.tokens import estimate_messages_tokens

# Pure, deterministic prompt budgeting helpers.
# These functions DO NOT log. Callers (e.g., gateway) should emit structured logs.

Messages = List[Dict[str, str]]
RenderFn = Callable[[Dict[str, Any]], Messages]
TruncateFn = Callable[..., Tuple[Any, Dict[str, Any]]]

def _copy_envelope_without_evidence(envelope: Dict[str, Any]) -> Dict[str, Any]:
    env = dict(envelope)
    env.setdefault("evidence", {})
    ev = env.get("evidence") or {}
    if isinstance(ev, dict):
        ev = dict(ev)
        ev["events"] = []
        ev.setdefault("transitions", {})
        ev["transitions"] = {"preceding": [], "succeeding": []}
        env["evidence"] = ev
    return env

def plan_budget(
    render_fn: RenderFn,
    envelope: Dict[str, Any],
    *,
    context_window: int,
    guard_tokens: int,
    desired_completion_tokens: int,
) -> Dict[str, int]:
    """
    Compute overhead-only tokens and the evidence budget.
    Returns: {"overhead_tokens","prompt_tokens_overhead","evidence_budget_tokens"}.
    """
    overhead_env = _copy_envelope_without_evidence(envelope)
    overhead_messages = render_fn(overhead_env)
    overhead_tokens = int(estimate_messages_tokens(overhead_messages))
    prompt_tokens_overhead = overhead_tokens
    evidence_budget_tokens = max(
        0, context_window - guard_tokens - desired_completion_tokens - overhead_tokens
    )
    return {
        "overhead_tokens": overhead_tokens,
        "prompt_tokens_overhead": prompt_tokens_overhead,
        "evidence_budget_tokens": evidence_budget_tokens,
    }

def _deterministic_jitter(base: int, jitter_pct: float, *, seed: int) -> int:
    # Jitter is bounded in [ -jitter_pct, 0 ] of base (we only shrink)
    rnd = random.Random(seed)
    frac = rnd.random() * jitter_pct
    return int(base * (1.0 - frac))

def gate_budget(
    render_fn: RenderFn,
    truncate_fn: TruncateFn,
    *,
    envelope: Dict[str, Any],
    evidence_obj: Any,
    context_window: int,
    guard_tokens: int,
    desired_completion_tokens: int,
    max_retries: int = 2,
    shrink_factor: float = 0.8,
    jitter_pct: float = 0.15,
    seed: int = 0,
) -> Tuple[Dict[str, Any], Any]:
    """
    Deterministic budget gate:
      - plans overhead and evidence budget
      - truncates evidence against budget
      - if still too big, shrinks completion deterministically and retries
    Returns (gate_plan, trimmed_evidence).
    gate_plan: {messages,max_tokens,prompt_tokens,overhead_tokens,evidence_tokens,
                desired_completion_tokens,shrinks,logs}
    """
    logs: List[Dict[str, Any]] = []
    shrinks: List[int] = []

    stats = plan_budget(
        render_fn,
        envelope,
        context_window=context_window,
        guard_tokens=guard_tokens,
        desired_completion_tokens=desired_completion_tokens,
    )
    overhead_tokens = stats["overhead_tokens"]

    trimmed_evidence, sel_meta = truncate_fn(
        evidence_obj,
        overhead_tokens=overhead_tokens,
        desired_completion_tokens=desired_completion_tokens,
        context_window=context_window,
        guard_tokens=guard_tokens,
    )
    env1 = dict(envelope)
    env1["evidence"] = getattr(trimmed_evidence, "model_dump", lambda: trimmed_evidence)()
    messages = render_fn(env1)
    prompt_tokens = int(estimate_messages_tokens(messages))
    evidence_tokens = max(0, prompt_tokens - overhead_tokens)
    logs.append({"step": "truncate", "prompt_tokens": prompt_tokens, **sel_meta})

    current_desired = desired_completion_tokens
    max_tokens = min(current_desired, max(1, context_window - guard_tokens - prompt_tokens))

    attempt = 0
    while (prompt_tokens + current_desired + guard_tokens) > context_window and attempt < max_retries:
        attempt += 1
        shrunken = int(current_desired * shrink_factor)
        shrunken = _deterministic_jitter(shrunken, jitter_pct, seed=seed + attempt)
        current_desired = max(1, shrunken)
        shrinks.append(current_desired)

        trimmed_evidence, sel_meta = truncate_fn(
            evidence_obj,
            overhead_tokens=overhead_tokens,
            desired_completion_tokens=current_desired,
            context_window=context_window,
            guard_tokens=guard_tokens,
        )
        env_retry = dict(envelope)
        env_retry["evidence"] = getattr(trimmed_evidence, "model_dump", lambda: trimmed_evidence)()
        messages = render_fn(env_retry)
        prompt_tokens = int(estimate_messages_tokens(messages))
        evidence_tokens = max(0, prompt_tokens - overhead_tokens)
        logs.append({"step": "shrink", "attempt": attempt, "prompt_tokens": prompt_tokens, **sel_meta})

        max_tokens = min(current_desired, max(1, context_window - guard_tokens - prompt_tokens))
        if (prompt_tokens + max_tokens + guard_tokens) <= context_window:
            break

    gate_plan = {
        "messages": messages,
        "max_tokens": max_tokens,
        "prompt_tokens": prompt_tokens,
        "overhead_tokens": overhead_tokens,
        "evidence_tokens": evidence_tokens,
        "desired_completion_tokens": desired_completion_tokens,
        "shrinks": shrinks,
        "logs": logs,
    }
    return gate_plan, trimmed_evidence