"""Token → USD cost using shared/config/pricing.json (SPEC 4.7)."""
from __future__ import annotations

import sys
import threading

_warned: set[str] = set()
_lock = threading.Lock()


def call_cost(config, model_key: str, prompt_tokens: int, cached_tokens: int, completion_tokens: int) -> float:
    pricing_key = config.models[model_key].pricing_key if model_key in config.models else model_key
    prices = (config.pricing.get("models") or {}).get(pricing_key)
    if prices is None:
        with _lock:
            if pricing_key not in _warned:
                _warned.add(pricing_key)
                print(f'⚠️  No price for "{pricing_key}" in pricing.json – counting its calls as $0.', file=sys.stderr)
        return 0.0
    uncached = max(prompt_tokens - cached_tokens, 0)
    return (uncached * float(prices.get("input_per_1m", 0))
            + cached_tokens * float(prices.get("cached_input_per_1m", prices.get("input_per_1m", 0)))
            + completion_tokens * float(prices.get("output_per_1m", 0))) / 1_000_000
