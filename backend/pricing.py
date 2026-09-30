"""Token prices, used to turn a run's usage into a cost event.

Prices are in euro cents per million tokens. They are demo defaults, not a
quote; override per deployment with PRICE_<MODEL>_INPUT / _OUTPUT (model name
upper-cased, non-alphanumerics as underscores) or globally with
PRICE_DEFAULT_INPUT / PRICE_DEFAULT_OUTPUT.

Local Ollama models cost nothing per token; set PRICE_OLLAMA_INPUT / _OUTPUT
to a notional price if you want budgets to bite in a local demo.
"""

import os
import re

# cents per 1M tokens (input, output)
_BUILTIN: dict[str, tuple[float, float]] = {
    "gpt-5": (125, 1000),
    "gpt-5-mini": (25, 200),
    "gpt-5-nano": (5, 40),
    "gpt-4.1": (200, 800),
    "gpt-4.1-mini": (40, 160),
    "gpt-4o": (250, 1000),
    "gpt-4o-mini": (15, 60),
    "mock": (125, 1000),
}


def _env_key(model: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", model.upper())


def price_for(model: str) -> tuple[float, float]:
    key = _env_key(model)
    env_in = os.environ.get(f"PRICE_{key}_INPUT")
    env_out = os.environ.get(f"PRICE_{key}_OUTPUT")
    if env_in and env_out:
        return float(env_in), float(env_out)
    # Longest matching prefix wins, so "gpt-5-mini-2026" prices as gpt-5-mini.
    for name in sorted(_BUILTIN, key=len, reverse=True):
        if model.startswith(name):
            return _BUILTIN[name]
    return (
        float(os.environ.get("PRICE_DEFAULT_INPUT", 125)),
        float(os.environ.get("PRICE_DEFAULT_OUTPUT", 1000)),
    )


def cost_cents(model: str, input_tokens: int, output_tokens: int, provider: str = "") -> float:
    if provider == "ollama":
        p_in = float(os.environ.get("PRICE_OLLAMA_INPUT", 0))
        p_out = float(os.environ.get("PRICE_OLLAMA_OUTPUT", 0))
    else:
        p_in, p_out = price_for(model)
    return round((input_tokens * p_in + output_tokens * p_out) / 1_000_000, 4)
