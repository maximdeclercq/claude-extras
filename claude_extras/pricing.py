"""Cost estimation for Claude token usage, at public list prices.

Matched per model family rather than per tier, since tiers reprice.
"""

MTOK = 1_000_000

# (model id fragment, input $/MTok, output $/MTok), first match wins.
PRICES = (
    ("claude-fable-5", 10.0, 50.0),
    ("claude-mythos-5", 10.0, 50.0),
    ("claude-opus-5", 5.0, 25.0),
    ("claude-opus-4-8", 5.0, 25.0),
    ("claude-opus-4-7", 5.0, 25.0),
    ("claude-opus-4-6", 5.0, 25.0),
    ("claude-sonnet-5", 3.0, 15.0),
    ("claude-sonnet-4-6", 3.0, 15.0),
    ("claude-sonnet-4-5", 3.0, 15.0),
    ("claude-haiku-4-5", 1.0, 5.0),
)

CACHE_WRITE_5M_MULT = 1.25
CACHE_WRITE_1H_MULT = 2.0
CACHE_READ_MULT = 0.10


def price_for(model):
    """Return (input, output) price for a model id, or None if unpriced."""
    name = model.lower()
    for fragment, inp, out in PRICES:
        if fragment in name:
            return (inp, out)
    return None


def cost(bucket, model):
    """USD estimate for a Bucket on a given model, or None if the model is unpriced."""
    price = price_for(model)
    if price is None:
        return None
    inp, out = price
    return (
        bucket.input * inp
        + bucket.output * out
        + bucket.cache_write_5m * inp * CACHE_WRITE_5M_MULT
        + bucket.cache_write_1h * inp * CACHE_WRITE_1H_MULT
        + bucket.cache_read * inp * CACHE_READ_MULT
    ) / MTOK
