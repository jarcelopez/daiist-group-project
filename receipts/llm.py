"""
Gemini client (COMP-10). Task 0.4: the pinned model and its prices.
Task 2.1 adds call(): retries, token and € accounting, and the disk cache.

Model choice (checked 2026-11-27 with a free key, test image eval/inputs/test__T-001.jpg):
- gemini-3.5-flash-lite: correct total, 0.6-1.0 s on 5 of 5 calls.
- gemini-3.6-flash: correct, but 18 s, 48 s and 298 s, even with thinking turned down.
- gemini-3.5-flash: correct, 49-100 s; no longer on Google's price page (deprecated).
- gemini-3.7-flash, gemini-3.8-flash: 503 "high demand". gemini-2.5-flash: 404, closed to new users.
"""

MODEL = "gemini-3.5-flash-lite"  # used by the vision step and the policy agent

# Paid-tier prices, Standard tier, from https://ai.google.dev/gemini-api/docs/pricing
# (checked 2026-11-27). Output includes thinking tokens. The free tier costs nothing,
# but Google may use free-tier data to improve its products; paid-tier data is not.
PRICES_CHECKED = "2026-11-27"
USD_PER_EUR = 1.0975  # ECB reference rate, 2026-11-26
PRICES_USD = {  # model: (USD per 1M input tokens, USD per 1M output tokens)
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.6-flash": (0.75, 3.75),  # rises to (1.50, 7.50) on 2027-01-01; kept in case we switch
}
PRICES = {  # model: (€ per 1M input tokens, € per 1M output tokens)
    model: (round(usd_in / USD_PER_EUR, 4), round(usd_out / USD_PER_EUR, 4))
    for model, (usd_in, usd_out) in PRICES_USD.items()
}


def cost_eur(model: str, tokens_in: int, tokens_out: int) -> float:
    """€ cost of one call at paid-tier prices (thinking tokens count as output)."""
    eur_in, eur_out = PRICES[model]
    return (tokens_in * eur_in + tokens_out * eur_out) / 1_000_000
