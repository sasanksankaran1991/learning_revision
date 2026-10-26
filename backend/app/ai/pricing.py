from __future__ import annotations

# USD per 1,000,000 tokens: (input, output). Unknown models use the mini rate.
_RATES = {
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "claude-3-5-haiku": (0.80, 4.0),
    "claude-haiku": (0.25, 1.25),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-flash": (0.10, 0.40),
}
_DEFAULT = (0.15, 0.60)


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rate = _DEFAULT
    lowered = model.lower()
    for name, candidate in _RATES.items():
        if name in lowered:
            rate = candidate
            break
    return (prompt_tokens * rate[0] + completion_tokens * rate[1]) / 1_000_000
