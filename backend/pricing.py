"""Token prices and cost for the token meter.

USD per 1,000,000 tokens, standard API tier, from the course's price data
(Lecture 11, mult_agent_hp_tokenomics/PRICING.md, retrieved 2026-10-06).
Cached input is billed as a slice of input: cached tokens at the cached rate,
the rest at the normal input rate.

A request whose input is over LONG_CONTEXT_THRESHOLD is repriced at the
long-context rate. Pitwall requests are a few thousand tokens, nowhere near
it, but the rule is applied per request in case that ever changes.
"""

from __future__ import annotations

LONG_CONTEXT_THRESHOLD = 272_000
SOURCE = "Lecture 11 PRICING.md (USD per 1M tokens, standard tier, retrieved 2026-10-06)"

PRICES: dict[str, dict[str, float]] = {
    "gpt-6-luna": {"input": 0.10, "cached_input": 0.01, "output": 0.50,
                   "long_input": 0.20, "long_output": 0.75},
}


def cost_usd(model: str, input_tokens: int, cached_input_tokens: int, output_tokens: int,
             long_context: bool = False) -> float | None:
    """Cost of some token counts, or None for a model we have no price for."""
    p = PRICES.get(model)
    if p is None:
        return None
    in_rate = p["long_input"] if long_context else p["input"]
    out_rate = p["long_output"] if long_context else p["output"]
    cached = max(0, min(cached_input_tokens, input_tokens))
    fresh = input_tokens - cached
    return round((fresh * in_rate + cached * p["cached_input"] + output_tokens * out_rate) / 1_000_000, 8)


def summarize(model: str, runs: list[dict]) -> dict:
    """Totals for one answer: every agent run that went into it (delegated
    runs, grounding retries and runs that failed partway included)."""
    total = {"requests": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    rows = []
    for r in runs:
        cost = cost_usd(model, r["input_tokens"], r["cached_input_tokens"], r["output_tokens"],
                        r.get("long_context", False))
        rows.append({**r, "cost_usd": cost})
        for k in ("requests", "input_tokens", "cached_input_tokens", "output_tokens"):
            total[k] += r[k]
        total["cost_usd"] = None if cost is None or total["cost_usd"] is None else round(total["cost_usd"] + cost, 8)
    return {"model": model, "runs": rows, "total": total, "prices_per_mtok": PRICES.get(model), "price_source": SOURCE}
